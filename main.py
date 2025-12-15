#!/usr/bin/env python3
import asyncio
import hashlib
import logging
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path
from typing import Literal, Optional

# This project targets Python 3.13+ (see pyproject.toml). Keep the file parsable
# on older Pythons so accidental direct execution yields a clear error.
MIN_PYTHON = (3, 13)
if sys.version_info < MIN_PYTHON:
    print(
        f"agent-say requires Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ (current: {sys.version.split()[0]}).",
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as e:
    if e.name == "mcp":
        print(
            "Missing dependency 'mcp'. If you're running from the repo, install deps first (e.g. `uv sync`).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    raise

# Avoid stdout logging; MCP uses stdout for JSON-RPC
logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(levelname)s %(message)s")

mcp = FastMCP("agent-say")

# Adaptive timeout parameters
DEFAULT_CHARS_PER_SEC = 6.0  # fallback speech rate
CJK_SLOWDOWN_FACTOR = 1.35  # slow down estimate for Japanese/Chinese/Korean text
MIN_TIMEOUT = 5.0
AUTO_CAP_SECONDS = 300.0  # prefer full read: floor to 300s unless explicitly overridden
DEFAULT_TIMEOUT_SECONDS = 20.0
HARD_TIMEOUT_SECONDS = 600.0  # hard upper bound to avoid stuck speech processes
BUFFER_SECONDS = 2.0
SMOOTHING = 0.2  # exponential moving average factor
MAX_CONCURRENT_SPEECH = 2
DEFAULT_DEDUPE_SECONDS = 30.0

# Speed control
DEFAULT_WPM = 175  # baseline for say/espeak
MIN_SPEED = 0.25
MAX_SPEED = 4.0

# Keep a simple in-memory per-engine rolling average of speed
engine_cps: dict[str, float] = {}

# Best-effort in-memory guards / bookkeeping
speech_semaphore = asyncio.Semaphore(MAX_CONCURRENT_SPEECH)
in_flight: set[str] = set()
recent_requests: dict[str, float] = {}
background_tasks: set[asyncio.Task[str]] = set()
active_procs: dict[str, asyncio.subprocess.Process] = {}


def request_key(engine_name: str, text: str, speed: float) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    speed_tag = f"{speed:.3f}"
    return f"{engine_name}:{speed_tag}:{digest}"


def prune_recent_requests(now: float, keep_seconds: float) -> None:
    if keep_seconds <= 0:
        recent_requests.clear()
        return
    cutoff = now - keep_seconds
    for key, ts in list(recent_requests.items()):
        if ts < cutoff:
            recent_requests.pop(key, None)


def format_status(
    *,
    engine_name: str,
    mode: str,
    speed: float,
    dedupe_seconds: float,
    hard_timeout_seconds: float,
    timeout_seconds: float,
    timeout_used: Optional[float],
    dynamic_timeout: Optional[float],
) -> str:
    parts: list[str] = [
        f"engine={engine_name}",
        f"mode={mode}",
        f"speed={speed:g}x",
        f"hard_timeout={hard_timeout_seconds:g}s",
        f"dedupe={dedupe_seconds:g}s",
        f"concurrency={MAX_CONCURRENT_SPEECH}",
    ]
    if mode == "sync":
        parts.append(f"timeout_seconds={timeout_seconds:g}s")
        if dynamic_timeout is not None:
            parts.append(f"dynamic_timeout={dynamic_timeout:g}s")
        if timeout_used is not None:
            parts.append(f"timeout_used={timeout_used:g}s")
    else:
        parts.append(f"timeout_seconds={timeout_seconds:g}s(ignored)")
    return "(" + ", ".join(parts) + ")"


async def stop_process(proc: asyncio.subprocess.Process, timeout: float = 2.0) -> None:
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()


async def run_speech_process(
    key: str,
    cmd: list[str],
    text: str,
    *,
    timeout_seconds: Optional[float],
    hard_timeout_seconds: float,
) -> tuple[int, float, str]:
    """
    Run speech engine and wait for completion.
    Returns (code, duration_seconds, err_message).
    code: 0 ok, -1 timed out, -2 not found, -3 not executable, else engine return code.
    """
    env = os.environ.copy()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            text,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return -2, 0.0, "command not found"
    except PermissionError:
        return -3, 0.0, "command not executable"

    active_procs[key] = proc
    start = time.monotonic()
    try:
        effective_timeout = None
        if timeout_seconds is not None and timeout_seconds > 0:
            effective_timeout = min(timeout_seconds, hard_timeout_seconds)
        else:
            effective_timeout = hard_timeout_seconds
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=effective_timeout)
        except asyncio.TimeoutError:
            await stop_process(proc)
            return -1, time.monotonic() - start, f"timeout after {effective_timeout} seconds"
        return proc.returncode, time.monotonic() - start, stderr.decode()
    finally:
        active_procs.pop(key, None)


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
            tmpdir = path
        else:
            tmpdir = None
    else:
        tmpdir = None

    if tmpdir is None:
        tmpdir = Path(__file__).resolve().parent / "tmp"
        tmpdir.mkdir(parents=True, exist_ok=True)
        os.environ["TMPDIR"] = str(tmpdir)

    # Swift (and its clang integration) may try to write module caches under ~/.cache.
    # Force a workspace-writable cache path when needed.
    clang_cache = os.environ.get("CLANG_MODULE_CACHE_PATH")
    if clang_cache:
        cache_path = Path(clang_cache)
        if not (cache_path.is_dir() and os.access(cache_path, os.W_OK)):
            cache_path = tmpdir / "clang-module-cache"
            cache_path.mkdir(parents=True, exist_ok=True)
            os.environ["CLANG_MODULE_CACHE_PATH"] = str(cache_path)
    else:
        cache_path = tmpdir / "clang-module-cache"
        cache_path.mkdir(parents=True, exist_ok=True)
        os.environ["CLANG_MODULE_CACHE_PATH"] = str(cache_path)

    return tmpdir


def get_cps(engine_name: Optional[str]) -> float:
    """Return current chars/sec estimate for the engine."""
    key = engine_name or "auto"
    return engine_cps.get(key, DEFAULT_CHARS_PER_SEC)


def update_cps(engine_name: Optional[str], measured_cps: float) -> None:
    """Update exponential moving average of chars/sec for the engine (normalized to speed=1.0)."""
    if measured_cps <= 0:
        return
    if not math.isfinite(measured_cps):
        return
    key = engine_name or "auto"
    prev = engine_cps.get(key, DEFAULT_CHARS_PER_SEC)
    engine_cps[key] = prev * (1 - SMOOTHING) + measured_cps * SMOOTHING


def estimate_timeout(text: str, base_timeout: float, engine_name: Optional[str], speed: float) -> float:
    """Estimate timeout from text length and engine speed."""
    # Slow down estimate for CJK text because TTS expands kanji to syllables.
    factor = CJK_SLOWDOWN_FACTOR if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text) else 1.0
    cps = get_cps(engine_name) * max(speed, 1e-3)
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


