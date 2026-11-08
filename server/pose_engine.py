"""
pose_engine.py - the pose-comparison brain of the Live Dance Coach server.

This is the code from pose_compare_video.py reorganised as a library (no
OpenCV windows, no keyboard handling) so main.py / session.py can drive it from
the website. The scoring maths is identical - same angle convention, same point
bands, same variables - so anything you tuned there carries over.

WHAT'S IN HERE
--------------
1. CONFIG / SCORING variables      - tune the camera, calibration and score bands
2. Geometry                        - 11 body-segment angles from 33 landmarks
3. Scoring                         - angle difference -> points -> green/amber/red
4. Calibration check               - "are you standing in the box?"
5. Reference clips                 - analyse an mp4 ONCE, cache the angles, look
                                     them up by video time (ClipLibrary,
                                     ReferenceTrack)
6. CameraWorker                    - background thread: webcam -> pose -> live
                                     scores -> annotated JPEG for the browser

ANGLE CONVENTION (unchanged)
----------------------------
Each segment is a line between two landmarks, ordered so it points "downwards"
when you stand upright (e.g. shoulder -> elbow). Its angle is measured against
straight down, as seen on screen:
    0 deg = pointing straight down,  + = clockwise,  - = anticlockwise.

LEFT / RIGHT
------------
Segments are labelled L / R by which side of the PICTURE they are on (decided
per frame from the shoulder/hip positions). We always compare what you see on
screen with what you see on screen, so this works whether or not the camera view
is mirrored and whether the dancer faces the camera or not.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cv2

# =============================================================================
# CONFIG
# =============================================================================
SERVER_DIR = Path(__file__).resolve().parent

MODEL_VARIANT = "lite"            # "lite" (fastest), "full", or "heavy" (most accurate)
MODEL_PATH = SERVER_DIR / f"pose_landmarker_{MODEL_VARIANT}.task"
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    f"pose_landmarker_{MODEL_VARIANT}/float16/latest/"
    f"pose_landmarker_{MODEL_VARIANT}.task"
)

CAMERA_INDEX = 0                  # try 1 if you have multiple cameras
CAMERA_REQUEST_WIDTH = 1280       # asked of the camera (it may pick something else)
CAMERA_REQUEST_HEIGHT = 720
CAMERA_FPS_CAP = 30               # max frames per second processed
STREAM_MAX_WIDTH = 960            # frames sent to the browser are shrunk to this width
JPEG_QUALITY = 80

MIN_VISIBILITY = 0.5              # ignore landmarks the model isn't confident about
REF_PROCESS_HEIGHT = 480          # reference frames shrunk to this for analysis (faster,
                                  # doesn't change the angles)
CLIP_MIN_TRACKED_PCT = 40         # below this % of frames with a dancer, the clip is unusable
CLIP_WARN_TRACKED_PCT = 80        # below this, the clip is usable but we warn

# ---- calibration ("stand in the box") ---------------------------------------
# On the calibration screen the dashed box is the whole camera picture, so
# "inside the box" means "inside the picture" and these are fractions of it.
CALIB_MIN_BODY_HEIGHT = 0.55      # you must fill at least this fraction of the box height
CALIB_EDGE_MARGIN = 0.01          # touching the box edge closer than this counts as cut off
CALIB_CENTER_TOLERANCE = 0.15     # how far from the box centre (fraction of width) is OK
CALIB_HOLD_SECONDS = 2.0          # how long you must hold still before the countdown starts
                                  # (the page says "Whole body visible for 2 seconds")

# =============================================================================
# SCORING - point ranges stored as variables (same as pose_compare_video.py)
# =============================================================================
# Each band covers a range of angle differences (degrees). At the START of the
# band you get *_SCORE_HIGH points, at the END *_SCORE_LOW, falling linearly.

# Dead zone: 0 -> DEADZONE_DEG degrees difference  =>  100 points. The webcam's pose
# tracking wobbles by about this much even when you copy the reference perfectly, so
# without it a perfect dance could never reach 100.
DEADZONE_DEG = 8

# Band 1: DEADZONE_DEG -> 20 degrees difference  =>  100 -> 60 points
BAND1_MAX_DIFF = 20
BAND1_SCORE_HIGH = 100
BAND1_SCORE_LOW = 60

# Band 2: 20 -> 35 degrees difference  =>  59 -> 20 points  (was 20 -> 40)
BAND2_MAX_DIFF = 35
BAND2_SCORE_HIGH = 59
BAND2_SCORE_LOW = 20

# Band 3: 35 -> BAND3_MAX_DIFF degrees difference  =>  19 -> 0 points (was 40 -> 60)
BAND3_MAX_DIFF = 50
BAND3_SCORE_HIGH = 19
BAND3_SCORE_LOW = 0

# (start_diff, end_diff, score_at_start, score_at_end) for each band
SCORE_BANDS = [
    (0,              DEADZONE_DEG,   BAND1_SCORE_HIGH, BAND1_SCORE_HIGH),
    (DEADZONE_DEG,   BAND1_MAX_DIFF, BAND1_SCORE_HIGH, BAND1_SCORE_LOW),
    (BAND1_MAX_DIFF, BAND2_MAX_DIFF, BAND2_SCORE_HIGH, BAND2_SCORE_LOW),
    (BAND2_MAX_DIFF, BAND3_MAX_DIFF, BAND3_SCORE_HIGH, BAND3_SCORE_LOW),
]

# OVERALL per frame: half the plain average of the body parts, half the average of the
# WORST third. Otherwise body parts that barely move (legs, torso, head in most moves)
# score ~95 and hide a badly wrong arm, so the score could never go really low.
WORST_SHARE = 1 / 3      # how many body parts count as "the worst" (a third of them)
WORST_WEIGHT = 0.5       # 0 = plain average (old behaviour), 1 = only the worst parts

# The website's three colours. A score is "green" at/above GREEN_MIN_SCORE,
# "amber" at/above AMBER_MIN_SCORE, otherwise "red".
GREEN_MIN_SCORE = BAND1_SCORE_LOW
AMBER_MIN_SCORE = BAND2_SCORE_LOW
BAND_BGR = {                      # colours used on the camera overlay (BGR)
    "green": (0, 200, 0),
    "amber": (0, 165, 255),
    "red": (0, 0, 255),
}

# =============================================================================
# SEGMENTS - which body parts we measure
# =============================================================================
# A landmark can be an int, or a tuple of ints meaning "midpoint of these".
# For left/right pairs the "other side" landmark index is this index + 1.
LIMB_SEGMENTS = [
    ("upper arm", 11, 13),   # shoulder -> elbow
    ("forearm",   13, 15),   # elbow -> wrist
    ("torso",     11, 23),   # shoulder -> hip
    ("thigh",     23, 25),   # hip -> knee
    ("shin",      25, 27),   # knee -> ankle (use 31 / 32 for toes instead)
]
HEAD_SEGMENT = ("head", (7, 8), (11, 12))  # ear midpoint -> shoulder midpoint

SEGMENT_NAMES = (
    [f"{side} {name}" for side in ("L", "R") for name, _, _ in LIMB_SEGMENTS]
    + [HEAD_SEGMENT[0]]
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
# SCORING FUNCTIONS
# =============================================================================
def angle_diff(live: float, ref: float) -> float:
    """Signed difference live - ref, wrapped to -180..180 (so +179 vs -179 = 2)."""
    return (live - ref + 180) % 360 - 180


def score_diff(diff: float) -> float:
    """Turn an angle difference (degrees, sign ignored) into 0-100 points."""
    d = abs(diff)
    for start, end, score_high, score_low in SCORE_BANDS:
        if d <= end:
            t = (d - start) / (end - start)  # 0 at band start, 1 at band end
            return score_high + t * (score_low - score_high)
    return BAND3_SCORE_LOW  # past the last band


def band_for(score: Optional[float]) -> str:
    """'green' / 'amber' / 'red' for a score, or 'none' if there is no score."""
    if score is None:
        return "none"
    if score >= GREEN_MIN_SCORE:
        return "green"
    if score >= AMBER_MIN_SCORE:
        return "amber"
    return "red"


# =============================================================================
# GEOMETRY
# =============================================================================
# A "landmark list" is 33 tuples (x, y, visibility) with x and y in 0..1.

def to_landmarks(pose) -> list:
    """MediaPipe landmark objects -> plain (x, y, visibility) tuples."""
    out = []
    for lm in pose:
        vis = getattr(lm, "visibility", None)
        out.append((lm.x, lm.y, 1.0 if vis is None else vis))
    return out


def _point(lms, spec, w, h):
    """Pixel position of a landmark (or midpoint), or None if not visible."""
    idxs = spec if isinstance(spec, tuple) else (spec,)
    xs, ys = [], []
    for i in idxs:
        x, y, v = lms[i]
        if v < MIN_VISIBILITY:
            return None
        xs.append(x * w)
        ys.append(y * h)
    return sum(xs) / len(xs), sum(ys) / len(ys)


def _angle(p, q) -> float:
    """Angle of p->q vs straight down on screen; clockwise is positive."""
    dx, dy = q[0] - p[0], q[1] - p[1]  # image y points down
    return math.degrees(math.atan2(-dx, dy))


def _screen_left_offset(lms) -> int:
    """0 or 1: which landmark of each left/right pair is on the LEFT of the picture.

    For a person facing the camera the model's "right" landmarks (index + 1) are
    on the left of the picture, so the default is 1. We check the real x
    positions so it also works if the dancer faces away.
    """
    a = [lms[i][0] for i in (11, 23) if lms[i][2] >= MIN_VISIBILITY]
    b = [lms[i][0] for i in (12, 24) if lms[i][2] >= MIN_VISIBILITY]
    if a and b:
        return 0 if sum(a) / len(a) < sum(b) / len(b) else 1
    return 1


def get_segments(lms, w, h) -> dict:
    """{segment name: (start_point, end_point)} in pixels (None if hidden)."""
    left = _screen_left_offset(lms)
    segs = {}
    for label, offset in (("L", left), ("R", 1 - left)):
        for name, a, b in LIMB_SEGMENTS:
            segs[f"{label} {name}"] = (
                _point(lms, a + offset, w, h),
                _point(lms, b + offset, w, h),
            )
    name, a, b = HEAD_SEGMENT
    segs[name] = (_point(lms, a, w, h), _point(lms, b, w, h))
    return segs


def segment_angles(segs: dict) -> dict:
    """{segment name: angle in degrees, or None if not visible}."""
    return {
        name: (_angle(p, q) if p and q else None)
        for name, (p, q) in segs.items()
    }


def segment_label(name: str, mirror: bool = True) -> str:
    """'L forearm' -> 'left forearm' (from the dancer's own point of view).

    L/R in segment names means left/right of the PICTURE. With the camera view
    mirrored (selfie view) that is your own left/right; with it un-mirrored it is
    the opposite.
    """
    if name[:2] in ("L ", "R "):
        is_left = name[0] == "L"
        body_left = is_left if mirror else not is_left
        return ("left " if body_left else "right ") + name[2:]
    return name


def missing_parts(angles: Optional[dict], mirror: bool = True) -> list:
    """Body parts that can't be seen, named like the website expects."""
    if not angles:
        return ["body"]
    out = []
    for side in ("L", "R"):
        body = segment_label(f"{side} x", mirror).split()[0]  # "left" / "right"
        if any(angles.get(f"{side} {n}") is None for n in ("upper arm", "forearm")):
            out.append(f"{body}_arm")
        if any(angles.get(f"{side} {n}") is None for n in ("thigh", "shin")):
            out.append(f"{body}_leg")
    if angles.get("L torso") is None and angles.get("R torso") is None:
        out.append("torso")
    if angles.get("head") is None:
        out.append("head")
    return out


# =============================================================================
# COMPARISON
# =============================================================================
def compare(live_angles: dict, ref_angles: dict) -> dict:
    """Score every segment. Returns {name: dict or None}.

    Each dict has: live, ref (angles), diff (signed, wrapped), score (0-100).
    A segment is None if it isn't visible in the live feed or the reference.
    """
    results = {}
    for name, live in live_angles.items():
        ref = ref_angles.get(name)
        if live is None or ref is None:
            results[name] = None
            continue
        diff = angle_diff(live, ref)
        results[name] = {
            "live": live, "ref": ref, "diff": diff, "score": score_diff(diff),
        }
    return results


def overall_score(results: dict) -> Optional[float]:
    """Score for the whole body this frame (None if nothing could be compared).

    Half the plain average of the body parts, half the average of the worst third
    (WORST_WEIGHT / WORST_SHARE), so one badly wrong limb really shows."""
    scores = sorted(r["score"] for r in results.values() if r is not None)
    if not scores:
        return None
    k = max(1, round(len(scores) * WORST_SHARE))
    mean = sum(scores) / len(scores)
    worst = sum(scores[:k]) / k
    return (1 - WORST_WEIGHT) * mean + WORST_WEIGHT * worst


# =============================================================================
# CALIBRATION
# =============================================================================
_CALIB_REQUIRED = {
    "shoulders": (11, 12), "hips": (23, 24), "knees": (25, 26), "feet": (27, 28),
}


def check_calibration(lms: Optional[list], mirror: bool = True):
    """Is the dancer standing properly in view? Returns (ok, [reasons])."""
    if lms is None:
        return False, ["I can't see you yet - step into the box"]

    reasons = []
    missing = [name for name, idxs in _CALIB_REQUIRED.items()
               if any(lms[i][2] < MIN_VISIBILITY for i in idxs)]
    if missing:
        reasons.append("Step back so your whole body is in view "
                       f"(can't see your {', '.join(missing)})")

    seen = [(x, y) for x, y, v in lms if v >= MIN_VISIBILITY]
    if len(seen) >= 4:
        xs = [p[0] for p in seen]
        ys = [p[1] for p in seen]
        height = max(ys) - min(ys)
        if not missing and height < CALIB_MIN_BODY_HEIGHT:
            reasons.append("Come a little closer")
        if (min(ys) < CALIB_EDGE_MARGIN or max(ys) > 1 - CALIB_EDGE_MARGIN) and not missing:
            reasons.append("Step back a little - you're touching the edge of the box")
        offset = (min(xs) + max(xs)) / 2 - 0.5
        if abs(offset) > CALIB_CENTER_TOLERANCE:
            need_screen_right = offset < 0
            if mirror:
                direction = "right" if need_screen_right else "left"
            else:
                direction = "left" if need_screen_right else "right"
            reasons.append(f"Move a little to your {direction} to centre yourself")
    return (not reasons), reasons


# =============================================================================
# DRAWING (camera overlay)
# =============================================================================
def draw_skeleton(frame, lms, color=(255, 255, 255)):
    h, w = frame.shape[:2]
    thick = max(2, w // 400)
    pts = [(int(x * w), int(y * h)) if v >= MIN_VISIBILITY else None for x, y, v in lms]
    for a, b in POSE_CONNECTIONS:
        if pts[a] and pts[b]:
            cv2.line(frame, pts[a], pts[b], color, thick, cv2.LINE_AA)
    for p in pts:
        if p:
            cv2.circle(frame, p, thick + 2, color, -1, cv2.LINE_AA)


def draw_segment_colors(frame, segs, results):
    """Re-draw each measured segment in green / amber / red by its score."""
    thick = max(4, frame.shape[1] // 200)
    for name, r in results.items():
        if r is None:
            continue
        p, q = segs[name]
        cv2.line(frame, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])),
                 BAND_BGR.get(band_for(r["score"]), (255, 255, 255)), thick, cv2.LINE_AA)


