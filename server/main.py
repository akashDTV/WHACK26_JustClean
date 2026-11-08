"""
main.py - Live Dance Coach server (FastAPI).

Run it either way:
    * press Run on this file in your editor (it starts the server and opens the
      browser), or
    * from the project folder:  uvicorn server.main:app

WHAT THIS FILE DOES (the real work is in pose_engine.py and session.py)
-----------------------------------------------------------------------
    /ws            websocket - every message between the page and Python
    /camera        live webcam picture with your skeleton drawn on it
                   (also at /camera.mjpg; the page's <img class="cam-feed">
                   elements show this)
    /upload        the page's "upload a clip" button posts a video here
    /clips/...     the reference videos + thumbnails (the page plays these)
    /              the website itself (the web folder)

FOLDERS
-------
    <project>/web/      index.html, app.js, ...
    <project>/clips/    reference videos (.mp4 / .webm / .mov). Drop files here, or
                        upload them in the page. Each one is analysed once and the
                        angles are cached next to it (clip.pose.json).
    <project>/server/   this file, session.py, pose_engine.py
"""

from __future__ import annotations

import asyncio
import threading
import traceback
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from .pose_engine import CameraWorker, ClipLibrary, ensure_model
    from .session import Client, ClipService, DanceSession, Hub
except ImportError:  # run as a plain script: python server/main.py
    from pose_engine import CameraWorker, ClipLibrary, ensure_model
    from session import Client, ClipService, DanceSession, Hub


# --------------------------------------------------
# SETTINGS
# --------------------------------------------------

HOST = "127.0.0.1"
PORT = 8000
OPEN_BROWSER = True            # open the page automatically when run as a script
MAX_UPLOAD_MB = 200


# --------------------------------------------------
# PATHS
# --------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
CLIPS_DIR = BASE_DIR / "clips"


# --------------------------------------------------
# SHARED OBJECTS (one webcam, one clips folder, all browsers)
# --------------------------------------------------

hub = Hub()
library = ClipLibrary(CLIPS_DIR)          # creates the clips folder if needed
clip_service = ClipService(library, hub)
camera = CameraWorker()


# --------------------------------------------------
# APPLICATION
# --------------------------------------------------

def _warm_up_model():
    """Download the pose model now so the first Start isn't slow."""
    try:
        ensure_model()
    except Exception as exc:
        print("[server] Could not download the pose model:", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[server] Clips folder: {CLIPS_DIR}")
    asyncio.get_running_loop().run_in_executor(None, _warm_up_model)
    clip_service.analyse_pending()         # analyse any clips dropped into the folder
    yield
    await asyncio.to_thread(camera.stop)


app = FastAPI(title="Live Dance Coach", lifespan=lifespan)


# --------------------------------------------------
# WEBSOCKET
# --------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):

    await websocket.accept()
    print("[WS] Browser connected")

    client = Client(websocket.send_json)
    hub.clients.add(client)
    session = DanceSession(client.send, camera, library, clip_service)

    try:
        # Initial state + clip list (the page also asks with a "hello" command).
        await client.send({"type": "state", "state": "waiting"})
        await clip_service.send_clips(client.send)

        while True:
            # Receive a message from JavaScript and let the session deal with it.
            msg = await websocket.receive_json()
            await session.handle(msg)

    except WebSocketDisconnect:
        print("[WS] Browser disconnected")

    except Exception:
        print("[WS] Error:")
        traceback.print_exc()

    finally:
        hub.clients.discard(client)
        await session.shutdown()           # stops the camera


# --------------------------------------------------
# LIVE CAMERA STREAM
# --------------------------------------------------

@app.get("/camera")        # the URL in index.html's data-src
@app.get("/camera.mjpg")   # same stream, with a file-like name
async def camera_stream():
    """Motion-JPEG stream of the webcam with the skeleton drawn on it.

    It only has pictures while a dance is running (the camera is off otherwise),
    and ends by itself when the camera stops.
    """

    async def frames():
        last = -1
        while camera.running:
            seq, jpeg = camera.latest()
            if jpeg is None or seq == last:
                await asyncio.sleep(0.02)
                continue
            last = seq
            yield (b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                   + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")

    return StreamingResponse(
        frames(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store"},
    )


# --------------------------------------------------
# UPLOAD A REFERENCE CLIP
# --------------------------------------------------

@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """Save the video, then analyse it in the background.

    Progress and the result reach the page over the websocket
    (upload_progress / upload_result), so this returns straight away.
    """
    dest = library.new_upload_path(file.filename or "")
    if dest.suffix.lower() not in ClipLibrary.EXTENSIONS:
        return JSONResponse({"ok": False, "error": "Please upload an MP4, WebM or MOV file."},
                            status_code=400)

    limit = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with dest.open("wb") as out:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                return JSONResponse({"ok": False, "error": f"File is over {MAX_UPLOAD_MB} MB."},
                                    status_code=413)
            out.write(chunk)

    print(f"[upload] Saved {dest.name} ({size / 1_000_000:.1f} MB)")
    clip_service.start_analysis(dest.stem, upload=True)
    return {"ok": True, "id": dest.stem}


# --------------------------------------------------
# STATIC FILES
# --------------------------------------------------

# Reference videos and thumbnails (must come before the "/" mount).
app.mount("/clips", StaticFiles(directory=str(CLIPS_DIR)), name="clips")

# The website. Keep this LAST so it doesn't intercept /ws, /upload etc.
app.mount(
    "/",
    StaticFiles(directory=str(WEB_DIR), html=True),
    name="web"
)


# --------------------------------------------------
# RUN FROM YOUR EDITOR
# --------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    url = f"http://{HOST}:{PORT}"
    print(f"[server] Live Dance Coach running at {url}  (Ctrl+C to stop)")
    if OPEN_BROWSER:
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=HOST, port=PORT)
