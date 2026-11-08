"""Run scores: POSITION, TIMING, SHARPNESS, HOLDS and the weighted OVERALL."""

import numpy as np

from .config import (
    EVENT_MATCH_WINDOW_S, EVENT_MAX_GAP_S, EVENT_MERGE_GAP_S, EVENT_MIN_AMPLITUDE,
    EVENT_MIN_MOVE_S, EVENT_MOVE_DPS, EVENT_SMOOTH_S, EVENT_STABLE_MIN_S,
    HOLD_TOL_S, MIN_EVENTS_PER_COMPONENT, SHARPNESS_TOL_S, TIMING_TOL_S,
    WEIGHT_HOLDS, WEIGHT_POSITION, WEIGHT_SHARPNESS, WEIGHT_TIMING,
)

# POSITION  - how closely your segment angles match the reference (the average of
#             the per-frame overall score above).
# TIMING    - do you ARRIVE at each pose at the same time as the reference?
# SHARPNESS - do you complete each movement as fast as the reference?
# HOLDS     - do you keep each pose for as long as the reference?
# All are 0-100. Timing / sharpness / holds are measured from "events" found in each
# segment's angle over time: a movement (angle changing fast enough), its arrival
# (it stops), and the hold (it stays still until the next movement). Your events are
# paired with the reference's in order, using the real video clock.
def _smooth(t, a, win):
    """Moving average of `a` over +-win/2 seconds (t sorted, no NaNs)."""
    lo = np.searchsorted(t, t - win / 2, "left")
    hi = np.searchsorted(t, t + win / 2, "right")
    cs = np.concatenate([[0.0], np.cumsum(a)])
    return (cs[hi] - cs[lo]) / (hi - lo)


def _valid_pieces(t, a):
    """Split a series into pieces with no missing values and no big time gaps.

    Angles are unwrapped so +179 -> -179 counts as a 2 degree move, not 358.
    """
    pieces, start = [], None
    for i in range(len(t)):
        if np.isnan(a[i]):
            if start is not None:
                pieces.append((start, i))
                start = None
        elif start is None:
            start = i
        elif t[i] - t[i - 1] > EVENT_MAX_GAP_S:
            pieces.append((start, i))
            start = i
    if start is not None:
        pieces.append((start, len(t)))
    for s, e in pieces:
        if e - s >= 4:
            yield t[s:e], np.degrees(np.unwrap(np.radians(a[s:e])))


def detect_events(t, a, smooth_s=None):
    """Find the movements in one angle-over-time series.

    Returns a list of (start_s, arrival_s, hold_s). hold_s is None if the pose was
    still being held when the data ran out (so that hold can't be measured).
    """
    events = []
    for tt, aa in _valid_pieces(np.asarray(t, float), np.asarray(a, float)):
        s = _smooth(tt, aa, EVENT_SMOOTH_S if smooth_s is None else smooth_s)
        vel = np.zeros(len(tt))
        dt = np.diff(tt)
        vel[1:] = np.abs(np.diff(s) / np.where(dt > 0, dt, np.inf))
        moving = vel > EVENT_MOVE_DPS

        runs, i = [], 0
        while i < len(moving):
            if moving[i]:
                j = i
                while j + 1 < len(moving) and moving[j + 1]:
                    j += 1
                runs.append([i, j])
                i = j + 1
            else:
                i += 1
        merged = []
        for r in runs:
            if merged and tt[r[0]] - tt[merged[-1][1]] < EVENT_MERGE_GAP_S:
                merged[-1][1] = r[1]
            else:
                merged.append(r)
        good = [(r0, r1) for r0, r1 in merged
                if tt[r1] - tt[r0] >= EVENT_MIN_MOVE_S
                and np.ptp(s[max(0, r0 - 1):r1 + 1]) >= EVENT_MIN_AMPLITUDE]

        for k, (r0, r1) in enumerate(good):
            start_t, arr_t = float(tt[max(0, r0 - 1)]), float(tt[r1])
            nxt = float(tt[max(0, good[k + 1][0] - 1)]) if k + 1 < len(good) else None
            still_until = nxt if nxt is not None else float(tt[-1])
            if still_until - arr_t < EVENT_STABLE_MIN_S:
                continue            # didn't stay still: not a reliable arrival
            events.append((start_t, arr_t, None if nxt is None else nxt - arr_t))
    return events


