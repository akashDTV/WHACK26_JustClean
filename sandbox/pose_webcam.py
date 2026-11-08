"""
pose_webcam.py - copy a pose from a reference VIDEO and get scored live.
Merged version:
  * SCORING        from Nervin's version (the /scoring package: per-segment
                   scores, run scores, score bar, smoother, hooks)
  * WEBSOCKET/WEB  from Yash's version (bridge to the Live Dance Coach page,
                   speed slider, clip upload / chooser, start / reset)

HOW IT WORKS
------------
1. START-UP: the reference .mp4 is read once from start to finish. Every frame
   is run through MediaPipe Pose Landmarker and we store, per frame, the
   skeleton and the angle of 11 body segments (see SEGMENTS below). This takes a
   little while for long videos (a progress % is printed).
2. PLAYBACK: the reference video plays next to your live webcam feed. After a
   short countdown it plays at the current playback speed (1.0 = real time,
   0.5 = half speed, 2.0 = double), so you can follow along with it.
3. SCORING: for every webcam frame we work out your 11 live angles and compare
   them with the angles of the reference frame that is on screen at that moment.
   For each segment: difference = how far your angle is from the reference
   angle (always 0-180 degrees, wrap-around handled). That difference is turned
   into a score from 0 to 100 using the point bands below.
4. DISPLAY: live feed on the left, reference video on the right. Your skeleton
   is coloured per segment (green = good, orange = so-so, red = off). Each
   segment's score is written on the body, and a table lists live angle,
   reference angle, difference and score per segment. The big SCORE and AVG
   texts and the score bar can be switched on with SHOW_OVERALL_SCORE,
   SHOW_AVG_SCORE and SHOW_SCORE_BAR (all off by default).
5. RUN SCORES: while the reference plays, every scored frame is recorded. When the
   run ends (video finishes / loops, or you quit) the console shows POSITION,
   TIMING, SHARPNESS, HOLDS (each 0-100) and OVERALL (see scoring/run_scores.py).
6. SCORE BAR (hidden by default: SHOW_SCORE_BAR = False): a vertical bar on the
   right edge of the live feed starts 50% green / 50% red, like the evaluation
   bar on chess.com. Matching the video pushes it towards green, missing pushes
   it towards red, and a perfect run ends fully green.
7. WEBSOCKET: a small WebSocket server runs in the background.
     script -> page : the live score (for the score bar), band and streak
     page -> script : the speed slider, clip choice, start / reset
   The script also serves the web page and accepts clip uploads.
   See the "WEBSOCKET" section for the exact messages.

ANGLE CONVENTION
----------------
Each segment is a line between two landmarks, ordered so it points "downwards"
when you stand upright (e.g. shoulder -> elbow). Its angle is measured against
straight down, as seen on screen:
    0 deg = pointing straight down,  + = clockwise,  - = anticlockwise.
Because only angle DIFFERENCES matter, the sign convention is just used
consistently for both the live feed and the reference.

SCORING (all tunable in scoring/config.py)
------------------------------------------
    difference 0-20 deg   -> 100 down to 60 points   (band 1)
    difference 20-40 deg  -> 59 down to 20 points    (band 2)
    difference 40+ deg    -> 19 down to 0 points     (band 3)
Inside a band the score falls in a straight line. Examples:
    10 deg off -> 80,  20 deg -> 60,  30 deg -> 39.5,  40 deg -> 20,  50 deg -> 9.5
Band 3 reaches 0 points at BAND3_MAX_DIFF (60 deg) and stays 0 beyond that.

SETUP
-----
    pip install mediapipe opencv-python websockets

(The script still runs without `websockets`; it just skips the bridge.)
The /scoring package must sit in the folder ABOVE this script (as in Nervin's
layout: /scoring next to /sandbox).

HOW TO RUN (no terminal needed)
-------------------------------
1. Put your reference video (.mp4) in the same folder as this script.
2. Set REFERENCE_VIDEO in the CONFIG section below to the file name
   (or a full path, e.g. "C:/Users/me/Videos/pose.mp4" - use forward slashes).
3. Run the script from your editor (IDLE, Spyder, PyCharm, VS Code "Run" ...).
   From a Jupyter notebook or Python prompt instead:
       import pose_webcam
       pose_webcam.main()                  # uses REFERENCE_VIDEO
       pose_webcam.main("other_video.mp4") # or pass a different video
Relative file names are looked up next to this script, so it doesn't matter
which folder your editor runs it from.

CONTROLS (click the video window first so it has focus)
-------------------------------------------------------
    q or ESC  quit
    SPACE     pause / resume the reference video
    r         restart the reference video (with the countdown); resets the run

USAGE NOTES
-----------
* The reference video is mirrored when MIRROR = True, exactly like the live
  feed, so you copy it like looking in a mirror.
* Use a reference filmed from a similar viewpoint (front-on vs front-on). The
  angles are 2D screen angles, so a side-on video won't match a front-on you.
* Segments that aren't visible (below MIN_VISIBILITY) in either the live feed or
  the current reference frame are shown as "--" and left out of the overall
  score. If the reference has no person in a frame, nothing is scored.
* The video's sound is not played.
"""

import asyncio
import collections
import json
import math
import mimetypes
import os
import re
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
from email import message_from_bytes, policy
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

try:
    import websockets
except ImportError:          # the script still works, just without the bridge
    websockets = None

