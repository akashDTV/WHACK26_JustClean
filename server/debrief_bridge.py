"""
debrief_bridge.py - turn a real webcam run into the Claude debrief + coach voices.

session.py records the run in RunStats. When the dance ends it calls
    msg = await asyncio.to_thread(debrief_bridge.report, stats, coach_id, speed, mirror)
and sends msg to the page. That runs to_claude (Claude writes the words) and
web_report (puts it in the shape report.js renders, plus the ElevenLabs audio).

Only real measurements go in: the camera measures body-part ANGLES against the
reference, so the report has position scores only. Timing / sharpness / holds are
not measured here, so they are left out rather than made up.
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

MIN_SAMPLES = 5          # a body part needs this many readings in a section to count
FINDING_MIN_DEG = 10     # don't report differences smaller than this as a "fix"
MAX_FINDINGS = 5         # to_claude picks the top 3 of these


def _key(segment_name: str, mirror: bool) -> str:
    """'L forearm' -> 'left_forearm' (the dancer's own left/right)."""
    return engine.segment_label(segment_name, mirror).replace(" ", "_")


def build_inputs(stats, mirror: bool):
    """RunStats -> (PARAMS, COMPARISON) in the format to_claude expects."""
    sec_len = stats.section_seconds
    duration = stats.duration or (max(stats.sec_ticks) * sec_len if stats.sec_ticks else 0)

    # ---- sections ----
    sections, candidates = [], []
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
        sections.append(sec)

    # ---- findings: the biggest real angle differences, one per body part ----
    findings, used = [], set()
    for mean, k, n, v, conf in sorted(candidates, key=lambda c: -c[0]):
        if n in used:
            continue
        used.add(n)
        findings.append({
            "type": "position",
            "body_part": engine.segment_label(n, mirror),
            "section": k,
            "timestamp_s": round(v["worst_t"], 1),
            "measured_value": f"{mean:.0f} degree mean difference from reference",
            # how reliably this body part was seen in that section
            "confidence": round(min(1.0, conf * v["n"] / max(1, stats.sec_count.get(k, 1))), 2),
        })
        if len(findings) >= MAX_FINDINGS:
            break

    # ---- whole-run scores ----
    seg_scores = [stats.seg_sum[n] / stats.seg_count[n] for n in stats.seg_sum if stats.seg_count[n]]
    scores = {"overall": round(stats.score_sum / stats.count) if stats.count else None,
              "scale": "0-100"}
    if seg_scores:
        scores["position"] = round(sum(seg_scores) / len(seg_scores))

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
            "measured": "body-part angles only (no timing, sharpness or hold measurements)",
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
