"""
text_to_speak.py - play a string as text-to-speech with ElevenLabs, in the voice of one of
four personalities.

    from text_to_speak import text_to_speak
    text_to_speak("Nice work, your timing is getting there.", "gordon")

Personalities: priyaa, gordon, ada, bobby (not case sensitive).

Setup:
    pip install elevenlabs
    put your key in a file called .env next to this file, on one line:
        ELEVENLABS_API_KEY=your_key_here
    (or export ELEVENLABS_API_KEY in your terminal). Never commit .env to git.

How it works: the text is sent to ElevenLabs, the audio comes back as an mp3, and it is played
with the Mac's built-in `afplay`. If ElevenLabs fails (no internet, no key, out of credits),
you get a clear error. For a live demo you can pass fallback_to_say=True so it speaks with
the standard Mac voice instead of going silent.

To change a personality's voice, replace its voice_id in PERSONALITIES below. Browse voices at
elevenlabs.io/app/voice-library and copy the ID of the one you want. To try a different model,
set ELEVENLABS_MODEL (default below).

To run it from the command line, use speak_main.py.
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

MODEL_ID = os.environ.get("ELEVENLABS_MODEL", "eleven_multilingual_v2")
OUTPUT_FORMAT = "mp3_44100_128"

# The voices below are ElevenLabs "default" voices (available to every account).
# priyaa is a placeholder: swap in an Indian-English voice from the Voice Library.
PERSONALITIES = {
    "priyaa": {"voice_name": "Sarah", "voice_id": "EXAVITQu4vr4xnSDxMaL",
               "intro": "Hi, I'm Priyaa. Let's look at how that routine went."},
    "gordon": {"voice_name": "Daniel", "voice_id": "onwK4e9ZLuTAKqWW03F9",       # British, authoritative
               "intro": "Right, I'm Gordon. Let's get that routine tightened up."},
    "ada":    {"voice_name": "Alice", "voice_id": "Xb7hH8MSUJpSbSDYk0k2",        # British, confident
               "intro": "Hello, I'm Ada. Let's go through your routine carefully."},
    "bobby":  {"voice_name": "Chris", "voice_id": "iP95p4xoKVk53GoZ742B",        # American, casual
               "intro": "Hey, I'm Bobby. Nice and easy, let's see how that went."},
}


def _load_env_file():
    """If ELEVENLABS_API_KEY isn't set, read it from a .env file (next to this file or in the cwd)."""
    if os.environ.get("ELEVENLABS_API_KEY"):
        return
    for folder in (Path(__file__).resolve().parent, Path.cwd()):
        env = folder / ".env"
        if not env.is_file():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ELEVENLABS_API_KEY") and "=" in line:
                value = line.split("=", 1)[1].strip().strip('"').strip("'")
                if value:
                    os.environ["ELEVENLABS_API_KEY"] = value
                    return


def _generate_audio(text, voice_id):
    """Ask ElevenLabs for the audio; returns mp3 bytes."""
    try:
        from elevenlabs.client import ElevenLabs
    except ImportError:
        raise RuntimeError("The elevenlabs package isn't installed. Run: pip install elevenlabs")
    _load_env_file()
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("No ElevenLabs key found. Put ELEVENLABS_API_KEY=... in a .env file "
                           "next to text_to_speak.py.")
    client = ElevenLabs(api_key=key)
    audio = client.text_to_speech.convert(
        text=text, voice_id=voice_id, model_id=MODEL_ID, output_format=OUTPUT_FORMAT)
    data = bytes(audio) if isinstance(audio, (bytes, bytearray)) else \
        b"".join(chunk for chunk in audio if isinstance(chunk, bytes))
    if not data:
        raise RuntimeError("ElevenLabs returned no audio.")
    return data


def _play_mp3(data):
    """Play mp3 bytes through the speakers (macOS afplay), then delete the temp file."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        subprocess.run(["afplay", path], check=True)
    finally:
        os.remove(path)


def text_to_speak(text, personality, fallback_to_say=False):
    """Speak `text` out loud in the voice for `personality` (priyaa, gordon, ada or bobby).

    Waits until it has finished speaking. Returns the name of the voice used.
    If ElevenLabs fails this raises an error, unless fallback_to_say=True, in which case it
    speaks with the standard Mac voice and returns "Mac fallback voice".
    """
    key = str(personality).strip().lower()
    if key not in PERSONALITIES:
        raise ValueError(f"Unknown personality {personality!r}. "
                         f"Choose one of: {', '.join(PERSONALITIES)}")
    if not isinstance(text, str) or not text.strip():
        return None
    if platform.system() != "Darwin" or shutil.which("afplay") is None:
        raise RuntimeError("text_to_speak() plays audio with the macOS 'afplay' command, "
                           "which isn't available here.")

    cfg = PERSONALITIES[key]
    try:
        _play_mp3(_generate_audio(text, cfg["voice_id"]))
        return cfg["voice_name"]
    except Exception as exc:        # network, key, credits, playback...
        if not fallback_to_say:
            raise
        print(f"ElevenLabs failed ({type(exc).__name__}: {exc}); using the Mac voice instead.",
              file=sys.stderr)
        subprocess.run(["say"], input=text, text=True, check=True)
        return "Mac fallback voice"
