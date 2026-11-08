"""
session.py - the Live Dance Coach state machine.

This is the glue between the website (app.js) and pose_engine.py. It speaks the
website's message protocol (the same names app.js uses) and runs one dance:

    waiting -> loading -> calibrating -> countdown -> playing <-> paused -> finished
                 (any step can end in "error")

WHO DOES WHAT
-------------
* The BROWSER plays the reference video and tells us where it is (`video_time`,
  about 10 times a second) and when it ends (`video_ended`).
* The CAMERA WORKER (pose_engine.py) runs in a background thread: for every
  webcam frame it finds your pose, compares your 11 segment angles with the
  reference angles at the current video time, and keeps the latest result.
* This file reads that result 10 times a second, smooths it, and sends the
  website what it needs: `calibration`, `countdown`, `score`, `video_cmd`,
  `state`, `banner`, `report`.

MESSAGES WE SEND (Python -> page)
---------------------------------
    clips            {clips: [{id, name, url, thumb, duration_s, ref_ok, warning}]}
    state            {state: waiting|loading|calibrating|countdown|playing|paused|
                              finished|error, reason?}
    calibration      {ok, progress 0..1, reasons: [..]}
    countdown        {n}
    video_cmd        {cmd: play|pause|seek, t?}
    score            {score 0-100, band: green|amber|red|none, streak (seconds),
                      section, section_count, missing_parts: [..], t, segments}
    banner           {text}
    report           {coach_id, overall, feedback, segments, sections, ...}
    replay           {ref_url, clips: [], score_log}
    upload_progress  {percent, stage}
    upload_result    {ok, clip, warnings, tracked_pct}

MESSAGES WE RECEIVE (page -> Python)
------------------------------------
    command {name: hello | choose_clip | choose_coach | set_speed | mirror |
                   auto_pause | start | reset | try_again | get_replay | ask_panel,
             value?, clip?, speed?, coach?}
    video_time {t, playing, rate}
    video_ended
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
import traceback
from typing import Optional

try:
    from . import pose_engine as engine
    from . import debrief_bridge
except ImportError:  # run as a plain script from the server folder
    import pose_engine as engine
    import debrief_bridge

# =============================================================================
# CONFIG
# =============================================================================
TICK_SECONDS = 0.1            # how often we read the camera result and message the page
COUNTDOWN_SECONDS = 3         # "3, 2, 1" before the reference starts
SCORE_SMOOTHING = 0.35        # 0-1: higher = score reacts faster, lower = steadier
SECTION_SECONDS = 10          # the routine is split into sections this long ("Section 2 of 4")
AUTO_PAUSE_AFTER = 1.5        # seconds out of view before the reference pauses (if auto-pause is on)
RESUME_AFTER = 0.7            # seconds back in view before it resumes
CAMERA_STALE_SECONDS = 1.0    # a camera result older than this counts as "not seen"


# =============================================================================
# CLIENTS
# =============================================================================
class Client:
    """One connected browser. Serialises sends so two tasks can't interleave."""

    def __init__(self, send_json):
        self._send_json = send_json
        self._lock = asyncio.Lock()

    async def send(self, msg: dict):
        async with self._lock:
            await self._send_json(msg)


class Hub:
    """All connected browsers (used to broadcast upload progress etc.)."""

    def __init__(self):
        self.clients: set = set()

    async def broadcast(self, msg: dict):
        for client in list(self.clients):
            try:
                await client.send(msg)
            except Exception:
                self.clients.discard(client)


