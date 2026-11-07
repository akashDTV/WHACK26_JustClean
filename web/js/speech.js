// Coach voice using the browser's Web Speech API.
// Captions are always shown on screen; speech is the extra layer.

window.Speech = {
  queue: [],
  speaking: false,
  onStart: null,   // (item) => void
  onEnd: null,     // (item) => void

  available() { return "speechSynthesis" in window; },

  // Speak a list of {text, coach, tag}. Plays one after another.
  speakAll(items) {
    this.stop();
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
      setTimeout(() => { if (this.onEnd) this.onEnd(item); this._next(); }, 2500);
      return;
    }
    const u = new SpeechSynthesisUtterance(item.text);
    const v = (item.coach && item.coach.voice) || {};
    u.rate = v.rate || 1;
    u.pitch = v.pitch || 1;
    if (v.voiceName) {
      const match = speechSynthesis.getVoices().find((x) => x.name === v.voiceName);
      if (match) u.voice = match;
    }
    u.onend = () => { if (this.onEnd) this.onEnd(item); this._next(); };
    u.onerror = u.onend;
    speechSynthesis.speak(u);
  },

  stop() {
    this.queue = [];
    this.speaking = false;
    if (this.available()) speechSynthesis.cancel();
  }
};
