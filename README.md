# JustClean — a live AI dance coach

**JustClean watches you dance through your webcam, compares you with the choreographer's video in real time, and coaches you with a personality you choose.**

It is built for dancers who already know the steps and want to *clean* them: every arm at the exact angle, every pose held for its full length, every movement landing on time. Cleaning normally needs a teacher watching. JustClean is the coach that watches when the teacher can't.

Built at WHACK26 for the challenge: *build an agent that reacts to a live feed as it happens, with personality.*

---

## Contents

- [What it does](#what-it-does)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Scoring](#scoring)
- [The AI coach](#the-ai-coach)
- [Project layout](#project-layout)
- [Configuration](#configuration)
- [Other ways to run it](#other-ways-to-run-it)
- [Troubleshooting](#troubleshooting)
- [Privacy](#privacy)
- [Known limitations](#known-limitations)
- [Roadmap](#roadmap)

---

## What it does

1. **Upload a reference video** — the choreographer's clip you want to clean (MP4, WebM or MOV, up to 7 minutes, one dancer, whole body in view). It's analysed once and cached, so next time it's ready straight away.
2. **Set a practice speed** from 0.5× to 1.2×, and optionally **mirror the dancer** so you can copy them like a reflection.
3. **Pick your coach** — Priya (hype), Gordon (strict), Ada (technical) or Bobby (gentle).
4. **Calibrate** — stand in the box until your whole body has been in frame for 2 seconds. A 3-2-1 countdown follows.
5. **Dance** — the reference plays on the left, your webcam (with your skeleton drawn on it) on the right. The agent reacts live:
   - the screen edge glows **green**, **amber** or **red** — colour *and* thickness change, so it isn't colour alone;
   - a score bar and a streak counter update about ten times a second;
   - your coach's catchphrase pops up when your score stays in their zone;
   - step out of frame for 1.5 s and the agent **pauses the video itself**, then resumes when you're back.
6. **Debrief** — your coach talks you through your overall score, the four components (position, timing, sharpness, holds), your weakest body parts, which section went wrong, and a drill for each fix. A **detailed breakdown** shows every section and body part, and you can ask **the whole panel** of coaches for their take on the same run.
7. **Score timeline** — on the Replay screen, a timeline of your run coloured by score. Click any patch to jump the reference video to that moment.

---

## Quick start

### Requirements

- **Python 3.10, 3.11 or 3.12.** MediaPipe does not support Python 3.13 yet.
- A webcam.
- A recent Chrome, Edge or Safari.
- *(Optional)* An Anthropic API key for the Claude debrief and an ElevenLabs API key for the coach voices. Without them everything still runs: the debrief uses a template and the coaches use the browser's built-in voice.

### Install

```bash
git clone https://github.com/sillybabs/WHACK26.git
cd WHACK26
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt    # use the root requirements.txt, not server/requirements.txt
```

### API keys (optional)

Create a file called `.env` in the repo root (copy `.env.example`):

```bash
ANTHROPIC_API_KEY=your-anthropic-key
ELEVENLABS_API_KEY=your-elevenlabs-key
```

`.env` is git-ignored, so the keys are never committed.

### Run

```bash
python server/main.py
```

Your browser opens **http://127.0.0.1:8000**. Always use that address — don't open `web/index.html` from disk (see [fake mode](#its-showing-made-up-data)).

The first time it runs, the MediaPipe pose model is downloaded into `models/` automatically.

### Keyboard shortcuts

| Key | Action |
|---|---|
| Space | Start dancing |
| M | Mirror your webcam view on/off |
| P | Auto-pause on/off |
| R | Reset |
| D | Show the debug panel |

---

## How it works

```
 ┌────────────────────── Browser (web/) ──────────────────────┐
 │  reference <video>        webcam picture (MJPEG /camera)   │
 │  glow · score bar · streak · catchphrases · report         │
 └───────▲──────────────────────────────┬─────────────────────┘
         │ score, state, video_cmd,      │ video_time every 100 ms,
         │ report …  (WebSocket /ws)     │ commands
 ┌───────┴──────────────────────────────▼─────────────────────┐
 │  FastAPI server (server/)                                   │
 │   session.py   state machine: calibrate → countdown → play  │
 │                → pause/resume → finish                      │
 │   pose_engine  webcam thread: MediaPipe → 11 angles →       │
 │                compare with the reference at the video time │
 │   scoring/     timing, sharpness, holds after the dance     │
 │   debrief_bridge → to_claude (Claude) → web_report (voices) │
 └─────────────────────────────────────────────────────────────┘
```

**Seeing.** A background camera thread runs **MediaPipe Pose Landmarker** on every webcam frame, finds 33 body landmarks and turns them into **11 body-segment angles**: left and right upper arm, forearm, torso, thigh and shin, plus the head.

**The reference is processed once.** When a clip is added, the whole video is run through the same pipeline and its angles are cached next to it as `clip.pose.json`. During a dance, only the webcam is processed.

**Staying in sync.** The browser plays the reference video and is the clock. It reports the video's position to the server over the WebSocket every 100 ms (`video_time`), and the server's `VideoClock` estimates the position between reports using the playback rate. Every camera frame is therefore compared with the matching frame of the reference, at any practice speed.

**Reacting.** Ten times a second the dance loop reads the latest camera result, updates the score, and sends a `score` message to the page. It also watches whether you're in frame: after 1.5 s out of view it sends `video_cmd: pause` and a banner, and resumes 0.7 s after you're back.

**Debriefing.** When the video ends, the run's statistics go to the debrief pipeline in a worker thread, so the server never freezes while Claude and ElevenLabs answer.

Every message between the page and the server is documented in [`server/messages.md`](server/messages.md).

---

## Scoring

All scores are out of 100 and calculated in Python.

### Position (live)

For each body segment, the angle difference $d$ between you and the reference is mapped to points with a banded curve (linear within each band):

| Angle difference | Points |
|---|---|
| 0° – 8° | 100 (dead zone: webcam tracking wobbles about this much) |
| 8° – 20° | 100 → 60 |
| 20° – 35° | 59 → 20 |
| 35° – 50° | 19 → 0 |
| over 50° | 0 |

Each frame's score is half the average of all segments and half the average of the **worst third**, so one badly wrong limb can't hide behind body parts that barely move:

$$
F = \tfrac{1}{2}\,\overline{s}_{\text{all}} + \tfrac{1}{2}\,\overline{s}_{\text{worst }1/3}
$$

The live score is smoothed so the bar doesn't flicker. **Green** is 60 and above, **amber** 20–59, **red** below 20.

### Timing, sharpness and holds (after the dance)

For each segment's angle over time, we detect **movement events**: where it starts moving faster than 30°/s, and where it settles. Your events are paired with the reference's (within 1 s of each other) and scored with a 0.5 s tolerance:

$$
T_i = \operatorname{clamp}\!\left(100\left(1 - \frac{\lvert t^{\text{you}}_{\text{arrive}} - t^{\text{ref}}_{\text{arrive}}\rvert}{0.5\,\text{s}}\right), 0, 100\right)
$$

- **Timing** — did you arrive at each position when the reference did?
- **Sharpness** — did the movement take as long as the reference's?
- **Holds** — did you hold each pose for as long as the reference?

### Overall

$$
\text{Overall} = \frac{\sum_{k \in A} w_k\, s_k}{\sum_{k \in A} w_k},
\qquad
w_{\text{position}} = 75,\; w_{\text{timing}} = 12.5,\; w_{\text{sharpness}} = 6.25,\; w_{\text{holds}} = 6.25
$$

where $A$ is the set of components that could actually be measured. **Missing data is never counted as 100**: an unmeasurable component is left out and the others are reweighted. Scores are also given per body part and per 10-second section.

> If only Position appears on the report, too many camera readings were lost to detect movements. Improve the lighting, make sure your whole body is in frame, and close other heavy apps.

---

## The AI coach

Python builds a structured summary of the run — scores, sections, body-part scores and the biggest measured problems (which body part, which section, how far off) — and sends it to **Claude**. Claude writes the debrief in the chosen coach's personality, set by four sliders in [`to_claude_program/coaches.json`](to_claude_program/coaches.json): kindness, technicality, energy and directness.

**Python owns every number.** Claude's text may not contain any numbers, apart from "Section N" labels. Every reply is validated; any part that breaks the rule is replaced with template text, and if Claude fails or times out the whole debrief falls back to a template. The page always gets a report.

Each coach has an **ElevenLabs** voice (`text_to_speech/`), with the browser's Web Speech API as a fallback, and captions typed in step with the voice.

| Coach | Style | Kindness | Technicality | Energy | Directness |
|---|---|---|---|---|---|
| Priya | Hype coach | 0.9 | 0.3 | 1.0 | 0.4 |
| Gordon | Strict teacher | 0.1 | 0.8 | 0.7 | 1.0 |
| Ada | Technical analyst | 0.6 | 1.0 | 0.3 | 0.7 |
| Bobby | Gentle giant | 1.0 | 0.5 | 0.4 | 0.3 |

Coach ids and sliders must stay in sync between `to_claude_program/coaches.json` and `web/js/coaches.js`.

---

## Project layout

| Path | What's in it |
|---|---|
| `server/main.py` | FastAPI app: `/ws` WebSocket, `/camera` MJPEG stream, `/upload`, `/clips`, and serves `web/` |
| `server/session.py` | One dance session: state machine, calibration, countdown, live scoring loop, auto-pause, debrief |
| `server/pose_engine.py` | MediaPipe, segment angles, score curve, calibration checks, camera thread, clip library, `VideoClock`, mirroring |
| `server/debrief_bridge.py` | Turns a finished run into the inputs for the Claude debrief |
| `server/messages.md` | Every WebSocket message between the page and the server |
| `scoring/` | Timing, sharpness and holds (`run_scores.py`) and their settings (`config.py`) |
| `to_claude_program/` | Claude debrief (`to_claude.py`), report shaping and voices (`web_report.py`), coach personalities (`coaches.json`), test inputs `test1`–`test5` |
| `text_to_speech/` | ElevenLabs coach voices |
| `web/` | Front end — plain HTML, CSS and JavaScript, no framework, no build step (see [`web/README.md`](web/README.md)) |
| `clips/` | Reference videos used by the server, each with its cached `.pose.json` |
| `models/` | MediaPipe pose model (downloaded automatically) |
| `sandbox/` | Early single-script prototype (webcam window + page) |
| `config.py` | Settings from the original scoring spec |

---

## Configuration

Most settings are constants at the top of each file, so tuning never means editing logic.

| Setting | Where | Default |
|---|---|---|
| Host / port | `server/main.py` → `HOST`, `PORT` | `127.0.0.1`, `8000` |
| Camera | `server/pose_engine.py` → `CAMERA_INDEX` | `0` (try `1` if the wrong camera opens) |
| Score curve | `server/pose_engine.py` → `DEADZONE_DEG`, `BAND*` | 8° / 20° / 35° / 50° |
| Worst-part weighting | `server/pose_engine.py` → `WORST_SHARE`, `WORST_WEIGHT` | ⅓, 0.5 |
| Section length, auto-pause | `server/session.py` → `SECTION_SECONDS`, `AUTO_PAUSE_AFTER`, `RESUME_AFTER` | 10 s, 1.5 s, 0.7 s |
| Event detection | `scoring/config.py` | 30°/s, 0.5 s tolerances |
| Claude model | env var `DEBRIEF_MODEL` | `claude-sonnet-5-5` |
| Claude timeout | env var `DEBRIEF_TIMEOUT` | 30 s |
| ElevenLabs model | env var `ELEVENLABS_MODEL` | `eleven_multilingual_v2` |
| Max upload | `server/main.py` → `MAX_UPLOAD_MB`; `web/js/app.js` → `MAX_UPLOAD_S` | 200 MB, 7 min |

---

## Other ways to run it

| What | Command | Notes |
|---|---|---|
| **Page only, fake data** | Open `web/index.html` from disk, or add `?fake=1` to the URL | Plays a scripted run with made-up scores. For front-end work only. |
| **Debrief server, no camera** | `python to_claude_program/debrief_server.py` | Made-up live scores, real Claude debrief. Add `--no-api` to skip Claude. Uses port 8000 too. |
| **Debrief to files** | `python to_claude_program/main.py test3` | Writes the debrief for test input `test3` to `to_claude_program/output/`. Add `--no-api` for the template. |
| **Coach voice test** | `python text_to_speech/speak_main.py` | Plays a line in a coach's voice. |

---

## Troubleshooting

### It's showing made-up data
You're in **fake mode**. It turns on automatically when `index.html` is opened from disk (`file://`), or with `?fake=1`. The connection badge says *"Fake mode · no server"*. Run `python server/main.py` and use http://127.0.0.1:8000. Also make sure `debrief_server.py` isn't the server running on port 8000 — it uses made-up live scores on purpose.

### The report is template text, not Claude
Look in the server terminal for `Claude call failed (…); using template.` Check `ANTHROPIC_API_KEY` in `.env`, your network, and the model name (`DEBRIEF_MODEL`).

### Only Position shows on the report
Timing, sharpness and holds need unbroken tracking to detect movements. If too many camera readings are lost, they're marked unavailable rather than guessed. Check *Detailed breakdown → % of frames tracked*. Fix: whole body in frame, light from the front, close other heavy apps, or try a slower practice speed.

### The coaches (or other parts of the setup screen) are missing
A JavaScript error stopped the page from finishing. Open the browser console. A common cause is an element that `web/js/app.js` expects having been removed from `index.html` — every `$("id")` in `app.js` needs a matching `id` in the HTML.

### The wrong camera opens, or none
Change `CAMERA_INDEX` in `server/pose_engine.py`. On macOS, allow camera access for your terminal or editor the first time.

### `pip install` fails on mediapipe
You're probably on Python 3.13. Create the virtual environment with Python 3.12.

### The coach voice doesn't play in Safari
Safari only plays audio started by a click. The page unlocks audio on your first click, so click anywhere on the page before you dance.

---

## Privacy

- Your webcam video is processed on your own machine and is **never uploaded**.
- Only a summary of the scores goes to Claude, and only the debrief text goes to ElevenLabs.
- API keys live in `.env`, which is git-ignored.

---

## Known limitations

- The dancer isn't recorded yet, so the Replay screen shows the reference video with your score timeline, not a side-by-side of you.
- Wrists, feet and hands aren't scored separately.
- There is no audio or beat analysis: timing is measured against the reference dancer's movements, not the music.
- Designed for one dancer, standing, with a fixed camera.

## Roadmap

- **Beat tracking**, so the coach can call out counts ("count five").
- **A coach that speaks live** during the dance, not only in the debrief.
- **User accounts**, to track progress over time.
- **Side-by-side replay** with a recording of the dancer.
- **More accessibility** features.
