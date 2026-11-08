"""
text_to_speak.py - play a string as text-to-speech with ElevenLabs, in the voice of one of
four personalities.

    from text_to_speak import text_to_speak
    text_to_speak("Nice work, your timing is getting there.", "gordon")

Personalities: priyaa, gordon, ada, bobby (not case sensitive).

Setup:
    pip install elevenlabs
    put your key in the repo's .env file (copy .env.example to .env), on one line:
        ELEVENLABS_API_KEY=your_key_here
    (or export ELEVENLABS_API_KEY in your terminal). Never commit .env to git.

How it works: the text is sent to ElevenLabs, the audio comes back as an mp3, and it is played
with whatever the computer has: `afplay` on macOS, Windows Media Player (through PowerShell)
on Windows, and ffplay / mpg123 / mpv on Linux. If ElevenLabs fails (no internet, no key, out
of credits), you get a clear error. For a live demo you can pass fallback_to_say=True so it
speaks with the computer's built-in voice instead of going silent.

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
               "intro": "Hi, I'm Priyaa. Let's look at how that routine went.",
               # built-in fallback voices, so each coach still sounds different offline
               "mac": ["Veena", "Isha", "Samantha"], "win": ("female", 0), "espeak": "en-gb+f3"},
    "gordon": {"voice_name": "Daniel", "voice_id": "onwK4e9ZLuTAKqWW03F9",       # British, authoritative
               "intro": "Right, I'm Gordon. Let's get that routine tightened up.",
               "mac": ["Daniel", "Oliver", "Arthur"], "win": ("male", 0), "espeak": "en-gb+m3"},
    "ada":    {"voice_name": "Alice", "voice_id": "Xb7hH8MSUJpSbSDYk0k2",        # British, confident
               "intro": "Hello, I'm Ada. Let's go through your routine carefully.",
               "mac": ["Kate", "Serena", "Martha", "Karen"], "win": ("female", 1), "espeak": "en-gb+f2"},
    "bobby":  {"voice_name": "Chris", "voice_id": "iP95p4xoKVk53GoZ742B",        # American, casual
               "intro": "Hey, I'm Bobby. Nice and easy, let's see how that went.",
               "mac": ["Alex", "Tom", "Fred", "Ralph"], "win": ("male", 1), "espeak": "en-us+m7"},
}


HERE = Path(__file__).resolve().parent
REPO_DIR = HERE.parent


def _load_env_file():
    """If ELEVENLABS_API_KEY isn't set, read it from a .env file: the repo root first,
    then next to this file, then the current folder."""
    if os.environ.get("ELEVENLABS_API_KEY"):
        return
    for folder in (REPO_DIR, HERE, Path.cwd()):
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
        raise RuntimeError("No ElevenLabs key found. Put ELEVENLABS_API_KEY=... in the .env file "
                           "at the root of the repo (see .env.example).")
    client = ElevenLabs(api_key=key)
    audio = client.text_to_speech.convert(
        text=text, voice_id=voice_id, model_id=MODEL_ID, output_format=OUTPUT_FORMAT)
    data = bytes(audio) if isinstance(audio, (bytes, bytearray)) else \
        b"".join(chunk for chunk in audio if isinstance(chunk, bytes))
    if not data:
        raise RuntimeError("ElevenLabs returned no audio.")
    return data


# Windows: play an mp3 with the built-in Windows Media Player engine, and wait for it to end.
_WINDOWS_PLAY = (
    "Add-Type -AssemblyName PresentationCore; "
    "$p = New-Object System.Windows.Media.MediaPlayer; $p.Open([uri]$args[0]); "
    "$n = 0; while (-not $p.NaturalDuration.HasTimeSpan -and $n -lt 100) "
    "{ Start-Sleep -Milliseconds 50; $n++ }; "
    "$p.Play(); Start-Sleep -Milliseconds ([int]$p.NaturalDuration.TimeSpan.TotalMilliseconds + 200); "
    "$p.Close()"
)


def _player_command(path):
    """The command that plays an mp3 file on this computer, or None if there isn't one."""
    system = platform.system()
    if system == "Darwin" and shutil.which("afplay"):
        return ["afplay", path]
    if system == "Windows":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            return [ps, "-NoProfile", "-Command", _WINDOWS_PLAY, path]
    for cmd in (["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"],   # Linux and others
                ["mpg123", "-q"], ["mpv", "--no-video", "--really-quiet"]):
        if shutil.which(cmd[0]):
            return cmd + [path]
    return None