def _pair_events(ref_events, user_events):
    """Pair reference and user events in order, by arrival time (within the match window)."""
    pairs, j = [], 0
    for r in ref_events:
        while j < len(user_events) and user_events[j][1] < r[1] - EVENT_MATCH_WINDOW_S:
            j += 1
        if j < len(user_events) and abs(user_events[j][1] - r[1]) <= EVENT_MATCH_WINDOW_S:
            pairs.append((r, user_events[j]))
            j += 1
    return pairs


def _tol_score(diff_s, tol_s):
    """0 s apart -> 100 points, tol_s or more apart -> 0."""
    return max(0.0, min(100.0, 100.0 * (1.0 - diff_s / tol_s)))


def _mean(values):
    values = [v for v in values if v is not None]
    return float(np.mean(values)) if values else None


def _combine(position, timing, sharp, holds):
    """OVERALL = weighted mix of what could be measured. A component that couldn't be
    measured is left out and the others re-weighted - it never counts as 100."""
    comps = {"position": (position, WEIGHT_POSITION), "timing": (timing, WEIGHT_TIMING),
             "sharpness": (sharp, WEIGHT_SHARPNESS), "holds": (holds, WEIGHT_HOLDS)}
    have = {k: c for k, c in comps.items() if c[0] is not None}
    overall = (sum(v * w for v, w in have.values()) / sum(w for _, w in have.values())
               if have and position is not None else None)
    return overall, [k for k, (v, _) in comps.items() if v is None]


def _r(x, nd=1):
    return None if x is None else round(float(x), nd)


MIN_SMOOTH_SAMPLES = 5   # smooth over at least this many of YOUR samples (see _events)

# A body segment needs this many scored frames before it gets its own position score
MIN_SEGMENT_FRAMES = 10


