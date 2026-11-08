"""
debrief_bridge.py - turn a real webcam run into the Claude debrief + coach voices.

session.py records the run in RunStats. When the dance ends it calls
    msg = await asyncio.to_thread(debrief_bridge.report, stats, coach_id, speed, mirror)
and sends msg to the page. That runs to_claude (Claude writes the words) and
web_report (puts it in the shape report.js renders, plus the ElevenLabs audio).

Only real measurements go in. Position comes from the body-part angles vs the reference;
timing, sharpness and holds come from the team's scorer (scoring/run_scores.py, the same
RunRecorder the sandbox uses), so the page, the sandbox and Claude all agree.
If something can't be measured in a section (e.g. the reference never holds still there)
it is left out for that section rather than made up.
"""

from __future__ import annotations

import sys
from pathlib import Path

try:
    from . import pose_engine as engine
except ImportError:  # run as a plain script from the server folder
    import pose_engine as engine

# to_claude.py / web_report.py live in <repo>/to_claude_program (not a package)
_TC = Path(__file__).resolve().parent.parent / "to_claude_program"
if str(_TC) not in sys.path:
    sys.path.insert(0, str(_TC))

import web_report  # noqa: E402  (after the sys.path line above)

_REPO = Path(__file__).resolve().parent.parent          # so "import scoring" works
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
from scoring.run_scores import RunRecorder  # noqa: E402


def make_recorder(track, section_seconds):
    """The team's RunRecorder for one run, fed with the server's reference track."""
    ref = [(None, dict(zip(engine.SEGMENT_NAMES, row)) if row is not None else None)
           for row in track.frames]
    return RunRecorder(ref, track.fps, section_seconds)


def _events(stats):
    """Per-move results from RunRecorder: timing / sharpness / hold per matched move."""
    rec = getattr(stats, "recorder", None)
    if rec is None or not rec.has_data:
        return []
    try:
        return rec._measures(rec._events())
    except Exception as exc:                       # never let scoring break the report
        print("[debrief] run_scores failed:", exc, file=sys.stderr)
        return []


def _section_metrics(events, lo, hi):
    """(T, S, H) for one section, in the shape build_inputs uses, or None if not measurable."""
    sm = [m for m in events if lo <= m["t"] < hi]
    if not sm:
        return None, None, None
    c = RunRecorder._components(sm)

    def worst(key):
        vals = [(abs(m[key]), m) for m in sm if m.get(key) is not None]
        return max(vals, key=lambda x: x[0])[1] if vals else None

    T = S = H = None
    if c["timing"] is not None:
        w = worst("arrival_offset_ms")
        T = {"score": round(c["timing"]), "offset_ms": round(c["arrival_offset_ms"]),
             "body_part": w["segment"] if w else None}
    if c["sharpness"] is not None:
        w = worst("duration_diff_ms")
        S = {"score": round(c["sharpness"]),
             "movement_duration_difference_ms": round(c["duration_diff_ms"]),
             "body_part": w["segment"] if w else None}
    if c["holds"] is not None:
        held = [m for m in sm if m.get("hold") is not None]
        w = min(held, key=lambda m: m["hold"]) if held else None
        H = {"score": round(c["holds"]), "hold_duration_difference_ms": round(c["hold_diff_ms"]),
             "holds": len(held), "worst_t": w["t"] if w else lo}
    return T, S, H

MIN_SAMPLES = 5          # a body part needs this many readings in a section to count
FINDING_MIN_DEG = 10     # don't report differences smaller than this as a "fix"
MAX_FINDINGS = 6         # to_claude picks the top 3 of these
WEIGHTS = {"position": 75, "timing": 12.5, "sharpness": 6.25, "holds": 6.25}  # as shown on the page
TIMING_FINDING_MS = 150  # report timing only if at least this far off
SHARPNESS_FINDING = 70   # report sharpness if its score is below this (and you were slower)
HOLDS_FINDING = 70       # report holds if the holds score is below this


def _weighted(parts: dict):
    """Overall score from whichever components were measured, using the page's weights."""
    have = {k: v for k, v in parts.items() if isinstance(v, (int, float))}
    if not have:
        return None
    return round(sum(WEIGHTS[k] * v for k, v in have.items()) / sum(WEIGHTS[k] for k in have))