# Folder this script lives in (falls back to the current folder in a notebook)
try:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    BASE_DIR = os.getcwd()

# The scoring code lives in /scoring, one folder up from this script. Make that
# folder importable so "import scoring" works wherever you run from.
_PARENT_DIR = os.path.dirname(BASE_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from scoring import (  # noqa: E402
    BalanceBar, RunRecorder, ScoreSmoother, balance_band, color_for_score, compare,
    overall_score,
)
from scoring import finish_run as _finish_run  # noqa: E402
from scoring.config import COLOR_BAD, COLOR_GOOD  # noqa: E402

# =============================================================================
# CONFIG
# =============================================================================
REFERENCE_VIDEO = "reference.mp4"  # <-- your reference video: file name (next to this
                                   #     script) or a full path
PLAYBACK_SPEED = 1.0      # STARTING speed of the reference video: 1.0 = normal,
                          # 0.5 = half speed, 2.0 = double. Must be > 0.
                          # While running, the web page's speed slider changes it.
LOOP_REFERENCE = True     # replay the video (with a new countdown) when it ends
COUNTDOWN_SECONDS = 3     # get-ready time before the reference starts playing
REF_PROCESS_HEIGHT = 480  # reference frames are shrunk to this height for the
                          # start-up analysis (faster; doesn't change the angles)

CAMERA_INDEX = 0          # try 1 if you have multiple cameras
MODEL_VARIANT = "lite"    # "lite" (fastest), "full", or "heavy" (most accurate)
MIRROR = True             # selfie-style view (applied to the reference too)
MIN_VISIBILITY = 0.5      # ignore landmarks the model isn't confident about
MAX_POSES = 1             # track at most this many people in the live feed
TARGET_FPS = 30           # set to None for uncapped
PRINT_SCORES = True       # also print the scores to the console
PRINT_INTERVAL = 0.5      # seconds between console prints

# =============================================================================
# DISPLAY (scoring overlays)
# =============================================================================
# Point bands, colours, score-bar tuning and run-score weights live in
# scoring/config.py. These only choose what is drawn on the live feed.
SHOW_SCORE_BAR = False       # chess.com-style green/red bar on the right edge
SHOW_PART_SCORES = True      # write each segment's score on the body as well as in the table
SHOW_OVERALL_SCORE = False   # the big "SCORE: nn" text at the bottom left of the live feed
SHOW_AVG_SCORE = False       # the "AVG: nn" text above it

# Optional hook so other code can receive the scores. Set it to a function that
# takes one dict:  {"time", "overall", "parts": {segment: score or None}, "balance"}
# It is called once per frame while the reference is playing and a person is seen.
SCORE_CALLBACK = None

# Optional hook: set to a function taking one dict (the run scores) - called
# once when a run ends.
RUN_SCORE_CALLBACK = None

# =============================================================================
# WEBSOCKET
# =============================================================================
# The page connects to ws://WS_HOST:WS_PORT (point the page's ws.js at it).
#
# script -> page   (matches the page's "score" handler)
#   {"type": "score", "score": 0-100, "band": "green" | "amber" | "red" | "none",
#    "streak": seconds of continuous green, "missing_parts": ["L forearm", ...]}
#
# page -> script
#   {"type": "command", "name": "set_speed", "value": 0.75}
#   {"type": "command", "name": "choose_clip" | "start" | "reset" | "try_again" | "hello", ...}
ENABLE_WEBSOCKET = True
WS_HOST = "localhost"     # use "0.0.0.0" if the page is on another device
WS_PORT = 8765
WS_SEND_INTERVAL = 0.1    # seconds between score messages (10 per second)
# Web page + uploads: the script also serves the page (index.html, style.css, js/)
# so the "Upload your own clip" card works. Open http://localhost:HTTP_PORT
HTTP_HOST = "localhost"   # use "0.0.0.0" to reach the page from another device
HTTP_PORT = 8000
WEB_DIR = None            # folder containing index.html; None = auto-find it (next to
                          # this script, ./web, or ../web)
UPLOAD_DIR = "uploads"    # uploaded clips are saved here (next to this script)
MAX_UPLOAD_MB = 200
WAIT_FOR_PAGE_START = True  # True: the reference waits (paused) until you press
                            # "Start dancing" on the page. If no page is
                            # connected, press SPACE in the video window, or set
                            # this to False to start immediately.
MIN_SPEED = 0.1           # safety limits for speeds received from the page
MAX_SPEED = 4.0

# =============================================================================
# MODEL
# =============================================================================
MODEL_PATH = os.path.join(BASE_DIR, f"pose_landmarker_{MODEL_VARIANT}.task")
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    f"pose_landmarker_{MODEL_VARIANT}/float16/latest/"
    f"pose_landmarker_{MODEL_VARIANT}.task"
)

# Standard 33-point BlazePose skeleton connections
POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (28, 30), (29, 31), (30, 32), (27, 31), (28, 32),
]

# =============================================================================
# SEGMENTS - which body parts we measure
# =============================================================================
# Each segment is a line between two landmarks, ordered so it points "downwards"
# when you stand upright. A landmark can be an int, or a tuple of ints meaning
# "midpoint of these". For left/right pairs the right landmark index is the
# left index + 1.
LIMB_SEGMENTS = [
    ("upper arm", 11, 13),   # shoulder -> elbow
    ("forearm",   13, 15),   # elbow -> wrist
    ("torso",     11, 23),   # shoulder -> hip
    ("thigh",     23, 25),   # hip -> knee
    ("shin",      25, 27),   # knee -> ankle (use 31 / 32 for toes instead)
]
HEAD_SEGMENT = ("head", (7, 8), (11, 12))  # ear midpoint -> shoulder midpoint


