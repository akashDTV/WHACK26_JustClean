// Fake Python for building the UI without a server.
// Open index.html?fake=1  — it plays a whole run: calibrate → countdown →
// dance (with one pause) → report. Commands from the page are answered here.

(function () {
  if (!window.App.state.fake) return;

  const timers = [];
  const later = (ms, fn) => timers.push(setTimeout(fn, ms));
  const every = (ms, fn) => timers.push(setInterval(fn, ms));
  const clearAll = () => { timers.forEach((t) => { clearTimeout(t); clearInterval(t); }); timers.length = 0; };
  const emit = (msg) => window.App.handle(msg);

  // ---------- sample data (from the v3 scoring spec) ----------
  const DETAILS = {
    playback_rate: 1,
    scores: { overall: 78, position: 80, timing: 60, sharpness: 70, holds: 90, scale: "0-100" },
    sections: [
      { section: 1, start_s: 0, end_s: 4, overall: 84, position: 86, timing: 72, sharpness: 75, holds: 92, weakest_joint: "right_knee", mean_angle_error_deg: 7, arrival_offset_ms: 90, confidence: 0.96 },
      { section: 2, start_s: 4, end_s: 8, overall: 71, position: 74, timing: 55, sharpness: 68, holds: 88, weakest_joint: "left_elbow", mean_angle_error_deg: 18, arrival_offset_ms: 250, confidence: 0.94 },
      { section: 3, start_s: 8, end_s: 12, overall: 74, position: 79, timing: 50, sharpness: 66, holds: 90, weakest_joint: "left_shoulder", mean_angle_error_deg: 11, arrival_offset_ms: 250, confidence: 0.91 },
      { section: 4, start_s: 12, end_s: 16, overall: 82, position: 83, timing: 68, sharpness: 72, holds: null, weakest_joint: "right_knee", mean_angle_error_deg: 9, arrival_offset_ms: 120, confidence: 0.93 }
    ],
    body_part_scores: {
      left_elbow: { score: 82, mean_angle_error_deg: 9, confidence: 0.94, tracked_samples: 120 },
      right_knee: { score: 68, mean_angle_error_deg: 16, confidence: 0.91, tracked_samples: 118 },
      left_shoulder: { score: 77, mean_angle_error_deg: 11, confidence: 0.9, tracked_samples: 116 },
      right_hip: { score: 88, mean_angle_error_deg: 6, confidence: 0.95, tracked_samples: 121 },
      left_wrist: { score: null, metric: "unavailable", confidence: 0, tracked_samples: 0 }
    },
    data_quality: { tracked_frame_percentage: 96, valid_sections: [1, 2, 3, 4], excluded_sections: [], timing_events_detected: 12, hold_events_detected: 5 }
  };

  const BASE_FEEDBACK = {
    overall_score: 78,
    score_scale: "0-100",
    score_summary: "Your positions are generally close to the reference, but a few movements arrive late.",
    component_feedback: [
      { component: "position", score: 80, status: "good", summary: "Your overall body positions are close to the reference." },
      { component: "timing", score: 60, status: "needs_work", summary: "Some movements reach their final positions later than the reference." },
      { component: "sharpness", score: 70, status: "improving", summary: "A few movements take longer to complete." },
      { component: "holds", score: 90, status: "strong", summary: "You generally maintain positions for the right amount of time." }
    ],
    body_part_feedback: [
      { body_part: "right_knee", score: 68, status: "improving", summary: "Your right knee position differed more from the reference." },
      { body_part: "left_shoulder", score: 77, status: "improving", summary: "Left shoulder was slightly off in section 3." },
      { body_part: "left_elbow", score: 82, status: "good", summary: "Your left elbow closely matched the reference." },
      { body_part: "right_hip", score: 88, status: "good", summary: "Right hip matched well." },
      { body_part: "left_wrist", score: null, status: "unavailable", summary: "There was not enough reliable wrist data to score." }
    ],
    strengths: [{ title: "Strong holds", detail: "Your hold durations were close to the reference." }],
    priority_fixes: [
      { rank: 1, title: "Left elbow positioning", body_part: "left_elbow", section: 2, timestamp_s: 6.4, what_happened: "Your left elbow was more bent than the reference.", evidence: "18 degree mean angle difference; high confidence.", drill: "Repeat this section slowly and focus on matching the elbow extension.", focus_cue: "Match the final arm shape before moving on." },
      { rank: 2, title: "Arrive on time", body_part: "left_arm", section: 3, timestamp_s: 9.2, what_happened: "You reached the target position 250 ms after the reference.", evidence: "Measured arrival offset: +250 ms; high confidence.", drill: "Loop the transition and aim to reach the final position with the reference.", focus_cue: "Arrive with the choreographer, not after them." }
    ],
    next_practice_plan: { first: "Repeat section 2 slowly, focusing on left elbow shape.", second: "Loop section 3 and focus on arriving at the same time.", success_target: "Improve the section 2 position score and section 3 timing score on the next attempt." },
    data_caveat: null
  };

  const HEADLINES = {
    hype: "78! Those holds were ROCK SOLID — now let's make that left arm land on time!",
    strict: "Holds: acceptable. Left elbow: not acceptable. Eighteen degrees. Again.",
    technical: "Position 80, timing 60. Largest difference: left elbow, 18° at 6.4 s.",
    gentle: "You held your positions beautifully. Let's give your arms a little more care."
  };
  const feedbackFor = (id) => ({ ...BASE_FEEDBACK, headline: HEADLINES[id] || HEADLINES.hype });

  let CLIPS = [
    { id: "clip_a", name: "Demo clip A", duration_s: 16, ref_ok: true, tracked_pct: 98 },
    { id: "clip_b", name: "Demo clip B", duration_s: 20, ref_ok: true, tracked_pct: 96 },
    { id: "clip_c", name: "Shaky test clip", duration_s: 18, ref_ok: false, warning: "Only 71% of frames tracked — pick another clip" }
  ];

  let coachId = "hype";
  let clipId = null;
  let scoreLog = [];

  // ---------- the scripted run ----------
  function run() {
    clearAll();
    scoreLog = [];
    emit({ type: "state", state: "calibrating" });
    emit({ type: "calibration", ok: false, reasons: ["Step back", "Feet not visible"], progress: 0 });
    later(1200, () => emit({ type: "calibration", ok: false, reasons: ["Move a little to your left"], progress: 0 }));
    let p = 0;
    later(2200, () => every(100, () => {
      p = Math.min(1, p + 0.05);
      emit({ type: "calibration", ok: true, reasons: [], progress: p });
      if (p >= 1) { clearAll(); countdownThen(dance); }
    }));
  }

  function countdownThen(fn) {
    emit({ type: "state", state: "countdown" });
    [3, 2, 1].forEach((n, i) => later(i * 800, () => emit({ type: "countdown", n })));
    later(2400, () => { emit({ type: "state", state: "playing" }); emit({ type: "video_cmd", cmd: "play", t: 0 }); fn(); });
  }

  function dance(startT = 0) {
    let t = startT, streak = 0, base = 70, paused = false;
    every(50, () => {
      if (paused) return;
      t += 0.05;
      base += (Math.random() - 0.45) * 6;
      base = Math.max(30, Math.min(97, base));
      const score = Math.round(base);
      const band = score >= 80 ? "green" : score >= 60 ? "amber" : "red";
      streak = band === "green" ? streak + 0.05 : band === "red" ? 0 : streak;
      const missing = t > 6 && t < 8 ? ["left_arm"] : [];
      emit({ type: "score", score, band, streak, weakest_joint: "left_elbow", missing_parts: missing, section: Math.floor(t / 4) + 1, section_count: 4 });
      if (Math.abs(t - Math.round(t)) < 0.026) scoreLog.push({ t: Math.round(t), score, band });

      if (t > 10 && t < 10.06 && startT === 0) {           // simulate leaving the frame once
        paused = true;
        clearAll();
        emit({ type: "state", state: "paused", reason: "left_frame" });
        emit({ type: "banner", text: "Step back into frame" });
        later(2500, () => { emit({ type: "banner", text: "" }); countdownThen(() => dance(8)); });
      }
      const v = document.getElementById("ref-video");
      const hasVideo = v.currentSrc && Number.isFinite(v.duration);
      const clip = CLIPS.find((c) => c.id === clipId) || {};
      if (!hasVideo && t >= (clip.duration_s || 16)) { clearAll(); finish(); }   // no file: use its length
    });
  }

  function finish() {
    emit({ type: "state", state: "finished" });
    later(1500, () => emit({ type: "report", coach_id: coachId, feedback: feedbackFor(coachId), details: DETAILS }));
  }

  // ---------- answer commands from the page ----------
  const Fake = {
    send(msg) {
      if (msg.type === "video_ended") { clearAll(); finish(); return; }   // the real end of the dance
      if (msg.type !== "command") return;
      switch (msg.name) {
        case "choose_coach":
          coachId = msg.value;
          if (msg.rerun_report) emit({ type: "report", coach_id: coachId, feedback: feedbackFor(coachId), details: DETAILS });
          break;
        case "start": coachId = msg.coach || coachId; clipId = msg.clip || clipId; run(); break;
        case "choose_clip": clipId = msg.value; break;
        case "rename_clip": {
          const c = CLIPS.find((x) => x.id === msg.value);
          if (c) c.name = msg.new_name;
          emit({ type: "clips", clips: CLIPS });
          break;
        }
        case "delete_clip": {
          const i = CLIPS.findIndex((x) => x.id === msg.value);
          if (i >= 0) CLIPS.splice(i, 1);
          emit({ type: "clips", clips: CLIPS });
          break;
        }
        case "restart":                       // start the dance again from the top
          clearAll(); scoreLog = []; countdownThen(dance); break;
        case "reset":
        case "try_again": clearAll(); emit({ type: "state", state: "waiting" }); break;
        case "ask_panel":
          later(900, () => emit({ type: "panel", details: DETAILS, coaches: window.COACHES.map((c) => ({ coach_id: c.id, feedback: feedbackFor(c.id) })) }));
          break;
        case "get_replay":
          emit({ type: "replay", ref_url: null, clips: [], score_log: scoreLog });
          break;
      }
    },

    upload(file) {
      // fake upload progress, then processing stages (percent = processing 0-100)
      let up = 0;
      const t = setInterval(() => { up = Math.min(30, up + 3); window.App.setUploadProgress(up, "Uploading…"); if (up >= 30) clearInterval(t); }, 60);
      const steps = [["Converting video…", 15], ["Converting video…", 35], ["Tracking the dancer…", 55], ["Tracking the dancer…", 80], ["Checking quality…", 92]];
      steps.forEach(([stage, percent], i) => setTimeout(() => emit({ type: "upload_progress", stage, percent }), 700 + i * 550));
      setTimeout(() => emit({
        type: "upload_result", ok: true, tracked_pct: 94, warnings: [],
        clip: (() => { const c = { id: `upload_${Date.now()}`, name: file.name.replace(/\.[^.]+$/, ""), ref_ok: true, url: URL.createObjectURL(file) }; CLIPS.push(c); return c; })()
      }), 3700);
    }
  };

  window.Fake = Fake;
  window.Transport = Fake;

  // boot
  setTimeout(() => {
    emit({ type: "state", state: "loading", reason: "Loading the body-tracking model (fake)" });
    setTimeout(() => emit({ type: "clips", clips: CLIPS }), 600);
  }, 100);
})();
