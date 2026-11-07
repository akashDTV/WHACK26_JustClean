#!/usr/bin/env python3
"""
to_claude.py - turn measured dance results into coaching feedback (one Claude call).

    to_claude(params, comparison, personality, out_dir="output")

Inputs (each can be a dict, a path to a JSON file, or a JSON string):
    params       the compact results JSON from the spec (section 11): scores, sections,
                 top_findings, data_quality
    comparison   reference-vs-user comparison data, normally the body_part_scores object
                 (spec 8.2). A dict containing a "body_part_scores" key also works.
    personality  coach sliders, e.g. {"kindness": 0.8, "technicality": 0.7,
                 "energy": 0.6, "directness": 0.5}, or a plain-text description

Outputs (the only three files written, into out_dir):
    overall_summary.txt        short summary of the run in the chosen personality's voice (3-4 sentences)
    priority_fixes.json        up to 3 priority fixes: title, subheading, description, fix
    detailed_breakdown.txt     plain-language breakdown by component, section and body part
                               (no scores), with a short note per section from Claude

Python owns all numbers. Claude only writes words. Every fix must map to a real entry in
top_findings (section, timestamp and body part are copied from that entry, never from
Claude), and any "NN/100" figure Claude writes must be a score that was supplied.
If the call fails or the reply is invalid, a template version is written instead.

Used from main.py:
    from params import PARAMS, COMPARISON, PERSONALITY
    from to_claude import to_claude
    to_claude(PARAMS, COMPARISON, PERSONALITY)

Setup:
    pip install anthropic
    export ANTHROPIC_API_KEY=...
"""

import json
import os
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

MODEL = os.environ.get("DEBRIEF_MODEL", "claude-sonnet-5-5")
API_TIMEOUT_S = float(os.environ.get("DEBRIEF_TIMEOUT", "30"))
MAX_TOKENS = 2200

MIN_CONFIDENCE = 0.6   # findings below this are not sent to Claude (spec: omit or qualify)
MAX_FIXES = 3

DEFAULT_PERSONALITY = {"kindness": 0.5, "technicality": 0.5, "energy": 0.5, "directness": 0.5}

# --------------------------------------------------------------------------
# Loading helpers
# --------------------------------------------------------------------------


def _load(x, what):
    """dict/list -> as is; path -> parsed JSON file; string -> parsed JSON string."""
    if x is None or isinstance(x, (dict, list)):
        return x
    p = Path(str(x))
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        return json.loads(str(x))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {what} from {x!r}: {exc}")


def load_personality(x):
    """Return {"sliders": {...0..1}, "description": str|None}."""
    sliders, description = dict(DEFAULT_PERSONALITY), None
    if x is None:
        return {"sliders": sliders, "description": description}
    if isinstance(x, str):
        p = Path(x)
        try:
            if p.exists():
                x = json.loads(p.read_text(encoding="utf-8"))
            else:
                x = json.loads(x)
        except (OSError, json.JSONDecodeError):
            return {"sliders": sliders, "description": x}      # free-text personality
    if isinstance(x, dict):
        for k in DEFAULT_PERSONALITY:
            if isinstance(x.get(k), (int, float)):
                sliders[k] = max(0.0, min(1.0, float(x[k])))
        description = x.get("description") or x.get("name")
    return {"sliders": sliders, "description": description}


def describe_personality(p):
    """Turn slider values into plain-English style instructions."""
    def level(v):
        return "low" if v < 0.35 else "high" if v > 0.65 else "mid"

    words = {
        "kindness": {"low": "blunt and matter-of-fact, no cushioning",
                     "mid": "fair and balanced",
                     "high": "very warm and encouraging, celebrates what went well"},
        "technicality": {"low": "plain everyday language, no jargon",
                         "mid": "a little technical vocabulary where it helps",
                         "high": "precise, uses joint and angle terminology"},
        "energy": {"low": "calm and measured",
                   "mid": "upbeat",
                   "high": "high-energy and enthusiastic"},
        "directness": {"low": "gentle, frames fixes as soft suggestions",
                       "mid": "clear but not harsh",
                       "high": "very direct, says plainly what to fix"},
    }
    parts = [f"{k}: {words[k][level(v)]}" for k, v in p["sliders"].items()]
    if p["description"]:
        parts.append(f"extra style note: {p['description']}")
    return "; ".join(parts)