def can_play_audio():
    """True if this computer has something text_to_speak() can play mp3s with."""
    return _player_command("x.mp3") is not None


def _play_mp3(data):
    """Play mp3 bytes through the speakers, then delete the temp file."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        cmd = _player_command(path)
        if cmd is None:
            raise RuntimeError("No audio player found. On Linux install one of: "
                               "ffmpeg (ffplay), mpg123 or mpv.")
        subprocess.run(cmd, check=True)
    finally:
        os.remove(path)


def _mac_voice(candidates):
    """First of `candidates` that is installed on this Mac (None = system default voice)."""
    try:
        listing = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    except OSError:
        return None
    installed = {line.split()[0].lower() for line in listing.splitlines() if line.strip()}
    return next((v for v in candidates if v.lower() in installed), None)


# Windows: pick the Nth installed voice of a gender, so two female coaches get different voices.
_WINDOWS_SAY = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$v = @($s.GetInstalledVoices() | Where-Object { $_.Enabled -and "
    "$_.VoiceInfo.Gender -eq $args[0] }); "
    "if ($v.Count -gt 0) { $s.SelectVoice($v[[int]$args[1] % $v.Count].VoiceInfo.Name) }; "
    "$s.Rate = [int]$args[2]; "
    "$s.Speak([Console]::In.ReadToEnd())"
)


def _say_fallback(text, cfg):
    """Speak with the computer's own built-in voices (no internet needed), using a different
    voice for each personality so the coaches still sound different."""
    system = platform.system()
    if system == "Darwin":
        voice = _mac_voice(cfg.get("mac", []))
        subprocess.run(["say"] + (["-v", voice] if voice else []), input=text, text=True, check=True)
    elif system == "Windows":
        ps = shutil.which("powershell") or shutil.which("pwsh")
        gender, index = cfg.get("win", ("female", 0))
        rate = "1" if index else "0"              # nudge the speed too, in case only one voice exists
        subprocess.run([ps, "-NoProfile", "-Command", _WINDOWS_SAY, gender.capitalize(), str(index), rate],
                       input=text, text=True, check=True)
    else:
        tool = shutil.which("espeak-ng") or shutil.which("espeak") or shutil.which("spd-say")
        if not tool:
            raise RuntimeError("No built-in voice found. On Linux install espeak-ng.")
        if tool.endswith("spd-say"):
            args = [tool, "--wait", text]
        else:
            args = [tool, "-v", cfg.get("espeak", "en"), text]
        subprocess.run(args, check=True)


def text_to_speak(text, personality, fallback_to_say=False):
    """Speak `text` out loud in the voice for `personality` (priyaa, gordon, ada or bobby).

    Waits until it has finished speaking. Returns the name of the voice used.
    If ElevenLabs fails this raises an error, unless fallback_to_say=True, in which case it
    speaks with the computer's built-in voice and returns "built-in fallback voice".
    """
    key = str(personality).strip().lower()
    if key not in PERSONALITIES:
        raise ValueError(f"Unknown personality {personality!r}. "
                         f"Choose one of: {', '.join(PERSONALITIES)}")
    if not isinstance(text, str) or not text.strip():
        return None
    cfg = PERSONALITIES[key]
    try:
        _play_mp3(_generate_audio(text, cfg["voice_id"]))
        return cfg["voice_name"]
    except Exception as exc:        # network, key, credits, playback...
        if not fallback_to_say:
            raise
        print("\n" + "!" * 70 +
              f"\n  ELEVENLABS FAILED for '{key}': {type(exc).__name__}: {exc}"
              "\n  Using the computer's built-in voice instead (not the ElevenLabs voice)."
              "\n" + "!" * 70, file=sys.stderr)
        _say_fallback(text, cfg)
        return "built-in fallback voice"


def check_elevenlabs():
    """Quick diagnosis: is the key found, and can it make audio with each voice? Plays nothing."""
    _load_env_file()
    key = os.environ.get("ELEVENLABS_API_KEY")
    print("API key:", f"found (ends ...{key[-4:]})" if key else "NOT FOUND")
    for name, cfg in PERSONALITIES.items():
        try:
            n = len(_generate_audio("Test.", cfg["voice_id"]))
            print(f"  {name:7} {cfg['voice_name']:7} OK ({n} bytes of audio)")
        except Exception as exc:
            print(f"  {name:7} {cfg['voice_name']:7} FAILED: {type(exc).__name__}: {exc}")


if __name__ == "__main__":
    check_elevenlabs()
