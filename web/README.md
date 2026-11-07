# web/ — Live Dance Coach front end

Plain HTML/CSS/JS. No React, no build step.

## Run it

- **Without Python (fake mode):** just double-click `web/index.html` — opened from disk it runs in fake mode automatically. A full run plays: calibrate → countdown → dance (one pause) → report → panel.
- **With the real server:** FastAPI serves this folder at `/`. The page connects to `ws://<host>/ws`.
  Override the URL with `?ws=ws://localhost:8000/ws`.

## Files

| File | What it does |
|---|---|
| `index.html` | All screens: loading, error, chooser, calibrating, dancing, report, panel, replay, plus overlays |
| `style.css` | Design tokens at the top (colours, sizes, motion). Accessibility modes override these. |
| `js/coaches.js` | The 4 coach personalities (name, colour, voice, sliders). Keep ids in sync with Python. |
| `js/a11y.js` | Accessibility hooks: `A11Y.announce()`, settings → `data-a11y-*` on `<html>`. Extend here. |
| `js/speech.js` | Coach voice (Web Speech API) and a queue for the panel |
| `js/report.js` | Report screen, **Detailed breakdown** dropdown, panel |
| `js/app.js` | Screens, chooser, live score, upload, keyboard shortcuts, and the message handlers table |
| `js/ws.js` | WebSocket client with auto-reconnect |
| `js/fake.js` | Fake Python for `?fake=1` |

## Messages the page uses — check against `server/messages.md`

If a name or field differs, change it in `HANDLERS` (`js/app.js`) and the `send()` calls only.

**Python → page**

- `clips {clips:[{id, name, duration_s, url, thumb?, ref_ok, tracked_pct?, warning?}]}`
- `state {state: waiting|loading|calibrating|countdown|playing|paused|finished|error, reason?}`
- `calibration {ok, reasons[], progress 0–1}`
- `countdown {n}`
- `banner {text}` (an empty text hides it)
- `score {score, band, streak, weakest_joint, missing_parts[], section, section_count?, pop?}`
- `video_cmd {cmd: play|pause|seek, t}`
- `report {coach_id, feedback: <v3 LLM output>, details: {scores, sections, body_part_scores, data_quality, playback_rate}}`
- `panel {coaches:[{coach_id, feedback}], details}`
- `replay {ref_url, clips:[{file, ref_start_s}], score_log:[{t, score, band}]}`
- `upload_progress {stage, percent}` / `upload_result {ok, tracked_pct, warnings[], clip}`
- `debug {...}` (shown in the D panel)

**Page → Python**

- `video_time {t, playing, rate}` about 10× a second while dancing
- `video_ended {}`
- `command {name, ...}` (`set_speed` value is any 0.25–1.5 in 0.05 steps, sent when the slider is released)
- `command {name, ...}` where name is one of: `hello`, `choose_clip`, `set_speed`, `choose_coach` (with `rerun_report`), `start`,
  `reset`, `try_again`, `mirror`, `auto_pause`, `ask_panel`, `get_replay`
- `POST /upload` (multipart `file`)

## Keyboard

Space start · M mirror · P auto-pause · R reset · D debug panel

## Accessibility: in place now / still to add

In place: semantic buttons and labels, visible focus, focus moves to each new screen, live regions for banners, countdown and
missing limbs, captions for the coach, bands shown by colour **and** glow thickness **and** text, prefers-reduced-motion.

To add: a settings panel, high-contrast theme (`html[data-a11y-high-contrast="on"]`), larger text (`--text-scale`),
sound cues for bands.
