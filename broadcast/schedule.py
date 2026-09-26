#!/usr/bin/env python3
"""
Live Wire — the network programming engine.

Loads the on-air schedule (anchors + the 24/7 daypart grid) from schedule.json and
answers "what's on right now?" by wall-clock time in the network timezone. A real
network runs different shows with different anchors at different hours — this module
is that grid. The rundown writer (anchor.py) asks current_program() each rebuild and
writes scripts in that show's voice; the UI renders the grid as a TV guide.

Pure data + time logic, no LLM. Colors are sanitized on load (the schedule is
machine-generated and occasionally emits a bad hex).
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

_HERE = os.path.dirname(os.path.abspath(__file__))
_DATA = json.load(open(os.path.join(_HERE, "schedule.json")))

NETWORK = _DATA.get("network", {"name": "Live Wire", "callsign": "LWN", "tagline": "The news that never sleeps."})
TIMEZONE = _DATA.get("timezone", "America/New_York")
_TZ = None
if ZoneInfo is not None:
    try:
        _TZ = ZoneInfo(TIMEZONE)
    except Exception:
        _TZ = None

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _hex(c, fallback):
    c = (c or "").strip()
    return c if _HEX.match(c) else fallback


def _darken(hexc, f=0.74):
    hexc = _hex(hexc, "#8a8a8a")
    r, g, b = int(hexc[1:3], 16), int(hexc[3:5], 16), int(hexc[5:7], 16)
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


def _svg_params(av):
    av = av or {}
    skin = _hex(av.get("skin"), "#C99B7A")
    return {
        "skin": skin, "skinShadow": _darken(skin, 0.78),
        "hair": _hex(av.get("hair"), "#2b2b30"),
        "jacket": _hex(av.get("jacket"), "#1B212B"),
        "tie": _hex(av.get("tie"), "#378ADD"),
        "shirt": "#E6EAF0", "mouth": "#7a3b3b", "mouthLine": "#5a2b2b", "eye": "#1a1a1a",
        "hairStyle": av.get("hairStyle", "short"),
        "facialHair": av.get("facialHair", "none"),
        "glasses": bool(av.get("glasses")),
        "age": av.get("age", "mid"),
    }


# ---- roster -----------------------------------------------------------------
ANCHORS = []
for a in _DATA.get("anchors", []):
    ANCHORS.append({
        "id": a["id"], "name": a["name"], "title": a.get("title", ""),
        "persona": a.get("persona", ""), "beat": a.get("beat", "general"),
        "voice": a.get("voice", "daniel"),
        "avatar": {"mode": "svg", "svgParams": _svg_params(a.get("avatar"))},
    })
ANCHOR_BY_ID = {a["id"]: a for a in ANCHORS}
ANCHOR_IDS = [a["id"] for a in ANCHORS]


def voice_for_analyst(anchor_id):
    a = ANCHOR_BY_ID.get(anchor_id)
    return a["voice"] if a else "daniel"


def anchors_public():
    """Roster for the browser (/api/analysts): id, name, title, beat, avatar spec."""
    return [{"id": a["id"], "name": a["name"], "title": a["title"], "beat": a["beat"],
             "persona": a["persona"], "avatar": a["avatar"]} for a in ANCHORS]


# ---- programs ---------------------------------------------------------------
PROGRAMS = list(_DATA.get("programs", []))


def _mins(hhmm):
    try:
        h, m = str(hhmm).split(":")
        return int(h) * 60 + int(m)
    except Exception:
        return 0


def _now():
    return datetime.now(_TZ) if _TZ is not None else datetime.now()


def _day_type(dt):
    return "weekends" if dt.weekday() >= 5 else "weekdays"


def _matches_day(prog, day_type):
    return prog.get("days") in (day_type, "daily")


def current_program(now=None):
    now = now or _now()
    dt = _day_type(now)
    cur = now.hour * 60 + now.minute
    for p in PROGRAMS:
        if not _matches_day(p, dt):
            continue
        s, e = _mins(p["start"]), _mins(p["end"])
        if e == 0:
            e = 1440
        if s <= cur < e:
            return p
    return PROGRAMS[0] if PROGRAMS else None


def next_program(now=None):
    now = now or _now()
    dt = _day_type(now)
    cur = now.hour * 60 + now.minute
    cands = sorted((p for p in PROGRAMS if _matches_day(p, dt)), key=lambda p: _mins(p["start"]))
    for p in cands:
        if _mins(p["start"]) > cur:
            return p
    # nothing later today → first show of TOMORROW's grid (handles Fri→Sat, Sun→Mon)
    tomorrow = "weekends" if (now.weekday() + 1) % 7 >= 5 else "weekdays"
    nxt = sorted((p for p in PROGRAMS if _matches_day(p, tomorrow)), key=lambda p: _mins(p["start"]))
    return nxt[0] if nxt else (cands[0] if cands else None)


def program_anchors(prog):
    return [ANCHOR_BY_ID[i] for i in (prog.get("anchor_ids") or []) if i in ANCHOR_BY_ID] or ANCHORS[:1]


def program_public(prog):
    if not prog:
        return None
    return {
        "id": prog["id"], "name": prog["name"], "tagline": prog.get("tagline", ""),
        "start": prog["start"], "end": prog["end"], "days": prog.get("days", "daily"),
        "satire_level": prog.get("satire_level", 2), "beats": prog.get("beats", []),
        "anchors": [{"id": a["id"], "name": a["name"], "title": a["title"]} for a in program_anchors(prog)],
    }


# There is one channel: the satirical daypart schedule above. The public edition
# has no markets desk, trading signals, or ticker feed.


def schedule_grid(day_type):
    progs = sorted((p for p in PROGRAMS if _matches_day(p, day_type)), key=lambda p: _mins(p["start"]))
    return [program_public(p) for p in progs]


def now_next(now=None):
    now = now or _now()
    return {
        "now": program_public(current_program(now)),
        "next": program_public(next_program(now)),
        "clock": now.strftime("%H:%M"),
        "day_type": _day_type(now),
        "tz": TIMEZONE,
    }


if __name__ == "__main__":
    nn = now_next()
    print(f"{NETWORK['name']} ({NETWORK['callsign']}) — {NETWORK['tagline']}")
    print(f"\nNOW [{nn['day_type']} {nn['clock']} {nn['tz']}]: {nn['now']['name']} — {nn['now']['tagline']}")
    print("  anchors:", ", ".join(a["name"] for a in nn["now"]["anchors"]))
    print(f"NEXT: {nn['next']['name']} at {nn['next']['start']}")
    print(f"\n{len(ANCHORS)} anchors, {len(PROGRAMS)} programs.")
