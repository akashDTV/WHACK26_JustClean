# WHACK26 — Live Dance Coach

Copy a dance from a reference video in front of your webcam, get scored live, then get a
debrief from an AI coach.

Every path in the code is worked out from where each file sits in the repo, so you can
clone it anywhere and run the scripts from any folder.

## Setup (once)

You need **Python 3.10–3.12** (mediapipe doesn't support 3.13 yet) and a webcam.

```bash
git clone https://github.com/sillybabs/WHACK26.git
cd WHACK26
python3 -m venv .venv          # Windows: py -3.12 -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

API keys (only needed for the Claude debrief and the ElevenLabs voices):

```bash
cp .env.example .env           # Windows: copy .env.example .env
```

Then put your own keys in `.env`. It is git-ignored, so the keys never get committed.
Without keys, everything still runs: the debrief falls back to a template.

## Run it

All commands are run from the repo root, but they work from any folder.

| What | Command | Then |
|---|---|---|
| **Live coach** (webcam + scoring + web page) | `python sandbox/pose_webcam.py` | Open http://localhost:8000 and press *Start dancing* (or press SPACE in the video window) |
| **Debrief server** (no camera, made-up live scores, real Claude debrief) | `python to_claude_program/debrief_server.py` | Open http://localhost:8000. Add `--no-api` to skip Claude |
| **Debrief to files** | `python to_claude_program/main.py test3` | Files are written to `to_claude_program/output/test3/` |
| **Coach voice** | `python text_to_speech/speak_main.py` | Plays the line set in `speak_main.py` |
| **Web page only** (fake data, no Python) | Double-click `web/index.html` | |

The live coach and the debrief server both use port 8000, so run one at a time.

In VS Code, the same entries are in the *Run and Debug* menu (`.vscode/launch.json`).

## Where things go

| Folder | Contents |
|---|---|
| `sandbox/pose_webcam.py` | Live coach: webcam, pose tracking, scoring, WebSocket bridge to the page |
| `scoring/` | Scoring package (per-frame scores, run scores, score bar). Settings in `scoring/config.py` |
| `config.py` | Spec settings for the full pipeline (sections, findings, LLM) |
| `to_claude_program/` | Claude debrief, coach personalities (`coaches.json`), test inputs `test1`–`test5` |
| `text_to_speech/` | ElevenLabs coach voices |
| `web/` | Front end (plain HTML/CSS/JS, see `web/README.md`) |
| `models/` | MediaPipe pose model (downloaded automatically if missing) |
| `TestCode/reference.mp4` | Sample reference clip that the live coach uses by default |
| `data/ref/` | Put your own reference clips here and set `REFERENCE_VIDEO` in `sandbox/pose_webcam.py` |
| `data/clips/` | Clip library for the debrief server |
| `data/uploads/` | Clips uploaded through the page while the live coach runs (git-ignored) |

## Notes for other operating systems

- **Webcam:** if the wrong camera opens, change `CAMERA_INDEX` in `sandbox/pose_webcam.py`.
  On macOS, allow camera access for your terminal or editor the first time.
- **Coach voices:** audio plays with `afplay` on macOS and PowerShell on Windows. On Linux,
  install `ffmpeg` (or `mpg123` / `mpv`). The offline fallback voice needs `espeak-ng` on Linux.
