#!/usr/bin/env python3
"""Live Wire public-edition server.

Default is demo mode: canned satirical rundowns, cartoon mouth sync, no API
keys. LLM generation, ElevenLabs voices, and rumor scanning are opt-in via
environment variables. There is no markets desk, trading signal, or chart code
in this edition.

Network posture: binds 127.0.0.1 by default. Every request must carry a local
(or explicitly allowed) Host header, and anything that can spend credits or
start work (POST /api/tts, POST /api/rumors/scan, GET /api/media) must also be
same-origin; POSTs must be application/json. See README "Security and privacy".
"""
import json
import math
import os
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "ingestion"))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

from ingestion.newswire import fetch_wire, resolve_video  # noqa: E402
from ingestion.rumors import refresh_rumors, rumor_id, same_story  # noqa: E402
from ingestion.intel import refresh_intel                 # noqa: E402  (primary-source intel)
from broadcast import intel_desk                          # noqa: E402  (deep AI investigations)
from broadcast.voice import synthesize, VOICES, VOICE_IDS  # noqa: E402
from broadcast.anchor import build_rundown        # noqa: E402
from broadcast.report import build_wire_report, report_slug  # noqa: E402
from broadcast import llm                          # noqa: E402
from broadcast import execution                    # noqa: E402
from broadcast import demo                          # noqa: E402
from app.gate1_read_model import (                 # noqa: E402
    FixtureReadError,
    Gate1ReadModel,
)
from app.gate1_capability import (                 # noqa: E402
    CAPABILITY_ID as GATE1_CAPABILITY_ID,
    CAPABILITY_MANIFEST as GATE1_CAPABILITY_MANIFEST,
    CAPABILITY_VERSION as GATE1_CAPABILITY_VERSION,
    capability_manifest_state as _capability_manifest_state,
    fixture_health as _fixture_health,
)
from editorial.models import SCHEMA_VERSION as EDITORIAL_SCHEMA_VERSION  # noqa: E402
from broadcast.schedule import (                   # noqa: E402
    anchors_public, voice_for_analyst, current_program, program_public,
    now_next, schedule_grid, NETWORK)

HOST = os.getenv("LIVE_WIRE_HOST", "127.0.0.1").strip() or "127.0.0.1"
PORT = int(os.getenv("LIVE_WIRE_PORT", "8899"))
VERSION = "0.1.0"
SCHEMA_VERSION = "public.1"
RELEASE_STATE = "public"
BUILD_ID = (os.getenv("LIVE_WIRE_BUILD_ID") or os.getenv("VERCEL_GIT_COMMIT_SHA")
            or "uncommitted")[:64]
CORRECTION_URL = "/corrections/2026-07-11-automated-rumor-confirmations/"
GATE1_READ_MODEL = Gate1ReadModel()


def background_loops_enabled():
    """Wire/rundown/intel loops feed live generation, so they start automatically
    when LIVE_WIRE_GENERATION_ENABLED=1. LIVE_WIRE_START_LOOPS=0/1 overrides."""
    raw = os.getenv("LIVE_WIRE_START_LOOPS", "").strip()
    if raw in ("0", "1"):
        return raw == "1"
    return execution.generation_enabled()

# Rumor ledger stays empty unless the operator opts into LIVE_WIRE_RUMORS_ENABLED=1.
EDITORIAL_CONTAINMENT = not execution.rumors_enabled()
WIRE_SEC = 120
RUMORS_SEC = 420
RUNDOWN_SEC = 180
BREAKING_SEC = 45
VIEWER_TTL = 180

MODE = "demo" if execution.demo_mode() else ("live" if llm.available() else "offline")

STATE = {
    "mode": MODE,
    "wire": [], "wireAsof": None,                 # aggregated public news wire
    "breaking": [], "breakingAsof": None,         # fast-lane in-the-minute breaking
    "reports": {}, "reportsOrder": [], "reportWriting": False,  # Live Wire's own re-reported articles
    "rumors": [], "rumorsAsof": None, "rumorsStatus": "contained",
    "rumorsScanning": False, "rumorsLastScan": 0.0,
    "rumor_tracks": {},      # id -> tracked rumor (history, status, confirmation) — persisted
    "rumorsConfirmed": 0,    # running count of rumors the live wire later confirmed
    "intel": [], "intelAsof": None, "intelStatus": "warming up",   # on-the-ground primary-source intel
    "intelBriefs": [], "intelBriefIds": [], "intelBriefAsof": None, "intelBriefing": False,   # deep AI investigations
    "rundowns": {},          # "world" -> the rundown the loop last built (raw)
    "analysts": anchors_public(),
    "lastViewer": 0.0,       # set by GET /api/rundown; loops only spend while watched
}
_lock = threading.Lock()


# ---- wire (our own aggregated feed) -----------------------------------------
def wire_loop():
    while True:
        try:
            w = fetch_wire(limit=90)
            stamp = time.strftime("%H:%M:%S")
            with _lock:
                STATE["wire"] = w
                STATE["wireAsof"] = stamp
        except Exception as e:  # noqa: BLE001
            with _lock:
                STATE["wireAsof"] = f"wire error: {type(e).__name__}"
        time.sleep(WIRE_SEC)


