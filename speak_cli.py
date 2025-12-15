#!/usr/bin/env python3
import sys
import subprocess
import re

DEFAULT_WPM = 175

def strip_markdown(text):
    """
    Remove common markdown syntax for cleaner speech.
    """
    # Remove bold/italic (**text**, __text__, *text*, _text_)
    text = re.sub(r'(\*\*|__)(.*?)\1', r'\2', text)
    text = re.sub(r'(\*|_)(.*?)\1', r'\2', text)
    # Remove links [text](url) -> text
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)
    # Remove code `text` -> text
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Remove blockquotes > text
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)
    # Remove headers # text
    text = re.sub(r'^#+\s?', '', text, flags=re.MULTILINE)
    return text.strip()

def speak_reply(text, *, speed=1.0):
    # Strip markdown for cleaner speech
    clean_text = strip_markdown(text)
    wpm = max(80, min(600, int(round(DEFAULT_WPM * speed))))
    
    try:
        # Use 'say' command directly. The agent's behavior is now corrected
        # to wait for the next user turn, so we no longer need a blocking call.
        subprocess.run(["say", "-r", str(wpm), clean_text], check=True)
    except FileNotFoundError:
        print("Info: 'say' command not found. Falling back to 'espeak'...")
        try:
            # Fallback to espeak.
            subprocess.run(["espeak", "-s", str(wpm), clean_text], check=True)
        except FileNotFoundError:
            print("Error: No suitable speech engine found (say or espeak).")
    except subprocess.CalledProcessError as e:
        print(f"Error speaking text: {e}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python speak_cli.py [--speed <multiplier>] <text_to_speak>")
        sys.exit(1)

    args = sys.argv[1:]
    speed = 1.0
    if len(args) >= 2 and args[0] == "--speed":
        try:
            speed = float(args[1])
        except ValueError:
            print("Error: invalid --speed value (must be a number)")
            sys.exit(2)
        if speed <= 0:
            print("Error: invalid --speed value (must be positive)")
            sys.exit(2)
        args = args[2:]
    if not args:
        print("Error: text is required")
        sys.exit(2)
    text_to_speak = " ".join(args)
    speak_reply(text_to_speak, speed=speed)

if __name__ == "__main__":
    main()