# --------------------------------------------------------------------------
# Preparing the data for Claude
# --------------------------------------------------------------------------

COMPONENTS = ["position", "timing", "sharpness", "holds"]


def pretty(name):
    return (name or "").replace("_", " ")


def band_label(score):
    return "green" if score >= 80 else "amber" if score >= 60 else "red"


def get_body_parts(comparison):
    if not comparison:
        return {}
    if isinstance(comparison, dict) and "body_part_scores" in comparison:
        return comparison["body_part_scores"] or {}
    return comparison if isinstance(comparison, dict) else {}


def prepare(params, comparison):
    """Split findings into reliable / dropped, tag them with ids, build the payload."""
    findings = params.get("top_findings") or []
    reliable, dropped = [], []
    for i, f in enumerate(findings):
        item = dict(f, finding_id=i)
        (reliable if f.get("confidence", 1.0) >= MIN_CONFIDENCE else dropped).append(item)
    payload = {
        "task": "Create concise, actionable dance-coach feedback from measured results.",
        "scores": params.get("scores", {}),
        "sections": params.get("sections", []),
        "top_findings": reliable,
        "body_part_scores": get_body_parts(comparison),
        "data_quality": params.get("data_quality", {}),
    }
    return payload, reliable, dropped


def allowed_scores(payload):
    """Every score Python supplied; Claude may only quote these as NN/100."""
    out = set()
    for k, v in payload["scores"].items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out.add(round(v))
    for s in payload["sections"]:
        for k in ["overall"] + COMPONENTS:
            if isinstance(s.get(k), (int, float)):
                out.add(round(s[k]))
    for bp in payload["body_part_scores"].values():
        if isinstance(bp, dict) and isinstance(bp.get("score"), (int, float)):
            out.add(round(bp["score"]))
    return out


# --------------------------------------------------------------------------
# The Claude call
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a dance rehearsal coach. The supplied reference video is the standard.
Your job is to explain measured differences and give practical rehearsal advice.

STRICT RULES:
- Return one JSON object matching the schema below, and nothing else (no markdown fences).
- Never calculate, alter or invent scores. If you quote a score, write it exactly as supplied, as NN/100.
- Only discuss facts present in the input. Do not invent body parts, sections, timestamps or measurements.
- Do not claim to hear or analyse the music or identify beats. Say "later than the reference", never "missed the beat".
- Do not call a movement technically wrong; describe how it differs from this reference.
- Each priority fix must come from a supplied top_findings entry: give its finding_id. Choose at most 3, ordered by size of difference AND confidence.
- Give one specific, achievable drill per fix.
- Treat body_part_scores with score null as unavailable; do not guess them.
- If confidence is low or data is missing, say so in data_caveat and avoid unsupported claims. If there are no reliable findings, return an empty priority_fixes array.
- Keep everything concise enough for an on-screen results page and to be spoken aloud.
- Write overall_summary fully in the personality style given in the input (coach_personality.style_guide): let the tone, word choice and energy clearly reflect the sliders. Do NOT include any score or number in overall_summary (the scores are shown elsewhere); describe how the run went in words. The priority fixes use the plain-language style described under PRIORITY FIX STYLE below.

PRIORITY FIX STYLE (title, subheading, description, fix, and section notes):
- Write like a friendly, observant coach talking to the dancer, not like a report. Use "you" and "your", everyday words and short sentences.
- Lead with what it looked or felt like compared with the reference (e.g. "your left arm stayed a bit curled where the reference opens it out"), not with measurements.
- Avoid jargon and metric language such as "mean angle difference", "arrival offset", "degrees of error" or "confidence".
- You may include at most ONE measured figure per fix, only if it helps, phrased naturally (e.g. "about a quarter of a second late"). Never add figures that are not in the input.
- Say which direction to correct only if the input gives a direction (e.g. "more bent"); otherwise just say what differed.
- The fix is one concrete, doable drill with a cue the dancer can feel or picture (e.g. "think of reaching out to the side as the reference does"), in a warm, encouraging tone. Not a lecture.
- Titles are short action phrases a dancer would say, like "Open up your left arm" or "Land the move with the reference".

