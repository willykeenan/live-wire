"""Canned satirical rundown for keyless demo mode.

Every name, company, and place in this module is invented. Demo mode never
calls an LLM, TTS vendor, or rumor scanner.
"""
from __future__ import annotations

DEMO_PROGRAM = {
    "id": "demo_hour",
    "name": "Live Wire Demo Hour",
    "tagline": "SATIRE · canned scripts · no keys required",
    "anchors": [
        {"id": "vance", "name": "Sterling Vance", "title": "Chief Anchor"},
        {"id": "dawn", "name": "Dawn Delgado", "title": "Morning Anchor"},
        {"id": "theo", "name": "Theo Park", "title": "Culture Desk"},
    ],
}

# Spoken copy is labelled satire. Operators who go live are responsible for
# what they broadcast; this pack is a local preview only.
DEMO_SEGMENTS = (
    {
        "id": "demo-open",
        "analyst_id": "vance",
        "beat": "world",
        "kicker": "SATIRE",
        "headline": "Harbor Grid's lighthouse files a weather report, then apologizes to the weather",
        "text": (
            "Good evening. This is Live Wire Demo Hour, a labelled satire preview. "
            "Tonight Harbor Grid, a fictional coastal utility, published a storm "
            "bulletin so polite it included an apology to the low-pressure system. "
            "Nothing here is a confirmed report. If you are watching this, you are "
            "running canned scripts with no API keys."
        ),
        "breaking": False,
    },
    {
        "id": "demo-tech",
        "analyst_id": "dawn",
        "beat": "tech",
        "kicker": "SATIRE",
        "headline": "Aster Labs open-sources the Helix runtime, immediately loses the USB cable",
        "text": (
            "Aster Labs, an invented research shop in this demo, says it has "
            "open-sourced the Helix runtime. The release notes are three sentences "
            "and a drawing of a cable that is, quote, somewhere in the lab. "
            "Live Wire has not evaluated the claim. This segment is satire."
        ),
        "breaking": False,
    },
    {
        "id": "demo-civic",
        "analyst_id": "theo",
        "beat": "us",
        "kicker": "SATIRE",
        "headline": "Northstar Energy paints a battery and calls it a civic monument",
        "text": (
            "In the fictional town of Vale Crossing, Northstar Energy unveiled a "
            "battery the size of a bus stop and invited residents to picnic on it. "
            "City hall issued a statement that began we are still checking whether "
            "this is a park. Again: satire, labelled, not a news confirmation."
        ),
        "breaking": False,
    },
    {
        "id": "demo-close",
        "analyst_id": "vance",
        "beat": "world",
        "kicker": "SATIRE",
        "headline": "The wire never sleeps. The demo, however, loops.",
        "text": (
            "That is the canned hour. Operators: label satire, never present a rumor "
            "as confirmed, and you are responsible for anything you broadcast beyond "
            "this preview. Sterling Vance, who does not exist, signing off."
        ),
        "breaking": False,
    },
)


def synthetic_alignment(text: str, cps: float = 14.0) -> dict:
    """Build ElevenLabs-shaped character timestamps from text length alone."""
    chars = list(str(text or ""))
    starts, ends = [], []
    t = 0.0
    step = 1.0 / max(8.0, float(cps))
    for ch in chars:
        starts.append(round(t, 4))
        dur = step * (2.2 if ch in ".!?" else 1.4 if ch in ",;:" else 1.0)
        t += dur
        ends.append(round(t, 4))
        if ch in ".!?":
            t += 0.18
    return {
        "characters": chars,
        "character_start_times_seconds": starts,
        "character_end_times_seconds": ends,
    }


def synthetic_speech(text: str) -> dict:
    alignment = synthetic_alignment(text)
    ends = alignment["character_end_times_seconds"]
    duration = float(ends[-1]) if ends else 1.0
    return {
        "audio_b64": "",
        "mime": "audio/mpeg",
        "alignment": alignment,
        "cached": False,
        "demo": True,
        "duration_sec": duration,
    }


def public_rundown(release_state: str = "public") -> dict:
    segments = [dict(seg) for seg in DEMO_SEGMENTS]
    return {
        "segments": segments,
        "count": len(segments),
        "mode": "demo",
        "program": {
            **DEMO_PROGRAM,
            "anchors": [dict(a) for a in DEMO_PROGRAM["anchors"]],
        },
        "audio_enabled": True,
        "demo": True,
        "release_state": release_state,
    }
