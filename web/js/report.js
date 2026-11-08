// Report + panel rendering.
// Input shapes (see server/messages.md — keep in sync):
//
//  report: { type:"report", coach_id, feedback, details }
//  panel:  { type:"panel", coaches:[{coach_id, feedback}], details }
//
//  feedback = the v3 LLM output JSON (headline, overall_score, component_feedback,
//             body_part_feedback, strengths, priority_fixes, next_practice_plan, data_caveat)
//  details  = the numbers Python sent to the LLM (scores, sections, body_part_scores,
//             data_quality) plus playback_rate. Used for the "Detailed breakdown".
//
// All text from the LLM is inserted with textContent (never innerHTML).

(function () {
  const WEIGHTS = { position: 75, timing: 12.5, sharpness: 6.25, holds: 6.25 };
  const COMP_NAMES = { position: "Position", timing: "Timing", sharpness: "Sharpness", holds: "Holds" };
  const STATUS_TEXT = { strong: "Strong", good: "Good", improving: "Improving", needs_work: "Needs work", unavailable: "Unavailable" };

  // tiny DOM helper
  function el(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "style") n.setAttribute("style", v);
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      n.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return n;
  }
  const fmt = (v, suffix = "") => (v === null || v === undefined ? "Unavailable" : `${v}${suffix}`);
  const band = (s) => (s === null || s === undefined ? "none" : s >= 80 ? "green" : s >= 60 ? "amber" : "red");
  const prettyPart = (p) => String(p || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  const Report = {
    last: null,        // last report message
    lastPanel: null,

    // Text the coach speaks (also the caption)
    // The caption pieces, in speaking order. Joined with spaces they are exactly what the coach says.
    captionParts(feedback) {
      const parts = [feedback.headline, feedback.score_summary];
      const fix = (feedback.priority_fixes || [])[0];
      if (fix) parts.push(`${fix.what_happened} ${fix.drill}`);
      return parts.filter(Boolean);
    },

    speechFor(feedback) {
      return this.captionParts(feedback).join(" ");
    },

    // ---- typing captions (kept in step with the voice) ----
    typers: {},                      // tag -> Typer ("report" or a coach id on the panel)

    makeReportTyper(fb) {
      const [head, ...rest] = this.captionParts(fb);
      const more = rest.map(() => el("p"));
      document.getElementById("rep-caption-more").replaceChildren(...more);
      const segs = [{ el: document.getElementById("rep-caption"), text: head || "" }]
        .concat(rest.map((text, i) => ({ el: more[i], text })));
      if (this.typers.report) this.typers.report.stop();
      this.typers.report = new window.Typer(segs);
    },

    onSpeechStart(item) {
      if (item.tag === "report" && this.last) this.makeReportTyper(this.last.feedback || {});
      const t = this.typers[item.tag];
      if (t) {
        if (item.tag !== "report") { t.pos = t.target = 0; t.synced = false; t.finishing = false; t.render(); }
        t.start(item.coach && item.coach.voice ? item.coach.voice.rate : 1);
      }
    },
    onSpeechWord(item, i, len) { const t = this.typers[item.tag]; if (t) t.word(i, len); },
    onSpeechEnd(item) { const t = this.typers[item.tag]; if (t) t.finish(); },
    revealAllTyping() { Object.values(this.typers).forEach((t) => t.revealAll()); },

    render(msg) {
      this.last = msg;
      const fb = msg.feedback || {};
      const det = msg.details || {};
      const coach = window.coachById(msg.coach_id);

      // coach strip
      window.fillAvatar(document.getElementById("rep-avatar"), coach);
      document.getElementById("rep-speaking").textContent = `${coach.name} · ${coach.role}`;
      // big one-liner, then the rest in smaller paragraphs, typed as the coach speaks
      this.makeReportTyper(fb);

      // overall
      document.getElementById("rep-overall").textContent = fmt(fb.overall_score);
      const dq = det.data_quality || {};
      const meta = [];
      if (dq.tracked_frame_percentage != null) meta.push(`${dq.tracked_frame_percentage}% of frames tracked`);
      meta.push(`${det.playback_rate || 1}× speed`);
      document.getElementById("rep-meta").textContent = "";   // tracking % and speed live in the breakdown
      document.getElementById("rep-summary").textContent = fb.score_summary || "";

      // components
      const comps = document.getElementById("rep-components");
      comps.replaceChildren(...(fb.component_feedback || []).map((c) =>
        el("div", { class: "comp" },
          el("div", { class: "comp-head" },
            el("span", { style: "font-size: var(--fs-lg); font-weight:600", text: COMP_NAMES[c.component] || c.component }),
            el("span", { class: "pill", "data-status": c.status, text: STATUS_TEXT[c.status] || c.status })),
          el("span", { class: "comp-score", text: fmt(c.score) }),
          el("span", { class: "comp-summary", text: c.summary || "" }))
      ));

      // body parts: filter control (max 4 when sorted, or the ones you choose)
      this.parts = fb.body_part_feedback || [];
      this.picked = null;
      this.toggleFilter(false);          // each new report starts on the default view

      // priority fixes
      const fixes = fb.priority_fixes || [];
      document.getElementById("rep-fixes").replaceChildren(...(fixes.length ? fixes.map((f) =>
        el("article", { class: "fix" },
          el("div", { class: "fix-head" },
            el("span", { class: "fix-rank", text: f.rank }),
            el("div", { style: "display:flex; flex-direction:column; gap:2px" },
              el("span", { class: "fix-title", text: f.title }),
              el("span", { class: "muted", style: "font-size: var(--fs-sm)", text: f.evidence || `Section ${f.section}` }))),
          el("span", { style: "color: var(--text-soft)", text: f.what_happened }),
          el("div", { class: "drill" }, el("strong", { text: "Drill: " }), f.drill))
      ) : [el("p", { class: "muted", text: "No clear issues to fix — nice work." })]));

      // caveat
      const cav = document.getElementById("rep-caveat");
      cav.replaceChildren(fb.data_caveat ? el("div", { class: "caveat", role: "note", text: fb.data_caveat }) : "");

      // other coaches links
      const others = window.COACHES.filter((c) => c.id !== coach.id);
      document.getElementById("rep-other-coaches").replaceChildren(
        "Hear from another coach: ",
        ...others.flatMap((c, i) => [
          i ? " · " : "",
          el("a", { href: "#", onclick: (e) => { e.preventDefault(); window.App.send("command", { name: "choose_coach", value: c.id, rerun_report: true }); }, text: c.name })
        ])
      );

      this.renderBreakdown(fb, det);
    },

    // ---- body parts filter ----
    partsMode: "low",          // "low" (needs most work) | "high" (best first) | "pick"
    picked: null,              // Set of body_part keys chosen in "pick" mode
    MAX_PARTS: 4,

    MODE_LABELS: { low: "lowest first", high: "highest first", pick: "your choice" },

    setPartsMode(mode) {
      this.partsMode = mode;
      document.querySelectorAll("#parts-mode .seg-btn").forEach((b) =>
        b.setAttribute("aria-pressed", String(b.dataset.mode === mode)));
      document.getElementById("parts-mode-label").textContent = this.MODE_LABELS[mode];
      this.renderParts();
    },

    // Filter options are hidden until the little filter button is clicked.
    // Closed = default view: lowest scores first, max 4.
    toggleFilter(open) {
      const btn = document.getElementById("parts-filter-btn");
      const panel = document.getElementById("parts-filter");
      open = open === undefined ? btn.getAttribute("aria-expanded") !== "true" : open;
      btn.setAttribute("aria-expanded", String(open));
      panel.hidden = !open;
      if (!open) this.setPartsMode("low");
    },

    renderParts() {
      const all = this.parts || [];
      const scored = all.filter((p) => p.score != null);
      const pickBox = document.getElementById("parts-pick");
      pickBox.hidden = this.partsMode !== "pick";

      // default selection = the 4 that need most work
      const keys = new Set(all.map((p) => p.body_part));
      if (!this.picked || ![...this.picked].some((k) => keys.has(k))) {
        this.picked = new Set(scored.slice().sort((a, b) => a.score - b.score)
          .slice(0, this.MAX_PARTS).map((p) => p.body_part));
      }

      let shown;
      if (this.partsMode === "pick") {
        pickBox.replaceChildren(...all.map((p) => {
          const id = `pick-${p.body_part}`;
          const box = el("input", { type: "checkbox", id, class: "sr-only",
            onchange: (e) => { e.target.checked ? this.picked.add(p.body_part) : this.picked.delete(p.body_part); this.renderParts(); } });
          box.checked = this.picked.has(p.body_part);
          return el("span", { class: "pick-chip" }, box, el("label", { for: id, text: prettyPart(p.body_part) }));
        }));
        shown = all.filter((p) => this.picked.has(p.body_part));     // all selected, no limit
      } else {
        const dir = this.partsMode === "high" ? -1 : 1;
        shown = scored.slice().sort((a, b) => dir * (a.score - b.score)).slice(0, this.MAX_PARTS);
      }

      document.getElementById("rep-parts").replaceChildren(...(shown.length ? shown.map((p) =>
        el("div", { class: "part-row" },
          el("span", { class: "part-name", text: prettyPart(p.body_part) }),
          el("div", { class: "part-track", "aria-hidden": "true" },
            el("div", { class: "part-fill", "data-band": band(p.score), style: `width:${p.score || 0}%` })),
          el("span", { class: "part-val", text: p.score == null ? "Unavailable" : `${p.score} / 100` }))
      ) : [el("p", { class: "muted", style: "margin:0", text: this.partsMode === "pick" ? "Pick body parts above to see them here." : "No body part scores yet." })]));
    },

    renderBreakdown(fb, det) {
      const body = document.getElementById("breakdown-body");
      const scores = det.scores || {};
      const blocks = [];

      // 1. how the overall score is made
      blocks.push(el("div", {},
        el("h3", { text: "How your overall score is made" }),
        el("div", { class: "table-scroll" }, el("table", { class: "data" },
          el("thead", {}, el("tr", {}, el("th", { text: "Component" }), el("th", { text: "Score" }), el("th", { text: "Weight" }), el("th", { text: "What it measures" }))),
          el("tbody", {}, ...Object.keys(WEIGHTS).map((k) => {
            const cf = (fb.component_feedback || []).find((c) => c.component === k) || {};
            return el("tr", {},
              el("td", { text: COMP_NAMES[k] }),
              el("td", { text: fmt(scores[k] ?? cf.score) }),
              el("td", { text: `${WEIGHTS[k]}%` }),
              el("td", { text: cf.summary || "" }));
          }))))));

      // 2. section by section
      const secs = det.sections || [];
      if (secs.length) {
        blocks.push(el("div", {},
          el("h3", { text: "Section by section" }),
          el("div", { class: "table-scroll" }, el("table", { class: "data" },
            el("thead", {}, el("tr", {}, ...["Section", "Time", "Overall", "Position", "Timing", "Sharpness", "Holds", "Weakest joint", "Angle diff", "Arrival offset", "Confidence"].map((h) => el("th", { scope: "col", text: h })))),
            el("tbody", {}, ...secs.map((s) => el("tr", {},
              el("td", { text: s.section }),
              el("td", { text: `${s.start_s}–${s.end_s} s` }),
              el("td", { text: fmt(s.overall) }),
              el("td", { text: fmt(s.position) }),
              el("td", { text: fmt(s.timing) }),
              el("td", { text: fmt(s.sharpness) }),
              el("td", { text: fmt(s.holds) }),
              el("td", { text: prettyPart(s.weakest_joint) || "–" }),
              el("td", { text: fmt(s.mean_angle_error_deg, "°") }),
              el("td", { text: s.arrival_offset_ms == null ? "Unavailable" : `${s.arrival_offset_ms > 0 ? "+" : ""}${s.arrival_offset_ms} ms` }),
              el("td", { text: s.confidence == null ? "–" : `${Math.round(s.confidence * 100)}%` }))))))));
      }

      // 2b. coach's notes per section (from Claude, via to_claude.py)
      const notes = fb.section_notes || [];
      if (notes.length) {
        blocks.push(el("div", {},
          el("h3", { text: "Coach's notes by section" }),
          el("ul", { style: "margin:0; padding-left:20px; display:flex; flex-direction:column; gap:8px" },
            ...notes.map((n) => el("li", {}, el("strong", { text: `Section ${n.section}: ` }), n.note)))));
      }

      // 3. every body part with evidence
      const bps = det.body_part_scores || {};
      const bpKeys = Object.keys(bps);
      if (bpKeys.length) {
        blocks.push(el("div", {},
          el("h3", { text: "Every body part" }),
          el("div", { class: "table-scroll" }, el("table", { class: "data" },
            el("thead", {}, el("tr", {}, ...["Body part", "Score", "Avg angle diff", "Confidence", "Samples"].map((h) => el("th", { scope: "col", text: h })))),
            el("tbody", {}, ...bpKeys.map((k) => {
              const b = bps[k];
              return el("tr", {},
                el("td", { text: prettyPart(k) }),
                el("td", { text: fmt(b.score) }),
                el("td", { text: fmt(b.mean_angle_error_deg, "°") }),
                el("td", { text: b.confidence == null ? "–" : `${Math.round(b.confidence * 100)}%` }),
                el("td", { text: b.tracked_samples ?? "–" }));
            }))))));
      }

      // 4. strengths + plan
      const strengths = fb.strengths || [];
      const plan = fb.next_practice_plan;
      if (strengths.length || plan) {
        blocks.push(el("div", { class: "report-cols", style: "gap:24px" },
          strengths.length ? el("div", { style: "flex:1 1 300px" },
            el("h3", { text: "What went well" }),
            el("ul", { style: "margin:0; padding-left:20px; display:flex; flex-direction:column; gap:8px" },
              ...strengths.map((s) => el("li", {}, el("strong", { text: s.title + ": " }), s.detail)))) : "",
          plan ? el("div", { style: "flex:1 1 300px" },
            el("h3", { text: "Next practice plan" }),
            el("ol", { style: "margin:0; padding-left:20px; display:flex; flex-direction:column; gap:8px" },
              plan.first ? el("li", { text: plan.first }) : "",
              plan.second ? el("li", { text: plan.second }) : ""),
            plan.success_target ? el("p", { class: "muted", text: `Goal: ${plan.success_target}` }) : "") : ""));
      }

      // 5. data quality
      const dq = det.data_quality || {};
      blocks.push(el("div", {},
        el("h3", { text: "Data quality" }),
        el("div", { class: "kv" },
          el("div", {}, el("strong", { text: fmt(dq.tracked_frame_percentage, "%") }), "frames tracked"),
          el("div", {}, el("strong", { text: (dq.valid_sections || []).length }), "sections scored"),
          el("div", {}, el("strong", { text: (dq.excluded_sections || []).length }), "sections excluded"),
          el("div", {}, el("strong", { text: fmt(dq.timing_events_detected) }), "timing events"),
          el("div", {}, el("strong", { text: fmt(dq.hold_events_detected) }), "hold events"),
          el("div", {}, el("strong", { text: `${det.playback_rate || 1}×` }), "practice speed"))));

      body.replaceChildren(...blocks);
    },

    renderPanel(msg) {
      this.lastPanel = msg;
      const det = msg.details || {};
      const s = det.scores || {};
      document.getElementById("panel-scores").replaceChildren(
        el("span", { class: "chip", style: "font-weight:600", text: `${fmt(s.overall)} / 100 overall` }),
        el("span", { class: "chip", text: `Position ${fmt(s.position)} · Timing ${fmt(s.timing)} · Sharpness ${fmt(s.sharpness)} · Holds ${fmt(s.holds)}` }));

      const items = (msg.coaches || []).map((c) => ({ coach: window.coachById(c.coach_id), fb: c.feedback || {} }));
      const grid = document.getElementById("panel-grid");
      grid.replaceChildren(...items.map(({ coach, fb }) => {
        const fix = (fb.priority_fixes || [])[0];
        return el("article", { class: "panel-card", "data-coach": coach.id },
          el("div", { class: "coach-head" },
            window.makeAvatar(coach),
            el("div", { style: "display:flex; flex-direction:column; gap:2px" },
              el("span", { class: "coach-name", text: coach.name }),
              el("span", { class: "muted", style: "font-size: var(--fs-sm)", text: coach.role }))),
          el("span", { class: "speaking-pill", hidden: "", text: "Speaking now" }),
          el("p", { class: "panel-headline", style: "margin:0" }, "“", el("span", { class: "panel-quote" }), "”"),
          fix ? el("div", { class: "drill" },
            el("span", { class: "label", style: "color: var(--accent-soft); display:block; margin-bottom:4px", text: "#1 fix" }),
            fix.drill) : "",
          el("button", { class: "btn btn-ghost", onclick: () => this.speakPanel([{ coach, fb }]), text: `Play ${coach.name}` }));
      }));
      // set up a typer per coach card (empty until that coach speaks)
      Object.keys(this.typers).forEach((k) => { if (k !== "report") { this.typers[k].stop(); delete this.typers[k]; } });
      items.forEach(({ coach, fb }) => {
        const span = grid.querySelector(`.panel-card[data-coach="${coach.id}"] .panel-quote`);
        if (span) this.typers[coach.id] = new window.Typer([{ el: span, text: fb.headline || "" }]);
      });
      this.speakPanel(items);
    },

    speakPanel(items) {
      window.Speech.speakAll(items.map(({ coach, fb }) => ({ text: fb.headline || "", coach, tag: coach.id })));
    },

    markSpeaking(coachId) {
      document.querySelectorAll(".panel-card").forEach((card) => {
        const on = card.dataset.coach === coachId;
        card.classList.toggle("speaking", on);
        const pill = card.querySelector(".speaking-pill");
        if (pill) pill.hidden = !on;
      });
    }
  };

  window.Report = Report;
  window.el = el;
})();