SCHEMA:
{
  "overall_summary": "3-4 sentences in the requested personality's voice: how the run went overall, the strongest area, and the biggest opportunity. Plain text, no lists, no scores or numbers.",
  "priority_fixes": [
    {
      "finding_id": <integer copied from a top_findings entry>,
      "title": "short, plain action phrase, e.g. 'Open up your left arm'",
      "subheading": "one plain line on where it happens, e.g. 'Section 2, about 6 seconds in'",
      "description": "1-3 conversational sentences on what it looked like compared to the reference",
      "fix": "one specific, friendly drill with a cue the dancer can picture or feel"
    }
  ],
  "section_notes": [
    {"section": <integer copied from a supplied sections entry>, "note": "2-3 plain, conversational sentences on how this section went and what it looked like compared to the reference; no scores or numbers"}
  ],
  "data_caveat": null
}
Give one section_notes entry for each supplied section, in order."""


def call_claude(payload, personality):
    import anthropic  # imported here so --no-api works without the package

    message = dict(payload)
    message["coach_personality"] = {
        "sliders_0_to_1": personality["sliders"],
        "style_guide": describe_personality(personality),
    }
    client = anthropic.Anthropic(timeout=API_TIMEOUT_S)  # reads ANTHROPIC_API_KEY
    resp = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": json.dumps(message)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{"):]
    return json.loads(text)


# --------------------------------------------------------------------------
# Template fallback (deterministic, built from the same numbers)
# --------------------------------------------------------------------------

DRILLS = {
    "position": "Slow section {s} right down and check your {bp} in a mirror until it matches the "
                "reference, then build the speed back up.",
    "timing": "Loop the transition in section {s} and try to land in the final position at the same "
              "moment as the reference.",
    "sharpness": "Loop section {s} and aim to finish the movement in the same time as the "
                 "reference, with a clean, decisive finish.",
    "holds": "Repeat section {s} and count the hold in your head so you stay in the pose as long "
             "as the reference does.",
}
TITLES = {
    "position": "Match your {bp}",
    "timing": "Land your {bp} with the reference",
    "sharpness": "Finish your {bp} movement faster",
    "holds": "Hold the pose for longer",
}


def plain_description(f):
    """Turn a measured finding into a conversational sentence (template fallback)."""
    bp = pretty(f.get("body_part", "this part"))
    mv = f.get("measured_value", "")
    direction = f.get("direction", "")
    m_dir = re.match(r"user \w+ (.+)$", direction)
    m_deg = re.search(r"(\d+(?:\.\d+)?)\s*degree", mv)
    m_ms = re.search(r"(\d+(?:\.\d+)?)\s*ms (later|longer|earlier)", mv)
    typ = f.get("type", "position")
    if typ == "position" and m_dir:
        extra = f", by roughly {round(float(m_deg.group(1)))} degrees" if m_deg else ""
        return f"Your {bp} was {m_dir.group(1)} than the reference{extra}."
    if typ == "position":
        return f"Your {bp} shape didn't quite match the reference here."
    if m_ms:
        secs = round(float(m_ms.group(1)) / 1000, 2)
        word = {"later": "after the reference",
                "longer": "longer than the reference",
                "earlier": "earlier than the reference"}[m_ms.group(2)]
        if typ == "timing":
            return f"Your {bp} reached the position about {secs} seconds {word}."
        return f"This took about {secs} seconds {word}."
    return f"Compared with the reference: {mv}." if mv else "This differed from the reference here."


def fix_from_finding(f):
    typ = f.get("type", "position")
    bp = pretty(f.get("body_part", "this area"))
    s = f.get("section", "?")
    t = f.get("timestamp_s")
    where = f"Section {s}" + (f", about {round(t)} seconds in" if isinstance(t, (int, float)) else "")
    return {
        "finding_id": f["finding_id"],
        "title": TITLES.get(typ, TITLES["position"]).format(bp=bp),
        "subheading": where,
        "description": plain_description(f),
        "fix": DRILLS.get(typ, DRILLS["position"]).format(s=s, bp=bp),
    }


def canned_overall(payload, personality):
    """Template summary (no scores) in a tone chosen by the kindness slider; used if Claude fails."""
    k = personality["sliders"]["kindness"]
    opener = ("Nice work getting through that!" if k > 0.65
              else "Here's where you landed." if k < 0.35 else "Good effort.")
    sc = payload["scores"]
    comps = {c: sc[c] for c in COMPONENTS if isinstance(sc.get(c), (int, float))}
    parts = [opener]
    if len(comps) >= 2:
        best, worst = max(comps, key=comps.get), min(comps, key=comps.get)
        parts.append(f"Your strongest area was {best} and the biggest opportunity is {worst}.")
    fixes = payload["top_findings"]
    if fixes:
        parts.append(f"Start with your {pretty(fixes[0].get('body_part'))} "
                     f"in section {fixes[0].get('section')}.")
    else:
        parts.append("Keep practising and run it again.")
    return " ".join(parts)


def canned_section_note(sec):
    """Plain template note for one section (used if Claude's note is missing)."""
    comps = {c: sec[c] for c in COMPONENTS if isinstance(sec.get(c), (int, float))}
    parts = []
    if len(comps) >= 2:
        parts.append(f"{min(comps, key=comps.get).capitalize()} was the weakest part of this section.")
    if sec.get("weakest_joint"):
        parts.append(f"Keep an eye on your {pretty(sec['weakest_joint'])}.")
    if isinstance(sec.get("confidence"), (int, float)) and sec["confidence"] < MIN_CONFIDENCE:
        parts.append("Tracking was less reliable here, so treat this with some caution.")
    return " ".join(parts) or "No standout issues were measured in this section."


def canned_response(payload, personality):
    return {
        "overall_summary": canned_overall(payload, personality),
        "section_notes": {sec.get("section"): canned_section_note(sec) for sec in payload["sections"]},
        "priority_fixes": [fix_from_finding(f) for f in payload["top_findings"][:MAX_FIXES]],
        "data_caveat": None,
    }


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

SCORE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:/|out of)\s*100")
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")   # won't split inside "6.4 s"