# =============================================================================
# POSE MODEL
# =============================================================================
_model_lock = threading.Lock()


def ensure_model():
    """Download the pose model next to this file if it isn't there yet."""
    with _model_lock:
        if MODEL_PATH.exists():
            return
        print(f"[engine] Downloading {MODEL_PATH.name} ...")
        tmp = MODEL_PATH.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(MODEL_PATH)
        print("[engine] Model downloaded.")


class PoseDetector:
    """MediaPipe Pose Landmarker in VIDEO mode: BGR frame in, landmarks out."""

    def __init__(self):
        import mediapipe as mp
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision

        ensure_model()
        self._mp = mp
        options = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=str(MODEL_PATH)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._last_ts = -1

    def detect(self, bgr_frame, ts_ms: int) -> Optional[list]:
        """Landmarks (33 x (x, y, visibility)) for one person, or None."""
        ts = max(int(ts_ms), self._last_ts + 1)  # timestamps must keep increasing
        self._last_ts = ts
        rgb = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        result = self._landmarker.detect_for_video(image, ts)
        if result.pose_landmarks:
            return to_landmarks(result.pose_landmarks[0])
        return None

    def close(self):
        self._landmarker.close()


# =============================================================================
# REFERENCE CLIPS
# =============================================================================
class ReferenceTrack:
    """The reference dancer's segment angles for every frame of a clip."""

    def __init__(self, fps: float, frames: list, duration_s: float):
        self.fps = fps
        self.frames = frames          # per frame: list of angles (SEGMENT_NAMES order) or None
        self.duration_s = duration_s

    def angles_at(self, t: Optional[float]) -> Optional[dict]:
        """{segment: angle} at video time t (seconds), or None if no dancer there."""
        if t is None or not self.frames:
            return None
        i = min(max(int(round(t * self.fps)), 0), len(self.frames) - 1)
        row = self.frames[i]
        if row is None:
            return None
        return dict(zip(SEGMENT_NAMES, row))


