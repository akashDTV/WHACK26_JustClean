# Messages between Python and the page

One WebSocket, JSON objects, a `type` field on every message.
The browser is the clock: it sends `video_time` about 10 times a second.
Python sends `score` about 20 times a second while the dance is playing.
The webcam picture is not a WebSocket message. It is a live image stream at `/camera`.

## Python to the page

### hello
Sent when the socket connects, and after a reset.

```json
{
  "type": "hello",
  "state": "waiting",
  "reason": "",
  "clips": [],
  "mirror": true,
  "auto_pause": true,
  "coach": {"name": "Roux", "rate": 1.05, "pitch": 1.05}
}
```

`state` is one of: waiting, loading, calibrating, countdown, playing, paused, finished, error.

### calibration
```json
{"type": "calibration", "ok": false, "reasons": ["step back", "feet not visible"], "progress": 0.4}
```
`progress` is seconds-in-a-row that the check has passed, divided by 2, capped at 1.

### state
```json
{"type": "state", "state": "paused", "reason": "step back into frame"}
```

### countdown
```json
{"type": "countdown", "number": 3}
```

### score
Sent only while playing, and not during the first 0.5 s after a restart.
```json
{
  "type": "score",
  "score": 82,
  "band": "green",
  "streak": 2.4,
  "weakest_joint": "left_elbow",
  "missing": ["left_arm"],
  "section": 2,
  "raw_error": 14.2,
  "joint_errors": {"left_elbow": 30.0},
  "fps": 24.0
}
```
`score` is 0 to 100. `band` is green, amber, or red.
`raw_error` and `joint_errors` feed the hidden debug panel.

### banner
```json
{"type": "banner", "text": "Step back into frame"}
```
An empty string clears it.

### video_cmd
```json
{"type": "video_cmd", "cmd": "seek", "time": 4.0}
```
`cmd` is play, pause, or seek. `time` is seconds (used by seek).

### report
```json
{
  "type": "report",
  "text": "Three in-character sentences.",
  "drill": "One drill.",
  "summary": {},
  "coach": "Roux",
  "source": "backup"
}
```
`source` is `claude` or `backup`.

### replay
```json
{
  "type": "replay",
  "clips": [{"file": "/media/runs/abc/clip_0.webm", "ref_start": 0.0}],
  "score_log": [{"t": 1.0, "score": 70, "band": "amber", "weakest_joint": "left_knee", "section": 1, "interrupted": false}],
  "reference": "/media/clips/demo.mp4",
  "duration": 20.0
}
```

### debug
Same numbers as the latest score, also sent when the dance is quiet so the panel still moves.
```json
{"type": "debug", "fps": 24.0, "raw_error": null, "joint_errors": {}}
```

## Page to Python

### video_time
```json
{"type": "video_time", "time": 3.42, "playing": true, "duration": 20.0}
```

### video_ended
```json
{"type": "video_ended"}
```

### command
```json
{"type": "command", "name": "choose_clip", "clip_id": "demo", "source": "testdata"}
```

`name` is one of:

- `start`
- `reset`
- `try_again`
- `choose_clip` with `clip_id` and `source` (`camera` or `testdata`)
- `mirror` with `on` true or false
- `auto_pause` with `on` true or false
- `preview_fake` loads the sample debrief in `data/fake/run.json`