def text_ok(text, allowed):
    """A non-empty string whose NN/100 figures all match supplied scores."""
    if not isinstance(text, str) or not text.strip():
        return False
    return all(round(float(n)) in allowed for n in SCORE_RE.findall(text))


def mentions_score(sentence, allowed):
    """True if the sentence quotes a score (NN/100, 'scored 78', or any supplied score)."""
    if SCORE_RE.search(sentence):
        return True
    if re.search(r"\bscor\w*\W+(?:of\W+|at\W+|a\W+)?\d", sentence, re.I):
        return True
    for n in allowed:   # a bare supplied score, but not "250 ms", "6.4 s" or "18 degrees"
        if re.search(rf"(?<![\d.]){n}(?!\d)(?!\.\d)(?!\s*(?:s\b|ms\b|sec|degree|\u00b0))", sentence):
            return True
    return False


def strip_score_sentences(text, allowed):
    """Keep only the sentences that don't mention a score."""
    sentences = SENTENCE_SPLIT.split(text.strip())
    return " ".join(x for x in sentences if not mentions_score(x, allowed)).strip()


def validate(resp, payload, personality):
    """Keep what's valid, replace the rest with template text. Never raises."""
    fallback = canned_response(payload, personality)
    allowed = allowed_scores(payload)
    by_id = {f["finding_id"]: f for f in payload["top_findings"]}
    problems = []

    # priority fixes
    fixes, seen = [], set()
    for fx in (resp.get("priority_fixes") or []):
        fid = fx.get("finding_id")
        if fid not in by_id or fid in seen:
            problems.append(f"dropped fix with bad/duplicate finding_id {fid!r}")
            continue
        fields = [fx.get(k) for k in ("title", "subheading", "description", "fix")]
        if not all(text_ok(v, allowed) for v in fields):
            problems.append(f"dropped fix for finding {fid}: missing field or unsupplied score")
            continue
        seen.add(fid)
        fixes.append(fx)
    fixes = fixes[:MAX_FIXES]
    if not fixes and payload["top_findings"]:
        problems.append("no valid fixes from Claude; used template fixes")
        fixes = fallback["priority_fixes"]

    out = {
        "priority_fixes": fixes,
        "data_caveat": resp.get("data_caveat") if isinstance(resp.get("data_caveat"), str) else None,
    }
    raw = resp.get("overall_summary")
    cleaned = strip_score_sentences(raw, allowed) if isinstance(raw, str) else ""
    if isinstance(raw, str) and cleaned and cleaned != raw.strip():
        problems.append("removed sentence(s) that mentioned a score from overall_summary")
    if cleaned:
        out["overall_summary"] = cleaned
    else:
        problems.append("overall_summary missing or only mentioned scores; used template text")
        out["overall_summary"] = fallback["overall_summary"]

    # section notes: only for supplied sections, with any score sentences removed
    valid_ids = {sec.get("section") for sec in payload["sections"]}
    notes = {}
    for item in (resp.get("section_notes") or []):
        sid, text = item.get("section"), item.get("note")
        if sid not in valid_ids or sid in notes or not isinstance(text, str):
            problems.append(f"dropped section note for unknown/duplicate section {sid!r}")
            continue
        cleaned = strip_score_sentences(text, allowed)
        if cleaned:
            notes[sid] = cleaned
    for sid, text in fallback["section_notes"].items():
        notes.setdefault(sid, text)
    out["section_notes"] = notes
    return out, problems


