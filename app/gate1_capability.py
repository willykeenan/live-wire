"""Pure loader for the read-only editorial preview capability manifest."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.gate1_read_model import FixtureReadError, Gate1ReadModel
from scripts.build_gate1_capability import render as render_expected_manifest


ROOT = Path(__file__).resolve().parents[1]
CAPABILITY_ID = "livewire.editorial.read"
CAPABILITY_VERSION = "2.0.0-gate1"
CAPABILITY_MANIFEST = "capabilities/v2/livewire-editorial-read.manifest.json"
CAPABILITY_MANIFEST_PATH = ROOT / CAPABILITY_MANIFEST
MAX_CAPABILITY_MANIFEST_BYTES = 512 * 1024


def fixture_health(read_model: Gate1ReadModel) -> tuple[str, bool]:
    """Return canonical health without loading credentials or legacy runtime code."""
    try:
        read_model.story_index()
        return "degraded", True
    except FixtureReadError:
        return "unavailable", False


def _manifest_object(manifest: dict[str, Any], field: str) -> dict[str, Any]:
    value = manifest.get(field)
    if not isinstance(value, dict):
        raise FixtureReadError(f"capability manifest {field} must be an object")
    return value


def capability_manifest_state(read_model: Gate1ReadModel) -> dict[str, Any]:
    """Validate the committed manifest and bind it to current fixture health."""
    try:
        if CAPABILITY_MANIFEST_PATH.stat().st_size > MAX_CAPABILITY_MANIFEST_BYTES:
            raise ValueError("capability manifest exceeds byte limit")
        payload = CAPABILITY_MANIFEST_PATH.read_bytes()
        manifest = json.loads(payload.decode("utf-8"))
    except (
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
        RecursionError,
        MemoryError,
    ) as exc:
        raise FixtureReadError("capability manifest cannot be read") from exc
    if not isinstance(manifest, dict):
        raise FixtureReadError("capability manifest must be an object")
    if payload != render_expected_manifest().encode("utf-8"):
        raise FixtureReadError("capability manifest diverges from its deterministic v2 contract")
    required = {
        "productId", "capabilityId", "version", "inputSchema", "artifactSchema",
        "requiredAuthority", "sideEffects", "supportsPreview", "supportsRollback",
        "eventTypes", "health", "dataPlacement", "estimatedCost",
    }
    if not required.issubset(manifest):
        raise FixtureReadError("capability manifest core is incomplete")
    truth = _manifest_object(manifest, "gate1Truth")
    cost = _manifest_object(manifest, "estimatedCost")
    ownership = _manifest_object(manifest, "artifactOwnership")
    semantics = _manifest_object(manifest, "semanticValidation")
    routine = _manifest_object(manifest, "routine")
    preview = _manifest_object(manifest, "previewBehavior")
    failure = _manifest_object(manifest, "failureSemantics")
    if (
        manifest.get("manifestSchemaVersion") != "livewire.capability.v2"
        or manifest.get("productId") != "live-wire"
        or manifest.get("capabilityId") != CAPABILITY_ID
        or manifest.get("version") != CAPABILITY_VERSION
        or manifest.get("requiredAuthority") != []
        or manifest.get("sideEffects") != []
        or manifest.get("eventTypes") != []
        or manifest.get("supportsPreview") is not True
        or manifest.get("supportsRollback") is not False
        or manifest.get("health") != "degraded"
        or manifest.get("dataPlacement") != ["local"]
        or truth.get("readOnly") is not True
        or truth.get("fixtureOnly") is not True
        or truth.get("productionAuthority") is not False
        or truth.get("runtimeMutation") is not False
        or any(cost.get(field) != 0 for field in ("moneyMicros", "modelTokens", "externalActions", "retries"))
        or cost.get("concurrency") != 1
        or ownership.get("ownerProduct") != "live-wire"
        or ownership.get("custody") != "user_repository"
        or semantics.get("schemaOnlyAuthoritative") is not False
        or semantics.get("policyModule") != "editorial.policy"
        or routine.get("candidateOnly") is not True
        or routine.get("certified") is not False
        or routine.get("activationAuthorized") is not False
        or preview.get("executes") is not False
        or preview.get("productionMutation") is not False
        or failure.get("automaticRetry") is not False
        or failure.get("fallback") != "none"
        or failure.get("mode") != "fail_closed"
    ):
        raise FixtureReadError("capability manifest authority or cost boundary is invalid")
    health, fixture_available = fixture_health(read_model)
    result = dict(manifest)
    result["health"] = health
    result["runtimeHealth"] = {
        "fixtureAvailable": fixture_available,
        "manifestSha256": hashlib.sha256(payload).hexdigest(),
        "status": health,
    }
    return result