# =============================================================================
# CLIP SERVICE - listing, analysing and uploading reference clips
# =============================================================================
class ClipService:
    def __init__(self, library: engine.ClipLibrary, hub: Hub):
        self.library = library
        self.hub = hub
        self._busy: set = set()      # clips being analysed right now
        self._failed: dict = {}      # clip id -> why analysis failed
        self._tasks: set = set()
        self._lock: Optional[asyncio.Lock] = None   # created lazily inside the loop

    def clips_message(self) -> dict:
        clips = self.library.scan()
        for clip in clips:
            if clip["id"] in self._failed:
                clip["ref_ok"] = False
                clip["warning"] = f"Couldn't analyse this clip: {self._failed[clip['id']]}"
        return {"type": "clips", "clips": clips}

    async def send_clips(self, send):
        await send(self.clips_message())
        self.analyse_pending()

    def analyse_pending(self):
        """Start analysing every clip that has no cached angles yet."""
        for clip_id in self.library.pending():
            if clip_id not in self._failed:
                self.start_analysis(clip_id)

    def start_analysis(self, clip_id: str, upload: bool = False):
        if clip_id in self._busy:
            return
        self._busy.add(clip_id)
        task = asyncio.get_running_loop().create_task(self._analyse(clip_id, upload))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _analyse(self, clip_id: str, upload: bool):
        loop = asyncio.get_running_loop()
        if self._lock is None:
            self._lock = asyncio.Lock()

        def progress(pct, stage):  # called from the analysis thread
            if upload:
                asyncio.run_coroutine_threadsafe(self.hub.broadcast(
                    {"type": "upload_progress", "percent": pct, "stage": stage}), loop)

        info, error = None, None
        try:
            async with self._lock:   # one clip at a time
                print(f"[clips] Analysing {clip_id} ...")
                info = await asyncio.to_thread(self.library.analyse, clip_id, progress)
                print(f"[clips] {clip_id}: done")
        except Exception as exc:
            error = str(exc) or exc.__class__.__name__
            print(f"[clips] {clip_id}: failed - {error}")
            traceback.print_exc()
        finally:
            self._busy.discard(clip_id)

        if error:
            if upload:
                self.library.delete(clip_id)    # a file we can't use isn't worth keeping
            else:
                self._failed[clip_id] = error

        if upload:
            ok = bool(info and info["ref_ok"])
            warnings = [error] if error else ([info["warning"]] if info and info["warning"] else [])
            await self.hub.broadcast({
                "type": "upload_result", "ok": ok, "warnings": warnings,
                "tracked_pct": self.library.tracked_pct(clip_id) if info else None,
                "clip": info if ok else None,
            })
        await self.hub.broadcast(self.clips_message())


