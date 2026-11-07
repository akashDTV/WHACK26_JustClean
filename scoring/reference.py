"""Look up the reference angles that match the browser's video time."""

from __future__ import annotations

import json

from config import JOINTS, JOINT_WEIGHTS, WINDOW_S


def load_reference(path) -> dict:
    payload = json.loads(path.read_text())
    frames = payload.get("frames") or []
    duration = float(payload.get("duration") or (frames[-1]["t"] if frames else 0.0))
    return {
        "clip": payload.get("clip") or path.stem,
        "duration": duration,
        "tracked_ratio": float(payload.get("tracked_ratio") or 0.0),
        "second_person": bool(payload.get("second_person")),
        "frames": frames,
    }


def mirror_angles(angles: dict) -> dict:
    """Swap left and right so a mirrored camera matches the reference clip."""
    swapped = {}
    for name, value in angles.items():
        if name.startswith("left_"):
            swapped["right_" + name[5:]] = value
        elif name.startswith("right_"):
            swapped["left_" + name[6:]] = value
        else:
            swapped[name] = value
    return swapped


def frames_in_window(frames: list, video_t: float, window_s: float = WINDOW_S) -> list:
    start = video_t - window_s
    chosen = [frame for frame in frames if start <= frame["t"] <= video_t + 1e-3]
    if chosen or not frames:
        return chosen
    earlier = [frame for frame in frames if frame["t"] <= video_t]
    return earlier[-1:] if earlier else frames[:1]


def comparable_joints(user: dict, reference: dict) -> list:
    """Joints visible on both the dancer and the reference."""
    names = []
    for name in JOINTS:
        if user.get(name) is None or reference.get(name) is None:
            continue
        names.append(name)
    return names


def frame_error(user: dict, reference: dict, weights: dict | None = None):
    """Weighted average error in degrees, plus the error of each joint used."""
    weights = weights or JOINT_WEIGHTS
    per = {}
    total = 0.0
    weight_sum = 0.0
    for name in comparable_joints(user, reference):
        err = abs(float(user[name]) - float(reference[name]))
        weight = float(weights.get(name, 1.0))
        per[name] = err
        total += err * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None, {}
    return total / weight_sum, per


def best_match(user: dict, frames: list, weights: dict | None = None):
    """Smallest weighted error against any reference frame in the window.

    A dancer who is a fraction of a second late still hits the pose they are copying.
    """
    best = None
    best_per = {}
    for frame in frames:
        err, per = frame_error(user, frame.get("angles") or {}, weights)
        if err is None:
            continue
        if best is None or err < best:
            best = err
            best_per = per
    return best, best_per