def pick_command(engine: Optional[str]) -> Optional[list[str]]:
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


def with_speed_args(cmd: list[str], engine_name: str, speed: float) -> list[str]:
    if speed == 1.0:
        return cmd
    wpm = int(round(DEFAULT_WPM * speed))
    # Conservative clamps to avoid extreme values.
    wpm = max(80, min(600, wpm))

    if engine_name == "say":
        return cmd + ["-r", str(wpm)]
    if engine_name == "espeak":
        return cmd + ["-s", str(wpm)]
    if engine_name == "swift_tts.swift":
        return cmd + ["--speed", f"{speed:g}"]
    return cmd


@mcp.tool()
async def speak(
    text: str,
    engine: Optional[Literal["auto", "say", "swift", "espeak"]] = "auto",
    speed: float = 1.0,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    warmup: bool = False,
    wait_for_completion: bool = False,
    dedupe_seconds: float = DEFAULT_DEDUPE_SECONDS,
    hard_timeout_seconds: float = HARD_TIMEOUT_SECONDS,
) -> str:
    """音声で読み上げます。engineはauto/say/swift/espeakから選択できます。"""
    ensure_tmpdir()
    clean_text = strip_markdown(text)
    if not clean_text:
        return "Text is empty."
    if not math.isfinite(speed) or speed <= 0:
        return "Invalid speed (must be a positive number)."
    if speed < MIN_SPEED or speed > MAX_SPEED:
        return f"Invalid speed (supported range: {MIN_SPEED:g}..{MAX_SPEED:g})."
    cmd = pick_command(engine)
    if not cmd:
        return "No available speech engine (say/swift/espeak)."
    engine_name = Path(cmd[0]).name
    cmd = with_speed_args(cmd, engine_name, speed)
    mode = "sync" if wait_for_completion else "async"

    now = time.monotonic()
    prune_recent_requests(now, max(dedupe_seconds * 2, 60.0) if dedupe_seconds > 0 else 0)
    key = request_key(engine_name, clean_text, speed)
    if dedupe_seconds > 0:
        if key in in_flight:
            return f"Speech already running {format_status(engine_name=engine_name, mode=mode, speed=speed, dedupe_seconds=dedupe_seconds, hard_timeout_seconds=hard_timeout_seconds, timeout_seconds=timeout_seconds, timeout_used=None, dynamic_timeout=None)}"
        last = recent_requests.get(key)
        if last is not None and (now - last) < dedupe_seconds:
            return f"Speech request deduped {format_status(engine_name=engine_name, mode=mode, speed=speed, dedupe_seconds=dedupe_seconds, hard_timeout_seconds=hard_timeout_seconds, timeout_seconds=timeout_seconds, timeout_used=None, dynamic_timeout=None)}"
    recent_requests[key] = now

    async def _run() -> str:
        in_flight.add(key)
        try:
            if warmup:
                code, _, err = await run_speech_process(
                    key,
                    cmd,
                    "ウォームアップ",
                    timeout_seconds=timeout_seconds,
                    hard_timeout_seconds=hard_timeout_seconds,
                )
                if code != 0:
                    logging.warning("Warmup failed: %s", err.strip())

            timeout_used: Optional[float] = None
            dynamic_timeout: Optional[float] = None
            if wait_for_completion:
                dynamic_timeout = estimate_timeout(clean_text, timeout_seconds, engine_name, speed)
                timeout_used = min(dynamic_timeout, hard_timeout_seconds)
                code, duration, err = await run_speech_process(
                    key,
                    cmd,
                    clean_text,
                    timeout_seconds=dynamic_timeout,
                    hard_timeout_seconds=hard_timeout_seconds,
                )
            else:
                # Non-blocking mode: do not apply adaptive timeout; rely on a hard cap only.
                timeout_used = hard_timeout_seconds
                code, duration, err = await run_speech_process(
                    key,
                    cmd,
                    clean_text,
                    timeout_seconds=None,
                    hard_timeout_seconds=hard_timeout_seconds,
                )

            if code == 0 and duration > 0:
                measured_cps = len(clean_text) / duration
                update_cps(engine_name, measured_cps / max(speed, 1e-3))
            if code == -2:
                return "Speech engine command not found."
            if code == -3:
                return "Speech engine is not executable."
            if code == 0:
                return f"Spoken {format_status(engine_name=engine_name, mode=mode, speed=speed, dedupe_seconds=dedupe_seconds, hard_timeout_seconds=hard_timeout_seconds, timeout_seconds=timeout_seconds, timeout_used=timeout_used, dynamic_timeout=dynamic_timeout)}"
            if code == -1:
                timeout_label = timeout_used if timeout_used is not None else hard_timeout_seconds
                return f"Speech timed out after {timeout_label:g}s {format_status(engine_name=engine_name, mode=mode, speed=speed, dedupe_seconds=dedupe_seconds, hard_timeout_seconds=hard_timeout_seconds, timeout_seconds=timeout_seconds, timeout_used=timeout_used, dynamic_timeout=dynamic_timeout)}"
            return f"Speech failed with code {code}: {err.strip()}"
        finally:
            in_flight.discard(key)
            recent_requests[key] = time.monotonic()

    if wait_for_completion:
        async with speech_semaphore:
            return await _run()

    try:
        await asyncio.wait_for(speech_semaphore.acquire(), timeout=0.01)
    except asyncio.TimeoutError:
        return f"Speech busy (too many concurrent requests) {format_status(engine_name=engine_name, mode=mode, speed=speed, dedupe_seconds=dedupe_seconds, hard_timeout_seconds=hard_timeout_seconds, timeout_seconds=timeout_seconds, timeout_used=None, dynamic_timeout=None)}"
    task: asyncio.Task[str] = asyncio.create_task(_run())

    def _release(_: asyncio.Task[str]) -> None:
        try:
            speech_semaphore.release()
        except ValueError:
            pass
        background_tasks.discard(task)

    background_tasks.add(task)
    task.add_done_callback(_release)
    return f"Speech started {format_status(engine_name=engine_name, mode=mode, speed=speed, dedupe_seconds=dedupe_seconds, hard_timeout_seconds=hard_timeout_seconds, timeout_seconds=timeout_seconds, timeout_used=hard_timeout_seconds, dynamic_timeout=None)}"


@mcp.tool()
async def stop_speech(all: bool = True) -> str:
    """現在実行中の読み上げプロセスを停止します。"""
    if not active_procs:
        return "No active speech."
    keys = list(active_procs.keys())
    if not all:
        keys = keys[-1:]
    for key in keys:
        proc = active_procs.get(key)
        if proc:
            await stop_process(proc)
    return f"Stopped speech ({len(keys)})."


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