def _key(segment_name: str, mirror: bool) -> str:
    """'L forearm' -> 'left_forearm' (the dancer's own left/right)."""
    return engine.segment_label(segment_name, mirror).replace(" ", "_")


def build_inputs(stats, mirror: bool):
    """RunStats -> (PARAMS, COMPARISON) in the format to_claude expects."""
    sec_len = stats.section_seconds
    duration = stats.duration or (max(stats.sec_ticks) * sec_len if stats.sec_ticks else 0)

    # ---- sections ----
    sections, candidates, extra, hold_events = [], [], [], 0
    events = _events(stats)
    for k in sorted(stats.sec_ticks):
        ticks = stats.sec_ticks[k]
        seen = stats.sec_count.get(k, 0)
        segs = {n: v for n, v in stats.sec_seg.get(k, {}).items() if v["n"] >= MIN_SAMPLES}
        sec = {
            "section": k,
            "start_s": round((k - 1) * sec_len, 1),
            "end_s": round(min(k * sec_len, duration) if duration else k * sec_len, 1),
            "confidence": round(seen / ticks, 2) if ticks else 0.0,
        }
        if seen:
            sec["overall"] = round(stats.sec_sum[k] / seen)
        T, S, H = _section_metrics(events, (k - 1) * sec_len, k * sec_len)
        mid_t = round(((k - 1) * sec_len + sec["end_s"]) / 2, 1)
        if T:
            sec["timing"], sec["arrival_offset_ms"] = T["score"], T["offset_ms"]
            if abs(T["offset_ms"]) >= TIMING_FINDING_MS:
                ms = abs(T["offset_ms"])
                extra.append((ms / 400, {
                    "type": "timing",
                    "body_part": engine.segment_label(T["body_part"], mirror) if T["body_part"] else "whole body",
                    "section": k, "timestamp_s": mid_t,
                    "measured_value": f"{ms} ms {'later' if T['offset_ms'] > 0 else 'earlier'} than reference",
                    "confidence": sec["confidence"]}))
        if S:
            sec["sharpness"] = S["score"]
            sec["movement_duration_difference_ms"] = S["movement_duration_difference_ms"]
            d = S["movement_duration_difference_ms"]
            if S["score"] < SHARPNESS_FINDING and d > 0:
                mv = f"movement took {d} ms longer than reference"
                extra.append(((100 - S["score"]) / 60, {
                    "type": "sharpness",
                    "body_part": engine.segment_label(S["body_part"], mirror) if S["body_part"] else "whole body",
                    "section": k, "timestamp_s": mid_t, "measured_value": mv,
                    "confidence": sec["confidence"]}))
        if H:
            sec["holds"] = H["score"]
            sec["hold_duration_difference_ms"] = H["hold_duration_difference_ms"]
            hold_events += H["holds"]
            if H["score"] < HOLDS_FINDING and H["hold_duration_difference_ms"] < 0:
                extra.append(((100 - H["score"]) / 60, {
                    "type": "holds", "body_part": "whole body", "section": k,
                    "timestamp_s": round(H["worst_t"], 1),
                    "measured_value": f"hold ended {abs(H['hold_duration_difference_ms'])} ms earlier than reference",
                    "confidence": sec["confidence"]}))
        if segs:
            sec["position"] = round(sum(v["score"] / v["n"] for v in segs.values()) / len(segs))
            sec["mean_angle_error_deg"] = round(
                sum(v["absdiff"] / v["n"] for v in segs.values()) / len(segs), 1)
            worst = max(segs, key=lambda n: segs[n]["absdiff"] / segs[n]["n"])
            sec["weakest_joint"] = _key(worst, mirror)
            for n, v in segs.items():
                mean = v["absdiff"] / v["n"]
                if mean >= FINDING_MIN_DEG:
                    candidates.append((mean, k, n, v, sec["confidence"]))
        combined = _weighted({c: sec.get(c) for c in WEIGHTS})
        if combined is not None:
            sec["overall"] = combined
        sections.append(sec)

    # ---- findings: the biggest real angle differences, one per body part ----
    findings, used = [], set()
    ranked = []
    for mean, k, n, v, conf in sorted(candidates, key=lambda c: -c[0]):
        if n in used:
            continue
        used.add(n)
        ranked.append((mean / 25, {
            "type": "position",
            "body_part": engine.segment_label(n, mirror),
            "section": k,
            "timestamp_s": round(v["worst_t"], 1),
            "measured_value": f"{mean:.0f} degree mean difference from reference",
            # how reliably this body part was seen in that section
            "confidence": round(min(1.0, v["n"] / max(1, stats.sec_ticks.get(k, 1))), 2),
        }))
    # the biggest problem of EACH kind first (so lateness can't be buried under the position
    # errors it causes), then the rest by size; at most 2 position findings in total
    pool = sorted(ranked + extra, key=lambda x: -x[0])
    first = {}
    for sev, f in pool:
        first.setdefault(f["type"], (sev, f))
    picked = sorted(first.values(), key=lambda x: -x[0])
    for item in pool:
        if item not in picked and len(picked) < MAX_FINDINGS:
            if item[1]["type"] == "position" and sum(f["type"] == "position" for _, f in picked) >= 2:
                continue
            picked.append(item)
    findings = [f for _, f in picked]

    # ---- whole-run scores ----
    seg_scores = [stats.seg_sum[n] / stats.seg_count[n] for n in stats.seg_sum if stats.seg_count[n]]
    scores = {"overall": round(stats.score_sum / stats.count) if stats.count else None,
              "scale": "0-100"}
    if seg_scores:
        scores["position"] = round(sum(seg_scores) / len(seg_scores))
    for comp in ("timing", "sharpness", "holds"):
        vals = [(s[comp], stats.sec_count.get(s["section"], 1)) for s in sections if comp in s]
        if vals:
            scores[comp] = round(sum(v * w for v, w in vals) / sum(w for _, w in vals))
    rec = getattr(stats, "recorder", None)
    if rec is not None and rec.has_data:            # whole-run values straight from run_scores
        try:
            res = rec.finish()
            for comp in ("timing", "sharpness", "holds"):
                if res.get(comp) is not None:
                    scores[comp] = round(res[comp])
        except Exception as exc:
            print("[debrief] run_scores finish failed:", exc, file=sys.stderr)
    combined = _weighted({c: scores.get(c) for c in WEIGHTS})
    if combined is not None:
        scores["overall"] = combined

    total_ticks = sum(stats.sec_ticks.values())
    valid = [s["section"] for s in sections if s["confidence"] >= 0.5]
    params = {
        "scores": scores,
        "sections": sections,
        "top_findings": findings,
        "data_quality": {
            "tracked_frame_percentage": round(100 * stats.count / total_ticks) if total_ticks else 0,
            "valid_sections": valid,
            "excluded_sections": [s["section"] for s in sections if s["section"] not in valid],
            "timing_events_detected": sum(1 for s in sections if "timing" in s),
            "hold_events_detected": hold_events,
        },
    }

    comparison = {"body_part_scores": {
        _key(n, mirror): {
            "score": round(stats.seg_sum[n] / stats.seg_count[n]),
            "mean_angle_error_deg": round(stats.seg_absdiff[n] / stats.seg_count[n], 1),
            "confidence": round(stats.seg_count[n] / total_ticks, 2) if total_ticks else 0,
            "tracked_samples": stats.seg_count[n],
        } for n in stats.seg_sum if stats.seg_count[n]}}
    return params, comparison


def _coach(coach_id):
    return coach_id if coach_id in web_report.COACHES else next(iter(web_report.COACHES))


def report(stats, coach_id, speed: float, mirror: bool) -> dict:
    """Blocking (Claude + ElevenLabs): call with asyncio.to_thread."""
    params, comparison = build_inputs(stats, mirror)
    coach = _coach(coach_id)
    msg = web_report.report_message(params, comparison, coach, playback_rate=speed)
    print(f"[debrief] report for {coach}: {msg['feedback'].get('source')}, "
          f"audio: {'yes' if msg['feedback'].get('audio') else 'NO (browser voice)'}")
    return msg


def panel(stats, speed: float, mirror: bool) -> dict:
    """All four coaches. Blocking: call with asyncio.to_thread."""
    params, comparison = build_inputs(stats, mirror)
    return web_report.panel_message(params, comparison, playback_rate=speed)