# ---- rumors: tracking across scans + confirmation loop ----------------------
# A "rumor DESK" needs memory. Each scanned rumor is merged onto a persistent TRACK
# (keyed by a stable id, fuzzy-matched so a re-worded story stays ONE card), so we
# can show its arc (credibility rising/falling), how long we've flagged it, and —
# the killer feature — auto-confirm it when the live wire proves it right ("Live
# Wire called it first"). Deterministic: the confirmation loop uses zero LLM.
DATA_DIR = os.path.join(ROOT, "data")
TRACKS_PATH = os.path.join(DATA_DIR, "rumor_tracks.json")
MAX_TRACKS = 120
MAX_HISTORY = 40
_FADE_SEC = 8 * 3600           # not re-seen in 8h + unconfirmed -> "faded"
_DENIAL = ("denies", "denied", "denial", "false", "no truth", "not true", "debunk",
           "walks back", "walked back", "refutes", "refuted", "pushes back", "unfounded")


def _persist_tracks():
    if EDITORIAL_CONTAINMENT:
        return False
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with _lock:
            snap = dict(STATE["rumor_tracks"])
        tmp = TRACKS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(snap, f, default=str)
        os.replace(tmp, TRACKS_PATH)
    except (OSError, ValueError):
        pass


def _load_tracks():
    if EDITORIAL_CONTAINMENT:
        with _lock:
            STATE["rumor_tracks"] = {}
            STATE["rumors"] = []
            STATE["rumorsConfirmed"] = 0
            STATE["rumorsStatus"] = "contained"
        return False
    try:
        with open(TRACKS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            with _lock:
                STATE["rumor_tracks"] = data
                STATE["rumorsConfirmed"] = sum(1 for t in data.values() if t.get("status") == "confirmed")
    except (OSError, ValueError):
        pass


def _rumors_view():
    """The list the UI reads (STATE['rumors']), derived from the tracks: confirmed
    floats up, debunked sinks, otherwise by jump_score."""
    if EDITORIAL_CONTAINMENT:
        return []
    with _lock:
        tracks = list(STATE["rumor_tracks"].values())

    def rank(t):
        base = t.get("jump_score", 0)
        st = t.get("status")
        if st == "confirmed":
            base += 60
        elif st == "debunked":
            base -= 300
        elif st == "faded":
            base -= 40
        return base
    return sorted(tracks, key=rank, reverse=True)


def upsert_tracks(vetted):
    """Merge a fresh scan into the persistent tracks. Returns True if anything changed."""
    if EDITORIAL_CONTAINMENT:
        return False
    now = time.time()
    changed = False
    with _lock:
        tracks = STATE["rumor_tracks"]
        for r in vetted:
            rid = r.get("id") or rumor_id(r.get("headline"), r.get("entities"))
            tr = tracks.get(rid)
            if tr is None:   # fuzzy: same evolving story under a different wording?
                for cand in tracks.values():
                    if same_story(r.get("headline"), r.get("entities"),
                                  cand.get("headline"), cand.get("entities")):
                        tr, rid = cand, cand["id"]
                        break
            new_cred = r.get("credibility", 0)
            if tr is not None:
                prev = tr.get("credibility", new_cred)
                for k, v in r.items():        # refresh latest snapshot fields
                    if k != "id":
                        tr[k] = v
                tr["id"] = rid
                tr["last_seen"] = now
                tr["times_seen"] = tr.get("times_seen", 1) + 1
                tr["cred_prev"] = prev
                tr["trend"] = ("rising" if new_cred > prev + 3 else
                               "falling" if new_cred < prev - 3 else "flat")
                hist = tr.get("history") or []
                hist.append({"ts": now, "credibility": new_cred, "jump_score": r.get("jump_score", 0)})
                tr["history"] = hist[-MAX_HISTORY:]
                if tr.get("status") not in ("confirmed", "debunked"):
                    tr["status"] = ("strengthening" if tr["trend"] == "rising" else
                                    "fading" if tr["trend"] == "falling" else "developing")
            else:
                tr = dict(r)
                tr["id"] = rid
                tr["first_seen"] = now
                tr["last_seen"] = now
                tr["times_seen"] = 1
                tr["cred_prev"] = new_cred
                tr["trend"] = "new"
                tr["status"] = "new"
                tr["history"] = [{"ts": now, "credibility": new_cred, "jump_score": r.get("jump_score", 0)}]
                tracks[rid] = tr
            changed = True
        # cap: evict oldest-seen non-confirmed tracks
        if len(tracks) > MAX_TRACKS:
            evictable = sorted((t for t in tracks.values() if t.get("status") != "confirmed"),
                               key=lambda t: t.get("last_seen", 0))
            for t in evictable[: len(tracks) - MAX_TRACKS]:
                tracks.pop(t.get("id"), None)
    if changed:
        _persist_tracks()
    return changed


def close_the_loop():
    """Public edition never auto-confirms or auto-debunks a rumor.

    Word overlap between chatter and a later headline is not the same event.
    The July 11 correction retired automated confirmation and lead-time claims.
    """
    return False


def _do_rumor_scan():
    """Run one scan+vet pass and merge it into the tracks. Slow (~3 min web search),
    so always runs in its own thread; the rumorsScanning flag prevents overlap."""
    if EDITORIAL_CONTAINMENT:
        with _lock:
            STATE["rumorsScanning"] = False
            STATE["rumorsStatus"] = "contained"
        return False
    try:
        r = refresh_rumors(hours=6, n=6)
        changed = upsert_tracks(r) if r else False
        view = _rumors_view()          # locks internally — compute before taking _lock
        with _lock:
            STATE["rumors"] = view
            if changed:
                STATE["rumorsAsof"] = time.strftime("%H:%M:%S")
            STATE["rumorsStatus"] = "ok" if view else (
                "no rumors surfaced this pass" if llm.available() else
                "rumor desk offline — try again later")
    except Exception as e:  # noqa: BLE001
        with _lock:
            STATE["rumorsStatus"] = f"rumors error: {type(e).__name__}"
    finally:
        with _lock:
            STATE["rumorsScanning"] = False


def trigger_rumor_scan():
    """Kick off a scan now unless one is already running. Returns True if started."""
    if EDITORIAL_CONTAINMENT:
        return False
    with _lock:
        if STATE.get("rumorsScanning"):
            return False
        STATE["rumorsScanning"] = True
        STATE["rumorsLastScan"] = time.time()   # both the timer and the button share this
        STATE["rumorsStatus"] = "scanning the web for chatter…"
    threading.Thread(target=_do_rumor_scan, daemon=True).start()
    return True


def rumors_loop():
    if EDITORIAL_CONTAINMENT:
        return
    while True:
        try:
            now = time.time()
            with _lock:
                viewer = (now - STATE["lastViewer"]) < VIEWER_TTL
                scanning = STATE.get("rumorsScanning")
                last = STATE.get("rumorsLastScan", 0.0)
            # the web scan + Claude vet are the spend here — only while watched,
            # and the cooldown is shared with the manual Scan button (STATE-based)
            if viewer and not scanning and now - last >= RUMORS_SEC:
                trigger_rumor_scan()
        except Exception as e:  # noqa: BLE001
            with _lock:
                STATE["rumorsStatus"] = f"rumors error: {type(e).__name__}"
        time.sleep(15)


# ---- breaking (deterministic fast-lane — no LLM, runs every 45s) -------------
_BREAKING_KW = (
    "breaking", "just in", "killed", "dead", "dies", "attack", "strike", "explosion",
    "shooting", "war", "invasion", "invades", "resign", "arrested", "halt", "halts",
    "crash", "collapse", "emergency", "evacuat", "earthquake", "hostage", "ceasefire",
    "sanction", "recall", "bankrupt", "plunge", "plunges", "surge", "surges", "soar",
    "soars", "tumble", "tumbles", "guilty", "indict", "ousted", "coup", "missile",
)


def _breaking_score(headline, ts, now):
    h = (headline or "").lower()
    impact = sum(1 for k in _BREAKING_KW if k in h)
    if not impact:
        return 0
    age_min = (now - ts) / 60.0 if ts else 9999
    if age_min > 20:
        return 0
    recency = 3 if age_min <= 3 else 2 if age_min <= 8 else 1
    return impact * 2 + recency


def compute_breaking():
    now = time.time()
    with _lock:
        items = list(STATE["wire"])
    out = []
    for it in items:
        sc = _breaking_score(it.get("headline"), it.get("ts") or 0, now)
        if sc >= 4:
            out.append({"headline": it.get("headline"), "source": it.get("source"),
                        "url": it.get("url"), "category": it.get("category"),
                        "ts": it.get("ts"), "published_at": it.get("published_at"),
                        "kind": "news", "score": sc})
    seen, ded = set(), []
    for x in sorted(out, key=lambda a: a["score"], reverse=True):
        k = (x["headline"] or "").lower().strip()
        if k and k not in seen:
            seen.add(k)
            x["report_slug"] = report_slug(x["headline"])   # link to its Wire Report (if/when written)
            ded.append(x)
    return ded[:6]


def _write_report(item):
    """Generate one Wire Report off the breaking item (slow: web research). Threaded."""
    try:
        if item.get("kind") != "news" or not execution.generation_enabled():
            return
        rep = build_wire_report(item, wire=list(STATE["wire"]))
        if rep:
            with _lock:
                STATE["reports"][rep["slug"]] = rep
                STATE["reportsOrder"] = [rep["slug"]] + [s for s in STATE["reportsOrder"] if s != rep["slug"]]
                STATE["reportsOrder"] = STATE["reportsOrder"][:12]
                for old in list(STATE["reports"]):
                    if old not in STATE["reportsOrder"]:
                        STATE["reports"].pop(old, None)
    finally:
        with _lock:
            STATE["reportWriting"] = False


def maybe_write_report(breaking):
    """If the top breaking story is solid and has no Wire Report yet, write one."""
    if not breaking or not execution.generation_enabled():
        return False
    top = breaking[0]
    if top.get("kind") != "news" or (top.get("score") or 0) < 5:
        return False
    slug = top.get("report_slug")
    with _lock:
        if not slug or slug in STATE["reports"] or STATE["reportWriting"]:
            return False
        STATE["reportWriting"] = True
    threading.Thread(target=_write_report, args=(dict(top),), daemon=True).start()
    return True


def breaking_loop():
    time.sleep(6)  # let the first wire land
    while True:
        try:
            b = compute_breaking()
            with _lock:
                STATE["breaking"] = b
                STATE["breakingAsof"] = time.strftime("%H:%M:%S")
            # Gate 0 is read-only: no report writer and no rumor state transition.
        except Exception:  # noqa: BLE001
            pass
        time.sleep(BREAKING_SEC)


# ---- intel (primary-source, on-the-ground signal ahead of media) ------------
INTEL_SEC = 90            # deterministic-signal poll cadence while watched
INTEL_IDLE_SEC = 300      # slow heartbeat when nobody's watching
BRIEF_SEC = 240           # how often the AI intel desk investigates the top new signal
_BRIEF_MAX = 24           # keep the deepest recent briefs


def _brief_intel_once():
    """Investigate the top novel high-edge signals that don't have a deep brief yet.
    Threaded + bounded (1-2 web-search calls) so it stays cheap on the subscription."""
    try:
        with _lock:
            intel = list(STATE["intel"])
            wire = list(STATE["wire"])
            done = set(STATE["intelBriefIds"])
        picks = intel_desk.pick_to_brief(intel, done, n=2)
        for it in picks:
            b = intel_desk.deep_brief(it, intel_pool=intel, wire=wire)
            with _lock:
                STATE["intelBriefIds"] = ([it.get("id")] + STATE["intelBriefIds"])[:200]
                if b:
                    STATE["intelBriefs"] = ([b] + [x for x in STATE["intelBriefs"]
                                                   if x.get("id") != b.get("id")])[:_BRIEF_MAX]
                    STATE["intelBriefAsof"] = time.strftime("%H:%M:%S")
    finally:
        with _lock:
            STATE["intelBriefing"] = False


def intel_loop():
    time.sleep(8)   # let the first wire land so dedup-vs-media works
    last_brief = 0.0
    while True:
        slept = INTEL_SEC
        try:
            now = time.time()
            with _lock:
                viewer = (now - STATE["lastViewer"]) < VIEWER_TTL
                wire = list(STATE["wire"])
                briefing = STATE["intelBriefing"]
            if viewer:
                intel = refresh_intel(wire=wire, limit=40)   # deterministic, keyless, no LLM
                with _lock:
                    STATE["intel"] = intel
                    STATE["intelAsof"] = time.strftime("%H:%M:%S")
                    STATE["intelStatus"] = "ok" if intel else "no primary signal this pass"
                # the deep AI read runs on a slower, bounded cadence in its own thread
                if llm.available() and not briefing and now - last_brief >= BRIEF_SEC:
                    last_brief = now
                    with _lock:
                        STATE["intelBriefing"] = True
                    threading.Thread(target=_brief_intel_once, daemon=True).start()
            else:
                slept = INTEL_IDLE_SEC
        except Exception as e:  # noqa: BLE001
            with _lock:
                STATE["intelStatus"] = f"intel error: {type(e).__name__}"
        time.sleep(slept)


# ---- rundown (anchor scripts) -----------------------------------------------
def rundown_loop():
    time.sleep(4)  # let the first wire fetch land
    last_key, last_build = None, 0.0
    while True:
        try:
            now = time.time()
            with _lock:
                viewer = (now - STATE["lastViewer"]) < VIEWER_TTL
                prev = STATE["rundowns"].get("world")
                wire = list(STATE["wire"])
                rumors = list(STATE["rumors"])
            prog = current_program()
            key = prog["id"] if prog else ""
            # a "weak" rundown (template/standing-by) should be retried fast, not held
            # for the full 3-min gate — otherwise a startup/rotation race shows stale.
            weak = (not prev) or prev.get("count", 0) < 2 or prev.get("mode") == "template"
            due = (key != last_key or now - last_build >= RUNDOWN_SEC
                   or (weak and now - last_build >= 25))
            # need real source material before building (never spend a model call on
            # an empty wire, and don't bake a standing-by card)
            if prog and due and viewer and wire:
                # cold start (no real rundown yet) → fast single-call so we're on air in
                # ~30s; once something's live, rebuild with the full editorial pipeline
                cold = (not prev) or prev.get("mode") in ("building", "template") or prev.get("count", 0) < 2
                rd = build_rundown(prog, wire=wire, rumors=rumors, fast=cold)
                with _lock:
                    STATE["rundowns"]["world"] = rd
                last_key, last_build = key, now
        except Exception:  # noqa: BLE001
            pass
        time.sleep(10)  # poll often; the gate above limits actual rebuilds


# ---- TTS --------------------------------------------------------------------
MAX_TTS_CHARS = 1500     # a segment script is 2-4 sentences; cap vendor spend per call
MAX_BODY_BYTES = 16_384  # JSON bodies are tiny; refuse anything bigger


def resolve_voice_id(body):
    """Map a request to a roster voice. Returns None for an unknown explicit
    voice_id: only ids listed in broadcast.voice.VOICES ever reach the vendor URL."""
    vid = body.get("voice_id")
    if vid not in (None, ""):
        return vid if isinstance(vid, str) and vid in VOICE_IDS else None
    aid = body.get("analyst_id")
    if isinstance(aid, str) and aid:
        alias = voice_for_analyst(aid)
        return VOICES.get(alias, VOICES["daniel"])["voice_id"]
    alias = body.get("voice")
    if isinstance(alias, str) and alias in VOICES:
        return VOICES[alias]["voice_id"]
    return VOICES["daniel"]["voice_id"]


# ---- request origin guard ---------------------------------------------------
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _bare_host(value):
    """'127.0.0.1:8899' -> '127.0.0.1', '[::1]:8899' -> '::1', lowercased."""
    v = (value or "").strip().lower()
    if v.startswith("["):
        return v[1:v.index("]")] if "]" in v else ""
    return v.rsplit(":", 1)[0] if v.count(":") == 1 else v


def allowed_hosts():
    """Loopback names, the bind address (unless it is a wildcard), and any
    LIVE_WIRE_ALLOWED_HOSTS entries (comma-separated) for LAN use."""
    hosts = set(_LOCAL_HOSTS)
    bind = HOST.strip("[]").lower()
    if bind and bind not in ("0.0.0.0", "::"):
        hosts.add(bind)
    for item in os.getenv("LIVE_WIRE_ALLOWED_HOSTS", "").split(","):
        item = _bare_host(item)
        if item:
            hosts.add(item)
    return hosts


# ---- HTTP -------------------------------------------------------------------
STATIC_JS = {"/broadcast.js", "/avatar.js", "/lipsync.js"}
CTYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css",
          ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg",
          ".webp": "image/webp", ".gif": "image/gif", ".json": "application/json"}