# --------------------------------------------------------------------------
# Writing the three output files
# --------------------------------------------------------------------------


def fmt_score(v):
    return "unavailable" if v is None else f"{round(v)}/100"


def data_notes(params, dropped):
    """Caveats Python can state itself, whatever Claude said."""
    dq = params.get("data_quality", {})
    notes = []
    if isinstance(dq.get("tracked_frame_percentage"), (int, float)):
        notes.append(f"Tracked frames: {dq['tracked_frame_percentage']}%")
    if dq.get("excluded_sections"):
        notes.append("Excluded sections (not scored): " +
                     ", ".join(str(s) for s in dq["excluded_sections"]))
    if dropped:
        notes.append(f"{len(dropped)} finding(s) left out because tracking confidence was below "
                     f"{MIN_CONFIDENCE}.")
    return notes


def level_word(score):
    """Turn a 0-100 score into a plain word (None if unavailable)."""
    if not isinstance(score, (int, float)):
        return None
    return "Excellent" if score >= 90 else "Strong" if score >= 80 else \
        "Decent" if score >= 60 else "Needs work"


COMPONENT_MEANING = {
    "position": "How closely your body shapes matched the reference.",
    "timing": "Whether you reached each position at the same moment as the reference.",
    "sharpness": "How quickly and cleanly you completed each movement.",
    "holds": "Whether you stayed in each pose for as long as the reference.",
}


def clock(seconds):
    seconds = int(seconds or 0)
    return f"{seconds // 60}:{seconds % 60:02d}"


def shape_phrase(deg):
    d = abs(deg)
    how = ("very close to" if d < 8 else "a little off from" if d < 15
           else "noticeably off from" if d < 25 else "well away from")
    return f"Your body shapes were {how} the reference."


def timing_phrase(ms):
    d, side = abs(ms), ("late" if ms >= 0 else "early")
    if d < 100:
        return "You reached positions right on time with the reference."
    return f"You reached positions {'slightly' if d < 250 else 'noticeably'} {side}."


