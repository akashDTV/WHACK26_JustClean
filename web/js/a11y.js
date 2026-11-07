// Accessibility hooks — deliberately small so the team can extend it.
//
// Already built in elsewhere:
//   - semantic <section>/<button>/<label>, visible focus rings
//   - colour bands also change glow thickness + have text labels
//   - aria-live regions for banners, countdown, missing limbs, calibration
//   - captions for everything the coach says
//   - prefers-reduced-motion respected in style.css
//
// TODO (add later): settings panel UI, high-contrast theme,
// larger-text toggle, sound cues for bands, haptics, etc.
// Each setting just flips a data-attribute on <html> that style.css can target.

window.A11Y = {
  settings: {
    reduceMotion: false,   // html[data-a11y-reduce-motion="on"]
    highContrast: false,   // html[data-a11y-high-contrast="on"]  (no styles yet)
    textScale: 1,          // sets --text-scale
    captions: true,        // show coach captions
    speakScore: false      // future: periodic spoken score updates
  },

  // Politely announce something to screen readers.
  announce(text) {
    const el = document.getElementById("announcer");
    if (!el) return;
    el.textContent = "";
    // small delay so repeated identical messages are still read
    setTimeout(() => { el.textContent = text; }, 50);
  },

  set(key, value) {
    this.settings[key] = value;
    this.apply();
    try { localStorage.setItem("a11y", JSON.stringify(this.settings)); } catch (e) {}
  },

  apply() {
    const s = this.settings, html = document.documentElement;
    html.dataset.a11yReduceMotion = s.reduceMotion ? "on" : "off";
    html.dataset.a11yHighContrast = s.highContrast ? "on" : "off";
    html.style.setProperty("--text-scale", String(s.textScale));
  },

  load() {
    try {
      const saved = JSON.parse(localStorage.getItem("a11y") || "null");
      if (saved) Object.assign(this.settings, saved);
    } catch (e) {}
    this.apply();
  }
};

window.A11Y.load();