def _sane_fps(fps) -> float:
    return fps if fps and fps == fps and fps >= 1 else 30.0  # missing / NaN / nonsense -> 30


def analyse_clip(video_path, cache_path, thumb_path=None,
                 progress_cb: Optional[Callable[[int, str], None]] = None,
                 detector_factory=None) -> dict:
    """Run a reference video through the pose model once and cache the result.

    Writes the per-frame angles to cache_path (JSON) and a thumbnail to
    thumb_path. Returns the cache dict. progress_cb(percent, stage) is called as
    it goes (from the calling thread).
    """
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise ValueError("Could not open this video file")
    fps = _sane_fps(cap.get(cv2.CAP_PROP_FPS))
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    detector = (detector_factory or PoseDetector)()
    frames, thumb, last_pct = [], None, -1
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame.shape[0] > REF_PROCESS_HEIGHT:
                scale = REF_PROCESS_HEIGHT / frame.shape[0]
                frame = cv2.resize(
                    frame, (max(1, int(frame.shape[1] * scale)), REF_PROCESS_HEIGHT))
            h, w = frame.shape[:2]

            lms = detector.detect(frame, int(len(frames) * 1000 / fps))
            if lms is None:
                frames.append(None)
            else:
                angles = segment_angles(get_segments(lms, w, h))
                frames.append([None if angles[n] is None else round(angles[n], 1)
                               for n in SEGMENT_NAMES])
                if thumb is None and (not total or len(frames) >= total * 0.1):
                    thumb = frame.copy()

            if progress_cb and total:
                pct = min(100, len(frames) * 100 // total)
                if pct != last_pct:
                    last_pct = pct
                    progress_cb(pct, "Tracking the dancer...")
    finally:
        cap.release()
        detector.close()

    if not frames:
        raise ValueError("The video has no readable frames")
    tracked = sum(1 for f in frames if f is not None)
    data = {
        "version": 1,
        "segments": SEGMENT_NAMES,
        "fps": fps,
        "n_frames": len(frames),
        "duration_s": len(frames) / fps,
        "tracked_pct": round(tracked * 100 / len(frames)),
        "frames": frames,
    }
    tmp = Path(str(cache_path) + ".part")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(cache_path)
    if thumb_path is not None and thumb is not None:
        cv2.imwrite(str(thumb_path), thumb, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return data


class ClipLibrary:
    """The clips folder: lists clips for the page, analyses them, hands out tracks.

    Next to each video (dance.mp4) we keep dance.pose.json (the cached angles)
    and dance.jpg (the thumbnail).
    """

    EXTENSIONS = (".mp4", ".webm", ".mov")

    def __init__(self, clips_dir, url_prefix="/clips", detector_factory=None):
        self.dir = Path(clips_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.url_prefix = url_prefix.rstrip("/")
        self._detector_factory = detector_factory
        self._memo: dict = {}   # video path -> (mtime, cache dict)

    # ---- file helpers -------------------------------------------------------
    def _videos(self) -> list:
        return sorted(p for p in self.dir.iterdir()
                      if p.is_file() and p.suffix.lower() in self.EXTENSIONS)

    def _find(self, clip_id: str) -> Optional[Path]:
        for video in self._videos():
            if video.stem == clip_id:
                return video
        return None

    @staticmethod
    def _cache_path(video: Path) -> Path:
        return video.with_name(video.stem + ".pose.json")

    @staticmethod
    def _thumb_path(video: Path) -> Path:
        return video.with_name(video.stem + ".jpg")

    def _data(self, video: Path) -> Optional[dict]:
        """The cached analysis for a video, or None if it still needs analysing."""
        cache = self._cache_path(video)
        if not cache.exists() or cache.stat().st_mtime < video.stat().st_mtime:
            return None
        mtime = cache.stat().st_mtime
        memo = self._memo.get(str(video))
        if memo and memo[0] == mtime:
            return memo[1]
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("segments") != SEGMENT_NAMES:   # segments changed -> re-analyse
            return None
        self._memo[str(video)] = (mtime, data)
        return data

    def new_upload_path(self, filename: str) -> Path:
        """A safe, unused path in the clips folder for an uploaded file."""
        suffix = Path(filename or "").suffix.lower()
        stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "").stem).strip("._") or "clip"
        candidate, n = stem, 1
        while any((self.dir / f"{candidate}{ext}").exists() for ext in self.EXTENSIONS):
            n += 1
            candidate = f"{stem}-{n}"
        return self.dir / f"{candidate}{suffix}"

    # ---- what the page sees -------------------------------------------------
    def _info(self, video: Path) -> dict:
        data = self._data(video)
        thumb = self._thumb_path(video)
        pretty = video.stem.replace("_", " ").replace("-", " ")
        info = {
            "id": video.stem,
            "name": pretty[:1].upper() + pretty[1:],
            "url": f"{self.url_prefix}/{urllib.parse.quote(video.name)}",
            "thumb": (f"{self.url_prefix}/{urllib.parse.quote(thumb.name)}"
                      if thumb.exists() else None),
            "duration_s": round(data["duration_s"], 1) if data else None,
            "ref_ok": False,
            "warning": None,
        }
        if data is None:
            info["warning"] = "Analysing this clip - one moment..."
        elif data["tracked_pct"] < CLIP_MIN_TRACKED_PCT:
            info["warning"] = (f"Dancer only found in {data['tracked_pct']}% of frames - "
                               "try one dancer, full body, steady camera.")
        else:
            info["ref_ok"] = True
            if data["tracked_pct"] < CLIP_WARN_TRACKED_PCT:
                info["warning"] = f"Dancer only found in {data['tracked_pct']}% of frames."
        return info

    def scan(self) -> list:
        """Clip list in the shape the page's `clips` message expects."""
        return [self._info(v) for v in self._videos()]

    def info(self, clip_id: str) -> Optional[dict]:
        video = self._find(clip_id)
        return self._info(video) if video else None

    def delete(self, clip_id: str):
        """Remove a clip and its cache/thumbnail files."""
        video = self._find(clip_id)
        if video is None:
            return
        for path in (video, self._cache_path(video), self._thumb_path(video)):
            try:
                path.unlink()
            except OSError:
                pass
        self._memo.pop(str(video), None)

    def tracked_pct(self, clip_id: str) -> Optional[int]:
        video = self._find(clip_id)
        data = self._data(video) if video else None
        return data["tracked_pct"] if data else None

    # ---- analysis -----------------------------------------------------------
    def pending(self) -> list:
        """Ids of clips that haven't been analysed yet."""
        return [v.stem for v in self._videos() if self._data(v) is None]

    def analyse(self, clip_id: str, progress_cb=None) -> dict:
        """Analyse one clip (slow - run it in a thread). Returns its page info."""
        video = self._find(clip_id)
        if video is None:
            raise ValueError(f"No clip called {clip_id!r}")
        analyse_clip(video, self._cache_path(video), self._thumb_path(video),
                     progress_cb, self._detector_factory)
        self._memo.pop(str(video), None)
        return self._info(video)

    def track(self, clip_id: str) -> ReferenceTrack:
        video = self._find(clip_id)
        data = self._data(video) if video else None
        if data is None:
            raise ValueError("That clip hasn't been analysed yet")
        return ReferenceTrack(data["fps"], data["frames"], data["duration_s"])


# =============================================================================
# VIDEO CLOCK
# =============================================================================
class VideoClock:
    """Where the browser's reference video is, estimated between its reports.

    The page tells us its video time ~10x a second (`video_time` messages). The
    camera runs faster than that, so we extrapolate from the last report using the
    playback rate.
    """

    def __init__(self):
        self._state = None  # (t, playing, rate, monotonic stamp)

    def update(self, t, playing, rate):
        if t is None:
            return
        self._state = (float(t), bool(playing), float(rate or 1.0), time.monotonic())

    def reset(self):
        self._state = None

    def now(self) -> Optional[float]:
        s = self._state
        if s is None:
            return None
        t, playing, rate, stamp = s
        return t + (time.monotonic() - stamp) * rate if playing else t


# =============================================================================
# CAMERA WORKER
# =============================================================================
@dataclass
class Snapshot:
    """What the camera thread knows about the latest frame."""
    ts: float                    # time.monotonic() when it was produced
    lms: Optional[list]          # 33 x (x, y, visibility), or None if nobody found
    angles: Optional[dict]       # live segment angles, or None
    results: Optional[dict]      # compare() output vs the reference (when playing)
    overall: Optional[float]     # average segment score for this frame, or None
    video_t: Optional[float]     # reference video time used for the comparison
    seq: int                     # frame counter


def _open_webcam():
    backend = cv2.CAP_DSHOW if os.name == "nt" else cv2.CAP_ANY
    cap = cv2.VideoCapture(CAMERA_INDEX, backend)
    if not cap.isOpened():
        raise RuntimeError(
            "Could not open the webcam. Close other apps that use it, check camera "
            "permissions, or change CAMERA_INDEX in pose_engine.py.")
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, CAMERA_REQUEST_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, CAMERA_REQUEST_HEIGHT)
    cap.set(cv2.CAP_PROP_FPS, CAMERA_FPS_CAP)
    return cap