def length_phrase(ms, what):
    d = abs(ms)
    how = ("about the same length as" if d < 150 else "a bit different in length from"
           if d < 350 else "quite different in length from")
    return f"Your {what} were {how} the reference's."


def tracking_phrase(conf):
    return ("Tracking was clear here." if conf >= 0.85 else
            "Tracking was fairly clear here." if conf >= 0.6 else
            "Tracking was shaky here, so treat this with some caution.")


def coverage_phrase(pct):
    return ("Almost the whole routine was tracked clearly." if pct >= 90 else
            "Most of the routine was tracked clearly." if pct >= 75 else
            "Only part of the routine was tracked clearly, so take the results with some caution."
            if pct >= 50 else
            "Very little of the routine was tracked clearly, so these results are rough.")


def build_breakdown(params, payload, dropped, result, notes):
    """Qualitative breakdown: no scores, just plain words."""
    scores = params.get("scores", {})
    L = ["DETAILED BREAKDOWN", "=" * 40]
    if level_word(scores.get("overall")):
        L.append(f"Overall match to the reference: {level_word(scores['overall'])}")

    L += ["", "HOW IT WENT"]
    for c in COMPONENTS:
        if c in scores:
            w = level_word(scores[c])
            L.append(f"  {c.capitalize()}: {w}. {COMPONENT_MEANING[c]}" if w else
                     f"  {c.capitalize()}: couldn't be judged this time "
                     f"(not enough clear moments were detected).")

    if payload["sections"]:
        L += ["", "SECTION BY SECTION"]
    for sec in sorted(payload["sections"], key=lambda x: x.get("section", 0)):
        sid = sec.get("section")
        head = f"Section {sid}"
        if "start_s" in sec and "end_s" in sec:
            head += f" ({clock(sec['start_s'])} - {clock(sec['end_s'])})"
        if level_word(sec.get("overall")):
            head += f" - {level_word(sec['overall']).lower()} overall"
        L += ["", head]
        L.append("  " + "; ".join(f"{c} {(level_word(sec.get(c)) or 'not judged').lower()}"
                                   for c in COMPONENTS if c in sec))
        if sec.get("weakest_joint"):
            L.append(f"  Main joint to watch: {pretty(sec['weakest_joint'])}.")
        if isinstance(sec.get("mean_angle_error_deg"), (int, float)):
            L.append("  " + shape_phrase(sec["mean_angle_error_deg"]))
        if isinstance(sec.get("arrival_offset_ms"), (int, float)):
            L.append("  " + timing_phrase(sec["arrival_offset_ms"]))
        if isinstance(sec.get("movement_duration_difference_ms"), (int, float)):
            L.append("  " + length_phrase(sec["movement_duration_difference_ms"], "movements"))
        if isinstance(sec.get("hold_duration_difference_ms"), (int, float)):
            L.append("  " + length_phrase(sec["hold_duration_difference_ms"], "holds"))
        if isinstance(sec.get("confidence"), (int, float)):
            L.append("  " + tracking_phrase(sec["confidence"]))
        if notes.get(sid):
            L.append(f"  {notes[sid]}")

    parts = payload["body_part_scores"]
    if parts:
        scored = sorted(((k, v) for k, v in parts.items()
                         if isinstance(v, dict) and isinstance(v.get("score"), (int, float))),
                        key=lambda kv: kv[1]["score"])
        scored_names = {k for k, _ in scored}
        L += ["", "BODY PARTS (the ones needing most attention first)"]
        for k, v in scored:
            shaky = " Tracking was shaky, so this is less certain." \
                if isinstance(v.get("confidence"), (int, float)) and v["confidence"] < MIN_CONFIDENCE else ""
            L.append(f"  {pretty(k).capitalize()}: {level_word(v['score'])}.{shaky}")
        for k in parts:
            if k not in scored_names:
                L.append(f"  {pretty(k).capitalize()}: couldn't be judged (not enough clear tracking).")

    dq = params.get("data_quality", {})
    L += ["", "HOW RELIABLE THIS IS"]
    if isinstance(dq.get("tracked_frame_percentage"), (int, float)):
        L.append("  " + coverage_phrase(dq["tracked_frame_percentage"]))
    if dq.get("valid_sections"):
        L.append("  Sections included: " + ", ".join(str(x) for x in dq["valid_sections"]) + ".")
    if dq.get("excluded_sections"):
        L.append("  Sections left out (the dancer was out of frame or tracking failed): "
                 + ", ".join(str(x) for x in dq["excluded_sections"]) + ".")
    if dq.get("timing_events_detected") == 0:
        L.append("  No clear timing moments were found, so timing wasn't judged.")
    if dq.get("hold_events_detected") == 0:
        L.append("  No clear holds were found, so holds weren't judged.")
    if dropped:
        L.append("  Left out because tracking was unreliable: " + "; ".join(
            f"{pretty(f.get('body_part'))} in section {f.get('section')}" for f in dropped) + ".")
    if result.get("data_caveat"):
        L.append(f"  {result['data_caveat']}")
    return "\n".join(L) + "\n"