# =============================================================================
# SCORING HOOKS
# =============================================================================
def finish_run(recorder, complete=True):
    """Score the finished run (see scoring/run_scores.py) and call RUN_SCORE_CALLBACK."""
    return _finish_run(recorder, complete, callback=RUN_SCORE_CALLBACK)


def band_for_score(score):
    """The band name the web page expects. Uses the same thresholds as the
    skeleton colours (scoring.color_for_score), so page and window always agree."""
    if score is None:
        return "none"
    color = color_for_score(score)
    if color == COLOR_GOOD:
        return "green"
    if color == COLOR_BAD:
        return "red"
    return "amber"


# =============================================================================
# GEOMETRY
# =============================================================================
def _point(lms, spec, w, h):
    """Pixel position of a landmark (or midpoint), or None if not visible."""
    idxs = spec if isinstance(spec, tuple) else (spec,)
    xs, ys = [], []
    for i in idxs:
        lm = lms[i]
        if lm.visibility is not None and lm.visibility < MIN_VISIBILITY:
            return None
        xs.append(lm.x * w)
        ys.append(lm.y * h)
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _angle(p, q):
    """Angle of p->q vs straight down on screen; clockwise is positive."""
    dx, dy = q[0] - p[0], q[1] - p[1]  # image y points down
    return math.degrees(math.atan2(-dx, dy))


def get_segments(lms, w, h):
    """Return {segment name: (start_point, end_point)} in pixels (None if hidden)."""
    segs = {}
    # The frame is mirrored before detection, so the model's "left" landmarks
    # are your right side when MIRROR is on. Swap so labels match your body.
    sides = (("L", 1 if MIRROR else 0), ("R", 0 if MIRROR else 1))
    for label, offset in sides:
        for name, a, b in LIMB_SEGMENTS:
            segs[f"{label} {name}"] = (
                _point(lms, a + offset, w, h),
                _point(lms, b + offset, w, h),
            )
    name, a, b = HEAD_SEGMENT
    segs[name] = (_point(lms, a, w, h), _point(lms, b, w, h))
    return segs


def segment_angles(segs):
    """{segment name: angle in degrees, or None if not visible}."""
    return {
        name: (_angle(p, q) if p and q else None)
        for name, (p, q) in segs.items()
    }


# =============================================================================
# DRAWING
# =============================================================================
def draw_skeleton(frame, landmarks):
    h, w = frame.shape[:2]
    pts = []
    for lm in landmarks:
        visible = (lm.visibility is None) or (lm.visibility >= MIN_VISIBILITY)
        pts.append((int(lm.x * w), int(lm.y * h)) if visible else None)

    for a, b in POSE_CONNECTIONS:
        if pts[a] and pts[b]:
            cv2.line(frame, pts[a], pts[b], (255, 255, 255), 2, cv2.LINE_AA)
    for p in pts:
        if p:
            cv2.circle(frame, p, 4, (255, 255, 255), -1, cv2.LINE_AA)


def draw_segment_colors(frame, segs, results):
    """Re-draw each measured segment in green / orange / red by its score."""
    for name, r in results.items():
        if r is None:
            continue
        p, q = segs[name]
        cv2.line(frame, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])),
                 color_for_score(r["score"]), 5, cv2.LINE_AA)


def _text(frame, s, pos, color=(255, 255, 255), scale=0.5, thick=1):
    cv2.putText(frame, s, pos, cv2.FONT_HERSHEY_SIMPLEX, scale, color,
                thick, cv2.LINE_AA)


def draw_part_scores(frame, segs, results):
    """Write each segment's score on the body, halfway along the segment."""
    for name, r in results.items():
        if r is None:
            continue
        p, q = segs[name]
        pos = (int((p[0] + q[0]) / 2) + 6, int((p[1] + q[1]) / 2))
        s = f"{r['score']:.0f}"
        _text(frame, s, pos, (0, 0, 0), 0.55, 4)                      # dark outline
        _text(frame, s, pos, color_for_score(r["score"]), 0.55, 2)    # coloured number


def draw_score_bar(frame, value):
    """Vertical green/red bar on the right edge. value = green share, 0..1."""
    h, w = frame.shape[:2]
    bar_w = 26
    x1 = w - 14
    x0 = x1 - bar_w
    y0, y1 = 70, h - 70
    fill_y = int(y1 - (y1 - y0) * value)

    cv2.rectangle(frame, (x0, y0), (x1, y1), COLOR_BAD, -1)         # red = misses
    cv2.rectangle(frame, (x0, fill_y), (x1, y1), COLOR_GOOD, -1)    # green rises from the bottom
    cv2.rectangle(frame, (x0, y0), (x1, y1), (255, 255, 255), 2)
    mid = (y0 + y1) // 2
    cv2.line(frame, (x0 - 5, mid), (x1 + 5, mid), (255, 255, 255), 2)  # the 50/50 line

    # The percentage is written as a number too, so it never relies on colour alone
    label = f"{value * 100:.0f}%"
    _text(frame, label, (w - 76, y0 - 12), (0, 0, 0), 0.7, 4)
    _text(frame, label, (w - 76, y0 - 12), (255, 255, 255), 0.7, 2)


