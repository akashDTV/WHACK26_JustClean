"""
pose_core.py - the pose maths used by server/main.py (no web code in here).

Same idea as pose_compare_video.py:
  * 11 body segments (L/R upper arm, forearm, torso, thigh, shin + head)
  * each segment's angle is measured against straight down on screen
  * the difference to the reference angle is turned into 0-100 points
    (0-20 deg = 100->60, 20-40 deg = 59->20, 40-60 deg = 19->0)

Reference videos are analysed ONCE (mirrored, like a selfie view) and the
angles are cached; the live webcam is compared against them while it plays.
"""

import math
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

BASE_DIR = Path(__file__).resolve().parent.parent

# =============================================================================
# CONFIG
# =============================================================================
MODEL_VARIANT = "lite"          # "lite" (fastest), "full" or "heavy"
MIN_VISIBILITY = 0.5            # ignore landmarks the model isn't confident about
REF_PROCESS_HEIGHT = 480        # reference frames are shrunk to this for analysis

# Where to look for the model file before downloading it
MODEL_CANDIDATES = [
    BASE_DIR / "sandbox" / "pose_landmark_lite.task",
    BASE_DIR / "sandbox" / "pose_landmarker_lite.task",
    BASE_DIR / "models" / f"pose_landmarker_{MODEL_VARIANT}.task",
]
MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
    f"pose_landmarker_{MODEL_VARIANT}/float16/latest/"
    f"pose_landmarker_{MODEL_VARIANT}.task"
)

# Scoring: (start_diff, end_diff, score_at_start, score_at_end), linear inside
SCORE_BANDS = [
    (0, 20, 100, 60),
    (20, 40, 59, 20),
    (40, 60, 19, 0),     # 60 deg is an assumption: beyond it the score stays 0
]
GREEN_FROM = 60              # score >= this is "green"
AMBER_FROM = 20              # score >= this is "amber", below is "red"

# =============================================================================
# SEGMENTS
# =============================================================================
LIMB_SEGMENTS = [
    ("upper arm", 11, 13),   # shoulder -> elbow
    ("forearm",   13, 15),   # elbow -> wrist
    ("torso",     11, 23),   # shoulder -> hip
    ("thigh",     23, 25),   # hip -> knee
    ("shin",      25, 27),   # knee -> ankle
]
HEAD_SEGMENT = ("head", (7, 8), (11, 12))  # ear midpoint -> shoulder midpoint

POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7), (0, 4), (4, 5), (5, 6), (6, 8), (9, 10),
    (11, 12), (11, 13), (13, 15), (15, 17), (15, 19), (15, 21), (17, 19),
    (12, 14), (14, 16), (16, 18), (16, 20), (16, 22), (18, 20),
    (11, 23), (12, 24), (23, 24),
    (23, 25), (24, 26), (25, 27), (26, 28),
    (27, 29), (28, 30), (29, 31), (30, 32), (27, 31), (28, 32),
]

COLOR_GOOD = (0, 200, 0)     # BGR
COLOR_OK = (0, 165, 255)
COLOR_BAD = (0, 0, 255)


class ClipError(Exception):
    """A reference video can't be used (unreadable, or nobody in it)."""


# =============================================================================
# MODEL
# =============================================================================
def ensure_model():
    """Return the path of the pose model, downloading it if it isn't there."""
    for p in MODEL_CANDIDATES:
        if p.exists():
            return p
    target = MODEL_CANDIDATES[-1]
    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {target.name} ...")
    urllib.request.urlretrieve(MODEL_URL, target)
    print("Done.")
    return target