def write_outputs(out_dir, params, payload, dropped, result, personality, source):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scores = params.get("scores", {})
    overall = scores.get("overall")

    # 1) overall_summary.txt: just the personality-voiced summary from Claude, nothing else
    (out / "overall_summary.txt").write_text(result["overall_summary"] + "\n", encoding="utf-8")
    notes = data_notes(params, dropped)
    if result.get("data_caveat"):
        notes.append(result["data_caveat"])

    # 3) detailed_breakdown.txt: numbers from Python, short section notes from Claude
    (out / "detailed_breakdown.txt").write_text(
        build_breakdown(params, payload, dropped, result, result.get("section_notes", {})),
        encoding="utf-8")

    # 2) priority_fixes.json  (section / timestamp / body part come from the finding, not Claude)
    by_id = {f["finding_id"]: f for f in payload["top_findings"]}
    fixes = []
    for rank, fx in enumerate(result["priority_fixes"], start=1):
        f = by_id[fx["finding_id"]]
        fixes.append({
            "rank": rank,
            "section": f.get("section"),
            "timestamp_s": f.get("timestamp_s"),
            "body_part": f.get("body_part"),
            "title": fx["title"],
            "subheading": fx["subheading"],
            "description": fx["description"],
            "fix": fx["fix"],
        })
    (out / "priority_fixes.json").write_text(
        json.dumps({"overall_score": overall, "priority_fixes": fixes}, indent=2), encoding="utf-8")

    return {
        "overall_summary": str(out / "overall_summary.txt"),
        "priority_fixes": str(out / "priority_fixes.json"),
        "detailed_breakdown": str(out / "detailed_breakdown.txt"),
        "source": source,
        "data_notes": notes,
    }


# --------------------------------------------------------------------------
# The function you call
# --------------------------------------------------------------------------


def to_claude(params, comparison=None, personality=None, out_dir="output", use_api=True):
    """Make one Claude call and write the three output files. Returns their paths."""
    params = _load(params, "params")
    comparison = _load(comparison, "comparison")
    if not isinstance(params, dict) or "scores" not in params:
        raise ValueError("params must be the results JSON with a 'scores' object")
    persona = load_personality(personality)
    payload, reliable, dropped = prepare(params, comparison)

    result, source = None, "template fallback"
    if use_api:
        try:
            raw = call_claude(payload, persona)
            result, problems = validate(raw, payload, persona)
            source = f"Claude ({MODEL})"
            for p in problems:
                print(f"validator: {p}", file=sys.stderr)
        except Exception as exc:  # any failure -> template, so the demo never dies
            print(f"Claude call failed ({type(exc).__name__}: {exc}); using template.",
                  file=sys.stderr)
    if result is None:
        result = canned_response(payload, persona)
        result["data_caveat"] = None
    return write_outputs(out_dir, params, payload, dropped, result, persona, source)
