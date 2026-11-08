"""Per-frame scoring: angle difference -> points, per-segment compare, overall."""

from .config import (
    BAND1_SCORE_LOW, BAND2_SCORE_LOW, BAND3_SCORE_LOW, COLOR_BAD, COLOR_GOOD,
    COLOR_OK, SCORE_BANDS, WORST_SHARE, WORST_WEIGHT,
)


def angle_diff(live, ref):
    """Signed difference live - ref, wrapped to -180..180 (so +179 vs -179 = 2)."""
    return (live - ref + 180) % 360 - 180


def score_diff(diff):
    """Turn an angle difference (degrees, sign ignored) into 0-100 points."""
    d = abs(diff)
    for start, end, score_high, score_low in SCORE_BANDS:
        if d <= end:
            t = (d - start) / (end - start)  # 0 at band start, 1 at band end
            return score_high + t * (score_low - score_high)
    return BAND3_SCORE_LOW  # past the last band


def color_for_score(score):
    if score >= BAND1_SCORE_LOW:
        return COLOR_GOOD
    if score >= BAND2_SCORE_LOW:
        return COLOR_OK
    return COLOR_BAD


def compare(live_angles, ref_angles):
    """Score every segment. Returns {name: dict or None}.

    Each dict has: live, ref (angles), diff (signed, wrapped), score (0-100).
    A segment is None if it isn't visible in the live feed or the reference
    (or if the reference has no person in the current frame).
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


def overall_score(results):
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