class CameraWorker:
    """Background thread: webcam -> pose -> live scores -> annotated JPEG.

    Control it from the session:
        camera.mirror = True/False         flip the picture (selfie view)
        camera.track  = ReferenceTrack     what to score against (None = just track)
        camera.clock  = callable -> video time in seconds
    Read from it:
        camera.snapshot                    latest Snapshot
        camera.latest()                    (sequence number, JPEG bytes) for the stream
        camera.error                       message if something broke, else None
    """

    def __init__(self, detector_factory=None, capture_factory=None):
        self._detector_factory = detector_factory or PoseDetector
        self._capture_factory = capture_factory or _open_webcam
        self.mirror = True
        self.track: Optional[ReferenceTrack] = None
        self.clock: Optional[Callable[[], Optional[float]]] = None
        self.error: Optional[str] = None
        self.snapshot = Snapshot(time.monotonic(), None, None, None, None, None, 0)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._jpeg: Optional[bytes] = None
        self._jpeg_seq = 0

    @property
    def running(self) -> bool:
        return (self._thread is not None and self._thread.is_alive()
                and not self._stop.is_set())

    def latest(self):
        return self._jpeg_seq, self._jpeg

    def start(self, timeout: float = 90.0):
        """Open the camera + load the model. Blocks until ready; raises on failure."""
        if self.running:
            return
        self.error = None
        self._jpeg = None
        stop, ready = threading.Event(), threading.Event()
        self._stop = stop
        self._thread = threading.Thread(
            target=self._run, args=(stop, ready), daemon=True, name="camera-worker")
        self._thread.start()
        if not ready.wait(timeout):
            stop.set()
            raise RuntimeError("Timed out starting the camera and the body-tracking model")
        if self.error:
            raise RuntimeError(self.error)

    def stop(self):
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
        self._jpeg = None

    # ---- thread body --------------------------------------------------------
    def _run(self, stop: threading.Event, ready: threading.Event):
        detector = cap = None
        try:
            detector = self._detector_factory()
            cap = self._capture_factory()
        except Exception as exc:  # model missing, camera busy, ...
            self.error = str(exc) or exc.__class__.__name__
            ready.set()
            if detector is not None:
                detector.close()
            return
        ready.set()

        t0 = time.monotonic()
        frame_time = 1.0 / CAMERA_FPS_CAP
        failures, seq = 0, 0
        try:
            while not stop.is_set():
                loop_start = time.monotonic()
                ok, frame = cap.read()
                if not ok or frame is None:
                    failures += 1
                    if failures > 100:
                        self.error = "The camera stopped sending pictures"
                        break
                    time.sleep(0.02)
                    continue
                failures = 0

                if self.mirror:
                    frame = cv2.flip(frame, 1)
                lms = detector.detect(frame, int((time.monotonic() - t0) * 1000))
                seq += 1
                snap = self._analyse(lms, frame.shape[1], frame.shape[0], seq)
                self.snapshot = snap
                self._publish(frame, snap)

                spare = frame_time - (time.monotonic() - loop_start)
                if spare > 0:
                    time.sleep(spare)
        except Exception as exc:
            self.error = f"Camera/pose error: {exc}"
        finally:
            cap.release()
            detector.close()

    def _analyse(self, lms, w, h, seq) -> Snapshot:
        angles = results = overall = video_t = None
        if lms is not None:
            angles = segment_angles(get_segments(lms, w, h))
            track, clock = self.track, self.clock
            if track is not None and clock is not None:
                video_t = clock()
                ref = track.angles_at(video_t)
                if ref is not None:
                    results = compare(angles, ref)
                    overall = overall_score(results)
        return Snapshot(time.monotonic(), lms, angles, results, overall, video_t, seq)

    def _publish(self, frame, snap: Snapshot):
        """Draw the skeleton on the frame and keep it as the latest JPEG."""
        h, w = frame.shape[:2]
        if snap.lms is not None:
            draw_skeleton(frame, snap.lms)
            if snap.results:
                draw_segment_colors(frame, get_segments(snap.lms, w, h), snap.results)
        if w > STREAM_MAX_WIDTH:
            frame = cv2.resize(frame, (STREAM_MAX_WIDTH, int(h * STREAM_MAX_WIDTH / w)))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if ok:
            self._jpeg = buf.tobytes()
            self._jpeg_seq += 1
