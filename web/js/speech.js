// Coach voice using the browser's Web Speech API.
// Captions are always shown on screen; speech is the extra layer.

window.Speech = {
  queue: [],
  speaking: false,
  onStart: null,   // (item) => void
  onEnd: null,     // (item) => void
  onBoundary: null, // (item, charIndex, charLength) => void   each spoken word
  onStop: null,     // () => void   speech was stopped early

  available() { return "speechSynthesis" in window; },

  // Speak a list of {text, coach, tag}. Plays one after another.
  speakAll(items) {
    this.stop(true);                   // replacing, not stopping: no onStop
    this.queue = items.slice();
    this._next();
  },

  speak(text, coach, tag) { this.speakAll([{ text, coach, tag }]); },

  _next() {
    const item = this.queue.shift();
    if (!item) { this.speaking = false; return; }
    this.speaking = true;
    if (this.onStart) this.onStart(item);
    if (!this.available()) {           // no voice: still advance captions
      const gen = this.gen;
      setTimeout(() => { if (gen !== this.gen) return; if (this.onEnd) this.onEnd(item); this._next(); }, Math.max(2500, item.text.length * 60));
      return;
    }
    const gen = this.gen;              // ignore late events from a cancelled utterance
    const u = new SpeechSynthesisUtterance(item.text);
    const v = (item.coach && item.coach.voice) || {};
    u.rate = v.rate || 1;
    u.pitch = v.pitch || 1;
    if (v.voiceName) {
      const match = speechSynthesis.getVoices().find((x) => x.name === v.voiceName);
      if (match) u.voice = match;
    }
    u.onboundary = (e) => { if (gen === this.gen && e.name === "word" && this.onBoundary) this.onBoundary(item, e.charIndex, e.charLength); };
    u.onend = () => { if (gen !== this.gen) return; if (this.onEnd) this.onEnd(item); this._next(); };
    u.onerror = u.onend;
    try { speechSynthesis.speak(u); }
    catch (err) {                      // voice broken: keep captions moving anyway
      setTimeout(() => { if (gen !== this.gen) return; if (this.onEnd) this.onEnd(item); this._next(); }, Math.max(2500, item.text.length * 60));
    }
  },

  stop(replacing = false) {
    const wasBusy = this.speaking || this.queue.length;
    this.gen = (this.gen || 0) + 1;
    this.queue = [];
    if (wasBusy && !replacing && this.onStop) this.onStop();
    this.speaking = false;
    if (this.available()) speechSynthesis.cancel();
  }
};
