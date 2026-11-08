"""
debrief_server.py - serve web/, play a practice dance, and send real Claude debriefs.

    pip install fastapi "uvicorn[standard]" anthropic python-dotenv python-multipart
    python debrief_server.py                 # uses test2.py as the run results
    python debrief_server.py test1 --no-api  # template debrief, no Claude call
    open http://localhost:8000

Clips live in data/clips/ (next to web/). Drop videos in there or upload them from the page.
Names you give clips are saved in data/clips/clips.json. Deleted clips are moved to
data/clips/_deleted/ (not destroyed), so a mistake can be undone by moving the file back.

Press Start: countdown, the reference video plays to the very end with made-up live scores
(there is no camera here), then the report for the chosen coach. Restart goes back to the
beginning. "Hear from another coach" and "Ask the panel" re-run Claude.

For the real server (person C): when the dance ends, call
    msg = await loop.run_in_executor(None, report_message, PARAMS, COMPARISON, coach_id, rate)
    await ws.send_json(msg)
Never call report_message directly inside async code: it blocks while Claude answers.
"""

try:                                   # read ANTHROPIC_API_KEY from a .env file if there is one
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import argparse
import asyncio
import importlib
import json
import os
import random
import re
import shutil
from pathlib import Path

import uvicorn
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from web_report import COACHES, panel_message, report_message

HERE = Path(__file__).resolve().parent
WEB_DIR = Path(os.environ.get("WEB_DIR", HERE.parent / "web"))
CLIPS_DIR = Path(os.environ.get("CLIPS_DIR", HERE.parent / "data" / "clips"))
CLIPS_JSON = CLIPS_DIR / "clips.json"
VIDEO_EXT = {".mp4", ".webm", ".mov", ".m4v"}
TIMEOUT = float(os.environ.get("DEBRIEF_TIMEOUT", "30"))   # spec says 4 s for the live demo
MAX_UPLOAD_S = 7 * 60          # the page checks this before uploading

app = FastAPI()
SOCKETS = set()
INPUTS = None      # module with PARAMS / COMPARISON, set in main()
USE_API = True


# --------------------------------------------------------------------------
# Clip library: files in data/clips/, display names in clips.json
# --------------------------------------------------------------------------

