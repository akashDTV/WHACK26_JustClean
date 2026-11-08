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
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from to_claude import (COMPONENTS, COMPONENT_MEANING, get_body_parts, pretty, debrief,
                       build_breakdown)
import base64


def _import_tts():
    """Import text_to_speak.py from wherever it lives in the repo (it needn't be next to this
    file). If it can't be found, the server still runs, just without the ElevenLabs voices."""
    try:
        import text_to_speak as tts
        return tts
    except ModuleNotFoundError:
        pass
    repo = Path(__file__).resolve().parent.parent
    skip = {".venv", "venv", "node_modules", ".git", "__pycache__", "site-packages"}
    for path in sorted(repo.rglob("text_to_speak.py")):
        if not skip.intersection(path.parts):
            sys.path.insert(0, str(path.parent))
            import text_to_speak as tts
            print(f"text_to_speak: using {path}", file=sys.stderr)
            return tts
    print("!! text_to_speak.py not found anywhere in the repo: coaches will use the "
          "browser voice.", file=sys.stderr)
    return None


_tts = _import_tts()
text_to_speak = _tts.text_to_speak if _tts else None
_generate_audio = _tts._generate_audio if _tts else None
PERSONALITIES = _tts.PERSONALITIES if _tts else {"priyaa": {}, "gordon": {}, "ada": {}, "bobby": {}}

COACHES = {k: v for k, v in json.loads(
    (Path(__file__).with_name("coaches.json")).read_text(encoding="utf-8")).items()
    if not k.startswith("_")}

# --------------------------------------------------------------------------
# Text to speech
# --------------------------------------------------------------------------

AUDIO_TO_BROWSER = True   # attach ElevenLabs mp3s to the messages; the page plays them
SPEAK = True              # used by the --voices / --speak tests (plays on this computer)
SPEAK_FALLBACK = True     # tests only: if ElevenLabs fails, use the Mac's built-in voice

# Which ElevenLabs voice each coach uses. Keys = coach ids in coaches.json,
# values = priyaa / gordon / ada / bobby (the voices in text_to_speak.py).
# You can also put  "voice": "gordon"  inside a coach in coaches.json instead.
VOICE_FOR = {
    "hype":      "priyaa",   # Priya, Hype coach
    "strict":    "gordon",   # Gordon, Strict teacher
    "technical": "ada",      # Ada, Technical analyst
    "gentle":    "bobby",    # Bobby, Gentle giant
}

_speech_lock = threading.Lock()   # one coach talks at a time, never on top of each other


def voice_for(coach_id):
    """coach id -> text_to_speak personality. Loud warning (not silence) if it isn't mapped."""
    cfg_voice = (COACHES.get(coach_id) or {}).get("voice")
    if not isinstance(cfg_voice, str):        # the page's "voice" is {rate, pitch}, not a name
        cfg_voice = None
    voice = str(VOICE_FOR.get(coach_id) or cfg_voice or coach_id).lower()
    if voice not in PERSONALITIES:
        print(f"!! No voice set for coach {coach_id!r}. Add it to VOICE_FOR in web_report.py "
              f"(one of: {', '.join(PERSONALITIES)}). Not speaking.", file=sys.stderr)
        return None
    return voice


def report_speech_text(feedback):
    """Exactly what report.js speaks for a single report (Report.captionParts joined)."""
    parts = [feedback.get("headline"), feedback.get("score_summary")]
    fixes = feedback.get("priority_fixes") or []
    if fixes:
        parts.append(f"{fixes[0].get('what_happened')} {fixes[0].get('drill')}")
    return " ".join(p for p in parts if p)


def panel_speech_text(feedback):
    """Exactly what report.js speaks for a coach on the panel (just the headline)."""
    return feedback.get("headline") or ""