# =============================================================================
# RUN STATISTICS (for the end-of-dance report)
# =============================================================================
class RunStats:
    def __init__(self, section_seconds: float):
        self.section_seconds = section_seconds
        self.score_sum = 0.0
        self.count = 0
        self.seg_sum: dict = {}
        self.seg_count: dict = {}
        self.band_time = {"green": 0.0, "amber": 0.0, "red": 0.0}
        self.sec_sum: dict = {}
        self.sec_count: dict = {}
        self.best_streak = 0.0
        self.log: list = []          # one entry per second: {t, score, band}
        self._next_log_t = 0.0
        # extra detail for the Claude debrief (debrief_bridge.py)
        self.duration = 0.0          # length of the reference clip, seconds
        self.sec_ticks: dict = {}    # section -> camera checks (seen or not)
        self.sec_seg: dict = {}      # section -> segment -> {n, score, absdiff, worst, worst_t}
        self.seg_absdiff: dict = {}  # segment -> sum of |angle difference|

    def tick(self, t: float):
        """Count every camera check while playing (seen or not), for tracking quality."""
        section = int(t // self.section_seconds) + 1
        self.sec_ticks[section] = self.sec_ticks.get(section, 0) + 1

    def add(self, t: float, score: float, band: str, results: Optional[dict],
            dt: float, streak: float):
        self.score_sum += score
        self.count += 1
        section = int(t // self.section_seconds) + 1
        per_seg = self.sec_seg.setdefault(section, {})
        for name, r in (results or {}).items():
            if r is not None:
                self.seg_sum[name] = self.seg_sum.get(name, 0.0) + r["score"]
                self.seg_count[name] = self.seg_count.get(name, 0) + 1
                d = abs(r["diff"])
                self.seg_absdiff[name] = self.seg_absdiff.get(name, 0.0) + d
                v = per_seg.setdefault(name, {"n": 0, "score": 0.0, "absdiff": 0.0,
                                              "worst": -1.0, "worst_t": t})
                v["n"] += 1
                v["score"] += r["score"]
                v["absdiff"] += d
                if d > v["worst"]:
                    v["worst"], v["worst_t"] = d, t
        if band in self.band_time:
            self.band_time[band] += dt
        self.sec_sum[section] = self.sec_sum.get(section, 0.0) + score
        self.sec_count[section] = self.sec_count.get(section, 0) + 1
        self.best_streak = max(self.best_streak, streak)
        if t >= self._next_log_t:
            self.log.append({"t": int(t), "score": round(score), "band": band})
            self._next_log_t = int(t) + 1

    def report(self, coach_id, clip_id, mirror: bool) -> dict:
        overall = self.score_sum / self.count if self.count else 0.0
        segments = sorted(
            ({"name": n, "label": engine.segment_label(n, mirror),
              "avg": round(self.seg_sum[n] / self.seg_count[n])} for n in self.seg_sum),
            key=lambda s: s["avg"])
        total_time = sum(self.band_time.values()) or 1.0
        return {
            "type": "report",
            "coach_id": coach_id,
            "clip": clip_id,
            "overall": round(overall),
            "best_streak_s": round(self.best_streak, 1),
            "time_in_band": {b: round(100 * s / total_time) for b, s in self.band_time.items()},
            "segments": segments,           # weakest first
            "sections": [{"section": k, "avg": round(self.sec_sum[k] / self.sec_count[k])}
                         for k in sorted(self.sec_sum)],
            "score_log": self.log,
            "feedback": self._feedback(overall, segments),
        }

    @staticmethod
    def _feedback(overall: float, segments: list) -> dict:
        if not segments:
            summary = ("I couldn't see enough of you to score this one - check the "
                       "camera framing and try again.")
        elif overall >= 80:
            summary = "Superb - you tracked the reference very closely."
        elif overall >= 60:
            summary = "Nice work - you matched most of the routine."
        elif overall >= 40:
            summary = "A decent first go - there's room to tighten up the moves."
        else:
            summary = "Tough one - try a slower speed first and build up."
        return {
            "summary": summary,
            "strengths": [f"Your {s['label']} was spot on (average {s['avg']})"
                          for s in reversed(segments[-2:]) if s["avg"] >= 60],
            "improvements": [f"Work on your {s['label']} (average {s['avg']})"
                             for s in segments[:2] if s["avg"] < 70],
        }


# =============================================================================
# THE DANCE SESSION
# =============================================================================
class DanceSession:
    """One browser's dance: handles its messages and runs the state machine."""

    def __init__(self, send, camera: engine.CameraWorker,
                 library: engine.ClipLibrary, clips: ClipService):
        self.send = send                  # async callable(dict)
        self.camera = camera
        self.library = library
        self.clips = clips

        # settings the page controls
        self.clip_id: Optional[str] = None
        self.speed = 1.0
        self.coach_id = None
        self.mirror = True
        self.auto_pause = True

        self.state = "waiting"
        self._task: Optional[asyncio.Task] = None
        self._clock = engine.VideoClock()
        self._ended = asyncio.Event()
        self.last_stats: Optional[RunStats] = None
        self.last_clip_id: Optional[str] = None

    # ---- incoming messages --------------------------------------------------
    async def handle(self, msg: dict):
        kind = msg.get("type")
        if kind == "command":
            await self._command(msg)
        elif kind == "video_time":
            self._clock.update(msg.get("t"), msg.get("playing"), msg.get("rate"))
        elif kind == "video_ended":
            print("[session] Reference video finished")
            self._ended.set()
        else:
            print("[session] Unknown message:", msg)

    async def _command(self, msg: dict):
        name, value = msg.get("name"), msg.get("value")
        if name == "hello":
            await self.clips.send_clips(self.send)
        elif name == "choose_clip":
            self.clip_id = value
        elif name == "choose_coach":
            self.coach_id = value
            if msg.get("rerun_report"):          # "Hear from another coach"
                await self._send_debrief()
        elif name == "set_speed":
            with contextlib.suppress(TypeError, ValueError):
                self.speed = float(value)
        elif name == "mirror":
            self.mirror = bool(value)
            self.camera.mirror = self.mirror
        elif name == "auto_pause":
            self.auto_pause = bool(value)
        elif name == "start":
            await self._start(msg)
        elif name in ("reset", "try_again"):
            await self._reset()
        elif name == "get_replay":
            await self._send_replay()
        elif name == "ask_panel":
            await self._send_panel()
        else:
            print(f"[session] Command received: {name}")

    # ---- starting / stopping ------------------------------------------------
    async def _start(self, msg: dict):
        if self._task and not self._task.done():
            return                                   # already running
        self.clip_id = msg.get("clip") or self.clip_id
        if msg.get("coach"):
            self.coach_id = msg["coach"]
        with contextlib.suppress(TypeError, ValueError):
            self.speed = float(msg.get("speed", self.speed))
        print(f"[session] Start: clip={self.clip_id} speed={self.speed} coach={self.coach_id}")
        self._task = asyncio.get_running_loop().create_task(self._run(self.clip_id))

    async def _reset(self):
        await self._cancel_run()
        await self._set_state("waiting")

    async def _cancel_run(self):
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._shutdown_camera()

    async def _shutdown_camera(self):
        self.camera.track = None
        self.camera.clock = None
        await asyncio.to_thread(self.camera.stop)

    async def shutdown(self):
        """The browser went away."""
        await self._cancel_run()

    async def _set_state(self, state: str, reason: Optional[str] = None):
        self.state = state
        msg = {"type": "state", "state": state}
        if reason:
            msg["reason"] = reason
        await self.send(msg)

    def _check_camera(self):
        if self.camera.error:
            raise RuntimeError(self.camera.error)
        if not self.camera.running:
            raise RuntimeError("The camera stopped unexpectedly")

    # ---- the dance ----------------------------------------------------------
    async def _run(self, clip_id: Optional[str]):
        try:
            if not clip_id:
                raise RuntimeError("Choose a clip first")
            try:
                track = self.library.track(clip_id)
            except ValueError as exc:
                raise RuntimeError(str(exc))

            await self._set_state("loading", "Starting the camera and body-tracking model")
            self.camera.mirror = self.mirror
            self.camera.track = None
            self.camera.clock = None
            await asyncio.to_thread(self.camera.start)

            await self._calibrate()
            await self._countdown()
            stats = await self._play(track)
            await self._finish(stats, clip_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print("[session] Error:", exc)
            traceback.print_exc()
            await self._shutdown_camera()
            await self._set_state("error", str(exc) or exc.__class__.__name__)

    async def _calibrate(self):
        """Wait until the dancer has stood properly in the box for a moment."""
        await self._set_state("calibrating")
        ok_since: Optional[float] = None
        while True:
            self._check_camera()
            snap = self.camera.snapshot
            fresh = time.monotonic() - snap.ts < CAMERA_STALE_SECONDS
            ok, reasons = engine.check_calibration(snap.lms if fresh else None, self.mirror)
            now = time.monotonic()
            if ok:
                ok_since = ok_since if ok_since is not None else now
                progress = min(1.0, (now - ok_since) / engine.CALIB_HOLD_SECONDS)
            else:
                ok_since, progress = None, 0.0
            await self.send({"type": "calibration", "ok": ok,
                             "progress": round(progress, 2), "reasons": reasons})
            if progress >= 1.0:
                return
            await asyncio.sleep(TICK_SECONDS)

    async def _countdown(self):
        await self._set_state("countdown")
        for n in range(COUNTDOWN_SECONDS, 0, -1):
            self._check_camera()
            await self.send({"type": "countdown", "n": n})
            await asyncio.sleep(1.0)

    async def _play(self, track: engine.ReferenceTrack) -> RunStats:
        stats = RunStats(SECTION_SECONDS)
        stats.duration = track.duration_s
        n_sections = max(1, math.ceil(track.duration_s / SECTION_SECONDS))
        self._ended.clear()
        self._clock.reset()
        self.camera.track = track
        self.camera.clock = self._clock.now

        await self._set_state("playing")
        await self.send({"type": "video_cmd", "cmd": "seek", "t": 0})
        await self.send({"type": "video_cmd", "cmd": "play"})

        smoothed: Optional[float] = None
        streak = 0.0
        paused = False
        lost_since = back_since = None
        last = time.monotonic()

        while not self._ended.is_set():
            await asyncio.sleep(TICK_SECONDS)
            self._check_camera()
            now = time.monotonic()
            dt, last = now - last, now
            snap = self.camera.snapshot
            present = snap.lms is not None and now - snap.ts < CAMERA_STALE_SECONDS

            # ---- auto-pause when the dancer leaves the picture ----
            if paused:
                if present or not self.auto_pause:
                    back_since = now if back_since is None else back_since
                    if not self.auto_pause or now - back_since >= RESUME_AFTER:
                        paused, lost_since, back_since = False, None, None
                        await self.send({"type": "banner", "text": ""})
                        await self._set_state("playing")
                        await self.send({"type": "video_cmd", "cmd": "play",
                                         "t": self._clock.now()})
                else:
                    back_since = None
                continue
            if self.auto_pause:
                if present:
                    lost_since = None
                else:
                    lost_since = now if lost_since is None else lost_since
                    if now - lost_since >= AUTO_PAUSE_AFTER:
                        paused, back_since = True, None
                        await self._set_state("paused")
                        await self.send({"type": "video_cmd", "cmd": "pause"})
                        await self.send({"type": "banner",
                                         "text": "Paused - step back into view to carry on"})
                        continue

            # ---- score ----
            t = self._clock.now() or 0.0
            stats.tick(t)
            overall = snap.overall if present else None
            if overall is not None:
                smoothed = (overall if smoothed is None else
                            SCORE_SMOOTHING * overall + (1 - SCORE_SMOOTHING) * smoothed)
            band = engine.band_for(smoothed)
            streak = streak + dt if band == "green" else 0.0
            if smoothed is not None and overall is not None:
                stats.add(t, smoothed, band, snap.results, dt, streak)

            await self.send({
                "type": "score",
                "score": round(smoothed, 1) if smoothed is not None else 0,
                "band": band,
                "streak": round(streak, 1),
                "section": min(n_sections, int(t // SECTION_SECONDS) + 1),
                "section_count": n_sections,
                "missing_parts": (engine.missing_parts(snap.angles, self.mirror)
                                  if present else ["body"]),
                "t": round(t, 2),
                "segments": {n: round(r["score"]) for n, r in (snap.results or {}).items()
                             if r is not None},
            })
        return stats

    async def _finish(self, stats: RunStats, clip_id: str):
        self.last_stats, self.last_clip_id = stats, clip_id
        await self._shutdown_camera()
        await self._set_state("finished")
        await self._send_debrief()

    async def _send_debrief(self):
        """Claude's report (+ ElevenLabs voice) for the last run, for the chosen coach.
        Runs in a thread so the server keeps working while Claude answers."""
        stats = self.last_stats
        if stats is None:
            return
        try:
            msg = await asyncio.to_thread(debrief_bridge.report, stats, self.coach_id,
                                          self.speed, self.mirror)
        except Exception as exc:          # never leave the page stuck on "reviewing..."
            print("[session] Debrief failed, sending the simple report:", exc)
            traceback.print_exc()
            msg = stats.report(self.coach_id, self.last_clip_id, self.mirror)
        await self.send(msg)

    async def _send_panel(self):
        if self.last_stats is None:
            return
        try:
            msg = await asyncio.to_thread(debrief_bridge.panel, self.last_stats,
                                          self.speed, self.mirror)
            await self.send(msg)
        except Exception as exc:
            print("[session] Panel failed:", exc)
            traceback.print_exc()

    async def _send_replay(self):
        info = self.library.info(self.last_clip_id) if self.last_clip_id else None
        await self.send({
            "type": "replay",
            "ref_url": info["url"] if info else None,
            "clips": [],     # recorded video of the dancer is not implemented yet
            "score_log": self.last_stats.log if self.last_stats else [],
        })
