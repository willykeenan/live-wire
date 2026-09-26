"""ElevenLabs TTS with per-character timestamps for avatar lip-sync.

The API key is read only from ELEVENLABS_API_KEY (process env or a project
`.env` already loaded by the server). There is no home-directory or sidecar
key file lookup. Demo mode returns synthetic timestamps and never calls the
network.
"""

import hashlib
import json
import os
import re
from pathlib import Path

import requests

from broadcast import demo, execution

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = Path(os.environ.get("LIVE_WIRE_VOICE_CACHE") or (ROOT / "data" / "voices"))

API_BASE = "https://api.elevenlabs.io/v1/text-to-speech"
OUTPUT_FORMAT = "mp3_44100_128"
DEFAULT_MODEL = "eleven_flash_v2_5"
MIME = "audio/mpeg"
REQUEST_TIMEOUT = 30  # seconds

VOICES = {
    "daniel":  {"voice_id": "onwK4e9ZLuTAKqWW03F9", "name": "Daniel — Steady Broadcaster"},
    "sarah":   {"voice_id": "EXAVITQu4vr4xnSDxMaL", "name": "Sarah — Reassuring, Confident"},
    "eric":    {"voice_id": "cjVigY5qzO86Huf0OWal", "name": "Eric — Smooth, Trustworthy"},
    "matilda": {"voice_id": "XrExE9yKIg1WjnnlVkGX", "name": "Matilda — Warm, Assured"},
    "brian":   {"voice_id": "nPczCjzI2devNBz1zQrb", "name": "Brian — Deep, Resonant"},
}

# Only these ids may be sent to the vendor: they are interpolated into the URL path.
VOICE_IDS = frozenset(v["voice_id"] for v in VOICES.values())
_VOICE_ID_RE = re.compile(r"^[A-Za-z0-9]{8,64}$")

SPEECH_SPEED = 1.07
VOICE_SETTINGS = {"stability": 0.45, "similarity_boost": 0.8, "speed": SPEECH_SPEED}

import re as _re
_DESPEAK = [
    (_re.compile(r"\b(?:article|comments?|source|story|video|image|link)s?\s+url\s*:?", _re.I), " "),
    (_re.compile(r"\b(?:https?://|www\.)\S+", _re.I), " "),
    (_re.compile(r"\b\S+\.\w{2,}/\S*", _re.I), " "),
    (_re.compile(r"\[\s*\d+\s*\]"), " "),
]


def _despeak(text):
    t = str(text or "")
    for rx, rep in _DESPEAK:
        t = rx.sub(rep, t)
    t = _re.sub(r"\s+", " ", t).strip()
    return _re.sub(r"\s+([.,!?;:])", r"\1", t)


def _api_key():
    """Resolve ELEVENLABS_API_KEY from the process environment only."""
    env = os.environ.get("ELEVENLABS_API_KEY")
    if env and env.strip():
        return env.strip()
    return None


def _cache_path(text, voice_id, model_id):
    sig = json.dumps(VOICE_SETTINGS, sort_keys=True)
    digest = hashlib.sha256(
        "{}|{}|{}|{}".format(voice_id, model_id, sig, text).encode("utf-8")
    ).hexdigest()[:24]
    return CACHE_DIR / (digest + ".json")


def _alignment_subset(alignment):
    if not isinstance(alignment, dict):
        return {
            "characters": [],
            "character_start_times_seconds": [],
            "character_end_times_seconds": [],
        }
    return {
        "characters": alignment.get("characters") or [],
        "character_start_times_seconds": alignment.get("character_start_times_seconds") or [],
        "character_end_times_seconds": alignment.get("character_end_times_seconds") or [],
    }


def synthesize(text, voice_id, model_id=DEFAULT_MODEL, cache=True):
    """Synthesize `text` with `voice_id`, returning audio + char timestamps.

    Demo mode (no vendor key) returns synthetic timestamps so cartoon mouth
    sync still runs. Live ElevenLabs calls require generation to be enabled
    and ELEVENLABS_API_KEY to be set.
    """
    if not text or not str(text).strip():
        return {"error": "text is empty"}
    text = _despeak(str(text))
    if not text:
        return {"error": "text is empty after sanitize"}

    # Demo is canned timestamps. A leftover vendor key must not force a
    # network call or a generation_disabled error on the keyless path.
    if execution.demo_mode() and not execution.generation_enabled():
        result = demo.synthetic_speech(text)
        result["voice_id"] = str(voice_id or "")
        return result

    if not execution.authorize("tts"):
        return {"error": "generation_disabled", "disabled": True}
    if not voice_id or not str(voice_id).strip():
        return {"error": "voice_id is empty"}

    voice_id = str(voice_id)
    if not _VOICE_ID_RE.match(voice_id):
        return {"error": "voice_id is not a vendor voice id"}
    model_id = str(model_id or DEFAULT_MODEL)
    cache_file = _cache_path(text, voice_id, model_id)

    if cache and cache_file.exists():
        try:
            with open(cache_file, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            return {
                "audio_b64": payload["audio_b64"],
                "mime": payload.get("mime", MIME),
                "alignment": _alignment_subset(payload.get("alignment")),
                "cached": True,
            }
        except (OSError, ValueError, KeyError):
            pass

    key = _api_key()
    if not key:
        return {"error": "no ElevenLabs API key found (set ELEVENLABS_API_KEY)"}

    url = "{}/{}/with-timestamps?output_format={}".format(API_BASE, voice_id, OUTPUT_FORMAT)
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    body = {
        "text": text,
        "model_id": model_id,
        "voice_settings": dict(VOICE_SETTINGS),
    }

    try:
        resp = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    except requests.exceptions.Timeout:
        return {"error": "request timed out after {}s".format(REQUEST_TIMEOUT)}
    except requests.exceptions.RequestException as exc:
        return {"error": "request failed: {}".format(exc)}

    if resp.status_code != 200:
        snippet = (resp.text or "").strip()
        if len(snippet) > 300:
            snippet = snippet[:300] + "..."
        return {"error": "API {}: {}".format(resp.status_code, snippet)}

    try:
        data = resp.json()
    except ValueError:
        return {"error": "API returned non-JSON response"}

    audio_b64 = data.get("audio_base64")
    if not audio_b64:
        return {"error": "API response missing audio_base64"}

    alignment = _alignment_subset(data.get("alignment"))
    result = {
        "audio_b64": audio_b64,
        "mime": MIME,
        "alignment": alignment,
        "cached": False,
    }

    if cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            tmp = cache_file.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {"audio_b64": audio_b64, "mime": MIME, "alignment": alignment},
                    fh,
                )
            os.replace(str(tmp), str(cache_file))
            _prune_cache()
        except OSError:
            pass

    return result


_CACHE_MAX_FILES = 4000


def _prune_cache():
    try:
        files = [p for p in CACHE_DIR.glob("*.json")]
        if len(files) <= _CACHE_MAX_FILES:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for p in files[: len(files) - _CACHE_MAX_FILES]:
            try:
                p.unlink()
            except OSError:
                pass
    except OSError:
        pass


def _main():
    text = "Good evening, this is Live Wire."
    voice_id = VOICES["daniel"]["voice_id"]
    result = synthesize(text, voice_id)
    if "error" in result:
        print(json.dumps({"error": result["error"]}))
        return
    align = result["alignment"]
    print("keys:", sorted(result.keys()))
    print("characters:", len(align["characters"]))
    print("cached:", result.get("cached"))
    print("demo:", result.get("demo", False))


if __name__ == "__main__":
    _main()