def audio_for(coach_id, text):
    """ElevenLabs mp3 for `text` in this coach's voice, ready to put in a message.
    Returns None on any failure; the page then uses its own browser voice instead."""
    voice = voice_for(coach_id)
    if not (AUDIO_TO_BROWSER and _generate_audio and voice and text):
        return None
    try:
        mp3 = _generate_audio(text, PERSONALITIES[voice]["voice_id"])
    except Exception as exc:
        print(f"!! ElevenLabs failed for coach {coach_id!r} ({voice}): "
              f"{type(exc).__name__}: {exc}. Page will use the browser voice.", file=sys.stderr)
        return None
    print(f"audio ready for coach {coach_id!r} ({voice}, {len(mp3) // 1024} KB)", file=sys.stderr)
    return {"mime": "audio/mpeg", "voice": voice, "text": text,
            "b64": base64.b64encode(mp3).decode("ascii")}


def _speak_now(items):
    """items = [(coach_id, text)]. Runs in a background thread, one after another."""
    with _speech_lock:
        for coach_id, text in items:
            voice = voice_for(coach_id)
            if not (text_to_speak and voice and text):
                continue
            try:
                used = text_to_speak(text, voice, fallback_to_say=SPEAK_FALLBACK)
                print(f"spoke coach {coach_id!r} with voice {voice} ({used})", file=sys.stderr)
            except Exception as exc:     # never let speech break the report
                print(f"!! Speech failed for coach {coach_id!r}: {type(exc).__name__}: {exc}",
                      file=sys.stderr)


def speak_in_background(items):
    """Start speaking without making the web page wait for it."""
    if SPEAK and items:
        threading.Thread(target=_speak_now, args=(items,), daemon=True).start()

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


def report_message(params, comparison, coach_id, playback_rate=1.0, use_api=True, timeout=None,
                   speak=False):
    """One coach -> the 'report' message. Blocking (runs the Claude call, then ElevenLabs).
    feedback["audio"] holds the coach's voice as an mp3 for the page to play (or None).
    speak=True also plays it on this computer (testing only; the page normally does it)."""
    feedback = feedback_for(params, comparison, coach_id, use_api, timeout)
    feedback["audio"] = audio_for(coach_id, report_speech_text(feedback))
    if speak:
        speak_in_background([(coach_id, report_speech_text(feedback))])
    return {"type": "report", "coach_id": coach_id,
            "feedback": feedback,
            "details": build_details(params, comparison, playback_rate)}


def panel_message(params, comparison, playback_rate=1.0, use_api=True, timeout=None, speak=False):
    """All four coaches at once (Claude + ElevenLabs calls run in parallel) -> 'panel' message.
    Each coach's feedback["audio"] holds its headline as an mp3 (or None)."""
    ids = list(COACHES)

    def one(c):
        fb = feedback_for(params, comparison, c, use_api, timeout)
        fb["audio"] = audio_for(c, panel_speech_text(fb))
        return fb

    with ThreadPoolExecutor(max_workers=len(ids)) as pool:
        fbs = list(pool.map(one, ids))
    if speak:
        speak_in_background([(c, panel_speech_text(fb)) for c, fb in zip(ids, fbs)])
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
    ap.add_argument("--speak", action="store_true", help="also read the feedback aloud")
    ap.add_argument("--voices", action="store_true",
                    help="just say a test line in every coach's voice, then stop")
    a = ap.parse_args()
    if a.voices:      # python web_report.py --voices   -> check each coach really sounds different
        for cid in COACHES:
            print(f"{cid} -> {voice_for(cid)}")
        _speak_now([(cid, f"Hi, I'm the {cid} coach.") for cid in COACHES])
        sys.exit()
    m = importlib.import_module(a.module)
    msg = report_message(m.PARAMS, m.COMPARISON, a.coach, use_api=not a.no_api, speak=False)
    if msg["feedback"].get("audio"):      # don't print a wall of base64
        msg["feedback"]["audio"] = dict(msg["feedback"]["audio"], b64="<%d chars>" % len(msg["feedback"]["audio"]["b64"]))
    print(json.dumps(msg, indent=2))
    if a.speak:       # speak in the foreground here, so the script waits until it's finished
        _speak_now([(a.coach, report_speech_text(msg["feedback"]))])