def _load_names():
    try:
        return json.loads(CLIPS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_names(names):
    CLIPS_JSON.write_text(json.dumps(names, indent=2), encoding="utf-8")


def clip_id(path):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", path.name)


def list_clips():
    names = _load_names()
    files = sorted(p for p in CLIPS_DIR.iterdir() if p.suffix.lower() in VIDEO_EXT)
    return [{"id": clip_id(p), "name": names.get(clip_id(p), p.stem.replace("_", " ")),
             "url": f"/clips/{p.name}", "ref_ok": True, "editable": True} for p in files]


def find_clip(cid):
    return next((p for p in CLIPS_DIR.iterdir()
                 if p.suffix.lower() in VIDEO_EXT and clip_id(p) == cid), None)


def rename_clip(cid, new_name):
    if find_clip(cid) and new_name.strip():
        names = _load_names()
        names[cid] = new_name.strip()[:60]
        _save_names(names)


def delete_clip(cid):
    p = find_clip(cid)
    if not p:
        return
    trash = CLIPS_DIR / "_deleted"
    trash.mkdir(exist_ok=True)
    shutil.move(str(p), trash / p.name)
    names = _load_names()
    names.pop(cid, None)
    _save_names(names)


async def broadcast(msg):
    for s in list(SOCKETS):
        try:
            await s.send_json(msg)
        except Exception:
            SOCKETS.discard(s)


# --------------------------------------------------------------------------
# One practice run (no camera: made-up live scores until the video ends)
# --------------------------------------------------------------------------

async def practice_run(ws):
    await ws.send_json({"type": "state", "state": "countdown"})
    for n in (3, 2, 1):
        await ws.send_json({"type": "countdown", "n": n})
        await asyncio.sleep(0.8)
    await ws.send_json({"type": "state", "state": "playing"})
    await ws.send_json({"type": "video_cmd", "cmd": "play", "t": 0})
    base, streak = 70.0, 0.0
    while True:                                    # runs until video_ended cancels it
        base = max(30, min(97, base + random.uniform(-3, 3.4)))
        score = round(base)
        band = "green" if score >= 80 else "amber" if score >= 60 else "red"
        streak = streak + 0.05 if band == "green" else 0 if band == "red" else streak
        await ws.send_json({"type": "score", "score": score, "band": band, "streak": streak,
                            "weakest_joint": "left_elbow", "missing_parts": [], "section": None})
        await asyncio.sleep(0.05)


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    SOCKETS.add(ws)
    loop = asyncio.get_running_loop()
    session = {"coach": next(iter(COACHES)), "rate": 1.0, "run": None}

    def stop_run():
        if session["run"]:
            session["run"].cancel()
            session["run"] = None

    def start_run():
        stop_run()
        session["run"] = asyncio.create_task(practice_run(ws))

    async def send_report():
        await ws.send_json({"type": "state", "state": "finished"})
        msg = await loop.run_in_executor(None, lambda: report_message(
            INPUTS.PARAMS, INPUTS.COMPARISON, session["coach"], session["rate"],
            use_api=USE_API, timeout=TIMEOUT))
        print(f"report for {session['coach']}: {msg['feedback']['source']}")
        await ws.send_json(msg)

    try:
        while True:
            m = await ws.receive_json()
            kind, name = m.get("type"), m.get("name")
            if kind == "video_ended":                 # the dance is over: report time
                if session["run"]:
                    stop_run()
                    await send_report()
                continue
            if kind != "command":
                continue                              # video_time: nothing to do here
            if name == "hello":
                await ws.send_json({"type": "clips", "clips": list_clips()})
                await ws.send_json({"type": "state", "state": "waiting"})
            elif name == "set_speed":
                session["rate"] = float(m.get("value") or 1.0)
            elif name == "choose_coach":
                if m.get("value") in COACHES:
                    session["coach"] = m["value"]
                if m.get("rerun_report"):
                    await send_report()
            elif name == "start":
                if m.get("coach") in COACHES:
                    session["coach"] = m["coach"]
                session["rate"] = float(m.get("speed") or session["rate"])
                start_run()
            elif name == "restart":
                start_run()
            elif name == "rename_clip":
                rename_clip(str(m.get("value")), str(m.get("new_name") or ""))
                await broadcast({"type": "clips", "clips": list_clips()})
            elif name == "delete_clip":
                delete_clip(str(m.get("value")))
                await broadcast({"type": "clips", "clips": list_clips()})
            elif name == "ask_panel":
                msg = await loop.run_in_executor(None, lambda: panel_message(
                    INPUTS.PARAMS, INPUTS.COMPARISON, session["rate"],
                    use_api=USE_API, timeout=TIMEOUT))
                await ws.send_json(msg)
            elif name in ("reset", "try_again"):
                stop_run()
                await ws.send_json({"type": "state", "state": "waiting"})
            elif name == "get_replay":
                await ws.send_json({"type": "replay", "ref_url": None, "clips": [], "score_log": []})
    except WebSocketDisconnect:
        pass
    finally:
        stop_run()
        SOCKETS.discard(ws)


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Demo only: saves the clip into data/clips/ and pretends to process it so the
    page's loading bar runs. The real server runs the reference-prep script here instead."""
    name = Path(file.filename or "clip.mp4").name
    dest = CLIPS_DIR / name
    n = 1
    while dest.exists():                              # don't overwrite a clip with the same name
        dest = CLIPS_DIR / f"{Path(name).stem}_{n}{Path(name).suffix}"
        n += 1
    with dest.open("wb") as f:
        while chunk := await file.read(1 << 20):
            f.write(chunk)

    async def fake_processing():
        for stage, pct in [("Converting video…", 20), ("Tracking the dancer…", 50),
                           ("Tracking the dancer…", 80), ("Checking quality…", 95)]:
            await asyncio.sleep(0.7)
            await broadcast({"type": "upload_progress", "stage": stage, "percent": pct})
        await asyncio.sleep(0.5)
        clip = next(c for c in list_clips() if c["id"] == clip_id(dest))
        await broadcast({"type": "upload_result", "ok": True, "tracked_pct": 95, "warnings": [],
                         "clip": clip})
    asyncio.create_task(fake_processing())
    return {"ok": True}


def main():
    global INPUTS, USE_API
    ap = argparse.ArgumentParser()
    ap.add_argument("module", nargs="?", default="test2", help="inputs file without .py")
    ap.add_argument("--no-api", action="store_true", help="template debrief only")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    INPUTS = importlib.import_module(a.module)
    USE_API = not a.no_api
    if not WEB_DIR.exists():
        raise SystemExit(f"web folder not found at {WEB_DIR} (set WEB_DIR=...)")
    CLIPS_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/clips", StaticFiles(directory=CLIPS_DIR), name="clips")
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")   # after /ws
    print(f"Serving {WEB_DIR} at http://localhost:{a.port}  (inputs: {a.module}, "
          f"{'Claude' if USE_API else 'template only'})")
    print(f"Clips folder: {CLIPS_DIR}  ({len(list_clips())} clip(s))")
    uvicorn.run(app, host="0.0.0.0", port=a.port)


if __name__ == "__main__":
    main()
