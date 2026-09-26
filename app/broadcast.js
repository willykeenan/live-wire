/* Live Wire — public demo + live studio
 * Client-rendered rundown, cartoon mouth sync, ticker, and newsroom surfaces.
 * Demo mode plays canned satire scripts with no API keys.
 */
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => Array.prototype.slice.call((r || document).querySelectorAll(s));
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---- helpers ----
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  function rel(iso) {
    if (!iso) return ""; const t = Date.parse(iso); if (isNaN(t)) return "";
    let s = Math.max(0, Math.floor((Date.now() - t) / 1000));
    if (s < 60) return s + "s ago"; const m = Math.floor(s / 60);
    if (m < 60) return m + "m ago"; const h = Math.floor(m / 60);
    if (h < 24) return h + "h ago"; return Math.floor(h / 24) + "d ago";
  }
  function fmtDate(iso) {
    if (!iso) return "Not published by Live Wire";
    const d = new Date(iso); if (isNaN(d.getTime())) return "Unavailable";
    return d.toLocaleString([], {
      year: "numeric", month: "short", day: "numeric",
      hour: "numeric", minute: "2-digit", timeZoneName: "short",
    });
  }
  function safeUrl(value) {
    try { const u = new URL(String(value || ""), location.origin); return /^https?:$/.test(u.protocol) ? u.href : ""; }
    catch (e) { return ""; }
  }
  function safeCorrectionPath(value) {
    const path = String(value || "");
    return /^\/corrections\/(?:[a-z0-9][a-z0-9._~-]*\/)+$/.test(path) ? path : "";
  }
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("show"); setTimeout(() => t.classList.remove("show"), 1900); }
  function openExt(u) { u = safeUrl(u); if (!u) return; try { window.open(u, "_blank", "noopener,noreferrer"); } catch (e) {} }
  function tickTimes() { $$(".nmeta-time[data-ts]").forEach((s) => { const v = rel(s.getAttribute("data-ts")); if (v) s.textContent = v; }); }
  window.openExt = openExt;

  // ============================ state ============================
  let ANALYSTS = [], ANALYST_BY_ID = {};
  let avatar = null, currentAnalystId = null, lipSync = null;
  let RUNDOWN = [], RUNDOWN_LATEST = [], rdIdx = 0, playing = false, currentSegId = null;
  let playLoopRunning = false, playQueue = [], recentKeys = [];
  let WIRE = [];
  let STORY_LOAD_STATE = "loading";
  let wireFilter = "all";
  let wirePayloadKey = "";
  let wireAnnouncementVersion = 0;
  let SCHED = null, SCHEDULE_LOAD_STATE = "loading", schedDay = "weekday", schedDayPinned = false;
  let pipOn = false, brollTimer = 0, brollClipTimer = 0, anchorHome = null;
  let CUR_PROGRAM = null, RUNDOWN_MODE = "demo";
  let INTEL = [], INTEL_META = {}, INTEL_BRIEFS = [];
  const mediaCache = {};   // source_url -> {video,type} | null  (resolved real footage)
  const LOCAL = /^(127\.0\.0\.1|localhost|0\.0\.0\.0|\[?::1\]?)$/.test(location.hostname);

  // ============================ clock + tabs ============================
  function clock() { const c = $("#clock"); if (c) c.textContent = new Date().toLocaleTimeString(); }

  function setTab(tab) {
    document.body.setAttribute("data-tab", tab);
    $$(".tabs .seg, .mtabs .mtab").forEach((b) => {
      const selected = b.dataset.tab === tab;
      b.classList.toggle("on", selected);
      b.setAttribute("aria-current", selected ? "page" : "false");
    });
    ["onair", "wire", "rumors", "intel", "schedule"].forEach((t) => {
      const el = $("#tab-" + t);
      if (el) el.classList.toggle("hidden", t !== tab);
    });
    if (tab === "wire") renderWire();
    if (tab === "intel") renderIntel();
    if (tab === "schedule") renderSchedule();
    updatePip();
  }
  $$(".tabs .seg, .mtabs .mtab").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.tab)));

  // ============================ avatar / analysts ============================
  async function loadAnalysts() {
    try {
      ANALYSTS = await (await fetch("/api/analysts", { cache: "no-store" })).json();
    } catch (e) { ANALYSTS = []; }
    ANALYST_BY_ID = {};
    ANALYSTS.forEach((a) => (ANALYST_BY_ID[a.id] = a));
    if (ANALYSTS.length) switchAnalyst(ANALYSTS[0].id, true);
  }

  function switchAnalyst(id, force) {
    if (!force && id === currentAnalystId) return;
    const a = ANALYST_BY_ID[id] || ANALYSTS[0];
    if (!a) return;
    if (lipSync) { lipSync.stop(); lipSync = null; }
    if (avatar) avatar.destroy();
    avatar = window.LWAvatar.buildAvatar($("#avatar"), a.avatar || { mode: "svg" });
    window.LWAvatar.startBlinking(avatar);
    currentAnalystId = a.id;
    $("#anchorName").textContent = a.name;
    $("#anchorTitle").textContent = a.title;
  }

  // ============================ chyron / caption ============================
  // live recency from the story's publish time (epoch seconds), computed at display
  // time so it's always accurate. "" when undated or stale beyond a few hours.
  function ageLabel(seg) {
    const ts = seg && seg.ts; if (!ts) return "";
    const m = Math.max(0, Math.floor(Date.now() / 1000 - ts) / 60 | 0);
    if (m <= 2) return "JUST IN";
    if (m < 90) return m + "m ago";
    if (m < 240) return Math.floor(m / 60) + "h ago";
    return "";
  }
  function setChyron(seg) {
    const a = ANALYST_BY_ID[seg.analyst_id] || {};
    $("#anchorName").textContent = a.name || "Live Wire";
    $("#anchorTitle").textContent = a.title || "";
    const k = $("#anchorKick");
    k.textContent = (seg.kicker || "SATIRE").toUpperCase();
    k.className = "chyron-kick";
    const age = ageLabel(seg);
    const tag = age ? `<b style="color:#F09595">${age}</b> · ` : "";
    const span = `<span style="padding:0 28px">${tag}${esc(seg.headline)}</span>`;
    $("#chyronHead").innerHTML = span + span; // duplicated for seamless roll
  }

  let captionTimer = 0;
  function startCaption(text, alignment, audio) {
    clearInterval(captionTimer);
    const cap = $("#caption");
    if (!alignment || !alignment.character_start_times_seconds) {
      cap.innerHTML = `<span class="spoken">${esc(text)}</span>`; return;
    }
    captionTimer = setInterval(() => {
      const n = window.LWLipSync.spokenCharCount(alignment, audio.currentTime) || 0;
      cap.innerHTML = `<span class="spoken">${esc(text.slice(0, n))}</span><span class="ahead">${esc(text.slice(n))}</span>`;
      if (audio.ended) clearInterval(captionTimer);
    }, 60);
  }
  function clearCaption() { clearInterval(captionTimer); }

  // ---- b-roll / cutaways: VO model ----------------------------------------
  // Real broadcast never cuts the anchor off mid-sentence. Footage rolls MUTED
  // as an overlay (over-the-shoulder, then full-frame) while the anchor keeps
  // narrating; we cut TO and FROM the footage on clean sentence boundaries
  // (from timed alignment data), and dissolve back to the anchor to close.
  // SOT (a clip with its own audio) is reserved for segments the server marks
  // seg.is_sot — off by default until real soundbites are sourced (phase 2).
  function brollClear() {
    clearInterval(brollTimer); brollTimer = 0;
    clearTimeout(brollClipTimer); brollClipTimer = 0;
    ["brollBg", "brollOts", "brollFull", "brollVideo", "brollFrame", "brollCredit"].forEach((id) => $("#" + id).classList.remove("on"));
    const v = $("#brollVideo"); try { v.pause(); } catch (e) {} v.onended = null;
    v.removeAttribute("src"); v.removeAttribute("data-src");
    const f = $("#brollFrame"); f.removeAttribute("data-vid"); f.src = "about:blank";
  }

  // Pick the player for a resolved clip: a bare 11-char token is a YouTube id,
  // anything else (a URL / .mp4) is a direct file.
  function clipType(v, t) {
    if (t) return t;
    if (!v) return "";
    return /^[A-Za-z0-9_-]{11}$/.test(v) ? "youtube" : "mp4";
  }

  // The cutaway window, snapped to whole sentences so we never open/close on a
  // half-spoken word. Uses the TTS alignment (independent of audio.duration);
  // falls back to a duration window only when alignment is missing. Returns
  // {in,out} or null = no valid window (stay on the anchor).
  function brollWindow(alignment, text, dur) {
    const bounds = (window.LWLipSync.sentenceBoundaryTimes(text, alignment)) || [];
    if (bounds.length) {
      const ends = alignment.character_end_times_seconds || [];
      const end = ends.length ? ends[ends.length - 1] : (bounds[bounds.length - 1].t + 1);
      const cin = bounds.find((b) => b.t >= 2.0);              // let the anchor open the story
      let cout = null;
      for (const b of bounds) if (b.t <= end - 1.5) cout = b.t; // be back on the anchor to close
      if (cin && cout != null && cout > cin.t + 1.5) return { in: cin.t, out: cout };
      return null;                                             // short item → no cutaway
    }
    if (dur && isFinite(dur) && dur > 6) return { in: dur * 0.30, out: dur * 0.80 };
    return null;
  }

  function brollStart(seg, audio, alignment) {
    brollClear();
    const img = seg && seg.image, srcUrl = seg && seg.source_url, headline = (seg && seg.headline) || "";
    if (!img && !headline) return;                             // nothing to illustrate → anchor on cam
    if (seg.image_source) { $("#brollCredit").textContent = "Source: " + seg.image_source; $("#brollCredit").classList.add("on"); }
    const ots = $("#brollOts"), im = ots.querySelector("img"), vel = $("#brollVideo"), frm = $("#brollFrame");
    if (img) {
      $("#brollBg").style.backgroundImage = `url("${img}")`; $("#brollBg").classList.add("on");
      im.style.display = ""; im.src = img; ots.querySelector(".src").textContent = seg.image_source || "";
      $("#brollFull").style.backgroundImage = `url("${img}")`;
      ots.classList.add("on");                                 // over-the-shoulder while the anchor sets up
    }
    // resolve a real clip for this story (muted b-roll under the VO) — async, cached
    const media = { video: seg.video || "", type: clipType(seg.video || "", seg.video_type || "") };
    const mkey = headline || srcUrl || "";
    if (!media.video && mkey) {
      if (mediaCache[mkey] !== undefined) { if (mediaCache[mkey]) Object.assign(media, mediaCache[mkey]); }
      else {
        // Bound the media cache during long studio preview sessions.
        const keys = Object.keys(mediaCache);
        if (keys.length > 300) keys.slice(0, 150).forEach((k) => delete mediaCache[k]);
        fetch("/api/media?q=" + encodeURIComponent(headline) + (srcUrl ? "&url=" + encodeURIComponent(srcUrl) : ""), { cache: "no-store" })
          .then((r) => r.json()).then((d) => { const m = (d && d.video) ? { video: d.video, type: clipType(d.video, d.type) } : null; mediaCache[mkey] = m; if (m) Object.assign(media, m); })
          .catch(() => { mediaCache[mkey] = null; });
      }
    }

    let win = null, mode = "ots", shownVid = "";
    function showFull() {
      ots.classList.remove("on");
      if (media.video && media.type === "youtube") {
        if (shownVid !== media.video) {
          frm.src = "https://www.youtube-nocookie.com/embed/" + media.video + "?autoplay=1&mute=1&controls=0&playsinline=1&loop=1&playlist=" + media.video;
          frm.setAttribute("data-vid", media.video); shownVid = media.video;
          $("#brollCredit").textContent = "Footage via YouTube"; $("#brollCredit").classList.add("on");
        }
        frm.classList.add("on"); vel.classList.remove("on"); $("#brollFull").classList.remove("on");
      } else if (media.video) {                                // mp4, muted VO
        if (shownVid !== media.video) { vel.src = media.video; vel.setAttribute("data-src", media.video); vel.muted = true; vel.loop = true; vel.play().catch(() => {}); shownVid = media.video; }
        vel.classList.add("on"); frm.classList.remove("on"); $("#brollFull").classList.remove("on");
      } else if (img) {                                        // image-only full frame
        $("#brollFull").classList.add("on"); vel.classList.remove("on"); frm.classList.remove("on");
      } else { showOts(); }
    }
    function showOts() {
      $("#brollFull").classList.remove("on");
      vel.classList.remove("on"); try { vel.pause(); } catch (e) {}
      frm.classList.remove("on");                              // keep iframe src (muted) so re-show won't reload
      if (img) ots.classList.add("on");
    }

    brollTimer = setInterval(() => {
      if (audio.ended) { clearInterval(brollTimer); return; }
      if (win === null) {                                      // undecided
        const w = brollWindow(alignment, seg.text, audio.duration);
        if (w) win = w;
        else if ((alignment && alignment.character_start_times_seconds) || (audio.duration && isFinite(audio.duration))) win = false; // commit: no cutaway
        else return;                                           // alignment-less, metadata still loading
      }
      if (!win) return;                                        // stay on the anchor (OTS image if any)
      const t = audio.currentTime || 0;
      const want = (t >= win.in && t <= win.out) && (media.video || img) ? "full" : "ots";
      if (want !== mode) { (want === "full" ? showFull : showOts)(); mode = want; }
      else if (want === "full" && media.video && media.video !== shownVid) showFull(); // clip resolved mid-window
    }, 150);
  }

  // ---- pop-out anchor (watch while on other tabs) ----
  function updatePip() {
    const frame = document.querySelector(".anchor-frame");
    if (!frame) return;
    if (!anchorHome) anchorHome = frame.parentNode;
    const float = pipOn && document.body.getAttribute("data-tab") !== "onair";
    if (float) {
      if (frame.parentNode !== $("#pipFrame")) { $("#pipFrame").appendChild(frame); frame.classList.add("in-pip"); }
      $("#pip").classList.add("on");
    } else {
      if (anchorHome && frame.parentNode !== anchorHome) { anchorHome.insertBefore(frame, anchorHome.firstChild); frame.classList.remove("in-pip"); }
      $("#pip").classList.remove("on");
    }
    $("#pipBtn").classList.toggle("on", pipOn);
  }
  function initPipDrag() {
    const pip = $("#pip"), bar = $("#pipBar");
    let dragging = false, dx = 0, dy = 0;
    bar.addEventListener("mousedown", (e) => {
      if (e.target && e.target.id === "pipDock") return;
      const r = pip.getBoundingClientRect();
      pip.style.left = r.left + "px"; pip.style.top = r.top + "px";
      pip.style.right = "auto"; pip.style.bottom = "auto";
      dx = e.clientX - r.left; dy = e.clientY - r.top; dragging = true;
      document.body.style.userSelect = "none"; e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const w = pip.offsetWidth, h = pip.offsetHeight;
      pip.style.left = Math.max(0, Math.min(window.innerWidth - w, e.clientX - dx)) + "px";
      pip.style.top = Math.max(0, Math.min(window.innerHeight - h, e.clientY - dy)) + "px";
    });
    window.addEventListener("mouseup", () => { if (dragging) { dragging = false; document.body.style.userSelect = ""; } });
  }

  async function fetchSpeech(text, analystId) {
    try {
      const r = await fetch("/api/tts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: text, analyst_id: analystId }),
      });
      return await r.json();
    } catch (e) { return null; }
  }

  // Vendor audio (live generation + ELEVENLABS_API_KEY): play it and drive the
  // mouth and karaoke caption from the audio clock. Resolves true when it played,
  // false when the browser refused playback (the caller then mimes silently).
  function playVendorAudio(tts, text) {
    return new Promise((resolve) => {
      const audio = new Audio("data:" + (tts.mime || "audio/mpeg") + ";base64," + tts.audio_b64);
      const driver = window.LWLipSync.driveSegment(avatar, audio, tts.alignment);
      let done = false, started = false;
      const handle = { stop: () => finish() };
      function finish() {
        if (done) return; done = true;
        try { audio.pause(); } catch (e) {}
        driver.stop(); clearCaption();
        if (lipSync === handle) lipSync = null;
        resolve(started);
      }
      lipSync = handle;
      audio.addEventListener("ended", finish);
      audio.addEventListener("error", finish);
      startCaption(text, tts.alignment, audio);
      audio.play().then(() => { started = true; }).catch(finish);
    });
  }

  // Demo (or no vendor audio): synthetic timestamps drive the mouth silently.
  async function mimeSpeech(text, tts) {
    let alignment = null;
    let durationMs = Math.max(4000, Math.min(18000, String(text || "").length * 70));
    if (tts && tts.alignment && tts.alignment.characters && tts.alignment.characters.length) {
      alignment = tts.alignment;
      if (tts.duration_sec) durationMs = Math.max(2500, tts.duration_sec * 1000);
    }
    if (alignment && avatar && window.LWLipSync && window.LWLipSync.buildVisemeTrack) {
      const track = window.LWLipSync.buildVisemeTrack(alignment);
      let idx = 0;
      const t0 = performance.now();
      avatar.setSpeaking(true);
      const timer = setInterval(() => {
        const t = (performance.now() - t0) / 1000;
        while (idx < track.length - 1 && track[idx + 1].t <= t) idx++;
        const vis = track[idx];
        avatar.setMouth(vis ? vis.viseme : "REST");
      }, 40);
      lipSync = { stop: function () { clearInterval(timer); if (avatar) { avatar.setMouth("REST"); avatar.setSpeaking(false); } } };
      await wait(durationMs);
      if (lipSync) { lipSync.stop(); lipSync = null; }
      return;
    }
    await wait(durationMs);
  }

  async function speakSegment(text, analystId) {
    const tts = await fetchSpeech(text, analystId);
    if (!playing) return;
    if (tts && tts.audio_b64 && avatar && window.LWLipSync) {
      if (await playVendorAudio(tts, text)) return;
      if (!playing) return;
    }
    await mimeSpeech(text, tts);
  }

  async function thinkingBeat() {
    if (!avatar) return;
    // a single natural breath between stories — NOT the old ~680ms dead-air stall
    // that read as an awkward pause. Anchors flow story-to-story; keep it tight.
    avatar.setMouth("REST"); avatar.blink();
    await wait(140);
  }

  // ============================ rundown ============================
  // `beat` is the anchor's desk, not the segment's content, so it is not checked.
  function isPublicPreviewSegment(seg) {
    if (!seg || !seg.headline) return false;
    const labels = [seg.kicker, seg.status].filter(Boolean).join(" ").toLowerCase();
    return !seg.breaking && !/(rumor|unconfirmed|confirmed|debunked)/.test(labels);
  }
  const MODE_LABEL = { demo: "demo · canned satire", live: "live · AI-written satire",
    template: "live · wire template", warming_up: "live · warming up" };
  async function refreshRundown() {
    try {
      const d = await (await fetch("/api/rundown", { cache: "no-store" })).json();
      RUNDOWN_LATEST = (d.segments || []).filter(isPublicPreviewSegment);
      CUR_PROGRAM = d.program || null;
      RUNDOWN_MODE = d.mode || "demo";
      const m = $("#rdMode");
      if (m) m.textContent = MODE_LABEL[RUNDOWN_MODE] || RUNDOWN_MODE;
      if (!RUNDOWN.length) { RUNDOWN = RUNDOWN_LATEST.slice(); renderRundown(); }
      renderProgramBar();
    } catch (e) {}
  }
  function airStatusText() {
    return RUNDOWN_MODE === "demo"
      ? "SATIRE · canned demo script · not a confirmed report."
      : "SATIRE · AI-written from public headlines · verify at the source.";
  }
  function renderRundown() {
    const ol = $("#rundownList");
    if (!RUNDOWN.length) { ol.innerHTML = '<li><span class="rd-txt">No safe preview cards are available.</span></li>'; return; }
    ol.innerHTML = RUNDOWN.map((s, i) => {
      const a = ANALYST_BY_ID[s.analyst_id] || {};
      const cls = s.id === currentSegId ? "live" : "";
      const beatTag = s.beat === "business" || s.beat === "markets" ? "blue" : "";
      const age = ageLabel(s);
      return `<li class="${cls}" data-id="${s.id}"><span class="rd-num">${i + 1}</span>
        <span class="rd-txt">${esc(s.headline)}<br><span class="nmeta">${esc(a.name || "")}${age ? " · " + age : ""}</span></span>
        <span class="tag ${beatTag} rd-beat">${esc((s.beat || "news").toLowerCase())}</span></li>`;
    }).join("");
  }
  function markRundown(id) { currentSegId = id; renderRundown(); }

  // ============================ visual preview loop ============================
  async function startBroadcast() {
    playing = true;
    $("#playBtn").textContent = "⏸ Pause";
    $("#airStatus").textContent = RUNDOWN_MODE === "demo" ? "Demo on air · SATIRE · canned scripts." : "On air · SATIRE · AI-written.";
    if (playLoopRunning) return;
    if (!RUNDOWN_LATEST.length) await refreshRundown();
    playLoop();
  }
  function pauseBroadcast() {
    playing = false;
    if (lipSync) { lipSync.stop(); lipSync = null; }
    $("#playBtn").textContent = "▶ Resume";
    $("#airStatus").textContent = "Paused. Satire remains labelled.";
  }
  $("#playBtn").addEventListener("click", () => (playing ? pauseBroadcast() : startBroadcast()));

  // signature of a rundown = its ordered segment ids; lets us notice a fresher build
  function rundownSig(rd) { return (rd || []).map((s) => s.id).join("|"); }
  // Build a preview queue from the safe public subset and avoid immediate repeats.
  function buildQueue() {
    const src = (RUNDOWN_LATEST.length ? RUNDOWN_LATEST : RUNDOWN) || [];
    let q = src.filter((s) => recentKeys.indexOf(s.headline) < 0);
    if (!q.length) { recentKeys = []; q = src.slice(); }   // all aired recently → fresh lap
    return q;
  }

  async function playLoop() {
    if (playLoopRunning) return;
    playLoopRunning = true;
    try {
      while (playing) {
        if (!playQueue.length) {
          await refreshRundown();
          if (RUNDOWN_LATEST.length) { RUNDOWN = RUNDOWN_LATEST.slice(); renderRundown(); }
          playQueue = buildQueue();
          if (!playQueue.length) { await wait(1500); continue; }
        }
        const seg = playQueue.shift();
        recentKeys.push(seg.headline); if (recentKeys.length > 6) recentKeys.shift();
        markRundown(seg.id);
        switchAnalyst(seg.analyst_id);
        setChyron(seg);
        const spoken = seg.text || seg.headline || "";
        $("#caption").innerHTML = `<span class="spoken">${esc(spoken.slice(0, 360))}</span>`;
        $("#airStatus").textContent = airStatusText();
        await speakSegment(spoken, seg.analyst_id);
        if (!playing) break;
        clearCaption(); $("#caption").textContent = "";
        if (!playing) break;
        await thinkingBeat();
        // Adopt a refreshed safe rundown between preview cards.
        if (RUNDOWN_LATEST.length && rundownSig(RUNDOWN_LATEST) !== rundownSig(RUNDOWN)) {
          RUNDOWN = RUNDOWN_LATEST.slice(); renderRundown();
          playQueue = buildQueue();
        }
      }
    } finally { playLoopRunning = false; }
  }

  // ============================ ticker ============================
  function renderTicker() {
    const el = $("#tickerTrack");
    if (!WIRE.length) { el.innerHTML = ""; return; }
    const tint = (c) => (c === "markets" || c === "business" ? "var(--blue)" : c === "tech" ? "var(--green)" : "var(--amber)");
    const items = WIRE.slice(0, 26).map((w) =>
      `<span class="ti"><span class="dotk" style="background:${tint(w.category)}"></span>${esc(w.headline)} <span class="src">${esc(w.source || "")}</span></span>`).join("");
    el.innerHTML = items + items;
  }

  // ============================ program bar + schedule ============================
  function renderProgramBar() {
    const n = CUR_PROGRAM || (SCHED && SCHED.now);
    if (!n) return;
    $("#pbName").textContent = n.name;
    const who = n.anchors && n.anchors.length ? "with " + n.anchors.map((a) => a.name).join(" & ") : "";
    $("#pbAnchors").textContent = [who, n.tagline].filter(Boolean).join("  ·  ");
    const nx = SCHED ? SCHED.next : null;
    $("#pbNext").textContent = nx ? `${nx.name} · ${nx.start}` : "";
    // While idle, show the character assigned to the current schedule slot.
    if (!playing && n.anchors && n.anchors[0] && n.anchors[0].id !== currentAnalystId) {
      switchAnalyst(n.anchors[0].id);
    }
  }
  function renderSchedule() {
    const el = $("#schedGrid");
    $$("[data-day]").forEach((x) => x.classList.toggle("on", x.dataset.day === schedDay)); // keep toggle in sync
    if (!SCHED) {
      el.innerHTML = SCHEDULE_LOAD_STATE === "contained"
        ? '<div class="empty">Program scheduling is off in the read-only evidence preview. No scheduler or background worker is running.</div>'
        : '<div class="empty"><span class="spinner"></span>loading the grid…</div>';
      return;
    }
    const grid = schedDay === "weekend" ? (SCHED.grid_weekend || []) : (SCHED.grid_weekday || []);
    const dayMatches = (SCHED.day_type === "weekends") === (schedDay === "weekend");
    const currentId = dayMatches && SCHED.now ? SCHED.now.id : null;
    $("#schedTz").textContent = `${SCHED.day_type || ""} · ${SCHED.clock || ""} ${SCHED.tz || ""}`;
    el.innerHTML = grid.map((p) => `
      <div class="sched-row ${p.id === currentId ? "live" : ""}">
        <div class="sched-time">${p.start}–${p.end}</div>
        <div><div class="sched-name">${esc(p.name)} ${p.id === currentId ? '<span class="pb-live" style="color:#F0C780">CURRENT SLOT</span>' : ""}</div>
          <div class="sched-tag">${esc(p.tagline || "")}</div></div>
        <div class="sched-right"><div class="sched-anchors">${esc((p.anchors || []).map((a) => a.name).join(", "))}</div>
          <span class="sat">satire ${p.satire_level}/5</span></div>
      </div>`).join("");
  }

  // ============================ Wire tab ============================
  const WIRE_CATS = [["all", "All"], ["markets", "Markets"], ["business", "Business"], ["world", "World"], ["tech", "Tech"], ["politics", "Politics"], ["us", "US"]];
  function renderWireFilters() {
    $("#wireFilters").innerHTML = WIRE_CATS.map(([k, l]) =>
      `<button type="button" class="seg ${k === wireFilter ? "on" : ""}" data-cat="${k}" aria-pressed="${k === wireFilter}">${l}</button>`).join("");
    $$("#wireFilters .seg").forEach((b) => b.addEventListener("click", () => {
      wireFilter = b.dataset.cat;
      $$("#wireFilters .seg").forEach((item) => {
        const selected = item.dataset.cat === wireFilter;
        item.classList.toggle("on", selected);
        item.setAttribute("aria-pressed", String(selected));
      });
      renderWire();
      const visible = wireFilter === "all" ? WIRE.length : WIRE.filter((w) => w.category === wireFilter).length;
      const live = $("#wireStatus");
      if (live) live.textContent = `${visible} inspectable Stories match the ${wireFilter} filter.`;
    }));
  }
  function renderWire() {
    const feed = $("#wireFeed");
    let items = WIRE;
    if (wireFilter !== "all") items = WIRE.filter((w) => w.category === wireFilter);
    feed.setAttribute("aria-busy", STORY_LOAD_STATE === "loading" ? "true" : "false");
    const renderKey = JSON.stringify([wireFilter, STORY_LOAD_STATE, items]);
    if (feed.dataset.renderKey === renderKey) return;
    feed.dataset.renderKey = renderKey;
    if (!items.length) {
      const copy = STORY_LOAD_STATE === "loading"
        ? '<span class="spinner"></span>loading inspectable Stories…'
        : STORY_LOAD_STATE === "error"
          ? "Inspectable Stories are unavailable. The preview has failed closed; no unreviewed fallback feed is shown."
          : "No inspectable Stories match this filter.";
      feed.innerHTML = `<div class="empty">${copy}</div>`;
      return;
    }
    feed.innerHTML = items.slice(0, 60).map((w) => `
      <article class="card story-card" data-story-id="${esc(w.storyId || "")}">
        <div class="story-top"><span class="story-state ${w.editorialState === "corrected" ? "corrected" : ""}">${esc(w.editorialState || "reported")}</span>
          <span class="story-freshness">first seen ${esc(fmtDate(w.firstSeenAt))}</span></div>
        <h2 class="nhead">${esc(w.headline)}</h2>
        ${w.summary ? `<div class="nsum">${esc(w.summary)}</div>` : ""}
        ${w.correctionCount ? `<div class="correction-note">${w.editorialState === "retracted" ? "Retracted" : "Corrected"} · ${esc(String(w.correctionCount))} attributable update${w.correctionCount === 1 ? "" : "s"}</div>` : ""}
        <div class="subrow"><span class="nmeta">${esc(w.sourceName || "Source on Receipt")}</span><span>·</span>
          <span class="nmeta nmeta-time" data-ts="${esc(w.sourcePublishedAt || "")}">${rel(w.sourcePublishedAt)}</span>
          <span class="tag">${esc(w.category || "news")}</span></div>
        <div class="story-actions">
          ${safeUrl(w.sourceUrl) ? `<a class="story-source" href="${esc(safeUrl(w.sourceUrl))}" target="_blank" rel="noopener noreferrer">Open attributed source ↗</a>` : '<span class="nmeta">Source URL unavailable</span>'}
          <button type="button" class="receipts-btn" data-story-id="${esc(w.storyId || "")}" aria-label="View Receipts for ${esc(w.headline || "this Story")}">Receipts</button>
        </div>
      </article>`).join("");
    $$("#wireFeed .receipts-btn").forEach((b) => b.addEventListener("click", () => openStoryReceipts(b.dataset.storyId, b)));
  }

  function announceWireChange(initial) {
    const live = $("#wireStatus");
    if (!live) return;
    if (STORY_LOAD_STATE === "error") {
      live.textContent = "Inspectable Stories are unavailable. The preview failed closed.";
    } else if (STORY_LOAD_STATE === "empty") {
      live.textContent = "No inspectable Stories are available.";
    } else if (initial) {
      live.textContent = `${WIRE.length} inspectable Stories are available.`;
    } else {
      wireAnnouncementVersion += 1;
      live.textContent = `Inspectable Story evidence update ${wireAnnouncementVersion}. ${WIRE.length} Stories are available.`;
    }
  }

  // ============================ Receipts / corrections =========================
  let receiptOpener = null;
  let receiptOpenerId = "";
  let receiptStoryId = "";
  function reviewName(review) {
    if (!review) return "No human review recorded";
    const name = review.reviewerName || review.name || review.reviewerId || "Named reviewer unavailable";
    const role = review.reviewerRole || "role unavailable";
    const reviewed = review.reviewedAt ? ` · reviewed ${fmtDate(review.reviewedAt)}` : "";
    return `${name} · ${role}${reviewed}`;
  }
  function renderSources(sources) {
    if (!sources || !sources.length) return '<div class="receipt-status warn">No attributable source is available. This record cannot advance.</div>';
    return sources.map((s) => {
      const url = safeUrl(s.url);
      return `<div class="receipt-source-row"><strong>${esc(s.name || "Source")}</strong> · ${s.attributable ? "attributable" : "not attributable"}<br>
        ${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(url)}</a>` : "Invalid source URL"}<br>
        <span class="nmeta">Published ${esc(fmtDate(s.sourcePublishedAt))} · first seen ${esc(fmtDate(s.firstSeenAt))} · ${esc(s.sourceType || "source")}</span></div>`;
    }).join("");
  }
  function agreementLabel(value) {
    return value === true ? "match" : value === false ? "mismatch" : "not established";
  }
  function renderEvidence(evidence) {
    if (!evidence || !evidence.length) return '<div class="receipt-status warn">No evidence is attached. Confirmation and publication remain unavailable.</div>';
    return evidence.map((e) => `<div class="receipt-source-row"><strong>${esc(e.relation || "context")}</strong> · ${e.attributable ? "attributable" : "not attributable"}<br>
      <span class="nmeta">Evaluated wording:</span> ${esc(e.targetText || "Unavailable")}<br>
      <span class="nmeta">${esc(e.description || e.locator || "Evidence record")}</span><br>
      <span class="nmeta">claim ${agreementLabel(e.claimAgreement)} · event ${agreementLabel(e.eventAgreement)} · entity ${agreementLabel(e.entityAgreement)} · location ${agreementLabel(e.locationAgreement)} · time ${agreementLabel(e.timeAgreement)}</span></div>`).join("");
  }
  function renderHistory(history) {
    if (!history || !history.length) return '<div class="receipt-status">No later state changes are recorded.</div>';
    return history.map((h) => `<div class="receipt-history-row"><strong>${esc(h.action || h.state || "update")}</strong> · ${esc(fmtDate(h.occurredAt || h.at))}<br>
      <span class="nmeta">${esc(h.fromState || h.fromEditorialState || h.publicationState || "")}${(h.fromState || h.fromEditorialState) ? " → " : ""}${esc(h.toState || h.toEditorialState || "")}</span><br>${esc(h.reason || h.summary || "No reason supplied")}</div>`).join("");
  }
  function renderCorrections(corrections) {
    if (!corrections || !corrections.length) return '<div class="receipt-status">No correction has been issued for this Story.</div>';
    return corrections.map((c) => {
      const path = safeCorrectionPath(c.correctionPath);
      const replacement = c.correctedText || "Withdrawn; no replacement wording.";
      return `<div class="receipt-correction-row" id="${esc(c.correctionId || "")}"><strong>${esc(c.correctionType || "correction")}</strong> · ${esc(fmtDate(c.issuedAt))}<br>
        <span class="nmeta">Superseded:</span> ${esc(c.priorText || "")}<br><span class="nmeta">Replacement:</span> ${esc(replacement)}<br>
        <span class="nmeta">Reason:</span> ${esc(c.reason || "")}<br><span class="nmeta">Reviewer:</span> ${esc(reviewName(c.humanReview))}<br>
        ${path ? `<a class="story-source" href="${esc(path)}">Open stable fixture correction path →</a>` : '<span class="nmeta">Stable correction path unavailable</span>'}</div>`;
    }).join("");
  }
  function receiptMarkup(receipt) {
    const verification = receipt.verification;
    const verificationCopy = verification
      ? `${esc(verification.outcome || "reviewed")} · ${esc(verification.reason || "No reason recorded")} · reviewer ${esc(reviewName(verification.humanReview))}`
      : "No truth evaluation was performed. A source report is not a Live Wire confirmation.";
    return `<div class="receipt-section"><h3>Claim and state</h3><p class="receipt-claim">${esc(receipt.claimText || "Claim unavailable")}</p>
        <div class="receipt-status ${["corrected", "retracted"].includes(receipt.editorialState) ? "warn" : ""}" style="margin-top:12px">${esc(receipt.editorialState || "reported")} · publication ${esc(receipt.publicationState || "withheld")}</div></div>
      <div class="receipt-section"><h3>Clock truth</h3><dl class="receipt-grid">
        <dt>Source published</dt><dd>${esc(fmtDate(receipt.sourcePublishedAt))}</dd><dt>Live Wire first saw</dt><dd>${esc(fmtDate(receipt.firstSeenAt))}</dd>
        <dt>Live Wire published</dt><dd>${esc(fmtDate(receipt.liveWirePublishedAt))}</dd><dt>Record updated</dt><dd>${esc(fmtDate(receipt.updatedAt))}</dd></dl></div>
      <div class="receipt-section"><h3>Sources</h3>${renderSources(receipt.sources)}</div>
      <div class="receipt-section"><h3>Evidence relationship</h3>${renderEvidence(receipt.evidence)}</div>
      <div class="receipt-section"><h3>Verification</h3><div class="receipt-status">${verificationCopy}</div></div>
      <div class="receipt-section"><h3>Reviewer / provenance</h3><div class="receipt-status">${esc(reviewName(receipt.reviewer))}<br><span class="nmeta">Synthetic fixture evidence only · no production authority · Contract ${esc(receipt.schemaVersion || "unknown")} · Receipt ${esc(receipt.receiptId || "unknown")}</span></div></div>
      <div class="receipt-section"><h3>Update history</h3>${renderHistory(receipt.updateHistory)}</div>
      <div class="receipt-section"><h3>Corrections and superseded text</h3>${renderCorrections(receipt.corrections)}
        ${(receipt.supersededTexts || []).length ? `<div class="receipt-status warn" style="margin-top:10px">Preserved prior wording: ${esc(receipt.supersededTexts.join(" · "))}</div>` : ""}</div>`;
  }
  function openReceiptPanel(title, eyebrow, opener) {
    receiptOpener = opener || document.activeElement;
    receiptOpenerId = receiptOpener && receiptOpener.id ? receiptOpener.id : "";
    $("#receiptTitle").textContent = title;
    $("#receiptEyebrow").textContent = eyebrow;
    $("#receiptBody").innerHTML = '<div class="receipt-empty"><span class="spinner"></span><br>Loading attributable records…</div>';
    $("#receiptBackdrop").hidden = false;
    document.body.classList.add("receipt-open");
    $("#receiptPanel").focus();
  }
  function closeReceiptPanel() {
    $("#receiptBackdrop").hidden = true;
    document.body.classList.remove("receipt-open");
    let returnTarget = receiptOpener && receiptOpener.isConnected ? receiptOpener : null;
    if (!returnTarget && receiptStoryId) {
      returnTarget = $$("#wireFeed .receipts-btn").find((button) => button.dataset.storyId === receiptStoryId) || null;
    }
    if (!returnTarget && receiptOpenerId) returnTarget = document.getElementById(receiptOpenerId);
    if (returnTarget && typeof returnTarget.focus === "function") returnTarget.focus();
    receiptOpener = null;
    receiptOpenerId = "";
    receiptStoryId = "";
  }
  async function openStoryReceipts(storyId, opener) {
    if (!storyId) return;
    receiptStoryId = storyId;
    openReceiptPanel("Story evidence", "Receipts", opener);
    try {
      const response = await fetch("/api/story?id=" + encodeURIComponent(storyId), { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.available || !data.receipts || !data.receipts.length) throw new Error("receipt unavailable");
      $("#receiptTitle").textContent = (data.story && (data.story.title || data.story.headline)) || "Story evidence";
      $("#receiptBody").innerHTML = data.receipts.map(receiptMarkup).join("");
    } catch (e) {
      $("#receiptBody").innerHTML = '<div class="receipt-empty" role="alert"><strong>Receipts unavailable.</strong><br>The preview failed closed and will not substitute unreviewed data.</div>';
    }
  }
  async function openCorrections(opener) {
    receiptStoryId = "";
    openReceiptPanel("Corrections ledger", "Public accountability", opener);
    try {
      const response = await fetch("/api/corrections", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok || !data.available) throw new Error("ledger unavailable");
      $("#receiptBody").innerHTML = data.corrections && data.corrections.length
        ? `<div class="receipt-section"><h3>${esc(String(data.corrections.length))} attributable correction${data.corrections.length === 1 ? "" : "s"}</h3>${renderCorrections(data.corrections)}</div>`
        : '<div class="receipt-empty">No fixture correction is recorded.</div>';
    } catch (e) {
      $("#receiptBody").innerHTML = '<div class="receipt-empty" role="alert"><strong>Correction ledger unavailable.</strong><br>The preview failed closed.</div>';
    }
  }
  $("#receiptClose").addEventListener("click", closeReceiptPanel);
  $("#receiptBackdrop").addEventListener("click", (event) => { if (event.target === $("#receiptBackdrop")) closeReceiptPanel(); });
  $("#correctionsBtn").addEventListener("click", (event) => openCorrections(event.currentTarget));
  document.addEventListener("keydown", (event) => {
    if ($("#receiptBackdrop").hidden) return;
    if (event.key === "Escape") { event.preventDefault(); closeReceiptPanel(); return; }
    if (event.key !== "Tab") return;
    const focusable = $$("a[href],button:not([disabled]),[tabindex]:not([tabindex='-1'])", $("#receiptPanel"));
    if (!focusable.length) { event.preventDefault(); $("#receiptPanel").focus(); return; }
    const first = focusable[0], last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });

  // The public Rumor Desk is a fixed fail-closed maintenance surface.
  // It intentionally performs no reads, writes, scoring, publication or playback.

  // ============================ public-source monitor ============================
  const INTEL_ICON = { filing: "📄", insider: "👔", quake: "🌐", alert: "⚠️",
    recall: "⛔", court: "⚖️", wiki: "📈", gdelt: "🛰️", reddit: "💬" };
  const INTEL_SRC = { "SEC EDGAR": "blue", "USGS": "amber", "NWS": "amber",
    "openFDA": "amber", "CourtListener": "", "Wikipedia": "" };
  function renderIntel() {
    const board = $("#intelBoard"); if (!board) return;
    const st = $("#intelStatus");
    if (st) st.textContent = (INTEL_META.status === "ok" ? INTEL.length + " source items" : (INTEL_META.status || ""))
      + (INTEL_META.asof ? " · " + INTEL_META.asof : "");
    if (!INTEL.length && !INTEL_BRIEFS.length) {
      board.innerHTML = INTEL_META.status === "contained"
        ? '<div class="empty">Public-source monitoring is off in the read-only evidence preview. No acquisition or analysis worker is running.</div>'
        : (INTEL_META.status ? `<div class="empty">${esc(INTEL_META.status)}. Public-source monitoring runs with live generation enabled.</div>`
          : `<div class="empty"><span class="spinner"></span>loading public-source links — filings, court records, seismographs and alerts…</div>`);
      return;
    }
    // Generated source briefs remain explicitly labeled and require verification.
    const briefsHtml = INTEL_BRIEFS.map((b) => {
      const conv = (b.conviction || "medium").toLowerCase();
      const cc = conv === "high" ? "var(--green)" : conv === "low" ? "var(--tx3)" : "var(--amber)";
      const sup = b.supersedes ? '<span class="intel-tag ahead">AI-generated analysis</span>' : "";
      const conf = b.confidence ? `<span class="ib-conf ${esc(b.confidence)}">${esc(b.confidence)}</span>` : "";
      return `<div class="intel-brief" ${b.url ? `data-url="${esc(b.url)}"` : ""}>
        <div class="ib-kick">🛰️ GENERATED SOURCE BRIEF ${sup}
          <span class="ib-conv" style="color:${cc};border-color:${cc}55">${esc(conv)} conviction</span>${conf}</div>
        <div class="ib-head">${esc(b.headline)}</div>
        ${b.signal ? `<div class="ib-signal"><b>Signal:</b> ${esc(b.signal)}</div>` : ""}
        ${b.read ? `<div class="ib-read">${esc(b.read)}</div>` : ""}
        ${b.media_gap ? `<div class="ib-gap"><b>Context to verify:</b> ${esc(b.media_gap)}</div>` : ""}
        ${b.what_to_watch ? `<div class="ib-watch"><b>Watch:</b> ${esc(b.what_to_watch)}</div>` : ""}
        <div class="ib-foot">
          ${(b.sources && b.sources.length) ? `<span class="nmeta">Sources: ${esc(b.sources.join(", "))}</span>` : ""}
          ${b.asof ? `<span class="nmeta" style="margin-left:auto">${esc(b.asof)}</span>` : ""}
        </div>
      </div>`;
    }).join("");
    board.innerHTML = briefsHtml + INTEL.map((it) => {
      const edge = it.edge || 0;
      const ec = edge >= 75 ? "var(--green)" : edge >= 55 ? "var(--amber)" : "var(--tx3)";
      const ic = INTEL_ICON[it.kind] || "•";
      const ago = it.ts ? rel(new Date(it.ts * 1000).toISOString()) : "";
      const ents = [].concat((it.entities || {}).companies || [], (it.entities || {}).people || [],
        ((it.entities || {}).tickers || []).map((t) => "$" + t), (it.entities || {}).places || []).slice(0, 4);
      const lead = it.covered
        ? '<span class="intel-tag covered">linked source item</span>'
        : '<span class="intel-tag ahead">primary-source item</span>';
      return `<div class="intel-card" ${it.url ? `data-url="${esc(it.url)}"` : ""}>
        <div class="intel-top">
          <span class="intel-edge" style="color:${ec};border-color:${ec}55">RANK ${edge}</span>
          <span class="intel-src tag ${INTEL_SRC[it.source] || ""}">${ic} ${esc(it.source)}</span>
          ${lead}${ago ? `<span class="nmeta" style="margin-left:auto">${ago}</span>` : ""}
        </div>
        <div class="intel-head">${esc(it.title)}</div>
        ${it.detail ? `<div class="intel-detail">${esc(it.detail)}</div>` : ""}
        <div class="intel-foot">
          ${it.why ? `<span class="nmeta">${esc(it.why)}</span>` : ""}
          ${ents.length ? `<span class="intel-ents">${ents.map((e) => `<span class="tag">${esc(e)}</span>`).join("")}</span>` : ""}
        </div>
      </div>`;
    }).join("");
    $$("#intelBoard .intel-card[data-url], #intelBoard .intel-brief[data-url]").forEach((c) => c.addEventListener("click", () => openExt(c.dataset.url)));
  }

  // ============================ polling ============================
  async function poll() {
    try {
      const [wj, scj, ij] = await Promise.all([
        fetch("/api/stories", { cache: "no-store" }).then((r) => r.json()).catch(() => ({ available: false })),
        fetch("/api/schedule", { cache: "no-store" }).then((r) => r.json()).catch(() => null),
        fetch("/api/intel", { cache: "no-store" }).then((r) => r.json()).catch(() => ({})),
      ]);
      const nextWire = wj.available ? (wj.previews || []) : [];
      const nextStoryState = wj.available ? (nextWire.length ? "ready" : "empty") : "error";
      const nextWirePayloadKey = JSON.stringify([nextStoryState, nextWire]);
      const wireChanged = nextWirePayloadKey !== wirePayloadKey;
      const initialWireLoad = wirePayloadKey === "";
      if (wireChanged) {
        WIRE = nextWire;
        STORY_LOAD_STATE = nextStoryState;
        wirePayloadKey = nextWirePayloadKey;
        announceWireChange(initialWireLoad);
      }
      INTEL = ij.intel || INTEL; INTEL_META = ij || INTEL_META; INTEL_BRIEFS = ij.briefs || INTEL_BRIEFS;
      if (scj && scj.now) {
        SCHED = scj;
        if (!schedDayPinned) schedDay = SCHED.day_type === "weekends" ? "weekend" : "weekday";
        renderProgramBar();
      } else if (scj && scj.mode === "contained") {
        SCHED = null;
        SCHEDULE_LOAD_STATE = "contained";
      }
      $("#status").textContent = WIRE.length ? (WIRE.length + " inspectable Stories") : (STORY_LOAD_STATE === "error" ? "evidence preview unavailable" : "");
      renderTicker();
      const tab = document.body.getAttribute("data-tab");
      if (tab === "wire" && wireChanged) renderWire();
      if (tab === "intel") renderIntel();
      if (tab === "schedule") renderSchedule();
    } catch (e) { $("#status").textContent = "server not responding"; }
    refreshRundown(); // keep the next-lap rundown fresh
  }

  // ============================ init ============================
  async function init() {
    clock(); setInterval(clock, 1000); setInterval(tickTimes, 1000);
    $("#pipBtn").addEventListener("click", () => { pipOn = !pipOn; updatePip(); });
    $("#pipDock").addEventListener("click", () => { pipOn = false; updatePip(); });
    initPipDrag();
    renderWireFilters();
    $$("[data-day]").forEach((b) => b.addEventListener("click", () => {
      schedDay = b.dataset.day; schedDayPinned = true;
      $$("[data-day]").forEach((x) => x.classList.toggle("on", x === b));
      renderSchedule();
    }));
    await loadAnalysts();
    await poll();
    await refreshRundown();
    setInterval(poll, 5000);
  }
  // Surface a boot failure instead of dying to a silent blank page (e.g. if a
  // third-party-frame API throws on livewire.show). The static chrome still renders.
  init().catch((e) => { try { console.error("Live Wire boot error:", e); } catch (_) {} });
})();
