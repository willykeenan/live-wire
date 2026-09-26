"""Execution authority for the public Live Wire edition.

Generation (LLM scripts, vendor TTS, rumor scanning) is opt-in. Demo mode is
the default: canned satire scripts, no keys, no network adapters.
"""
from __future__ import annotations

import os
import threading


# Public edition: generation is an operator opt-in, never the default.
CONTAINMENT_RELEASE = False
GENERATION_ENV = "LIVE_WIRE_GENERATION_ENABLED"
DEMO_ENV = "LIVE_WIRE_DEMO"
RUMORS_ENV = "LIVE_WIRE_RUMORS_ENABLED"
_lock = threading.Lock()
_attempts = {}
_blocked = {}


def _flag(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def generation_requested() -> bool:
    """Return whether the operator requested generation, without granting it."""
    return _flag(GENERATION_ENV) == "1"


def generation_enabled() -> bool:
    """Live model/TTS adapters run only when the operator opts in."""
    return generation_requested()


def demo_mode() -> bool:
    """Canned-script preview. Default on whenever generation is not enabled."""
    explicit = _flag(DEMO_ENV)
    if explicit == "0":
        return False
    if explicit == "1":
        return True
    return not generation_enabled()


def rumors_enabled() -> bool:
    """X/Grok rumor scanning is optional and off by default."""
    return _flag(RUMORS_ENV) == "1"


def authorize(capability: str) -> bool:
    """Record an adapter invocation and return its effective authority."""
    name = str(capability or "unknown")
    allowed = generation_enabled()
    with _lock:
        _attempts[name] = _attempts.get(name, 0) + 1
        if not allowed:
            _blocked[name] = _blocked.get(name, 0) + 1
    return allowed


def metrics() -> dict:
    """Return non-secret process-local evidence for idle and authority tests."""
    with _lock:
        return {
            "attempted": dict(_attempts),
            "blocked": dict(_blocked),
            "attempted_total": sum(_attempts.values()),
            "blocked_total": sum(_blocked.values()),
        }


def reset_metrics_for_tests() -> None:
    """Reset process-local counters; intended only for deterministic tests."""
    with _lock:
        _attempts.clear()
        _blocked.clear()


def public_state() -> dict:
    """Stable, non-secret capability truth for health and UI contracts."""
    demo = demo_mode()
    gen = generation_enabled()
    return {
        "containment": not rumors_enabled(),
        "generation_requested": generation_requested(),
        "generation_enabled": gen,
        "audio_enabled": demo or gen,
        "demo_mode": demo,
        "rumors_enabled": rumors_enabled(),
        "generation_metrics": metrics(),
    }