def draw_panel(frame, results, overall):
    """Table of live angle / reference angle / difference / score per segment."""
    x0, y0, row_h = 5, 45, 21
    rows = len(results) + 1
    box_w, box_h = 340, row_h * rows + 10

    # Semi-transparent dark background so the text is readable
    overlay = frame.copy()
    cv2.rectangle(overlay, (x0, y0), (x0 + box_w, y0 + box_h), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)

    cols = [x0 + 6, x0 + 115, x0 + 170, x0 + 225, x0 + 285]
    y = y0 + row_h
    for label, x in zip(("segment", "live", "ref", "diff", "score"), cols):
        _text(frame, label, (x, y), (200, 200, 200), 0.45)

    for name, r in results.items():
        y += row_h
        if r is None:
            _text(frame, name, (cols[0], y))
            _text(frame, "--", (cols[1], y), (150, 150, 150))
            continue
        col = color_for_score(r["score"])
        _text(frame, name, (cols[0], y))
        _text(frame, f"{r['live']:+.0f}", (cols[1], y))
        _text(frame, f"{r['ref']:+.0f}", (cols[2], y))
        _text(frame, f"{r['diff']:+.0f}", (cols[3], y))
        _text(frame, f"{r['score']:.0f}", (cols[4], y), col, 0.55, 2)

    if SHOW_OVERALL_SCORE and overall is not None:
        h = frame.shape[0]
        _text(frame, f"SCORE: {overall:.0f}", (10, h - 15),
              color_for_score(overall), 1.0, 3)


# =============================================================================
# WEBSOCKET BRIDGE
# =============================================================================
class WebBridge:
    """Runs a WebSocket server in a background thread.

    * send_score(...)  - broadcast the live score to every connected page
    * speed            - the latest speed set by the page's slider (read it
                         every loop; it is a plain float, safe across threads)

    If the `websockets` package is missing, or the port is taken, the bridge
    switches itself off with a message and the rest of the script carries on.
    """

    def __init__(self, host, port, initial_speed):
        self.host, self.port = host, port
        self.speed = float(initial_speed)
        self.clips = []                    # clip list sent to the page on "hello"
        self.clip_store = {}               # clip id -> {path, data, fps}
        self.events = collections.deque()  # "start" / "reset" for the video loop
        self.enabled = False
        self._clients = set()
        self._loop = None
        self._ready = threading.Event()
        self._failed = None

    # ---- lifecycle ---------------------------------------------------------
    def start(self):
        if websockets is None:
            print("WebSocket bridge off: run `pip install websockets` to enable it.")
            return self
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(timeout=5)
        if self._failed:
            print(f"WebSocket bridge off: {self._failed}")
        else:
            self.enabled = True
            print(f"WebSocket bridge listening on ws://{self.host}:{self.port}")
        return self

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._serve())
        except Exception as e:  # e.g. port already in use
            self._failed = e
            self._ready.set()

    async def _serve(self):
        async with websockets.serve(self._handler, self.host, self.port):
            self._ready.set()
            await asyncio.Future()  # run forever

    # ---- page -> script ----------------------------------------------------
    async def _handler(self, ws, path=None):  # `path` for older websockets versions
        self._clients.add(ws)
        try:
            async for raw in ws:
                self._on_message(raw)
        except Exception:
            pass
        finally:
            self._clients.discard(ws)

    def _set_speed(self, raw_value):
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            return
        if math.isfinite(value):
            self.speed = min(MAX_SPEED, max(MIN_SPEED, value))
            print(f"Playback speed set from page: x{self.speed:g}")

    def _on_message(self, raw):
        # Runs on the websocket thread. Anything the video loop must act on is
        # put in self.events and handled by main() (see the event loop there).
        try:
            msg = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(msg, dict) or msg.get("type") != "command":
            return  # e.g. the page's video_time messages are ignored
        name = msg.get("name")
        if name == "hello":
            # The page asks for its clip list and state when it connects;
            # without these it stays on the "Getting ready..." screen.
            asyncio.ensure_future(self._broadcast(json.dumps(
                {"type": "clips", "clips": self.clips})))
            asyncio.ensure_future(self._broadcast(json.dumps(
                {"type": "state", "state": "waiting"})))
        elif name == "set_speed":
            self._set_speed(msg.get("value"))
        elif name == "choose_clip":
            self.events.append(("choose_clip", msg.get("value")))
        elif name == "start":
            if msg.get("speed") is not None:
                self._set_speed(msg.get("speed"))
            self.events.append(("start", msg.get("clip")))
        elif name in ("reset", "try_again"):
            self.events.append(("reset", None))

    def send(self, msg):
        """Send any message dict to every connected page (thread-safe)."""
        if self.enabled and self._clients:
            asyncio.run_coroutine_threadsafe(
                self._broadcast(json.dumps(msg)), self._loop)

    def send_state(self, state):
        """Tell the page which screen to show: waiting / playing / ..."""
        self.send({"type": "state", "state": state})

    # ---- clips (the initial reference video + clips uploaded on the page) ---
    def register_clip(self, clip_id, name, path, data, fps):
        """Remember an analysed clip and add it to the page's clip list."""
        self.clip_store[clip_id] = {"path": path, "data": data, "fps": fps}
        page_clip = {
            "id": clip_id,
            "name": name,
            "duration_s": len(data) / fps,
            "ref_ok": True,
            "url": f"/clips/{clip_id}",   # served by the web server below
        }
        self.clips = [c for c in self.clips if c["id"] != clip_id] + [page_clip]
        return page_clip

    # ---- script -> page ----------------------------------------------------
    def send_score(self, score, streak, missing_parts):
        if not self.enabled or not self._clients:
            return
        msg = json.dumps({
            "type": "score",
            "score": round(score) if score is not None else 0,
            "band": band_for_score(score),
            "streak": round(streak, 1),
            "missing_parts": missing_parts,
        })
        asyncio.run_coroutine_threadsafe(self._broadcast(msg), self._loop)

    async def _broadcast(self, msg):
        for ws in list(self._clients):
            try:
                await ws.send(msg)
            except Exception:
                self._clients.discard(ws)


