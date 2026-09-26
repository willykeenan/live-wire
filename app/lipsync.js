/* Live Wire — Lip-sync
 *
 * Turns ElevenLabs per-character timestamps into mouth-shape (viseme) swaps,
 * scheduled against the <audio> clock. If no alignment is available, falls back
 * to a Web-Audio amplitude meter (3-level openness). Either way it only ever
 * calls avatar.setMouth(viseme), so the avatar engine stays mode-agnostic.
 */
(function (global) {
  "use strict";

  // Lowercased single character -> viseme. Anything unlisted -> REST.
  const CHAR_VISEME = {
    a: "AI", i: "AI",
    e: "E",
    o: "O",
    u: "WQ", w: "WQ",
    m: "MBP", b: "MBP", p: "MBP",
    f: "FV", v: "FV",
    l: "L",
    r: "E", s: "E", t: "E", d: "E", n: "E", k: "E", g: "E", c: "E",
    h: "E", j: "E", z: "E", q: "WQ", x: "E", y: "E",
    " ": "REST", "\n": "REST", "\t": "REST",
    ".": "REST", ",": "REST", "!": "REST", "?": "REST", ";": "REST",
    ":": "REST", "-": "REST", "—": "REST", '"': "REST", "'": "REST",
    "(": "REST", ")": "REST", "…": "REST",
  };

  function charToViseme(ch) {
    if (ch == null) return "REST";
    return CHAR_VISEME[ch.toLowerCase()] || "REST";
  }

  // alignment = {characters:[], character_start_times_seconds:[], character_end_times_seconds:[]}
  // -> [{t, dur, viseme, idx}]  (idx = source character index, for caption karaoke)
  function buildVisemeTrack(alignment) {
    const out = [];
    if (!alignment || !alignment.characters) return out;
    const ch = alignment.characters;
    const st = alignment.character_start_times_seconds || [];
    const en = alignment.character_end_times_seconds || [];
    let lastV = null;
    for (let k = 0; k < ch.length; k++) {
      const v = charToViseme(ch[k]);
      const start = st[k] != null ? st[k] : (out.length ? out[out.length - 1].t : 0);
      const end = en[k] != null ? en[k] : start + 0.05;
      if (v === lastV && out.length) {
        out[out.length - 1].dur = Math.max(out[out.length - 1].dur, end - out[out.length - 1].t);
        continue;
      }
      out.push({ t: start, dur: Math.max(0.03, end - start), viseme: v, idx: k });
      lastV = v;
    }
    const MIN_HOLD = 0.045; // ~22 swaps/sec ceiling so fast text doesn't strobe
    for (let k = 0; k < out.length; k++) if (out[k].dur < MIN_HOLD) out[k].dur = MIN_HOLD;
    return out;
  }

  // ---- PRIMARY: schedule visemes against the audio clock --------------------
  // Driven by setInterval, NOT requestAnimationFrame: rAF is throttled to ~1fps
  // in a backgrounded tab, which would freeze the anchor's mouth whenever the
  // viewer looks away. A 40ms timer (~25fps) stays smooth and keeps running while
  // audio plays. Time is read from the audio clock so it stays in sync regardless.
  const TICK_MS = 40;

  function LipSync(avatar, audioEl) {
    this.av = avatar; this.audio = audioEl;
    this.track = []; this.idx = 0; this.timer = 0;
  }
  LipSync.prototype.load = function (track) { this.track = track || []; this.idx = 0; };
  LipSync.prototype.start = function () {
    clearInterval(this.timer);
    this.av.setSpeaking(true);
    const self = this;
    this.timer = setInterval(function () {
      const t = self.audio.currentTime;
      while (self.idx < self.track.length - 1 && self.track[self.idx + 1].t <= t) self.idx++;
      while (self.idx > 0 && self.track[self.idx].t > t) self.idx--; // handle seek-back
      const seg = self.track[self.idx];
      const paused = self.audio.paused;
      self.av.setMouth(paused ? "REST" : (seg ? seg.viseme : "REST"));
      self.av.setSpeaking(!paused);
      if (self.audio.ended) { self.av.setMouth("REST"); self.av.setSpeaking(false); clearInterval(self.timer); }
    }, TICK_MS);
  };
  LipSync.prototype.stop = function () {
    clearInterval(this.timer);
    this.av.setMouth("REST"); this.av.setSpeaking(false);
  };

  // ---- FALLBACK: Web-Audio amplitude -> {REST, E, AI} -----------------------
  // MediaElementSource can only be created ONCE per <audio>, so cache the graph.
  function ensureAudioGraph(audioEl) {
    if (audioEl._lwGraph) return audioEl._lwGraph;
    const Ctx = global.AudioContext || global.webkitAudioContext;
    const ctx = new Ctx();
    const src = ctx.createMediaElementSource(audioEl);
    const an = ctx.createAnalyser(); an.fftSize = 1024;
    src.connect(an); an.connect(ctx.destination);
    audioEl._lwGraph = { ctx, src, an, buf: new Uint8Array(an.fftSize) };
    return audioEl._lwGraph;
  }

  function AmplitudeLipSync(avatar, audioEl) {
    this.av = avatar; this.audio = audioEl;
    this.g = ensureAudioGraph(audioEl);
    this.timer = 0; this.ema = 0;
  }
  AmplitudeLipSync.prototype.rms = function () {
    this.g.an.getByteTimeDomainData(this.g.buf);
    let s = 0;
    for (let i = 0; i < this.g.buf.length; i++) { const x = (this.g.buf[i] - 128) / 128; s += x * x; }
    return Math.sqrt(s / this.g.buf.length);
  };
  AmplitudeLipSync.prototype.start = function () {
    if (this.g.ctx.state === "suspended") this.g.ctx.resume();
    this.av.setSpeaking(true);
    const self = this;
    this.timer = setInterval(function () {
      const paused = self.audio.paused;
      if (!paused) self.ema = self.ema * 0.6 + self.rms() * 0.4;
      const lvl = paused ? 0 : self.ema;
      self.av.setMouth(lvl < 0.04 ? "REST" : lvl < 0.12 ? "E" : "AI");
      self.av.setSpeaking(!paused);
      if (self.audio.ended) { self.av.setMouth("REST"); self.av.setSpeaking(false); clearInterval(self.timer); }
    }, TICK_MS);
  };
  AmplitudeLipSync.prototype.stop = function () {
    clearInterval(this.timer);
    this.av.setMouth("REST"); this.av.setSpeaking(false);
  };

  // Pick the right driver for a segment.
  function driveSegment(avatar, audioEl, alignment) {
    if (alignment && alignment.characters && alignment.characters.length) {
      const ls = new LipSync(avatar, audioEl);
      ls.load(buildVisemeTrack(alignment));
      ls.start();
      return ls;
    }
    const al = new AmplitudeLipSync(avatar, audioEl);
    al.start();
    return al;
  }

  // ---- Caption karaoke: how many chars have been spoken by time t -----------
  function spokenCharCount(alignment, t) {
    if (!alignment || !alignment.character_start_times_seconds) return null;
    const st = alignment.character_start_times_seconds;
    let n = 0;
    while (n < st.length && st[n] <= t) n++;
    return n;
  }

  // ---- Sentence boundaries: the seconds the anchor finishes each sentence -----
  // Maps each REAL sentence end in the spoken text to its time in the TTS
  // alignment, so the b-roll engine cuts to/from footage on a clean pause —
  // never mid-word. The hard part is rejecting false terminators in real news
  // copy: abbreviations (U.S., U.N., Mr., a.m.) and decimals ($4.74) all contain
  // a period. A "." counts only when: it is followed by whitespace/EOF then a
  // capital/quote (a new sentence opens), the char before it is a lowercase
  // letter or digit, and the word it ends is not a known abbreviation. "!" and
  // "?" are near-unambiguous, so they only need the capital-after test.
  // Returns [{charIndex, t}], or [] when there is no usable alignment.
  var ABBREV = {
    mr: 1, mrs: 1, ms: 1, dr: 1, jr: 1, sr: 1, st: 1, vs: 1, inc: 1, corp: 1,
    co: 1, ltd: 1, gov: 1, sen: 1, rep: 1, gen: 1, lt: 1, sgt: 1, col: 1, no: 1,
    vol: 1, etc: 1, al: 1, am: 1, pm: 1, prof: 1, rev: 1, dept: 1, est: 1,
  };
  function sentenceBoundaryTimes(text, alignment) {
    if (!text || !alignment || !alignment.character_start_times_seconds) return [];
    const st = alignment.character_start_times_seconds;
    const n = text.length;
    const out = [];
    for (let i = 0; i < n; i++) {
      const c = text[i];
      if (c !== "." && c !== "!" && c !== "?") continue;
      let j = i + 1;
      while (j < n && (text[j] === "." || text[j] === "!" || text[j] === "?")) j++; // "?!" "…" "..."
      while (j < n && /["'’”’)\]]/.test(text[j])) j++;                               // closing quotes/brackets
      const atEnd = j >= n;
      if (!atEnd && !/\s/.test(text[j])) continue;          // no space after → "U.S." / "$4.74"
      let k = j; while (k < n && /\s/.test(text[k])) k++;    // start of the next sentence
      const nextOk = atEnd || k >= n || /[A-Z"'“‘(]/.test(text[k]); // new sentence opens capitalized
      if (!nextOk) continue;
      if (c === ".") {
        const prev = text[i - 1] || "";
        if (!/[a-z0-9]/.test(prev)) continue;               // "U." "S." initials, odd punctuation
        if (text[i - 2] === "." && /[A-Za-z]/.test(text[i - 3] || "")) continue; // x.y. → a.m. e.g. U.S.
        let s = i - 1; while (s >= 0 && /[A-Za-z]/.test(text[s])) s--;
        if (ABBREV[text.slice(s + 1, i).toLowerCase()]) continue; // Mr. Sen. etc.
      }
      const t = st[Math.min(k, st.length - 1)];
      if (typeof t === "number" && (!out.length || t - out[out.length - 1].t > 0.25)) {
        out.push({ charIndex: k, t: t });
      }
    }
    return out;
  }

  global.LWLipSync = {
    CHAR_VISEME, charToViseme, buildVisemeTrack,
    LipSync, AmplitudeLipSync, driveSegment, spokenCharCount, sentenceBoundaryTimes,
  };
})(typeof window !== "undefined" ? window : this);
