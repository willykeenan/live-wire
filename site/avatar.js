/* Live Wire — Avatar Engine
 *
 * A STORED 2D talking-head whose mouth is saved in different shapes ("visemes")
 * and swapped in time with the voice. Two render modes, one driver API:
 *   - "svg"    : a parametric face drawn in inline SVG (zero art assets). The
 *                mouth is ONE <path> whose `d` is morphed per viseme.
 *   - "sprite" : drop-in PNGs in /avatars/<id>/ (base, eyes_*, mouth_*).
 *
 * The lip-sync driver (lipsync.js) only ever calls avatar.setMouth(viseme) and
 * avatar.setEyes(state), so it never needs to know which mode is active.
 */
(function (global) {
  "use strict";

  // ---- The 9-shape viseme contract (sprite filenames + svg paths key off these)
  const VISEMES = ["REST", "AI", "E", "O", "U", "MBP", "FV", "L", "WQ"];

  // Nearest-neighbour fallback when a shape is missing for an analyst.
  const VISEME_FALLBACK = {
    AI: "O", E: "REST", O: "U", U: "O", MBP: "REST",
    FV: "E", L: "E", WQ: "U", REST: "REST",
  };

  function resolveViseme(set, v) {
    let cur = v, guard = 0;
    while (!set.has(cur) && guard++ < 10) cur = VISEME_FALLBACK[cur] || "REST";
    return set.has(cur) ? cur : "REST";
  }

  // ---- Parametric mouth paths (SVG mode). Centered on (200, 252), viewBox 400x400.
  const SVG_MOUTH = {
    REST: "M170 252 Q200 259 230 252 Q200 256 170 252 Z",
    AI:   "M166 245 Q200 233 234 245 Q238 273 200 289 Q162 273 166 245 Z",
    E:    "M162 249 Q200 243 238 249 Q200 263 162 249 Z",
    O:    "M181 247 Q200 241 219 247 Q230 266 200 279 Q170 266 181 247 Z",
    U:    "M185 249 Q200 245 215 249 Q222 263 200 272 Q178 263 185 249 Z",
    MBP:  "M171 253 Q200 251 229 253 Q200 255 171 253 Z",
    FV:   "M173 251 Q200 247 227 251 Q210 261 173 257 Z",
    L:    "M177 247 Q200 241 223 247 Q223 268 200 274 Q177 268 177 247 Z M192 254 L208 254 L200 266 Z",
    WQ:   "M187 249 Q200 247 213 249 Q220 259 200 267 Q180 259 187 249 Z",
  };

  // ---------------------------------------------------------------------------
  //  SVG face builder
  // ---------------------------------------------------------------------------
  // hair drawn BEHIND the head (afro halo / bun / ponytail)
  function hairBehind(style) {
    if (style === "afro") return `<ellipse cx="200" cy="150" rx="152" ry="142" fill="var(--hair)"/>`;
    if (style === "bun") return `<circle cx="200" cy="72" r="33" fill="var(--hair)"/>`;
    if (style === "ponytail") return `<path d="M296 150 q58 26 44 122 q-6 30 -32 22 q22 -64 -22 -150 Z" fill="var(--hair)"/>`;
    return "";
  }
  // hair drawn in FRONT (the top / hairline)
  function hairFront(style) {
    switch (style) {
      case "bald":
        return `<path d="M94 176 C92 150 98 128 110 122 C112 150 112 168 116 178 Z" fill="var(--hair)"/>`
             + `<path d="M306 176 C308 150 302 128 290 122 C288 150 288 168 284 178 Z" fill="var(--hair)"/>`;
      case "side":
        return `<path d="M92 168 C96 96 150 64 200 64 C250 64 304 96 308 168 C302 146 300 116 286 106 C246 150 158 118 120 124 C108 134 98 150 92 168 Z" fill="var(--hair)"/>`;
      case "swoop":
        return `<path d="M94 156 C98 92 156 64 206 64 C262 64 306 100 306 158 C300 136 280 110 250 110 C206 154 146 128 118 152 C110 158 100 158 94 156 Z" fill="var(--hair)"/>`;
      case "afro":
        return `<path d="M100 150 C108 96 154 70 200 70 C246 70 292 96 300 150 C298 128 268 120 200 120 C132 120 102 128 100 150 Z" fill="var(--hair)"/>`;
      case "bun":
      case "ponytail":
        return `<path d="M98 162 C102 100 154 66 200 66 C246 66 298 100 302 162 C296 140 250 118 200 118 C150 118 104 140 98 162 Z" fill="var(--hair)"/>`;
      default: // short
        return `<path d="M92 168 C96 96 150 64 200 64 C250 64 304 96 308 168 C300 150 296 124 290 112 C272 132 130 132 110 112 C104 124 100 150 92 168 Z" fill="var(--hair)"/>`;
    }
  }
  function facialHairSVG(kind) {
    if (kind === "stubble")
      return `<path d="M118 248 Q200 322 282 248 Q282 302 200 332 Q118 302 118 248 Z" fill="var(--hair)" opacity="0.20"/>`;
    if (kind === "mustache")
      return `<path d="M174 244 Q200 254 226 244 Q226 255 200 257 Q174 255 174 244 Z" fill="var(--hair)"/>`;
    if (kind === "beard")
      return `<path d="M112 204 Q118 304 200 338 Q282 304 288 204 Q286 272 200 302 Q114 272 112 204 Z" fill="var(--hair)"/>`
           + `<path d="M174 244 Q200 254 226 244 Q226 255 200 257 Q174 255 174 244 Z" fill="var(--hair)"/>`;
    return "";
  }
  function glassesSVG(on) {
    if (!on) return "";
    return `<g fill="none" stroke="#15181d" stroke-width="5" opacity="0.9">`
         + `<circle cx="158" cy="188" r="28"/><circle cx="242" cy="188" r="28"/>`
         + `<line x1="186" y1="186" x2="214" y2="186"/>`
         + `<line x1="130" y1="184" x2="100" y2="178"/><line x1="270" y1="184" x2="300" y2="178"/></g>`;
  }
  function ageSVG(age) {
    if (age !== "senior") return "";
    return `<g stroke="var(--skin-shadow)" stroke-width="2" fill="none" opacity="0.45">`
         + `<path d="M150 138 Q200 132 250 138"/><path d="M156 150 Q200 145 244 150"/></g>`;
  }

  function svgFace(params) {
    const p = Object.assign({
      skin: "#C99B7A", skinShadow: "#A8775A", hair: "#2b2b30",
      jacket: "#1B212B", shirt: "#E6EAF0", tie: "#378ADD",
      mouth: "#7a3b3b", mouthLine: "#5a2b2b", brow: null, eye: "#1a1a1a",
      hairStyle: "short", facialHair: "none", glasses: false, age: "mid",
    }, params || {});
    p.brow = p.brow || p.hair;
    const style =
      `--skin:${p.skin};--skin-shadow:${p.skinShadow};--hair:${p.hair};` +
      `--jacket:${p.jacket};--shirt:${p.shirt};--tie:${p.tie};` +
      `--mouth:${p.mouth};--mouth-line:${p.mouthLine};--brow:${p.brow};--eye:${p.eye}`;
    return `
<svg class="avatar-svg" viewBox="0 0 400 400" style="${style}" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="xMidYMid meet">
  <g class="svg-base">
    ${hairBehind(p.hairStyle)}
    <!-- shoulders / jacket -->
    <path d="M40 400 C46 322 96 300 140 292 L260 292 C304 300 354 322 360 400 Z" fill="var(--jacket)"/>
    <!-- shirt + tie -->
    <path d="M168 300 L200 330 L232 300 L222 400 L178 400 Z" fill="var(--shirt)"/>
    <path d="M193 312 L207 312 L214 400 L186 400 Z" fill="var(--tie)"/>
    <!-- neck -->
    <path d="M172 256 L172 300 Q200 320 228 300 L228 256 Z" fill="var(--skin-shadow)"/>
    <!-- ears -->
    <ellipse cx="92" cy="206" rx="15" ry="24" fill="var(--skin)"/>
    <ellipse cx="308" cy="206" rx="15" ry="24" fill="var(--skin)"/>
    <!-- head -->
    <ellipse cx="200" cy="196" rx="112" ry="128" fill="var(--skin)"/>
    ${hairFront(p.hairStyle)}
    ${ageSVG(p.age)}
    <!-- nose -->
    <path d="M200 196 q12 24 -2 38 q-9 5 -16 0" fill="none" stroke="var(--skin-shadow)" stroke-width="3" stroke-linecap="round"/>
    <!-- brows -->
    <g class="svg-brows" fill="var(--brow)">
      <rect x="134" y="158" width="48" height="9" rx="4.5"/>
      <rect x="218" y="158" width="48" height="9" rx="4.5"/>
    </g>
    ${glassesSVG(p.glasses)}
    ${facialHairSVG(p.facialHair)}
  </g>
  <!-- eyes (each group scales vertically on blink; pupils translate on lookAway) -->
  <g class="svg-eyes">
    <g class="eye eye-l">
      <ellipse class="eye-white" cx="158" cy="186" rx="23" ry="15" fill="#fff"/>
      <circle class="pupil pupil-l" cx="158" cy="188" r="7.5" fill="var(--eye)"/>
    </g>
    <g class="eye eye-r">
      <ellipse class="eye-white" cx="242" cy="186" rx="23" ry="15" fill="#fff"/>
      <circle class="pupil pupil-r" cx="242" cy="188" r="7.5" fill="var(--eye)"/>
    </g>
  </g>
  <!-- mouth: ONE morphing path -->
  <path class="svg-mouth" d="${SVG_MOUTH.REST}" fill="var(--mouth)" stroke="var(--mouth-line)" stroke-width="2"/>
</svg>`;
  }

  // ---------------------------------------------------------------------------
  //  Avatar object
  // ---------------------------------------------------------------------------
  function Avatar(root, spec) {
    this.root = root;
    this.mode = spec.mode === "sprite" ? "sprite" : "svg";
    this.dir = spec.dir || "";
    this._mouth = null;
    this._eyes = "open";
    this._blinkTimer = null;
    this._pupil = { x: 0, y: 0 };

    // Build the rig + layers (identical structure for both modes).
    root.classList.add("avatar");
    root.setAttribute("data-mode", this.mode);
    root.innerHTML =
      '<div class="avatar-rig">' +
        '<div class="avatar-base"></div>' +
        '<div class="avatar-eyes"></div>' +
        '<div class="avatar-mouth"></div>' +
      "</div>";
    this.rig = root.querySelector(".avatar-rig");
    this.elBase = root.querySelector(".avatar-base");
    this.elEyes = root.querySelector(".avatar-eyes");
    this.elMouth = root.querySelector(".avatar-mouth");

    if (this.mode === "svg") {
      // One SVG holds all layers; we manipulate its sub-elements directly.
      this.elBase.innerHTML = svgFace(spec.svgParams);
      this.svg = this.elBase.querySelector("svg");
      this.elBase.classList.add("svg-breathe");
      this.svgMouth = this.svg.querySelector(".svg-mouth");
      this.eyeGroups = this.svg.querySelectorAll(".eye");
      this.pupils = this.svg.querySelectorAll(".pupil");
      this.available = new Set(VISEMES); // svg has every shape
      this._hasHalf = true;              // svg can render a half-blink (scaleY)
    } else {
      // Sprite mode: background images on each layer.
      const bg = (el, file) => {
        el.style.backgroundImage = `url('${this.dir}/${file}')`;
        el.style.backgroundSize = "contain";
        el.style.backgroundRepeat = "no-repeat";
        el.style.backgroundPosition = "center";
      };
      bg(this.elBase, "base.png");
      bg(this.elEyes, "eyes_open.png");
      bg(this.elMouth, "mouth_REST.png");
      this.elBase.classList.add("svg-breathe");
      // Probe which optional shapes exist (async; default to full set meanwhile).
      this.available = new Set(VISEMES);
      this._hasHalf = false;
      this._probe();
    }

    this.setMouth("REST");
    this.setEyes("open");
  }

  // Async-probe sprite availability so resolveViseme can fall back if art is partial.
  Avatar.prototype._probe = function () {
    const self = this;
    const check = (file, ok, no) => {
      const im = new Image();
      im.onload = ok; im.onerror = no; im.src = `${self.dir}/${file}`;
    };
    VISEMES.forEach((v) => {
      check(`mouth_${v}.png`, null, () => self.available.delete(v));
    });
    check("eyes_half.png", () => { self._hasHalf = true; }, null);
  };

  Avatar.prototype.has = function (key) {
    if (key === "eyes_half") return this._hasHalf;
    return this.available.has(key);
  };

  // ---- Mouth (the lip-sync surface) -----------------------------------------
  Avatar.prototype.setMouth = function (v) {
    v = resolveViseme(this.available, v);
    if (v === this._mouth) return; // throttle: only touch DOM on real change
    this._mouth = v;
    if (this.mode === "svg") {
      this.svgMouth.setAttribute("d", SVG_MOUTH[v]);
    } else {
      this.elMouth.style.backgroundImage = `url('${this.dir}/mouth_${v}.png')`;
    }
  };

  // ---- Eyes / blink ---------------------------------------------------------
  Avatar.prototype.setEyes = function (state) {
    this._eyes = state;
    if (this.mode === "svg") {
      // Blink by collapsing the eyeball GEOMETRY (eye-white ry + pupil r) — the
      // ellipse center stays fixed, so the eye closes exactly where it is. No CSS
      // transforms (their origin resolves to the SVG corner here and threw the
      // eyes into the forehead).
      const ry = state === "closed" ? 1.5 : state === "half" ? 7 : 15;
      const pr = state === "closed" ? 0 : state === "half" ? 5 : 7.5;
      this.eyeGroups.forEach((g) => {
        g.style.transform = ""; g.removeAttribute("transform");
        const w = g.querySelector(".eye-white"); if (w) w.setAttribute("ry", ry);
        const p = g.querySelector(".pupil"); if (p) p.setAttribute("r", pr);
      });
    } else {
      const file = state === "closed" ? "eyes_closed.png"
        : state === "half" && this._hasHalf ? "eyes_half.png" : "eyes_open.png";
      this.elEyes.style.backgroundImage = `url('${this.dir}/${file}')`;
    }
  };

  Avatar.prototype.blink = function () {
    const self = this;
    const seq = this.has("eyes_half")
      ? [["half", 45], ["closed", 75], ["half", 45], ["open", 0]]
      : [["closed", 95], ["open", 0]];
    let d = 0;
    seq.forEach(([state, hold]) => {
      setTimeout(() => self.setEyes(state), d);
      d += hold;
    });
    if (Math.random() < 0.15) setTimeout(() => self.blink(), d + 130); // double-blink
  };

  // ---- lookAway: pupils drift (SVG); subtle, for the "thinking" beat --------
  Avatar.prototype.lookAway = function (amt) {
    amt = amt || 0;
    const dx = -7 * amt, dy = -4 * amt;
    this._pupil = { x: dx, y: dy };
    if (this.mode === "svg" && this.pupils) {
      this.pupils.forEach((p) => { p.style.transform = `translate(${dx}px,${dy}px)`; });
    }
  };

  // ---- speaking micro-motion: a faster head bob layered while talking -------
  Avatar.prototype.setSpeaking = function (on) {
    if (!this.rig) return;
    this.rig.classList.toggle("speaking", !!on);
  };

  Avatar.prototype.destroy = function () {
    clearTimeout(this._blinkTimer);
    this._blinkTimer = null;
  };

  // ---- factory + idle blink loop --------------------------------------------
  function buildAvatar(root, spec) {
    return new Avatar(root, spec || { mode: "svg" });
  }

  function startBlinking(avatar) {
    clearTimeout(avatar._blinkTimer);
    (function schedule() {
      const next = 3000 + Math.random() * 3000; // 3–6s
      avatar._blinkTimer = setTimeout(() => { avatar.blink(); schedule(); }, next);
    })();
  }

  global.LWAvatar = {
    buildAvatar, startBlinking, resolveViseme,
    VISEMES, SVG_MOUTH, VISEME_FALLBACK,
  };
})(typeof window !== "undefined" ? window : this);