# =============================================================================
# WEB SERVER - serves the page, accepts uploads, serves the clips
# =============================================================================
# GET  /                -> the page (redirected so it finds the websocket)
# POST /upload          -> the page's "Upload your own clip" card
# GET  /clips/<clip id> -> a clip's video file (with Range support for seeking)
ALLOWED_EXTENSIONS = (".mp4", ".webm", ".mov")


def process_upload(bridge, filename, raw):
    """Save an uploaded clip, analyse it, and tell the page how it went.

    Runs in its own thread so the live scoring isn't interrupted. On success the
    page selects the new clip, so it becomes the reference video.
    """
    def fail(text):
        print(f"Upload rejected: {text}")
        bridge.send({"type": "upload_result", "ok": False, "warnings": [text]})

    try:
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            ext = ".mp4"
        clip_id = "upload-" + uuid.uuid4().hex[:8]
        folder = os.path.join(BASE_DIR, UPLOAD_DIR)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, clip_id + ext)
        with open(path, "wb") as f:
            f.write(raw)
        print(f"Uploaded clip saved: {path}")

        bridge.send({"type": "upload_progress", "percent": 0,
                     "stage": "Tracking the dancer…"})
        data, fps = analyse_reference(
            path,
            on_progress=lambda p: bridge.send({
                "type": "upload_progress", "percent": p,
                "stage": f"Tracking the dancer… {p}%"}))
    except ReferenceVideoError as e:
        return fail(str(e))
    except Exception as e:
        return fail(f"Could not process that file ({e})")

    found = sum(1 for pose, _ in data if pose is not None)
    tracked_pct = round(found * 100 / len(data))
    warnings = []
    if tracked_pct < 80:
        warnings.append("The dancer was lost in part of this clip - "
                        "try one dancer, full body, steady camera.")
    name = os.path.splitext(os.path.basename(filename or ""))[0] or "Uploaded clip"
    page_clip = bridge.register_clip(clip_id, name, path, data, fps)
    bridge.send({"type": "upload_result", "ok": True, "tracked_pct": tracked_pct,
                 "warnings": warnings, "clip": page_clip})


def make_handler(bridge, web_dir):
    max_bytes = MAX_UPLOAD_MB * 1024 * 1024

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=web_dir, **kwargs)

        def log_message(self, fmt, *args):
            pass  # keep the console free for the scores

        def do_GET(self):
            parts = urllib.parse.urlsplit(self.path)
            query = parts.query
            if (parts.path in ("/", "/index.html")
                    and "ws=" not in query and "fake" not in query):
                # The page looks for its websocket on this port by default;
                # ours is on WS_PORT, so tell the page where it is.
                host = (self.headers.get("Host") or "localhost").split(":")[0]
                self.send_response(302)
                self.send_header("Location",
                                 f"/index.html?ws=ws://{host}:{WS_PORT}")
                self.end_headers()
                return
            if parts.path.startswith("/clips/"):
                return self._serve_clip(parts.path[len("/clips/"):])
            super().do_GET()

        def _serve_clip(self, clip_id):
            rec = bridge.clip_store.get(clip_id)
            if not rec or not os.path.exists(rec["path"]):
                self.send_error(404, "No such clip")
                return
            path = rec["path"]
            size = os.path.getsize(path)
            ctype = mimetypes.guess_type(path)[0] or "video/mp4"
            start, end, status = 0, size - 1, 200
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1))
                    if m.group(2):
                        end = min(int(m.group(2)), size - 1)
                else:  # "bytes=-N" = the last N bytes
                    start = max(0, size - int(m.group(2)))
                if start > end:
                    self.send_error(416, "Bad range")
                    return
                status = 206
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == 206:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            try:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the browser stopped reading (normal while seeking)

        def do_POST(self):
            if urllib.parse.urlsplit(self.path).path != "/upload":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > max_bytes:
                self.send_error(413, f"File empty or over {MAX_UPLOAD_MB} MB")
                return
            body = self.rfile.read(length)
            ctype = self.headers.get("Content-Type", "")
            msg = message_from_bytes(
                b"Content-Type: " + ctype.encode() +
                b"\r\nMIME-Version: 1.0\r\n\r\n" + body,
                policy=policy.default)
            filename, raw = None, None
            if msg.is_multipart():
                for part in msg.iter_parts():
                    if part.get_filename():
                        filename = part.get_filename()
                        raw = part.get_payload(decode=True)
                        break
            if not raw:
                self.send_error(400, "No file in the upload")
                return
            reply = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)
            threading.Thread(target=process_upload,
                             args=(bridge, filename, raw), daemon=True).start()

    return Handler


