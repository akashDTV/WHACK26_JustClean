// JustClean — main page logic.
// Every message from Python goes through App.handle(msg).
// Every message to Python goes through App.send(type, payload).
// Message names follow server/messages.md — if a name differs there,
// change it in the HANDLERS table below and in send() calls only.

(function () {
  const $ = (id) => document.getElementById(id);
  // Practice-speed slider settings — change these to widen/narrow the range.
  const SPEED = { min: 0.5, max: 1.2, step: 0.05, default: 1 };
  const fmtRate = (r) => `${Number(r.toFixed(2))}×`;   // 0.75 -> "0.75×", 1 -> "1×"
  // Live webcam picture (with your skeleton drawn on it) served by server/main.py (the HTML's data-src wins if it has one).
  // It is attached to every <img class="cam-feed"> while a dance is running.
  const CAMERA_URL = "/camera";

  const App = {
    state: {
      screen: "loading",
      clips: [],
      clipId: null,
      rate: 1,
      coachId: window.COACHES[0].id,
      mirror: true,
      mirrorRef: false,     // flip the reference video (dance it like a mirror)
      autoPause: true,
      lastStreakPop: 0,
      fake: false
    },

    // ---------- transport ----------
    send(type, payload = {}) {
      const msg = { type, ...payload };
      if (window.Transport) window.Transport.send(msg);
    },

    
    // ---------- routing ----------
    handle(msg) {
      if (!msg || !msg.type) return;
      const fn = HANDLERS[msg.type];
      if (fn) fn(msg);
      else console.warn("[app] unhandled message", msg);
    },

    show(screen) {
      if (screen !== this.state.screen) pageWipe();
      this.state.screen = screen;
      document.body.dataset.screen = screen;
      document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === `screen-${screen}`));
      if (screen !== "dancing") { document.body.dataset.band = "none"; $("pop").classList.remove("show"); }
      if (screen === "calibrating") { fitCalibration(); requestAnimationFrame(fitCalibration); }
      // move focus to the new screen's heading for keyboard / screen-reader users
      const h = document.querySelector(`#screen-${screen} h1`);
      if (h) { h.setAttribute("tabindex", "-1"); h.focus({ preventScroll: true }); }
    },

    // ---------- chooser ----------
    renderClips() {
      const list = $("clip-list");
      const upload = $("upload-card");
      list.querySelectorAll(".clip-card").forEach((n) => n.remove());
      const ed = this.state.editing || {};
      this.state.clips.forEach((c) => {
        const ok = c.ref_ok !== false;
        const info = probeClip(c, () => this.renderClips());        // first frame + real length
        const thumb = c.thumb || info.thumb;
        const dur = info.duration || c.duration_s;
        const selected = this.state.clipId === c.id;
        const renaming = ed.id === c.id && ed.mode === "rename";
        const deleting = ed.id === c.id && ed.mode === "delete";

        const pick = el("button", {
          class: "clip-pick", type: "button", "aria-pressed": String(selected),
          "aria-label": `${c.name}${dur ? `, ${fmtDuration(dur)}` : ""}`,
          onclick: () => this.chooseClip(c.id)
        },
          el("div", { class: "thumb" }, thumb ? el("img", { src: thumb, alt: "" }) : el("span", { class: "thumb-loading", text: c.url ? "Loading preview…" : "No preview" })),
          renaming ? "" : el("div", { class: "clip-title" },
            el("strong", { text: c.name }),
            el("span", { class: "muted", text: dur ? fmtDuration(dur) : "" })));

        let extra = "";
        if (renaming) {
          const input = el("input", { class: "rename-input", type: "text", value: c.name, maxlength: "60", "aria-label": "New name for this dance" });
          const save = () => { const v = input.value.trim(); if (v && v !== c.name) this.renameClip(c.id, v); this.editClip(null); };
          input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); if (e.key === "Escape") this.editClip(null); });
          extra = el("form", { class: "clip-edit", onsubmit: (e) => { e.preventDefault(); save(); } },
            input,
            el("div", { class: "btn-row" },
              el("button", { class: "btn btn-primary btn-sm", type: "submit", text: "Save" }),
              el("button", { class: "btn btn-ghost btn-sm", type: "button", text: "Cancel", onclick: () => this.editClip(null) })));
          setTimeout(() => { input.focus(); input.select(); });
        } else if (deleting) {
          extra = el("div", { class: "clip-edit clip-confirm", role: "alertdialog", "aria-label": `Delete ${c.name}?` },
            el("span", { text: `Delete “${c.name}”?` }),
            el("div", { class: "btn-row" },
              el("button", { class: "btn btn-danger btn-sm", type: "button", text: "Delete", onclick: () => this.deleteClip(c.id) }),
              el("button", { class: "btn btn-ghost btn-sm", type: "button", text: "Keep it", onclick: () => this.editClip(null) })));
          setTimeout(() => extra.querySelector(".btn-ghost").focus());
        }

        const actions = (renaming || deleting || c.editable === false) ? "" : el("div", { class: "clip-actions" },
          el("button", { class: "icon-btn on-thumb", type: "button", title: "Rename", "aria-label": `Rename ${c.name}`, onclick: () => this.editClip(c.id, "rename") }, pencilIcon()),
          el("button", { class: "icon-btn on-thumb", type: "button", title: "Delete", "aria-label": `Delete ${c.name}`, onclick: () => this.editClip(c.id, "delete") }, trashIcon()));

        const card = el("div", { class: `card clip-card${selected ? " selected" : ""}` },
          pick, actions, extra,
          !ok || c.warning ? el("span", { class: "clip-warning", text: c.warning || "Reference data missing — can't use this clip yet" }) : "");
        list.insertBefore(card, upload);
      });
    },

    editClip(id, mode) {
      this.state.editing = id ? { id, mode } : null;
      this.renderClips();
    },

    renameClip(id, name) {
      const c = this.state.clips.find((x) => x.id === id);
      if (c) c.name = name;                       // show it straight away; Python confirms with a clips message
      this.send("command", { name: "rename_clip", value: id, new_name: name });
    },

    deleteClip(id) {
      this.state.editing = null;
      this.state.clips = this.state.clips.filter((x) => x.id !== id);
      if (this.state.clipId === id) {
        this.state.clipId = null;
        $("start-btn").disabled = true;
        $("start-hint").textContent = "Pick a dance to begin · or press Space";
        const v = $("ref-video"); v.removeAttribute("src"); v.load(); $("ref-placeholder").hidden = false;
      }
      this.renderClips();
      this.send("command", { name: "delete_clip", value: id });
      window.A11Y.announce("Dance deleted");
    },

    chooseClip(id) {
      const clip = this.state.clips.find((c) => c.id === id);
      if (!clip) return;
      this.state.clipId = id;
      this.renderClips();
      const video = $("ref-video");
      if (clip.url) { video.src = clip.url; $("ref-placeholder").hidden = true; }
      this.refreshStart();
      this.send("command", { name: "choose_clip", value: id });
    },

    // Enable/disable the Start button for the chosen clip. Also called when Python
    // re-sends the clip list (e.g. a clip finished being analysed).
    refreshStart() {
      const clip = this.state.clips.find((c) => c.id === this.state.clipId);
      if (!clip) return;
      const ok = clip.ref_ok !== false;
      $("start-btn").disabled = !ok;
      $("start-hint").textContent = ok ? "or press Space · you'll stand in the outline box first" : (clip.warning || "This clip can't be used — pick another");
    },

    // Show / hide the live camera picture. Python only streams while a dance is
    // running (the webcam is off otherwise), so we attach it when one starts.
    setCamera(on) {
      if (this.state.fake) return;                      // demo mode has no server camera
      document.querySelectorAll("img.cam-feed").forEach((img) => {
        const ph = img.parentElement.querySelector(".placeholder");
        if (on) {
          if (img.dataset.live) return;                 // already attached
          img.dataset.live = "1";
          // use the URL from the HTML (data-src) if there is one; cache-buster = fresh stream
          img.src = `${img.dataset.src || CAMERA_URL}?t=${Date.now()}`;
          // A live stream doesn't always fire "load" for its first frame, so also
          // watch for the picture to get a size (checks for up to 15 s).
          clearInterval(img._camPoll);
          let tries = 0;
          img._camPoll = setInterval(() => {
            if (img.naturalWidth > 0) { markCameraReady(img); clearInterval(img._camPoll); }
            else if (++tries > 150) clearInterval(img._camPoll);
          }, 100);
        } else if (img.dataset.live) {
          clearInterval(img._camPoll);
          delete img.dataset.live;
          img.removeAttribute("src");
          img.parentElement.classList.remove("has-image");   // calibration box goes back to placeholder size
          if (ph) ph.hidden = false;
        }
      });
    },

    // Slider: updates the display live while dragging ("input"),
    // and only tells Python once the user lets go ("change").
    initSpeedSlider() {
      const s = $("speed-slider");
      s.min = SPEED.min; s.max = SPEED.max; s.step = SPEED.step; s.value = SPEED.default;
      $("speed-min-label").textContent = fmtRate(SPEED.min);
      $("speed-max-label").textContent = fmtRate(SPEED.max);
      s.addEventListener("input", () => this.setSpeed(parseFloat(s.value), false));
      s.addEventListener("change", () => this.setSpeed(parseFloat(s.value), true));
      $("speed-reset").addEventListener("click", () => this.setSpeed(SPEED.default, true));
      $("mirror-ref-btn").addEventListener("click", () => this.setMirrorRef(!this.state.mirrorRef));
    },

    // "Mirror the dancer": flip the reference video; the server mirrors the scoring to match.
    setMirrorRef(on) {
      this.state.mirrorRef = !!on;
      const b = $("mirror-ref-btn");
      b.setAttribute("aria-pressed", String(this.state.mirrorRef));
      b.textContent = `Mirror the dancer: ${this.state.mirrorRef ? "on" : "off"}`;
      document.body.classList.toggle("ref-mirrored", this.state.mirrorRef);
      this.send("command", { name: "mirror_ref", value: this.state.mirrorRef });
    },

    setSpeed(r, notify = true) {
      r = Math.round(r / SPEED.step) * SPEED.step;          // snap to 0.05 steps, avoid float drift
      r = Math.min(SPEED.max, Math.max(SPEED.min, Number(r.toFixed(2))));
      this.state.rate = r;
      const s = $("speed-slider");
      if (parseFloat(s.value) !== r) s.value = r;
      const pct = ((r - SPEED.min) / (SPEED.max - SPEED.min)) * 100;
      s.style.setProperty("--fill", `${pct}%`);
      s.setAttribute("aria-valuetext", `${fmtRate(r)} speed`);
      $("speed-value").textContent = fmtRate(r);
      $("ref-video").playbackRate = r;
      $("speed-chip").textContent = r === 1 ? "Full speed" : `Practising at ${fmtRate(r)}`;
      if (notify) this.send("command", { name: "set_speed", value: r });
    },

    renderCoaches() {
      $("coach-list").replaceChildren(...window.COACHES.map((c) => el("button", {
        class: "card", style: "min-height:150px", "aria-pressed": String(this.state.coachId === c.id),
        onclick: () => this.chooseCoach(c.id)
      },
        el("div", { class: "coach-head" },
          window.makeAvatar(c),
          el("div", { style: "display:flex; flex-direction:column; gap:2px" },
            el("span", { class: "coach-name", text: c.name }),
            el("span", { class: "muted", style: "font-size: var(--fs-sm)", text: c.role }))),
        el("span", { class: "coach-quote", text: c.quote }))));
    },

    chooseCoach(id) {
      this.state.coachId = id;
      $("coach-chip").textContent = `Coach: ${window.coachById(id).name}`;
      this.renderCoaches();
      this.send("command", { name: "choose_coach", value: id });
    },

    start() {
      if (!this.state.clipId || $("start-btn").disabled) return;
      unlockMedia();
      this.state.cp = null;                     // fresh catchphrase timing for each run
      this.send("command", { name: "start", clip: this.state.clipId, speed: this.state.rate, coach: this.state.coachId, mirror_ref: this.state.mirrorRef });
    },

    reset(name = "reset") {
      window.Speech.stop();
      hideOverlays();
      clearLive();
      $("breakdown").open = false;
      window.scrollTo({ top: 0 });
      const v = $("ref-video");
      v.pause();
      v.currentTime = 0;
      this.send("command", { name });
      this.show("chooser");
    },

    // Coach catchphrase: only once the score has stayed in range for a while, then a cool-down.
    // Short dips (under grace_s) don't reset the timer, so a jittery score still counts as "staying".
    checkCatchphrase(score) {
      const coach = window.coachById(this.state.coachId);
      const cp = coach.catchphrase;
      if (!cp || !Number.isFinite(score)) return;
      const { hold_s, cooldown_s, grace_s = 1 } = window.CATCHPHRASE_TIMING;
      const now = performance.now() / 1000;
      const c = this.state.cp || (this.state.cp = { since: null, lastIn: 0, lastPop: -Infinity });
      const inRange = cp.above != null ? score >= cp.above : score < cp.below;
      if (inRange) {
        if (c.since === null || now - c.lastIn > grace_s) c.since = now;
        c.lastIn = now;
        if (now - c.since >= hold_s && now - c.lastPop >= cooldown_s) {
          c.lastPop = now;
          c.since = null;                        // must stay in range again before the next one
          showPop(cp.text, { color: coach.color, ms: 1800, catchphrase: true, coach });
          window.A11Y.announce(`${coach.name}: ${cp.text}`);
        }
      }
    },

    // Restart the dance from the beginning (stays on the dancing screen).
    // Python should clear this run's score/log and do the countdown again.
    restart() {
      if (this.state.screen !== "dancing") return;
      const v = $("ref-video");
      v.pause();
      v.currentTime = 0;
      hideOverlays();
      clearLive();
      showPop("RESTART");
      this.send("command", { name: "restart" });
    },

    // ---------- upload ----------
    async upload(file) {
      const status = $("upload-status");
      const bar = $("upload-progress");
      const okType = /video\/(mp4|webm|quicktime)/.test(file.type);
      if (!okType) { status.textContent = "Please choose an MP4, WebM or MOV file."; return; }
      const dur = await videoDuration(file).catch(() => null);
      if (dur && dur > MAX_UPLOAD_S + 1) { status.textContent = `That clip is ${fmtDuration(dur)} long. Please trim it to ${MAX_UPLOAD_S / 60} minutes or less.`; return; }

      bar.hidden = false;
      bar.classList.remove("done", "failed");
      setProgress(bar, 0, "Uploading…");
      status.textContent = "";

      if (this.state.fake) { window.Fake.upload(file); return; }

      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress(bar, (e.loaded / e.total) * 30, "Uploading…"); };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) setProgress(bar, 30, "Tracking the dancer…");
        else uploadFailed("Upload failed. Try again.");
      };
      xhr.onerror = () => uploadFailed("Upload failed. Is the server running?");
      xhr.open("POST", "/upload");
      xhr.send(form);
    },

    // ---------- live score ----------
    onScore(m) {
      const score = Math.round(m.score);
      $("score-num").textContent = Number.isFinite(score) ? score : "–";
      $("bar-fill").style.height = `${Math.max(0, Math.min(100, score || 0))}%`;
      document.body.dataset.band = m.band || "none";
      $("band-label").textContent = { green: "Close match", amber: "Some differences", red: "Big mismatch" }[m.band] || "Waiting";
      const streak = m.streak || 0;
      $("streak-num").textContent = `${Math.floor(streak)} s`;
      if (m.section != null) $("section-chip").textContent = `Section ${m.section}${m.section_count ? ` of ${m.section_count}` : ""}`;

      // missing limbs (render only if changed)
      const missing = (m.missing_parts || []).join("|");
      const box = $("missing-list");
      if (box.dataset.key !== missing) {
        box.dataset.key = missing;
        box.replaceChildren(...(m.missing_parts || []).map((p) => el("div", { class: "missing-chip" },
          eyeOffIcon(), `Can't see ${String(p).replace(/_/g, " ")}`)));
      }

      // pops: Python can send m.pop; otherwise the chosen coach's catchphrase
      if (m.pop) showPop(m.pop);
      else this.checkCatchphrase(score);

      if (!$("debug").classList.contains("show")) return;
      $("debug-pre").textContent = JSON.stringify(m, null, 1);
    }
  };

  // ---------- message handlers (Python -> page) ----------
  const HANDLERS = {
    clips(m) {
      App.state.clips = m.clips || [];
      App.renderClips();
      App.refreshStart();
      if (App.state.screen === "loading") App.show("chooser");
    },

    state(m) {
      const map = { waiting: "chooser", loading: "loading", calibrating: "calibrating", countdown: "dancing", playing: "dancing", paused: "dancing", finished: "report", error: "error" };
      const screen = map[m.state] || m.state;
      if (m.state === "loading") $("loading-msg").textContent = m.reason || "Loading the body-tracking model";
      if (m.state === "error") $("error-msg").textContent = m.reason || "Please try again.";
      if (m.state === "playing") hideOverlays();
      // webcam picture: on from calibration until the dance ends
      if (["calibrating", "countdown", "playing", "paused"].includes(m.state)) App.setCamera(true);
      else if (["waiting", "loading", "finished", "error"].includes(m.state)) App.setCamera(false);
      if (m.state === "finished") {
        $("rep-caption").textContent = "Your coach is reviewing your dance…";
        $("rep-caption-more").replaceChildren();
        $("rep-overall").textContent = "…";
      }
      if (screen !== App.state.screen) App.show(screen);
      window.A11Y.announce({ calibrating: "Step into the box", playing: "Dance!", paused: "Paused", finished: "Dance finished" }[m.state] || "");
    },

    calibration(m) {
      $("calib-box").classList.toggle("ok", !!m.ok);
      const pct = Math.round((m.progress || 0) * 100);
      $("calib-progress").firstElementChild.style.width = `${pct}%`;
      $("calib-progress").setAttribute("aria-valuenow", String(pct));
      const reasons = m.ok ? ["Hold still… starting soon"] : (m.reasons || []);
      const key = reasons.join("|");
      const ul = $("calib-reasons");
      if (ul.dataset.key !== key) { ul.dataset.key = key; ul.replaceChildren(...reasons.map((r) => el("li", { text: r }))); }
    },

    countdown(m) {
      $("countdown-num").textContent = m.n;
      $("countdown-overlay").classList.add("show");
      $("banner-overlay").classList.remove("show");
      window.A11Y.announce(String(m.n));
    },

    banner(m) {
      const show = !!(m.text && m.text.trim());
      $("banner-text").textContent = m.text || "";
      $("banner-overlay").classList.toggle("show", show);
    },

    score(m) { App.onScore(m); },

    video_cmd(m) {
      const v = $("ref-video");
      if (m.t != null && (m.cmd === "seek" || Math.abs(v.currentTime - m.t) > 0.25)) v.currentTime = m.t;
      if (m.cmd === "play") { $("countdown-overlay").classList.remove("show"); v.playbackRate = App.state.rate; v.play().catch(() => {}); }
      if (m.cmd === "pause") v.pause();
    },

    report(m) {
      window.Report.render(m);
      App.show("report");
      const coach = window.coachById(m.coach_id);
      window.Speech.speak(window.Report.speechFor(m.feedback || {}), coach, "report");
    },

    panel(m) {
      App.show("panel");
      window.Report.renderPanel(m);
    },

    replay(m) { window.Replay.load(m); },

    upload_progress(m) {
      const bar = $("upload-progress");
      bar.hidden = false;
      setProgress(bar, 30 + (m.percent || 0) * 0.7, m.stage || "Processing…");
    },

    upload_result(m) {
      const bar = $("upload-progress");
      if (m.ok) { setProgress(bar, 100, "Ready!"); bar.classList.add("done"); setTimeout(() => { bar.hidden = true; }, 1400); }
      else uploadFailed("");
      const status = $("upload-status");
      if (!m.ok) { status.textContent = `We couldn't use that clip: ${(m.warnings || []).join(" ") || "try one dancer, full body, steady camera."}`; return; }
      status.textContent = (m.warnings || []).length ? `Ready, with a warning: ${m.warnings.join(" ")}` : `Ready · ${m.tracked_pct}% of frames tracked`;
      if (m.clip) {
        App.state.clips = App.state.clips.filter((c) => c.id !== m.clip.id).concat(m.clip);
        App.renderClips();
        App.chooseClip(m.clip.id);
      }
    },

    debug(m) { $("debug-pre").textContent = JSON.stringify(m, null, 1); }
  };

  // ---------- replay (stretch goal: simple version) ----------
  window.Replay = {
    data: null,
    load(m) {
      this.data = m;
      const ref = $("replay-ref"), user = $("replay-user");
      if (m.ref_url) ref.src = m.ref_url;
      const clip = (m.clips || [])[0];
      // no recording of the dancer: reference only, centred (guarded so it can never stop the timeline)
      const userPane = $("replay-user-pane");
      if (userPane) userPane.hidden = !clip;
      const screen = $("screen-replay");
      if (screen) screen.classList.toggle("ref-only", !clip);
      if (clip) { user.src = clip.file; user.dataset.offset = clip.ref_start_s || 0; }
      // keep the user video following the reference
      ref.ontimeupdate = () => {
        const c = this.clipAt(ref.currentTime);
        if (!c) return;
        if (!user.src.endsWith(c.file)) { user.src = c.file; user.dataset.offset = c.ref_start_s; }
        const want = ref.currentTime - c.ref_start_s;
        if (Math.abs(user.currentTime - want) > 0.3) user.currentTime = want;
      };
      ref.onplay = () => user.play().catch(() => {});
      ref.onpause = () => user.pause();
      this.drawTimeline();
    },
    clipAt(t) {
      const clips = (this.data && this.data.clips) || [];
      let found = null;
      clips.forEach((c) => { if (t >= c.ref_start_s) found = c; });
      return found;
    },
    drawTimeline() {
      const log = (this.data && this.data.score_log) || [];
      if (!log.length) return;
      const end = log[log.length - 1].t + 1;
      $("timeline").replaceChildren(...log.map((p) => el("button", {
        style: `left:${(p.t / end) * 100}%; width:${100 / log.length}%; background: var(--${p.band === "green" ? "green" : p.band === "amber" ? "amber" : "red"}); opacity:${p.band === "red" ? 1 : 0.55}`,
        "aria-label": `${p.t} seconds, score ${p.score}`,
        onclick: () => { const ref = $("replay-ref"); ref.currentTime = p.t; ref.play().catch(() => {}); }
      })));
    }
  };

  // ---------- helpers ----------
  const MAX_UPLOAD_S = 7 * 60;   // longest clip you can upload

  // Thumbnail (first frame) + real length for a clip, read from its video file. Cached per URL.
  const PROBES = new Map();
  function probeClip(c, onReady) {
    if (!c.url) return {};
    let p = PROBES.get(c.url);
    if (p) return p;
    p = {};
    PROBES.set(c.url, p);
    const v = document.createElement("video");
    v.muted = true; v.preload = "auto"; v.playsInline = true; v.crossOrigin = "anonymous";
    v.onloadedmetadata = () => {
      p.duration = Number.isFinite(v.duration) ? v.duration : null;
      v.currentTime = Math.min(0.05, (v.duration || 1) / 2);   // first frame (a hair in, so it's decoded)
    };
    v.onseeked = () => {
      try {
        const w = 480, h = Math.round(w * (v.videoHeight / v.videoWidth || 9 / 16));
        const cv = document.createElement("canvas"); cv.width = w; cv.height = h;
        cv.getContext("2d").drawImage(v, 0, 0, w, h);
        p.thumb = cv.toDataURL("image/jpeg", 0.8);
      } catch (e) { /* video from another site: no thumbnail */ }
      v.removeAttribute("src"); v.load();
      onReady();
    };
    v.onerror = () => { p.failed = true; };
    v.src = c.url;
    return p;
  }
  const svgIcon = (d) => { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("width", "16"); s.setAttribute("height", "16"); s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("fill", "none"); s.setAttribute("stroke", "currentColor"); s.setAttribute("stroke-width", "2.2");
    s.setAttribute("stroke-linecap", "round"); s.setAttribute("stroke-linejoin", "round"); s.setAttribute("aria-hidden", "true");
    s.innerHTML = d; return s; };
  const pencilIcon = () => svgIcon('<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M14 6l4 4"/>');
  const trashIcon = () => svgIcon('<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 13h10l1-13"/><path d="M9 7V4h6v3"/>');
  function fmtDuration(s) { s = Math.round(s); return s < 60 ? `${s} s` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`; }

  // Upload bar: fill, % label, stage text and the 4 step dots
  function setProgress(bar, pct, stage) {
    pct = Math.max(0, Math.min(100, pct));
    bar.querySelector(".progress-fill").style.width = `${pct}%`;
    bar.querySelector(".progress").setAttribute("aria-valuenow", String(Math.round(pct)));
    $("upload-pct").textContent = `${Math.round(pct)}%`;
    if (stage) $("upload-stage").textContent = stage;
    const step = pct >= 100 ? 4 : pct >= 90 ? 3 : pct >= 55 ? 2 : pct >= 30 ? 1 : 0;
    bar.querySelectorAll(".upload-steps li").forEach((li, i) => {
      li.classList.toggle("done", i < step);
      li.classList.toggle("now", i === step);
    });
    if (stage) window.A11Y.announce(stage);
  }
  function uploadFailed(text) {
    const bar = $("upload-progress");
    bar.classList.add("failed");
    $("upload-stage").textContent = "Upload failed";
    setTimeout(() => { bar.hidden = true; bar.classList.remove("failed"); }, 1800);
    if (text) $("upload-status").textContent = text;
  }

  // Colour sweep between screens (skipped when reduced motion is on)
  function pageWipe() {
    const w = $("page-wipe");
    w.classList.remove("go");
    void w.offsetWidth;               // restart the animation
    w.classList.add("go");
  }

  // Clear the live score display (restart / try again)
  function clearLive() {
    $("score-num").textContent = "–";
    $("bar-fill").style.height = "0%";
    $("band-label").textContent = "Waiting";
    $("streak-num").textContent = "0 s";
    $("missing-list").replaceChildren();
    $("missing-list").dataset.key = "";
    document.body.dataset.band = "none";
    App.state.lastStreakPop = 0;
    App.state.cp = null;
  }
  function hideOverlays() { $("countdown-overlay").classList.remove("show"); $("banner-overlay").classList.remove("show"); }

  // Calibration screen: let the camera picture (and its dashed frame) be as big as
  // fits in the window WITHOUT scrolling. Sets --calib-max-h, used by the CSS block
  // in index.html. Called when the screen opens, on resize, and when the picture arrives.
  function fitCalibration() {
    const wrap = document.querySelector("#screen-calibrating .calib-wrap");
    if (!wrap || !wrap.offsetParent) return;               // screen isn't showing
    const top = wrap.getBoundingClientRect().top + window.scrollY;
    const h = Math.max(240, Math.floor(window.innerHeight - top - 24));
    document.documentElement.style.setProperty("--calib-max-h", `${h}px`);
  }
  // First camera frame arrived: hide the placeholder; the frame now wraps the picture.
  function markCameraReady(img) {
    const ph = img.parentElement.querySelector(".placeholder"); if (ph) ph.hidden = true;
    img.parentElement.classList.add("has-image");
    fitCalibration();
  }
  let popTimer = null;
  function showPop(text, opts = {}) {
    const p = $("pop");
    p.classList.toggle("catchphrase", !!opts.catchphrase);
    if (opts.catchphrase && opts.coach) {
      // clean badge: small avatar + the word, text colour picked for contrast on the coach colour
      const av = window.makeAvatar(opts.coach);
      av.classList.add("pop-avatar");
      p.replaceChildren(av, el("span", { class: "pop-word", text: text }));
      p.style.setProperty("--pop-bg", opts.color);
      p.style.setProperty("--pop-fg", readableOn(opts.color));
    } else {
      p.textContent = text;
    }
    p.classList.remove("show");
    void p.offsetWidth;                   // restart the animation
    p.classList.add("show");
    clearTimeout(popTimer);
    popTimer = setTimeout(() => p.classList.remove("show"), opts.ms || 900);
  }

  // white text, or dark text on very light coach colours
  function readableOn(hex) {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex || "");
    if (!m) return "#fff";
    const n = parseInt(m[1], 16), ch = [n >> 16, (n >> 8) & 255, n & 255].map((v) => {
      v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    const L = 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
    return L > 0.42 ? "#14121F" : "#fff";      // white looks cleaner on strong colours
  }


  function eyeOffIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("width", "18"); svg.setAttribute("height", "18"); svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none"); svg.setAttribute("stroke", "var(--amber)"); svg.setAttribute("stroke-width", "2");
    svg.setAttribute("aria-hidden", "true");
    ["M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12z", "M3 3l18 18"].forEach((d) => {
      const p = document.createElementNS(ns, "path"); p.setAttribute("d", d); svg.appendChild(p);
    });
    return svg;
  }
  function videoDuration(file) {
    return new Promise((resolve, reject) => {
      const v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = () => { URL.revokeObjectURL(v.src); resolve(v.duration); };
      v.onerror = reject;
      v.src = URL.createObjectURL(file);
    });
  }
  // Chrome blocks autoplay + speech until the user interacts once.
  let unlocked = false;
  function unlockMedia() {
    if (unlocked) return;
    unlocked = true;
    if ("speechSynthesis" in window) speechSynthesis.speak(new SpeechSynthesisUtterance(""));
  }

  // ---------- wiring ----------
  function wire() {
    document.addEventListener("pointerdown", unlockMedia, { once: true });

    // buttons with data-action
    document.addEventListener("click", (e) => {
      const b = e.target.closest("[data-action]");
      if (!b) return;
      const a = b.dataset.action;
      if (a === "start") App.start();
      else if (a === "reset") App.reset("reset");
      else if (a === "try-again") App.reset("try_again");
      else if (a === "restart") App.restart();
      else if (a === "back") { App.send("command", { name: "reset" }); App.show("chooser"); }
      else if (a === "replay-voice" && window.Report.last) window.Speech.speak(window.Report.speechFor(window.Report.last.feedback || {}), window.coachById(window.Report.last.coach_id), "report");
      else if (a === "stop-voice") window.Speech.stop();
      else if (a === "ask-panel") { window.Speech.stop(); App.send("command", { name: "ask_panel" }); $("panel-grid").replaceChildren(el("p", { class: "muted", text: "Gathering the panel…" })); App.show("panel"); }
      else if (a === "back-to-report") { window.Speech.stop(); App.show("report"); }
      else if (a === "show-replay") { window.Speech.stop(); App.send("command", { name: "get_replay" }); App.show("replay"); }
    });

    // upload: file picker + drag and drop
    $("upload-input").addEventListener("change", (e) => { const f = e.target.files[0]; if (f) App.upload(f); e.target.value = ""; });
    const card = $("upload-card");
    card.addEventListener("dragover", (e) => { e.preventDefault(); card.classList.add("dragover"); });
    card.addEventListener("dragleave", () => card.classList.remove("dragover"));
    card.addEventListener("drop", (e) => { e.preventDefault(); card.classList.remove("dragover"); const f = e.dataTransfer.files[0]; if (f) App.upload(f); });

    // tell Python the video clock ~10x a second
    const video = $("ref-video");
    setInterval(() => {
      if (App.state.screen !== "dancing" || App.state.fake) return;
      App.send("video_time", { t: video.currentTime, playing: !video.paused, rate: video.playbackRate });
    }, 100);
    video.addEventListener("ended", () => App.send("video_ended"));

    // body parts filter: little button shows/hides the options
    $("parts-filter-btn").addEventListener("click", () => window.Report.toggleFilter());

    // body parts filter buttons on the report
    document.querySelectorAll("#parts-mode .seg-btn").forEach((b) =>
      b.addEventListener("click", () => window.Report.setPartsMode(b.dataset.mode)));

    // speech -> captions / panel highlight
    window.Speech.onStart = (item) => {
      if (item.tag !== "report") window.Report.markSpeaking(item.tag);
      window.Report.onSpeechStart(item);          // start typing the caption
    };
    window.Speech.onBoundary = (item, i, len) => window.Report.onSpeechWord(item, i, len);
    window.Speech.onEnd = (item) => {
      if (item.tag !== "report") window.Report.markSpeaking(null);
      window.Report.onSpeechEnd(item);            // type whatever is left
    };
    window.Speech.onStop = () => window.Report.revealAllTyping();   // stopped early: show all text

    // webcam stream (real mode only)
    document.querySelectorAll("img.cam-feed").forEach((img) => {
      img.addEventListener("load", () => { if (img.dataset.live) markCameraReady(img); });
      img.addEventListener("error", () => {
        const ph = img.parentElement.querySelector(".placeholder"); if (ph) ph.hidden = false;
        img.parentElement.classList.remove("has-image");
      });
    });

    window.addEventListener("resize", fitCalibration);

    // keyboard shortcuts: Space start, M mirror, P auto-pause, R reset, D debug
    document.addEventListener("keydown", (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (["input", "textarea", "select"].includes(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === " " && !["button", "a", "summary"].includes(tag)) {
        if (App.state.screen === "chooser") { e.preventDefault(); App.start(); }
      } else if (k === "m") {
        App.state.mirror = !App.state.mirror;
        App.send("command", { name: "mirror", value: App.state.mirror });
        showPop(App.state.mirror ? "MIRROR ON" : "MIRROR OFF");
      } else if (k === "p") {
        App.state.autoPause = !App.state.autoPause;
        App.send("command", { name: "auto_pause", value: App.state.autoPause });
        showPop(App.state.autoPause ? "AUTO-PAUSE ON" : "AUTO-PAUSE OFF");
      } else if (k === "r") {
        App.reset("reset");
      } else if (k === "d") {
        $("debug").classList.toggle("show");
      }
    });

    App.initSpeedSlider();
    App.renderCoaches();
    App.setSpeed(SPEED.default, false);
    App.chooseCoach(App.state.coachId);
  }

  window.App = App;
  App.setUploadProgress = (pct, stage) => setProgress($("upload-progress"), pct, stage);   // used by fake.js
  wire();
})();
