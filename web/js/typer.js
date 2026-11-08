// Typing effect for the coach's words, kept in step with the voice.
//
//   const t = new Typer([{ el, text }, { el, text }]);   // segments, joined by spaces = the spoken text
//   t.start(rate)          when the voice starts
//   t.word(charIndex, len) on each spoken word (Web Speech "boundary" event) -> exact sync
//   t.finish()             when the voice ends -> types the rest quickly
//   t.revealAll()          show everything now (stop button, reduced motion)
//
// If the voice never reports words (some Chrome voices don't), it types at speaking speed.

(function () {
  const CHARS_PER_SEC = 15;          // about how fast the browser voice talks at rate 1
  const reduceMotion = () =>
    window.matchMedia("(prefers-reduced-motion: reduce)").matches ||
    document.documentElement.dataset.a11yReduceMotion === "on";

  class Typer {
    constructor(segments) {
      this.segs = segments.filter((s) => s.el && s.text);
      let off = 0;
      this.segs.forEach((s) => { s.start = off; off += s.text.length + 1; });   // +1 = joining space
      this.total = Math.max(0, off - 1);
      this.pos = 0;
      this.target = 0;
      this.synced = false;           // true once real word timings arrive
      this.finishing = false;
      this.raf = null;
      this.render();
    }

    start(rate = 1) {
      if (reduceMotion()) return this.revealAll();
      this.rate = rate || 1;
      this.t0 = performance.now();
      this.last = this.t0;
      if (!this.raf) this.raf = requestAnimationFrame((t) => this.tick(t));
    }

    word(charIndex, len) {
      this.synced = true;
      const end = Math.min(this.total, charIndex + (len || this.wordLength(charIndex)));
      this.target = Math.max(this.target, end);
    }

    wordLength(i) {
      const full = this.segs.map((s) => s.text).join(" ");
      const m = /^\S+/.exec(full.slice(i));
      return m ? m[0].length : 1;
    }

    finish() {
      this.finishing = true;
      this.target = this.total;
      if (!this.raf && this.pos < this.total) this.raf = requestAnimationFrame((t) => this.tick(t));
    }

    revealAll() {
      this.stop();
      this.pos = this.target = this.total;
      this.render();
    }

    stop() {
      if (this.raf) cancelAnimationFrame(this.raf);
      this.raf = null;
    }

    tick(now) {
      const dt = Math.min(0.1, (now - this.last) / 1000);
      this.last = now;
      if (!this.synced && !this.finishing) {
        // no word timings: estimate where the voice is
        this.target = Math.min(this.total, ((now - this.t0) / 1000) * CHARS_PER_SEC * this.rate);
      }
      const gap = this.target - this.pos;
      const speed = this.finishing ? 140 : Math.max(30, gap * 10);   // chars/sec, catch up smoothly
      this.pos = Math.min(this.target, this.pos + speed * dt);
      this.render();
      if (this.pos >= this.total) { this.raf = null; return; }
      this.raf = requestAnimationFrame((t) => this.tick(t));
    }

    render() {
      const p = Math.floor(this.pos);
      const typing = p < this.total;
      let cursorPlaced = false;
      this.segs.forEach((s) => {
        const n = Math.max(0, Math.min(s.text.length, p - s.start));
        s.el.textContent = s.text.slice(0, n);
        // blinking cursor sits on the segment being typed
        const here = typing && !cursorPlaced && n < s.text.length;
        s.el.classList.toggle("typing", here);
        if (here) cursorPlaced = true;
        // screen readers get the full text, not every letter
        s.el.setAttribute("aria-label", s.text);
      });
    }
  }

  window.Typer = Typer;
})();