def find_web_dir():
    """Folder with index.html: WEB_DIR if set, else look next to the script,
    in a 'web' folder next to it, and in a 'web' folder one level up."""
    if WEB_DIR:
        candidates = [WEB_DIR]
    else:
        candidates = [BASE_DIR, os.path.join(BASE_DIR, "web"),
                      os.path.join(BASE_DIR, "..", "web")]
    for c in candidates:
        c = c if os.path.isabs(c) else os.path.join(BASE_DIR, c)
        if os.path.exists(os.path.join(c, "index.html")):
            return os.path.abspath(c)
    return None


def start_web_server(bridge):
    web_dir = find_web_dir()
    if not web_dir:
        print("Web page off: couldn't find index.html. Set WEB_DIR at the top "
              "to the folder that contains it (e.g. \"../web\").")
        return
    try:
        server = ThreadingHTTPServer((HTTP_HOST, HTTP_PORT),
                                     make_handler(bridge, web_dir))
    except OSError as e:
        print(f"Web page off: {e}")
        return
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"Serving {web_dir}")
    print(f"Open the page at http://{HTTP_HOST}:{HTTP_PORT}")


# =============================================================================
# SETUP HELPERS
# =============================================================================
def ensure_model():
    if not os.path.exists(MODEL_PATH):
        print(f"Downloading {os.path.basename(MODEL_PATH)} ...")
        urllib.request.urlretrieve(MODEL_URL, MODEL_PATH)
        print("Done.")


def resolve_path(path):
    """Relative paths are looked for next to this script first."""
    if not os.path.isabs(path):
        candidate = os.path.join(BASE_DIR, path)
        if os.path.exists(candidate):
            return candidate
    return path


# =============================================================================
# REFERENCE VIDEO
# =============================================================================
class ReferenceVideoError(Exception):
    """The reference video can't be used (unreadable, or nobody in it)."""


