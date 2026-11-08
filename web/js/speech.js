// Coach voice.
// 1st choice: the ElevenLabs mp3 the server sent with the feedback (feedback.audio), so each
//             coach has their own real voice.
// Fallback:   the browser's Web Speech API (same voice for everyone, only rate/pitch differ),
//             used when there's no audio for that text or it won't play.
// Captions are always shown on screen; speech is the extra layer.

window.Speech = {
  queue: [],
  speaking: false,
  onStart: null,    // (item) => void
  onEnd: null,      // (item) => void
  onBoundary: null, // (item, charIndex, charLength) => void   each spoken word
  onStop: null,     // () => void   speech was stopped early

  audioByText: new Map(),   // normalised text -> {mime, b64, voice}
  current: null,            // the <audio> element playing now, if any

  available() { return "speechSynthesis" in window; },

  _key(text) { return String(text || "").replace(/\s+/g, " ").trim(); },

  // Remember a server-made clip: feedback.audio = {mime, b64, voice, text}. Safe to pass null.
  addAudio(audio) {
    if (audio && audio.b64 && audio.text) this.audioByText.set(this._key(audio.text), audio);
  },

  // Speak a list of {text, coach, tag}. Plays one after another.
  speakAll(items) {
    this.stop(true);                   // replacing, not stopping: no onStop
    this.queue = items.slice();
    this._next();
  },

  speak(text, coach, tag) { this.speakAll([{ text, coach, tag }]); },

  _finish(item, gen) {
    if (gen !== this.gen) return;      // ignore late events from a cancelled item
    this.current = null;
    if (this.onEnd) this.onEnd(item);
    this._next();
  },

  _next() {
    const item = this.queue.shift();
    if (!item) { this.speaking = false; return; }
    this.speaking = true;
    if (this.onStart) this.onStart(item);
    const gen = this.gen;
    const clip = item.audio || this.audioByText.get(this._key(item.text));
    if (clip) this._playClip(item, clip, gen);
    else this._browserVoice(item, gen);
  },

  // ---- ElevenLabs clip ----
  _playClip(item, clip, gen) {
    const audio = new Audio(`data:${clip.mime || "audio/mpeg"};base64,${clip.b64}`);
    this.current = audio;

    // The mp3 has no word events, so estimate them from playback progress
    // to keep the typing captions in step with the voice.
    const words = [...String(item.text).matchAll(/\S+/g)].map((m) => [m.index, m[0].length]);
    let sent = 0;
    const tick = () => {
      if (gen !== this.gen || !audio.duration || !isFinite(audio.duration)) return;
      const due = Math.min(words.length, Math.floor((audio.currentTime / audio.duration) * words.length) + 1);
      for (; sent < due; sent++) if (this.onBoundary) this.onBoundary(item, words[sent][0], words[sent][1]);
    };
    audio.ontimeupdate = tick;
    audio.onended = () => {             // send any words not reached yet, then move on
      if (gen === this.gen) for (; sent < words.length; sent++) if (this.onBoundary) this.onBoundary(item, words[sent][0], words[sent][1]);
      this._finish(item, gen);
    };
    audio.onerror = () => {             // bad clip: say it with the browser voice instead
      if (gen !== this.gen) return;
      console.warn("Coach audio failed to load; using the browser voice.");
      this._browserVoice(item, gen);
    };
    audio.play().catch((err) => {     // e.g. autoplay blocked before the first click
      if (gen !== this.gen) return;
      console.warn("Coach audio couldn't play (" + err.name + "); using the browser voice.");
      this.current = null;
      this._browserVoice(item, gen);
    });
  },

  // ---- browser voice (fallback) ----
  _browserVoice(item, gen) {
    if (!this.available()) {           // no voice: still advance captions
      setTimeout(() => this._finish(item, gen), Math.max(2500, item.text.length * 60));
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
    u.onboundary = (e) => { if (gen === this.gen && e.name === "word" && this.onBoundary) this.onBoundary(item, e.charIndex, e.charLength); };
    u.onend = () => this._finish(item, gen);
    u.onerror = u.onend;
    try { speechSynthesis.speak(u); }
    catch (err) {                      // voice broken: keep captions moving anyway
      setTimeout(() => this._finish(item, gen), Math.max(2500, item.text.length * 60));
    }
  },

  stop(replacing = false) {
    const wasBusy = this.speaking || this.queue.length;
    this.gen = (this.gen || 0) + 1;
    this.queue = [];
    if (this.current) { this.current.pause(); this.current = null; }
    if (wasBusy && !replacing && this.onStop) this.onStop();
    this.speaking = false;
    if (this.available()) speechSynthesis.cancel();
  }
};