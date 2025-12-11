#!/usr/bin/env python3
import asyncio
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP

# Avoid stdout logging; MCP uses stdout for JSON-RPC
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")

mcp = FastMCP("agent-say")

# Adaptive timeout parameters
DEFAULT_CHARS_PER_SEC = 6.0  # fallback speech rate
CJK_SLOWDOWN_FACTOR = 1.35  # slow down estimate for Japanese/Chinese/Korean text
MIN_TIMEOUT = 5.0
AUTO_CAP_SECONDS = 300.0  # prefer full read: floor to 300s unless explicitly overridden
DEFAULT_TIMEOUT_SECONDS = 20.0
BUFFER_SECONDS = 2.0
SMOOTHING = 0.2  # exponential moving average factor

# Keep a simple in-memory per-engine rolling average of speed
engine_cps: dict[str, float] = {}


def strip_markdown(text: str) -> str:
    """Remove common markdown syntax for cleaner speech."""
    text = re.sub(r"(\*\*|__)(.*?)\1", r"\2", text)
    text = re.sub(r"(\*|_)(.*?)\1", r"\2", text)
    text = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"^>\s?", "", text, flags=re.MULTILINE)
    text = re.sub(r"^#+\s?", "", text, flags=re.MULTILINE)
    return text.strip()


def ensure_tmpdir() -> Path:
    """Ensure TMPDIR points to a workspace-writeable path."""
    current = os.environ.get("TMPDIR")
    if current:
        path = Path(current)
        if path.is_dir() and os.access(path, os.W_OK):
            return path
    fallback = Path(__file__).resolve().parent / "tmp"
    fallback.mkdir(parents=True, exist_ok=True)
    os.environ["TMPDIR"] = str(fallback)
    return fallback


def get_cps(engine_name: str | None) -> float:
    """Return current chars/sec estimate for the engine."""
    key = engine_name or "auto"
    return engine_cps.get(key, DEFAULT_CHARS_PER_SEC)


def update_cps(engine_name: str | None, measured_cps: float) -> None:
    """Update exponential moving average of chars/sec for the engine."""
    if measured_cps <= 0:
        return
    key = engine_name or "auto"
    prev = engine_cps.get(key, DEFAULT_CHARS_PER_SEC)
    engine_cps[key] = prev * (1 - SMOOTHING) + measured_cps * SMOOTHING


def estimate_timeout(text: str, base_timeout: float, engine_name: str | None) -> float:
    """Estimate timeout from text length and engine speed."""
    # Slow down estimate for CJK text because TTS expands kanji to syllables.
    factor = CJK_SLOWDOWN_FACTOR if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text) else 1.0
    cps = get_cps(engine_name)
    est = len(text) * factor / max(cps, 1e-3) + BUFFER_SECONDS
    est = max(est, base_timeout, MIN_TIMEOUT)

    # Respect explicit timeout requests as-is (other than the above minimum floor).
    if base_timeout != DEFAULT_TIMEOUT_SECONDS:
        return est

    # Auto mode: prefer finishing the read even if slow, floor to 300s, but allow
    # even longer runs when the estimate exceeds that.
    if est >= AUTO_CAP_SECONDS:
        return est
    return AUTO_CAP_SECONDS


async def run_cmd(cmd: list[str], timeout: float) -> tuple[int, str, str]:
    env = os.environ.copy()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return -2, "", "command not found"
    except PermissionError:
        return -3, "", "command not executable"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", f"timeout after {timeout} seconds"
    return proc.returncode, stdout.decode(), stderr.decode()


def pick_command(engine: str | None) -> list[str] | None:
    if engine in (None, "", "auto", "say"):
        if shutil.which("say"):
            return ["say"]
        if engine not in (None, "", "auto"):
            return None
    if engine in (None, "", "auto", "swift"):
        swift = Path(__file__).resolve().parent / "swift_tts.swift"
        if swift.exists():
            return [str(swift)]
        if engine == "swift":
            return None
    if engine in (None, "", "auto", "espeak"):
        if shutil.which("espeak"):
            return ["espeak"]
    return None


@mcp.tool()
async def speak(
    text: str,
    engine: Literal["auto", "say", "swift", "espeak"] | None = "auto",
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    warmup: bool = False,
) -> str:
    """音声で読み上げます。engineはauto/say/swift/espeakから選択できます。"""
    ensure_tmpdir()
    clean_text = strip_markdown(text)
    cmd = pick_command(engine)
    if not cmd:
        return "No available speech engine (say/swift/espeak)."
    engine_name = Path(cmd[0]).name

    if warmup:
        code, _, err = await run_cmd(cmd + ["ウォームアップ"], timeout_seconds)
        if code != 0:
            logging.warning("Warmup failed: %s", err.strip())

    dynamic_timeout = estimate_timeout(clean_text, timeout_seconds, engine_name)
    start = time.monotonic()
    code, _, err = await run_cmd(cmd + [clean_text], dynamic_timeout)
    duration = time.monotonic() - start
    if code == 0 and duration > 0:
        measured_cps = len(clean_text) / duration
        update_cps(engine_name, measured_cps)
    if code == -2:
        return "Speech engine command not found."
    if code == -3:
        return "Speech engine is not executable."
    if code == 0:
        return f"Spoken with {Path(cmd[0]).name}."
    if code == -1:
        return f"Speech timed out after {dynamic_timeout} seconds."
    return f"Speech failed with code {code}: {err.strip()}"


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