def analyse_reference(path, on_progress=None):
    """Run the whole reference video through the pose model once.

    Returns (data, fps) where data[i] = (pose, angles) for frame i:
        pose   - the 33 landmarks (for drawing), or None if nobody was found
        angles - {segment name: angle}, or None if nobody was found
    on_progress(percent) is called every 10% if given.
    Raises ReferenceVideoError if the video can't be used.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise ReferenceVideoError(f"Could not open the reference video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps < 1:  # missing / NaN / nonsense
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    print(f"Analysing reference video ({total or '?'} frames, {fps:.1f} fps) ...")
    data = []
    last_ts = -1
    next_report = 10
    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if MIRROR:
                frame = cv2.flip(frame, 1)  # same treatment as the live feed
            if frame.shape[0] > REF_PROCESS_HEIGHT:
                scale = REF_PROCESS_HEIGHT / frame.shape[0]
                frame = cv2.resize(
                    frame, (max(1, int(frame.shape[1] * scale)), REF_PROCESS_HEIGHT))
            h, w = frame.shape[:2]

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts = max(int(len(data) * 1000 / fps), last_ts + 1)  # must increase
            last_ts = ts
            result = landmarker.detect_for_video(mp_image, ts)

            if result.pose_landmarks:
                pose = result.pose_landmarks[0]
                data.append((pose, segment_angles(get_segments(pose, w, h))))
            else:
                data.append((None, None))

            if total and len(data) * 100 // total >= next_report:
                print(f"  {next_report}%")
                if on_progress:
                    on_progress(next_report)
                next_report += 10
    cap.release()

    if not data:
        raise ReferenceVideoError(f"The reference video has no readable frames: {path}")

    found = sum(1 for pose, _ in data if pose is not None)
    print(f"Done: {len(data)} frames, person found in {found} of them.")
    if found == 0:
        raise ReferenceVideoError("No person found anywhere in the reference video. "
                                  "Try a clearer, full-body video.")
    return data, fps


class ReferencePlayer:
    """Plays the reference video at `speed` and says which frame is current.

    Playback is driven by the clock, not by how fast the main loop runs, so the
    reference always stays in time even if the webcam loop is slow (frames are
    skipped to catch up). Call update(dt) once per loop.

    `speed` can be changed at any time (the web page's slider does this); it
    takes effect immediately without restarting the video.
    """

    def __init__(self, path, data, fps, speed=PLAYBACK_SPEED):
        self.data = data          # per-frame (pose, angles) from analyse_reference
        self.fps = fps
        self.n = len(data)
        self.speed = speed        # playback speed multiplier (live-changeable)
        self.cap = cv2.VideoCapture(path)
        self.paused = False
        self.restart()

    def restart(self):
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        self.index = -1           # index of the frame currently shown
        self.frame = None         # that frame (raw, un-mirrored)
        self.time = 0.0           # seconds of VIDEO played so far
        self.countdown = float(COUNTDOWN_SECONDS)
        self.finished = False

    def load(self, path, data, fps):
        """Switch to a different reference video (e.g. one uploaded on the page)."""
        self.cap.release()
        self.data, self.fps, self.n = data, fps, len(data)
        self.cap = cv2.VideoCapture(path)
        self.restart()

    @property
    def playing(self):
        """True while the video is actually running (scores count then)."""
        return self.countdown <= 0 and not self.paused and not self.finished

    @property
    def current(self):
        """(pose, angles) of the frame on screen, or (None, None)."""
        return self.data[self.index] if self.index >= 0 else (None, None)

    def update(self, dt):
        """Advance the clock by dt seconds. Returns True if the video looped."""
        if not self.paused:
            if self.countdown > 0:
                self.countdown = max(0.0, self.countdown - dt)
            elif not self.finished:
                # self.time is VIDEO time, so the speed multiplier goes here.
                # (The countdown above stays in real seconds.)
                self.time += dt * self.speed

        looped = False
        target = int(self.time * self.fps)
        if target >= self.n:
            if LOOP_REFERENCE:
                self.restart()
                looped = True
                target = 0
            else:
                target = self.n - 1
                self.finished = True
        self._seek(target)
        return looped

    def _seek(self, target):
        """Move forward to frame `target`, skipping (not decoding) frames in between."""
        while self.index < target:
            self.index += 1
            if self.index == target:
                ok, frame = self.cap.read()
                if ok:
                    self.frame = frame
            elif not self.cap.grab():
                self.index = target  # video ended earlier than expected
                break

    def render(self, height):
        """The current reference frame as an image `height` pixels tall."""
        if self.frame is None:
            img = np.zeros((height, int(height * 4 / 3), 3), np.uint8)
        else:
            img = cv2.flip(self.frame, 1) if MIRROR else self.frame.copy()
            scale = height / img.shape[0]
            img = cv2.resize(img, (max(1, int(img.shape[1] * scale)), height))

        pose, _ = self.current
        if pose is not None:
            draw_skeleton(img, pose)
        else:
            _text(img, "No person in this frame", (10, 60), (0, 0, 255), 0.6, 2)

        _text(img, "REFERENCE", (10, 30), (255, 255, 255), 0.8, 2)
        if self.finished:
            status = "FINISHED"
        elif self.paused:
            status = "PAUSED"
        else:
            status = (f"{self.time:.1f}s / {self.n / self.fps:.1f}s"
                      f"  (x{self.speed:g})")
        _text(img, status, (10, height - 15), (255, 255, 255), 0.7, 2)
        return img

    def release(self):
        self.cap.release()


# =============================================================================
# MAIN
# =============================================================================
def main(video_path=None):
    """Run the live comparison. video_path defaults to REFERENCE_VIDEO."""
    if PLAYBACK_SPEED <= 0:
        raise ValueError("PLAYBACK_SPEED must be greater than 0 (e.g. 0.5, 1.0, 2.0).")
    ensure_model()
    path = resolve_path(video_path or REFERENCE_VIDEO)
    try:
        data, ref_fps = analyse_reference(path)
    except ReferenceVideoError as e:
        sys.exit(f"{e}\nCheck REFERENCE_VIDEO at the top of the script.")
    player = ReferencePlayer(path, data, ref_fps, PLAYBACK_SPEED)
    current_clip = "reference"   # id of the clip the player is showing

    # ---- per-run scoring state (Nervin's scoring) ---------------------------
    balance = BalanceBar(player.n / player.fps)    # the chess.com-style bar
    smoother = ScoreSmoother()
    recorder = RunRecorder(player.data, player.fps)  # position / timing / sharpness / holds
    run_reported = False      # True once the current run's scores were printed
    score_sum, score_count = 0.0, 0   # running average since the video (re)started
    streak = 0.0              # seconds of continuous green score (sent to the page)

    def reset_run():
        """Throw away the current run and start a fresh one (on the current clip)."""
        nonlocal balance, recorder, run_reported, score_sum, score_count, streak
        balance = BalanceBar(player.n / player.fps)  # new clip may have a new length
        smoother.reset()
        recorder = RunRecorder(player.data, player.fps)
        run_reported = False
        score_sum, score_count, streak = 0.0, 0, 0.0

    def end_run(complete):
        """Report the current run's scores - once only."""
        nonlocal run_reported
        if not run_reported:
            run_reported = True
            finish_run(recorder, complete)

    # ---- websocket bridge (Yash's page <-> script) --------------------------
    bridge = None
    if ENABLE_WEBSOCKET:
        bridge = WebBridge(WS_HOST, WS_PORT, PLAYBACK_SPEED)
        bridge.register_clip("reference",
                             os.path.splitext(os.path.basename(path))[0],
                             path, data, ref_fps)
        bridge.start()
        if bridge.enabled:
            start_web_server(bridge)
        if bridge.enabled and WAIT_FOR_PAGE_START:
            player.paused = True
            print('Waiting for "Start dancing" on the page (or press SPACE here).')

    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=MODEL_PATH),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=MAX_POSES,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )

    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        raise RuntimeError("Could not open webcam. Check CAMERA_INDEX / permissions.")
    cap.set(cv2.CAP_PROP_FPS, TARGET_FPS or 0)  # hint to the camera (not always honoured)
    frame_time = 1.0 / TARGET_FPS if TARGET_FPS else 0

    start = time.time()
    prev = start              # for the FPS counter
    last_loop = start         # for the reference clock
    last_print = 0.0
    last_ws_send = 0.0

    with vision.PoseLandmarker.create_from_options(options) as landmarker:
        while True:
            loop_start = time.time()
            dt = loop_start - last_loop
            last_loop = loop_start

            ok, frame = cap.read()
            if not ok:
                break
            if MIRROR:
                frame = cv2.flip(frame, 1)
            h, w = frame.shape[:2]

            # Commands from the page: speed slider, clip choice, start, reset
            if bridge is not None and bridge.enabled:
                player.speed = bridge.speed
                while bridge.events:
                    event, clip_id = bridge.events.popleft()
                    # Page picked / uploaded a clip -> it becomes the reference
                    if event in ("choose_clip", "start") and clip_id != current_clip:
                        rec = bridge.clip_store.get(clip_id)
                        if rec:
                            player.load(rec["path"], rec["data"], rec["fps"])
                            current_clip = clip_id
                            reset_run()
                            print(f"Reference video is now: {rec['path']}")
                    if event == "choose_clip":  # still on the chooser screen
                        player.paused = True
                    elif event == "start":      # page: "Start dancing"
                        player.restart()
                        player.paused = False
                        reset_run()
                        bridge.send_state("playing")
                    elif event == "reset":      # page: back to the chooser
                        player.restart()
                        player.paused = True
                        reset_run()
                        bridge.send_state("waiting")

            # Advance the reference video. A loop ends the run (scores printed)
            # and starts a new one; without looping, the run ends once at the end.
            if player.update(dt):
                end_run(complete=True)
                reset_run()
            elif player.finished:
                end_run(complete=True)
            _, ref_angles = player.current

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            timestamp_ms = int((time.time() - start) * 1000)  # must increase each frame
            result = landmarker.detect_for_video(mp_image, timestamp_ms)

            ws_score, ws_missing = None, ["whole body"]  # used if nobody is detected

            if not result.pose_landmarks:
                _text(frame, "No person detected", (10, 70),
                      (0, 0, 255), 0.7, 2)

            for pose in result.pose_landmarks:
                draw_skeleton(frame, pose)

                segs = get_segments(pose, w, h)
                results = compare(segment_angles(segs), ref_angles or {})
                overall = overall_score(results)

                draw_segment_colors(frame, segs, results)
                if SHOW_PART_SCORES:
                    draw_part_scores(frame, segs, results)
                draw_panel(frame, results, overall)

                ws_score = overall
                ws_missing = [n for n, r in results.items() if r is None]

                # Only count scores while the reference is really playing
                if overall is not None and player.playing:
                    score_sum += overall
                    score_count += 1
                    recorder.record(player.time, results, overall)

                    # Score bar: smooth the overall score, band it, store it at this video time
                    smooth = smoother.update(overall, time.time())
                    balance.record(player.time, balance_band(smooth))

                    # Hand the per-body-part scores to anyone who asked for them
                    if SCORE_CALLBACK is not None:
                        SCORE_CALLBACK({
                            "time": player.time,
                            "overall": overall,
                            "parts": {n: (r["score"] if r else None)
                                      for n, r in results.items()},
                            "balance": balance.value,
                        })

                if PRINT_SCORES and time.time() - last_print >= PRINT_INTERVAL:
                    last_print = time.time()
                    parts = [
                        f"{n}: {r['score']:.0f}" if r else f"{n}: --"
                        for n, r in results.items()
                    ]
                    total = f"{overall:.0f}" if overall is not None else "--"
                    bar_txt = f"BAR {balance.value * 100:.0f}% | " if SHOW_SCORE_BAR else ""
                    print(f"TOTAL {total} | {bar_txt}" + " | ".join(parts))

            # Green streak: grows while playing and the score is green, else resets
            if player.playing and band_for_score(ws_score) == "green":
                streak += dt
            else:
                streak = 0.0

            # Send the score to the page ~10 times a second
            if bridge is not None and time.time() - last_ws_send >= WS_SEND_INTERVAL:
                last_ws_send = time.time()
                bridge.send_score(ws_score, streak, ws_missing)

            if SHOW_SCORE_BAR:
                draw_score_bar(frame, balance.value)

            if SHOW_AVG_SCORE and score_count:
                avg = score_sum / score_count
                _text(frame, f"AVG: {avg:.0f}", (10, h - 55),
                      color_for_score(avg), 0.8, 2)

            if player.countdown > 0:
                _text(frame, str(math.ceil(player.countdown)),
                      (w // 2 - 40, h // 2 + 50), (0, 255, 255), 4, 8)
                _text(frame, "Get ready!", (w // 2 - 80, h // 2 - 60),
                      (0, 255, 255), 1.0, 3)

            now = time.time()
            fps = 1.0 / max(now - prev, 1e-6)
            prev = now
            _text(frame, f"FPS: {fps:.0f}", (10, 30), (255, 255, 255), 0.8, 2)

            cv2.imshow("Pose compare (live | reference video)",
                       np.hstack([frame, player.render(h)]))

            key = cv2.waitKey(1) & 0xFF
            if key in (ord("q"), 27):
                break
            if key == ord(" "):
                player.paused = not player.paused
            elif key == ord("r"):
                player.restart()
                reset_run()          # discard the run so far

            # Cap the frame rate by sleeping off any leftover frame time
            elapsed = time.time() - loop_start
            if elapsed < frame_time:
                time.sleep(frame_time - elapsed)

    cap.release()
    player.release()
    cv2.destroyAllWindows()
    end_run(complete=False)   # a run stopped early is still reported
    if score_count:
        print(f"Average score: {score_sum / score_count:.0f}")
        if SHOW_SCORE_BAR:
            print(f"Final score bar: {balance.value * 100:.0f}% green")


if __name__ == "__main__":
    main()
