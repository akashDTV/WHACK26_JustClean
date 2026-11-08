// Live Dance Coach — main page logic.
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
      this.state.clips.forEach((c) => {
        const ok = c.ref_ok !== false;
        const card = el("button", {
          class: "card clip-card", "aria-pressed": String(this.state.clipId === c.id),
          onclick: () => this.chooseClip(c.id)
        },
          el("div", { class: "thumb" }, c.thumb ? el("img", { src: c.thumb, alt: "" }) : "[CLIP THUMBNAIL]"),
          el("div", { class: "clip-title" },
            el("strong", { text: c.name }),
            el("span", { class: "muted", text: c.duration_s ? `${Math.round(c.duration_s)} s` : "" })),
          !ok || c.warning ? el("span", { class: "clip-warning", text: c.warning || "Reference data missing — can't use this clip yet" }) : "");
        list.insertBefore(card, upload);
      });
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
          el("div", { class: "avatar", style: `background:${c.color}`, "aria-hidden": "true", text: c.initial }),
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
      this.send("command", { name: "start", clip: this.state.clipId, speed: this.state.rate, coach: this.state.coachId });
    },

    reset(name = "reset") {
      window.Speech.stop();
      hideOverlays();
      const v = $("ref-video");
      v.pause();
      v.currentTime = 0;
      this.send("command", { name });
      this.show("chooser");
    },

    // ---------- upload ----------
    async upload(file) {
      const status = $("upload-status");
      const bar = $("upload-progress");
      const okType = /video\/(mp4|webm|quicktime)/.test(file.type);
      if (!okType) { status.textContent = "Please choose an MP4, WebM or MOV file."; return; }
      const dur = await videoDuration(file).catch(() => null);
      if (dur && dur > 31) { status.textContent = `That clip is ${Math.round(dur)} s — please trim it to 30 s or less.`; return; }

      bar.hidden = false;
      setProgress(bar, 0);
      status.textContent = "Uploading…";

      if (this.state.fake) { window.Fake.upload(file); return; }

      const xhr = new XMLHttpRequest();
      const form = new FormData();
      form.append("file", file);
      xhr.upload.onprogress = (e) => { if (e.lengthComputable) setProgress(bar, (e.loaded / e.total) * 30); };
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) status.textContent = "Tracking the dancer… this takes a few seconds";
        else { status.textContent = "Upload failed — try again."; bar.hidden = true; }
      };
      xhr.onerror = () => { status.textContent = "Upload failed — is the server running?"; bar.hidden = true; };
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

      // pops: Python can send m.pop; otherwise pop every 5 s of green streak
      if (m.pop) showPop(m.pop);
      else if (streak >= this.state.lastStreakPop + 5) { this.state.lastStreakPop += 5; showPop("CLEAN!"); }
      if (streak < 1) this.state.lastStreakPop = 0;

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
      setProgress(bar, 30 + (m.percent || 0) * 0.7);
      $("upload-status").textContent = m.stage || "Processing…";
    },

    upload_result(m) {
      $("upload-progress").hidden = true;
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
  function setProgress(bar, pct) { bar.firstElementChild.style.width = `${Math.max(0, Math.min(100, pct))}%`; }
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
  function showPop(text) {
    const p = $("pop");
    p.textContent = text;
    p.classList.add("show");
    clearTimeout(popTimer);
    popTimer = setTimeout(() => p.classList.remove("show"), 900);
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

    // speech -> captions / panel highlight
    window.Speech.onStart = (item) => {
      if (item.tag === "report") $("rep-caption").textContent = item.text;
      else window.Report.markSpeaking(item.tag);
    };
    window.Speech.onEnd = (item) => { if (item.tag !== "report") window.Report.markSpeaking(null); };

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
  wire();
})();