_TTS_HITS = []          # timestamps of recent synth requests
_TTS_WINDOW = 60.0
_TTS_MAX = 20           # public synths / minute (legit playback needs ~3; a bot far more)


def _tts_allow():
    now = time.time()
    with _lock:
        _TTS_HITS[:] = [t for t in _TTS_HITS if now - t < _TTS_WINDOW]
        if len(_TTS_HITS) >= _TTS_MAX:
            return False
        _TTS_HITS.append(now)
        return True


def _json_safe(o):
    """Replace NaN/Infinity floats with None recursively. Python's json emits bare
    `NaN`/`Infinity`, which is invalid JSON — one NaN in a feed metric would make the
    whole response unparseable by the browser (JSON.parse throws)."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _json_safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_json_safe(v) for v in o]
    return o


def _gate1_fixture_health():
    return _fixture_health(GATE1_READ_MODEL)


def capability_state():
    """Versioned public truth; contains no host, credential, or operator data."""
    try:
        manifest = _capability_manifest_state(GATE1_READ_MODEL)
    except FixtureReadError:
        gate1_health, fixture_available, manifest_available = "unavailable", False, False
    else:
        gate1_health = manifest["runtimeHealth"]["status"]
        fixture_available = manifest["runtimeHealth"]["fixtureAvailable"]
        manifest_available = True
    exec_state = execution.public_state()
    return {
        "status": "ok" if fixture_available else "degraded",
        "version": VERSION,
        "schema_version": SCHEMA_VERSION,
        "build_id": BUILD_ID,
        "release_state": RELEASE_STATE,
        "containment": exec_state["containment"],
        "generation_enabled": exec_state["generation_enabled"],
        "audio_enabled": exec_state["audio_enabled"],
        "rumors_enabled": exec_state["rumors_enabled"],
        "demo_mode": exec_state["demo_mode"],
        "shared_live": False,
        "background_loops_enabled": background_loops_enabled(),
        "editorial_contract_version": EDITORIAL_SCHEMA_VERSION,
        "gate1_read_only_preview": True,
        "gate1_capability_id": GATE1_CAPABILITY_ID,
        "gate1_capability_version": GATE1_CAPABILITY_VERSION,
        "gate1_capability_health": gate1_health,
        "gate1_fixture_available": fixture_available,
        "gate1_manifest_available": manifest_available,
        "gate1_capability_manifest": GATE1_CAPABILITY_MANIFEST,
        "gate1_fixture_only": True,
        "gate1_data_placement": ["local"],
        "gate1_mutation_enabled": False,
        "gate1_side_effects": [],
        "gate1_estimated_cost": {
            "moneyMicros": 0,
            "modelTokens": 0,
            "concurrency": 1,
            "retries": 0,
            "externalActions": 0,
        },
        "gate1_production_authority": False,
        "correction_url": CORRECTION_URL,
        "generation_metrics": execution.metrics(),
    }


def capability_manifest_state():
    """Load the committed product manifest and bind it to current fixture health."""
    return _capability_manifest_state(GATE1_READ_MODEL)


def gate1_surface_error(error):
    """Return the same closed artifact shape as the isolated Gate 1 reader."""
    return {
        "schemaVersion": EDITORIAL_SCHEMA_VERSION,
        "available": False,
        "ownerProduct": "live-wire",
        "readOnly": True,
        "fixtureOnly": True,
        "productionAuthority": False,
        "error": error,
    }


_PREVIEW_FORBIDDEN = ("rumor", "unconfirmed", "confirmed", "debunked", "called it")
# Content labels a segment carries. `beat` is deliberately NOT checked: it is the
# anchor's desk (Theo and Cyrus sit on the rumors desk), not what the segment says,
# so checking it silently dropped every segment on their shows.
_PREVIEW_LABEL_FIELDS = ("kicker", "status", "kind", "headline", "text", "script")
_PREVIEW_SEGMENT_FIELDS = (
    "id", "analyst_id", "beat", "kicker", "headline", "text",
    "published_at", "ts", "age_min",
)


def _standby_rundown():
    """Live mode before the first model-written rundown lands (or when every
    segment was withheld). Never cached, never the canned demo pack."""
    program = program_public(current_program()) or {
        "id": "live_wire", "name": NETWORK.get("name", "Live Wire"), "tagline": "",
        "anchors": [{"id": "vance", "name": "Sterling Vance", "title": "Chief Anchor"}],
    }
    anchors = program.get("anchors") or [{"id": "vance"}]
    return {
        "segments": [{
            "id": "standby",
            "analyst_id": anchors[0]["id"],
            "beat": "general",
            "kicker": "STANDING BY",
            "headline": f"{program['name']} is gathering the latest wire",
            "text": ("Live Wire is reading the latest wire stories. "
                     "The first rundown will be on air shortly."),
            "breaking": False,
        }],
        "count": 1,
        "mode": "warming_up",
        "program": program,
        "audio_enabled": execution.public_state()["audio_enabled"],
        "release_state": RELEASE_STATE,
    }


def public_preview_rundown(rundown):
    """Return a public rundown. Demo mode serves canned satire scripts; live
    mode serves the model-written rundown minus anything rumor-flavoured."""
    if execution.demo_mode():
        return demo.public_rundown(RELEASE_STATE)
    source = rundown if isinstance(rundown, dict) else {}
    safe = []
    for segment in source.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        labels = " ".join(str(segment.get(key) or "") for key in _PREVIEW_LABEL_FIELDS).lower()
        if segment.get("breaking") or any(term in labels for term in _PREVIEW_FORBIDDEN):
            continue
        clean = {key: segment[key] for key in _PREVIEW_SEGMENT_FIELDS if key in segment}
        clean["breaking"] = False
        if not str(clean.get("kicker") or "").strip():
            clean["kicker"] = "SATIRE"
        safe.append(clean)
    if not safe:
        return _standby_rundown()
    program = source.get("program") if isinstance(source.get("program"), dict) else None
    return {
        "segments": safe,
        "count": len(safe),
        "mode": source.get("mode") or "template",
        "program": program or _standby_rundown()["program"],
        "audio_enabled": execution.public_state()["audio_enabled"],
        "release_state": RELEASE_STATE,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _file(self, path, ctype):
        try:
            with open(path, "rb") as f:
                return self._send(200, f.read(), ctype)
        except OSError:
            return self._send(404, b"not found", "text/plain")

    def _json(self, obj, code=200):
        return self._send(code, json.dumps(_json_safe(obj), default=str, allow_nan=False),
                          "application/json")

    def _read_json(self):
        """Parsed JSON object body, or None when it is too large or not an object."""
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if length < 0 or length > MAX_BODY_BYTES:
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8") or "{}")
        except (UnicodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _host_ok(self):
        """DNS-rebinding guard: the Host header must name this machine (or an
        operator-listed LAN host), whatever address the socket is bound to."""
        return _bare_host(self.headers.get("Host")) in allowed_hosts()

    def _same_origin(self):
        """CSRF guard for routes that spend credits or start work. Browsers mark
        cross-site requests with Sec-Fetch-Site and Origin; non-browser clients
        (curl, scripts) send neither and are allowed on an allowed Host."""
        if not self._host_ok():
            return False
        if (self.headers.get("Sec-Fetch-Site") or "").strip().lower() == "cross-site":
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        parsed = urlparse(origin.strip())
        host = (self.headers.get("Host") or "").strip().lower()
        return parsed.scheme in ("http", "https") and parsed.netloc.lower() == host

    def _refuse(self, code, error):
        return self._json({"error": error}, code=code)

    def do_GET(self):
        path = urlparse(self.path).path
        if not self._host_ok():
            return self._refuse(403, "host_not_allowed")
        if path in ("/", "/index.html"):
            return self._file(os.path.join(ROOT, "app", "broadcast.html"), "text/html; charset=utf-8")
        if path in STATIC_JS:
            return self._file(os.path.join(ROOT, "app", path.lstrip("/")), CTYPES[".js"])
        if path == "/assets/logo.svg":
            return self._file(os.path.join(ROOT, "assets", "logo.svg"), "image/svg+xml")
        if path.rstrip("/") == CORRECTION_URL.rstrip("/"):
            return self._file(
                os.path.join(ROOT, "site", "corrections", "2026-07-11-automated-rumor-confirmations", "index.html"),
                "text/html; charset=utf-8",
            )
        if path.startswith("/avatars/"):
            rel = os.path.normpath(path.lstrip("/"))
            if rel.startswith("avatars" + os.sep) or rel == "avatars":
                ext = os.path.splitext(rel)[1].lower()
                return self._file(os.path.join(ROOT, rel), CTYPES.get(ext, "application/octet-stream"))
            return self._send(403, b"forbidden", "text/plain")

        if path in ("/api/health", "/api/status"):
            return self._json(capability_state())
        if path == "/api/capabilities":
            try:
                manifest = capability_manifest_state()
            except FixtureReadError:
                return self._json({
                    "capabilityId": GATE1_CAPABILITY_ID,
                    "version": GATE1_CAPABILITY_VERSION,
                    "health": "unavailable",
                    "error": "gate1_capability_unavailable",
                }, code=503)
            return self._json(
                manifest,
                code=200 if manifest["runtimeHealth"]["fixtureAvailable"] else 503,
            )
        if path == "/api/stories":
            try:
                return self._json(GATE1_READ_MODEL.story_index())
            except FixtureReadError:
                return self._json(GATE1_READ_MODEL.unavailable(), code=503)
        if path == "/api/story":
            story_id = parse_qs(urlparse(self.path).query).get("id", [""])[0]
            if not story_id:
                return self._json(gate1_surface_error("story_id_required"), code=400)
            try:
                result = GATE1_READ_MODEL.story_detail(story_id)
            except FixtureReadError:
                return self._json(GATE1_READ_MODEL.unavailable(), code=503)
            if result is None:
                return self._json(gate1_surface_error("story_not_found"), code=404)
            return self._json(result)
        if path == "/api/receipts":
            story_id = parse_qs(urlparse(self.path).query).get("story", [""])[0]
            if not story_id:
                return self._json(gate1_surface_error("story_id_required"), code=400)
            try:
                result = GATE1_READ_MODEL.receipts(story_id)
            except FixtureReadError:
                return self._json(GATE1_READ_MODEL.unavailable(), code=503)
            if result is None:
                return self._json(gate1_surface_error("story_not_found"), code=404)
            return self._json(result)
        if path == "/api/corrections":
            try:
                return self._json(GATE1_READ_MODEL.corrections())
            except FixtureReadError:
                return self._json(GATE1_READ_MODEL.unavailable(), code=503)
        if path == "/api/analysts":
            return self._json(anchors_public())
        if path == "/api/schedule":
            nn = now_next()
            return self._json({"network": NETWORK, **nn,
                               "grid_weekday": schedule_grid("weekdays"),
                               "grid_weekend": schedule_grid("weekends")})
        if path == "/api/rundown":
            # One channel. The request never calls a model: rundown_loop builds in
            # the background (only while someone is watching, only with real wire
            # items) and this handler serves the latest build or a standby card.
            with _lock:
                STATE["lastViewer"] = time.time()
                rd = STATE["rundowns"].get("world")
            return self._json(public_preview_rundown(rd))
        if path == "/api/wire":
            qs = parse_qs(urlparse(self.path).query)
            cat = qs.get("cat", [None])[0]
            with _lock:
                w, asof = list(STATE["wire"]), STATE["wireAsof"]
            if cat and cat != "all":
                w = [i for i in w if i.get("category") == cat]
            return self._json({"wire": w, "asof": asof})
        if path == "/api/breaking":
            with _lock:
                return self._json({"breaking": list(STATE["breaking"]), "asof": STATE["breakingAsof"]})
        if path == "/api/report":
            slug = parse_qs(urlparse(self.path).query).get("slug", [""])[0]
            with _lock:
                rep = STATE["reports"].get(slug)
                writing = STATE["reportWriting"]
            if rep:
                return self._json({"report": rep, "status": "ready"})
            return self._json({"report": None, "status": "writing" if writing else "none"})
        if path == "/api/reports":
            with _lock:
                reps = [STATE["reports"][s] for s in STATE["reportsOrder"] if s in STATE["reports"]]
            return self._json({"reports": [{"slug": r["slug"], "headline": r["headline"], "dek": r["dek"],
                                            "category": r["category"], "confidence": r["confidence"],
                                            "asof": r["asof"], "sources": r["sources"]} for r in reps]})
        if path == "/api/media":
            # Scrapes YouTube / the article page: live generation only, same-origin only.
            if not execution.generation_enabled():
                return self._refuse(403, "generation_disabled")
            if not self._same_origin():
                return self._refuse(403, "cross_origin_refused")
            q = parse_qs(urlparse(self.path).query)
            try:
                return self._json(resolve_video(q.get("url", [""])[0], q.get("q", [""])[0]))
            except Exception:  # noqa: BLE001
                return self._json({})
        if path == "/api/rumors":
            return self._json({"rumors": [], "asof": None, "status": "contained",
                               "mode": MODE, "confirmed_count": 0,
                               "correction_url": CORRECTION_URL})
        if path == "/api/rumors/track":
            return self._json({"error": "rumor_history_withdrawn",
                               "correction_url": CORRECTION_URL}, code=410)
        if path == "/api/intel":
            with _lock:
                return self._json({"intel": STATE["intel"], "asof": STATE["intelAsof"],
                                   "status": STATE["intelStatus"],
                                   "briefs": STATE["intelBriefs"], "briefsAsof": STATE["intelBriefAsof"]})
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):
        path = urlparse(self.path).path
        if not self._host_ok():
            return self._refuse(403, "host_not_allowed")
        if path in ("/api/stories", "/api/story", "/api/receipts", "/api/corrections"):
            return self._json({"error": "read_only_surface", "readOnly": True}, code=405)
        if path not in ("/api/rumors/scan", "/api/tts"):
            return self._send(404, b"not found", "text/plain")
        # Both routes can spend credits. A cross-site page cannot send
        # application/json without a CORS preflight (which this server never
        # grants), and the Origin / Sec-Fetch-Site check catches the rest.
        if not self._same_origin():
            return self._refuse(403, "cross_origin_refused")
        if self.headers.get_content_type() != "application/json":
            return self._refuse(415, "json_required")
        body = self._read_json()
        if body is None:
            return self._refuse(413 if self._body_too_large() else 400, "invalid_json_body")
        if path == "/api/rumors/scan":
            if not execution.rumors_enabled():
                return self._json({"error": "rumors_disabled",
                                   "release_state": RELEASE_STATE}, code=403)
            started = trigger_rumor_scan()
            return self._json({"ok": bool(started), "scanning": True})
        if path == "/api/tts":
            if not (execution.demo_mode() or execution.generation_enabled()):
                return self._json({"error": "newsroom_authority_required",
                                   "release_state": RELEASE_STATE}, code=403)
            text = body.get("text")
            if not isinstance(text, str) or not text.strip():
                return self._refuse(400, "text_required")
            if len(text) > MAX_TTS_CHARS:
                return self._refuse(413, "text_too_long")
            voice_id = resolve_voice_id(body)
            if voice_id is None:
                return self._refuse(400, "unknown_voice_id")
            if not _tts_allow():
                return self._json({"error": "rate_limited"}, code=429)
            return self._json(synthesize(text, voice_id))

    def _body_too_large(self):
        try:
            return int(self.headers.get("Content-Length") or 0) > MAX_BODY_BYTES
        except (TypeError, ValueError):
            return False

    def do_PUT(self):
        return self._deny_gate1_mutation()

    def do_PATCH(self):
        return self._deny_gate1_mutation()

    def do_DELETE(self):
        return self._deny_gate1_mutation()

    def _deny_gate1_mutation(self):
        if urlparse(self.path).path in ("/api/stories", "/api/story", "/api/receipts", "/api/corrections"):
            return self._json({"error": "read_only_surface", "readOnly": True}, code=405)
        return self._send(404, b"not found", "text/plain")


def _boot_rumor_tracks():
    """Rehydrate tracked rumors from disk so the desk keeps its memory across restarts."""
    _load_tracks()
    view = _rumors_view()          # acquires _lock internally — must NOT be held here
    with _lock:
        if STATE["rumor_tracks"]:
            STATE["rumors"] = view
            STATE["rumorsStatus"] = "ok"   # we have tracks; not "scanning" until a scan runs


def _start_loops():
    _boot_rumor_tracks()
    if execution.demo_mode():
        with _lock:
            STATE["rundowns"]["world"] = demo.public_rundown(RELEASE_STATE)
            STATE["mode"] = "demo"
    if not background_loops_enabled():
        return []
    threads = []
    loops = [wire_loop, breaking_loop, intel_loop, rundown_loop]
    if execution.rumors_enabled():
        loops.append(rumors_loop)
    for fn in loops:
        thread = threading.Thread(target=fn, daemon=True)
        thread.start()
        threads.append(thread)
    return threads


def start_in_thread():
    url = f"http://{HOST}:{PORT}"
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        raise RuntimeError(f"refusing ambiguous existing listener at {url}") from exc
    _start_loops()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return url


def main():
    url = f"http://{HOST}:{PORT}"
    open_browser = not os.getenv("LIVE_WIRE_NO_BROWSER")
    try:
        srv = ThreadingHTTPServer((HOST, PORT), Handler)
    except OSError as exc:
        print(f"Live Wire refused to reuse an existing listener at {url}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    _start_loops()
    print(f"Live Wire [{MODE}] at {url}")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