class RunRecorder:
    """Collects the scored frames of one run and works out position / timing /
    sharpness / holds when the run ends - for the whole run, for EVERY BODY SEGMENT,
    and for every section of the dance.

    record() - call once per scored frame
    reset()  - call when the dance restarts from the beginning (throws the run away)
    finish() - returns {"overall", "position", "timing", "sharpness", "holds",
                        "segments": {name: {...}}, "sections": [{...}], ...}
    """

    def __init__(self, ref_data, ref_fps, section_seconds=4.0):
        self.ref_data = ref_data
        self.ref_fps = ref_fps
        self.section_seconds = float(section_seconds)
        self.reset()

    def reset(self):
        self.t = []        # video time of each scored frame
        self.overall = []  # that frame's overall (position) score
        self.live = []     # {segment: live angle or None}
        self.seg = []      # {segment: that frame's score or None}
        self.done = False

    def record(self, t, results, overall):
        self.t.append(float(t))
        self.overall.append(float(overall))
        self.live.append({n: (None if r is None else r["live"]) for n, r in results.items()})
        self.seg.append({n: (None if r is None else float(r["score"])) for n, r in results.items()})

    @property
    def has_data(self):
        return len(self.t) > 0

    def _events(self):
        """(segment, reference event, your event) for every pair that could be matched."""
        user_t = np.array(self.t)
        # Smooth you AND the reference over the same window, at least MIN_SMOOTH_SAMPLES of
        # your samples. At 30 fps (sandbox) that is under EVENT_SMOOTH_S, so nothing changes.
        # The web server samples ~10 times a second; there EVENT_SMOOTH_S covers one sample,
        # webcam jitter looks like movement and perfect runs got low timing / holds scores.
        step = float(np.median(np.diff(user_t))) if len(user_t) > 1 else 0.0
        win = max(EVENT_SMOOTH_S, MIN_SMOOTH_SAMPLES * step)
        names = sorted({n for p in self.live for n in p})
        out = []
        # Read the reference at exactly the moments YOU were sampled, so both sides are
        # measured the same way (otherwise 30 fps vs 10 per second biases arrivals and holds).
        idx = np.clip(np.round(user_t * self.ref_fps).astype(int), 0, max(0, len(self.ref_data) - 1))
        for name in names:
            ref_full = np.array([np.nan if ang is None or ang.get(name) is None else ang[name]
                                 for _, ang in self.ref_data], float)
            ref_a = ref_full[idx] if len(ref_full) else np.full(len(user_t), np.nan)
            user_a = np.array([np.nan if p.get(name) is None else p[name]
                               for p in self.live], float)
            for r, u in _pair_events(detect_events(user_t, ref_a, win), detect_events(user_t, user_a, win)):
                out.append((name, r, u))
        return out

    @staticmethod
    def _measures(pairs):
        """Per-event numbers. r / u = (start_s, arrival_s, hold_s)."""
        out = []
        for name, r, u in pairs:
            has_hold = r[2] is not None and u[2] is not None
            out.append({
                "segment": name, "t": r[1],               # which section it belongs to
                "timing": _tol_score(abs(u[1] - r[1]), TIMING_TOL_S),
                "sharpness": _tol_score(abs((u[1] - u[0]) - (r[1] - r[0])), SHARPNESS_TOL_S),
                "hold": _tol_score(abs(u[2] - r[2]), HOLD_TOL_S) if has_hold else None,
                "arrival_offset_ms": round((u[1] - r[1]) * 1000),                 # + = later
                "duration_diff_ms": round(((u[1] - u[0]) - (r[1] - r[0])) * 1000),  # + = slower
                "hold_diff_ms": round((u[2] - r[2]) * 1000) if has_hold else None,  # + = longer
            })
        return out

    @staticmethod
    def _components(ms):
        """timing / sharpness / holds (+ average ms differences) from a list of events."""
        n = MIN_EVENTS_PER_COMPONENT
        tl = [m["timing"] for m in ms]
        sl = [m["sharpness"] for m in ms]
        hl = [m["hold"] for m in ms if m["hold"] is not None]
        return {
            "timing": _mean(tl) if len(tl) >= n else None,
            "sharpness": _mean(sl) if len(sl) >= n else None,
            "holds": _mean(hl) if len(hl) >= n else None,
            "arrival_offset_ms": _mean([m["arrival_offset_ms"] for m in ms]) if len(tl) >= n else None,
            "duration_diff_ms": _mean([m["duration_diff_ms"] for m in ms]) if len(sl) >= n else None,
            "hold_diff_ms": _mean([m["hold_diff_ms"] for m in ms if m["hold_diff_ms"] is not None])
                            if len(hl) >= n else None,
            "events_matched": len(ms),
        }

    def finish(self):
        pairs = self._events()
        ms = self._measures(pairs)
        position = _mean(self.overall)
        run = self._components(ms)
        overall, unavailable = _combine(position, run["timing"], run["sharpness"], run["holds"])
        total = len(self.t)

        # ---- every body segment, whole run -------------------------------------------
        names = sorted({n for p in self.seg for n in p})
        segments = {}
        for n in names:
            scores = [p[n] for p in self.seg if p.get(n) is not None]
            c = self._components([m for m in ms if m["segment"] == n])
            segments[n] = {
                "position": _r(_mean(scores)) if len(scores) >= MIN_SEGMENT_FRAMES else None,
                "timing": _r(c["timing"]), "sharpness": _r(c["sharpness"]), "holds": _r(c["holds"]),
                "arrival_offset_ms": _r(c["arrival_offset_ms"], 0),
                "duration_diff_ms": _r(c["duration_diff_ms"], 0),
                "hold_diff_ms": _r(c["hold_diff_ms"], 0),
                "events_matched": c["events_matched"],
                "frames_scored": len(scores),
                "confidence": _r(len(scores) / total, 2) if total else 0.0,
                "by_section": [],
            }

        # ---- every section of the dance ------------------------------------------------
        sections = []
        if total:
            S = self.section_seconds
            for k in range(int(self.t[-1] // S) + 1):
                lo, hi = k * S, (k + 1) * S
                idx = [i for i, t in enumerate(self.t) if lo <= t < hi]
                if not idx:
                    continue
                sm = [m for m in ms if lo <= m["t"] < hi]
                c = self._components(sm)
                pos = _mean([self.overall[i] for i in idx])
                ov, un = _combine(pos, c["timing"], c["sharpness"], c["holds"])
                sec = {"section": k + 1, "start_s": lo, "end_s": hi, "frames_scored": len(idx),
                       "overall": _r(ov), "position": _r(pos), "timing": _r(c["timing"]),
                       "sharpness": _r(c["sharpness"]), "holds": _r(c["holds"]),
                       "unavailable": un, "segments": {}}
                for n in names:
                    sc = [self.seg[i][n] for i in idx if self.seg[i].get(n) is not None]
                    cc = self._components([m for m in sm if m["segment"] == n])
                    blk = {"position": _r(_mean(sc)) if len(sc) >= 3 else None,
                           "timing": _r(cc["timing"]), "sharpness": _r(cc["sharpness"]),
                           "holds": _r(cc["holds"])}
                    sec["segments"][n] = blk
                    segments[n]["by_section"].append({"section": k + 1, **blk})
                sections.append(sec)

        return {
            "overall": overall, "position": position, "timing": run["timing"],
            "sharpness": run["sharpness"], "holds": run["holds"],
            "unavailable": unavailable,
            "matched_events": len(pairs), "scored_frames": total,
            "segments": segments,       # every body part: position / timing / sharpness / holds
            "sections": sections,       # every section, with the same four scores per body part
            # per-event detail: how much later / slower / longer you were (ms)
            "events": [{"segment": m["segment"], "arrival_offset_ms": m["arrival_offset_ms"],
                        "duration_diff_ms": m["duration_diff_ms"], "hold_diff_ms": m["hold_diff_ms"]}
                       for m in ms],
        }


def save_run_scores(res, folder):
    """Write one run's scores to <folder>/run_<time>.json. Returns the path."""
    import json
    import os
    import time
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("run_%Y%m%d_%H%M%S.json"))
    with open(path, "w") as f:
        json.dump(res, f, indent=1)
    return path


def finish_run(recorder, complete=True, callback=None, save_dir=None):
    """Work out and print the run scores (once per run). Returns the result dict.

    callback - optional function called with the result dict when the run is scored
    save_dir - optional folder; the full scores are also saved there as JSON
    """
    if recorder is None or recorder.done or not recorder.has_data:
        return None
    if not complete and recorder.t[-1] - recorder.t[0] < 2.0:
        return None             # a few frames from a stopped run isn't worth reporting
    recorder.done = True
    res = recorder.finish()
    f = lambda v: "unavailable" if v is None else f"{v:.0f}"
    print("\n=== RUN SCORES (out of 100) ===")
    print(f"OVERALL {f(res['overall'])} | POSITION {f(res['position'])} | "
          f"TIMING {f(res['timing'])} | SHARPNESS {f(res['sharpness'])} | "
          f"HOLDS {f(res['holds'])}")
    if res["unavailable"]:
        print("Not measurable this run (left out of OVERALL):", ", ".join(res["unavailable"]))
    if not complete:
        print("(run was stopped before the video finished)")
    if save_dir:
        print("Saved:", save_run_scores(res, save_dir))
    if callback is not None:
        callback(res)
    return res