def create_landmarker(num_poses=1):
    """A new video-mode pose landmarker. Call .close() when finished."""
    options = vision.PoseLandmarkerOptions(
        base_options=mp_python.BaseOptions(model_asset_path=str(ensure_model())),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=num_poses,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    return vision.PoseLandmarker.create_from_options(options)


# =============================================================================
# SCORING
# =============================================================================
def angle_diff(live, ref):
    """Signed difference live - ref, wrapped to -180..180."""
    return (live - ref + 180) % 360 - 180


def score_diff(diff):
    """Angle difference (degrees, sign ignored) -> 0-100 points."""
    d = abs(diff)
    for start, end, high, low in SCORE_BANDS:
        if d <= end:
            return high + (d - start) / (end - start) * (low - high)
    return SCORE_BANDS[-1][3]


def band_for_score(score):
    if score is None:
        return "none"
    if score >= GREEN_FROM:
        return "green"
    if score >= AMBER_FROM:
        return "amber"
    return "red"


def color_for_score(score):
    return {"green": COLOR_GOOD, "amber": COLOR_OK}.get(band_for_score(score), COLOR_BAD)


def joint_name(segment):
    """'L upper arm' -> 'left_upper_arm' (the names used in messages.md)."""
    if segment.startswith("L "):
        return "left_" + segment[2:].replace(" ", "_")
    if segment.startswith("R "):
        return "right_" + segment[2:].replace(" ", "_")
    return segment.replace(" ", "_")


# =============================================================================
# GEOMETRY
# =============================================================================
def _point(lms, spec, w, h):
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
    dx, dy = q[0] - p[0], q[1] - p[1]
    return math.degrees(math.atan2(-dx, dy))


def get_segments(lms, w, h, flipped):
    """{segment name: (start_point, end_point)} in pixels (None if hidden).

    `flipped` = the image was mirrored before detection. The model then calls
    the person's left side "right", so the offsets are swapped to keep the
    labels matching the real body.
    """
    segs = {}
    sides = (("L", 1 if flipped else 0), ("R", 0 if flipped else 1))
    for label, offset in sides:
        for name, a, b in LIMB_SEGMENTS:
            segs[f"{label} {name}"] = (_point(lms, a + offset, w, h),
                                       _point(lms, b + offset, w, h))
    name, a, b = HEAD_SEGMENT
    segs[name] = (_point(lms, a, w, h), _point(lms, b, w, h))
    return segs


def segment_angles(segs):
    return {name: (_angle(p, q) if p and q else None) for name, (p, q) in segs.items()}


def orient(ref_angles, flipped):
    """Reference angles are stored for a mirrored video. If the live view is
    NOT mirrored, flip them back (mirroring just negates every angle)."""
    if not ref_angles:
        return {}
    if flipped:
        return ref_angles
    return {k: (-v if v is not None else None) for k, v in ref_angles.items()}


def compare(live_angles, ref_angles):
    """{segment: {live, ref, diff, score} or None if not comparable}."""
    results = {}
    for name, live in live_angles.items():
        ref = ref_angles.get(name)
        if live is None or ref is None:
            results[name] = None
            continue
        diff = angle_diff(live, ref)
        results[name] = {"live": live, "ref": ref, "diff": diff,
                         "score": score_diff(diff)}
    return results


def overall_score(results):
    scores = [r["score"] for r in results.values() if r is not None]
    return sum(scores) / len(scores) if scores else None


# =============================================================================
# CALIBRATION (is the whole body in frame?)
# =============================================================================
def check_framing(lms):
    """Returns (ok, [reasons]). `lms` = normalised landmarks, or None."""
    if lms is None:
        return False, ["step into the frame"]

    def seen(i):
        v = lms[i].visibility
        return v is None or v >= MIN_VISIBILITY

    reasons = []
    head_ok = seen(0)
    feet_ok = seen(27) and seen(28)
    if not head_ok:
        reasons.append("head not visible")
    if not feet_ok:
        reasons.append("step back")
        reasons.append("feet not visible")
    if head_ok and feet_ok:
        top = lms[0].y
        bottom = max(lms[27].y, lms[28].y)
        if top < 0.02 or bottom > 0.98:
            reasons.append("step back")
        elif bottom - top < 0.45:
            reasons.append("step closer")
        cx = (lms[11].x + lms[12].x + lms[23].x + lms[24].x) / 4
        if cx < 0.3 or cx > 0.7:
            reasons.append("move to the centre")
    return (not reasons), reasons


# =============================================================================
# DRAWING
# =============================================================================
def draw_skeleton(frame, landmarks):
    h, w = frame.shape[:2]
    pts = []
    for lm in landmarks:
        visible = lm.visibility is None or lm.visibility >= MIN_VISIBILITY
        pts.append((int(lm.x * w), int(lm.y * h)) if visible else None)
    for a, b in POSE_CONNECTIONS:
        if pts[a] and pts[b]:
            cv2.line(frame, pts[a], pts[b], (255, 255, 255), 2, cv2.LINE_AA)
    for p in pts:
        if p:
            cv2.circle(frame, p, 4, (255, 255, 255), -1, cv2.LINE_AA)


def draw_segment_colors(frame, segs, results):
    """Re-draw each measured segment green / orange / red by its score."""
    for name, r in results.items():
        if r is None:
            continue
        p, q = segs[name]
        cv2.line(frame, (int(p[0]), int(p[1])), (int(q[0]), int(q[1])),
                 color_for_score(r["score"]), 5, cv2.LINE_AA)


# =============================================================================
# REFERENCE VIDEO ANALYSIS
# =============================================================================
def analyse_video(path, on_progress=None):
    """Run a whole video through the pose model once.

    Returns (angles, fps): angles[i] = {segment: angle or None} for frame i
    (or None if nobody was found). Frames are mirrored first, like the live
    selfie view, so use orient() before comparing with an un-mirrored view.
    on_progress(percent) is called every 10%. Raises ClipError if unusable.
    """
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ClipError("Could not open that video file.")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps != fps or fps < 1:
        fps = 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

    angles = []
    last_ts = -1
    next_report = 10
    landmarker = create_landmarker(num_poses=1)
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            frame = cv2.flip(frame, 1)
            if frame.shape[0] > REF_PROCESS_HEIGHT:
                scale = REF_PROCESS_HEIGHT / frame.shape[0]
                frame = cv2.resize(frame, (max(1, int(frame.shape[1] * scale)),
                                           REF_PROCESS_HEIGHT))
            h, w = frame.shape[:2]
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            ts = max(int(len(angles) * 1000 / fps), last_ts + 1)
            last_ts = ts
            result = landmarker.detect_for_video(mp_image, ts)

            if result.pose_landmarks:
                segs = get_segments(result.pose_landmarks[0], w, h, flipped=True)
                angles.append(segment_angles(segs))
            else:
                angles.append(None)

            if total and len(angles) * 100 // total >= next_report:
                if on_progress:
                    on_progress(next_report)
                next_report += 10
    finally:
        landmarker.close()
        cap.release()

    if not angles:
        raise ClipError("That video has no readable frames.")
    if not any(angles):
        raise ClipError("No person found in that video. Try one dancer, full body.")
    return angles, fps
