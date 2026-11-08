"""
web_report.py - turn the to_claude debrief into the WebSocket messages web/ expects.

The page (web/js/report.js) renders:
    {"type": "report", "coach_id": ..., "feedback": {...}, "details": {...}}
    {"type": "panel",  "coaches": [{"coach_id", "feedback"}, ...], "details": {...}}

    from web_report import report_message, panel_message
    msg = report_message(PARAMS, COMPARISON, "hype", playback_rate=1.0)
    await websocket.send_json(msg)

Python owns every number: scores, sections, timestamps and body parts come from PARAMS /
COMPARISON. Claude only supplies the words (summary, fix text, section notes).
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from to_claude import (COMPONENTS, COMPONENT_MEANING, get_body_parts, pretty, debrief,
                       build_breakdown)

COACHES = {k: v for k, v in json.loads(
    (Path(__file__).with_name("coaches.json")).read_text(encoding="utf-8")).items()
    if not k.startswith("_")}

SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'“])")


def status_for(score):
    """Status words the page's pills understand."""
    if not isinstance(score, (int, float)):
        return "unavailable"
    return "strong" if score >= 85 else "good" if score >= 75 else \
        "improving" if score >= 60 else "needs_work"


def split_headline(summary):
    """First sentence becomes the big caption; the rest is the summary paragraph."""
    sentences = SENTENCE_SPLIT.split((summary or "").strip())
    n = 1
    while n < len(sentences) - 1 and len(" ".join(sentences[:n]).split()) < 6:
        n += 1                                # don't let "Right." be the whole caption
    return " ".join(sentences[:n]), " ".join(sentences[n:])


def personality_for(coach_id):
    c = COACHES.get(coach_id) or next(iter(COACHES.values()))
    return {k: c[k] for k in ("kindness", "technicality", "energy", "directness", "description")}


def build_feedback(d):
    """d = to_claude.debrief(...) output -> the 'feedback' object report.js reads."""
    params, payload, result = d["params"], d["payload"], d["result"]
    scores = params.get("scores", {})
    by_id = {f["finding_id"]: f for f in payload["top_findings"]}
    headline, rest = split_headline(result["overall_summary"])

    components = [{"component": c, "score": scores.get(c), "status": status_for(scores.get(c)),
                   "summary": COMPONENT_MEANING[c].capitalize() + "."}
                  for c in COMPONENTS if c in scores]

    body_parts = [{"body_part": k, "score": v.get("score"), "status": status_for(v.get("score")),
                   "summary": ""}
                  for k, v in payload["body_part_scores"].items() if isinstance(v, dict)]

    fixes = []
    for rank, fx in enumerate(result["priority_fixes"], start=1):
        f = by_id[fx["finding_id"]]           # where/when/what always from Python
        fixes.append({
            "rank": rank,
            "title": fx["title"],
            "body_part": f.get("body_part"),
            "section": f.get("section"),
            "timestamp_s": f.get("timestamp_s"),
            "what_happened": fx["description"],
            "evidence": fx["subheading"],
            "drill": fx["fix"],
        })

    notes = result.get("section_notes") or {}
    return {
        "headline": headline,
        "score_summary": rest,
        "overall_summary": result["overall_summary"],
        "overall_score": scores.get("overall"),
        "score_scale": "0-100",
        "component_feedback": components,
        "body_part_feedback": body_parts,
        "strengths": [],
        "priority_fixes": fixes,
        "section_notes": [{"section": s, "note": n} for s, n in sorted(notes.items()) if n],
        "next_practice_plan": None,
        "data_caveat": result.get("data_caveat"),
        "breakdown_text": build_breakdown(params, payload, d["dropped"], result, notes),
        "source": d["source"],                # "Claude (...)" or "template fallback"
    }


def build_details(params, comparison, playback_rate=1.0):
    """The numbers for the 'Detailed breakdown' tables (straight from Python)."""
    return {
        "playback_rate": playback_rate,
        "scores": params.get("scores", {}),
        "sections": params.get("sections", []),
        "body_part_scores": get_body_parts(comparison),
        "data_quality": params.get("data_quality", {}),
    }


def feedback_for(params, comparison, coach_id, use_api=True, timeout=None):
    d = debrief(params, comparison, personality_for(coach_id), use_api=use_api, timeout=timeout)
    return build_feedback(d)


def report_message(params, comparison, coach_id, playback_rate=1.0, use_api=True, timeout=None):
    """One coach -> the 'report' message. Blocking (runs the Claude call)."""
    return {"type": "report", "coach_id": coach_id,
            "feedback": feedback_for(params, comparison, coach_id, use_api, timeout),
            "details": build_details(params, comparison, playback_rate)}


def panel_message(params, comparison, playback_rate=1.0, use_api=True, timeout=None):
    """All four coaches at once (calls run in parallel) -> the 'panel' message."""
    ids = list(COACHES)
    with ThreadPoolExecutor(max_workers=len(ids)) as pool:
        fbs = list(pool.map(lambda c: feedback_for(params, comparison, c, use_api, timeout), ids))
    return {"type": "panel",
            "coaches": [{"coach_id": c, "feedback": fb} for c, fb in zip(ids, fbs)],
            "details": build_details(params, comparison, playback_rate)}


if __name__ == "__main__":
    # python web_report.py test2 strict --no-api   -> prints the report message
    import argparse
    import importlib
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", default="test2")
    ap.add_argument("coach", nargs="?", default="hype")
    ap.add_argument("--no-api", action="store_true")
    a = ap.parse_args()
    m = importlib.import_module(a.module)
    print(json.dumps(report_message(m.PARAMS, m.COMPARISON, a.coach, use_api=not a.no_api), indent=2))
