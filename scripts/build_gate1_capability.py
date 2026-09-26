#!/usr/bin/env python3
"""Build the read-only capability manifest for Live Wire's editorial evidence preview.

The manifest is deterministic: app/gate1_capability.py refuses to serve a file
that differs from render(). Regenerate with:

    python3 scripts/build_gate1_capability.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "capabilities" / "v2" / "livewire-editorial-read.manifest.json"


def _response(title: str, required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "fixtureId", "generatedAt", "sourceCapture", "fixtureSha256",
            "sourceCaptureSha256", "fixtureOnly", "productionAuthority",
            "artifactCustody", "ownerProduct",
        ],
        "properties": {
            "fixtureId": {"type": "string"},
            "generatedAt": {"type": "string"},
            "sourceCapture": {"type": "string"},
            "fixtureSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "sourceCaptureSha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "fixtureOnly": {"const": True},
            "productionAuthority": {"const": False},
            "artifactCustody": {"const": "user_repository"},
            "ownerProduct": {"const": "live-wire"},
        },
    }
    shared = {
        "schemaVersion": {"const": "livewire.editorial.v2"},
        "available": {"const": True},
        "ownerProduct": {"const": "live-wire"},
        "readOnly": {"const": True},
        "fixtureOnly": {"const": True},
        "productionAuthority": {"const": False},
        "snapshot": snapshot,
    }
    return {
        "title": title,
        "type": "object",
        "additionalProperties": False,
        "required": [*shared, *required],
        "properties": {**shared, **properties},
    }


def build() -> dict[str, object]:
    """Return the manifest: what the read surface accepts, returns, and never does."""
    story_ref = "../../editorial/contracts/v2/story.schema.json"
    claim_ref = "../../editorial/contracts/v2/claim.schema.json"
    source_ref = "../../editorial/contracts/v2/source.schema.json"
    evidence_ref = "../../editorial/contracts/v2/evidence.schema.json"
    verification_ref = "../../editorial/contracts/v2/verification.schema.json"
    transition_ref = "../../editorial/contracts/v2/editorial-transition.schema.json"
    receipt_ref = "../../editorial/contracts/v2/receipt.schema.json"
    correction_ref = "../../editorial/contracts/v2/correction.schema.json"
    artifact_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Live Wire editorial read artifacts",
        "oneOf": [
            _response(
                "story.index",
                ["count", "stories", "previews"],
                {
                    "count": {"minimum": 0, "type": "integer"},
                    "stories": {"items": {"$ref": story_ref}, "type": "array"},
                    "previews": {"type": "array"},
                },
            ),
            _response(
                "story.get",
                [
                    "story", "claims", "sources", "evidence", "verifications",
                    "transitions", "receipts", "corrections", "category",
                    "previewSegments",
                ],
                {
                    "story": {"$ref": story_ref},
                    "claims": {"items": {"$ref": claim_ref}, "type": "array"},
                    "sources": {"items": {"$ref": source_ref}, "type": "array"},
                    "evidence": {"items": {"$ref": evidence_ref}, "type": "array"},
                    "verifications": {"items": {"$ref": verification_ref}, "type": "array"},
                    "transitions": {"items": {"$ref": transition_ref}, "type": "array"},
                    "receipts": {"items": {"$ref": receipt_ref}, "type": "array"},
                    "corrections": {"items": {"$ref": correction_ref}, "type": "array"},
                    "category": {"enum": ["business", "markets", "politics", "tech", "us", "world"]},
                    "previewSegments": {"items": {"type": "object"}, "type": "array"},
                },
            ),
            _response(
                "receipt.list",
                ["storyId", "receipts"],
                {
                    "storyId": {"type": "string"},
                    "receipts": {"items": {"$ref": receipt_ref}, "type": "array"},
                },
            ),
            _response(
                "correction.list",
                ["count", "corrections"],
                {
                    "count": {"minimum": 0, "type": "integer"},
                    "corrections": {"items": {"$ref": correction_ref}, "type": "array"},
                },
            ),
            {
                "title": "bounded read error",
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "schemaVersion", "available", "ownerProduct", "readOnly",
                    "fixtureOnly", "productionAuthority", "error",
                ],
                "properties": {
                    "schemaVersion": {"const": "livewire.editorial.v2"},
                    "available": {"const": False},
                    "ownerProduct": {"const": "live-wire"},
                    "readOnly": {"const": True},
                    "fixtureOnly": {"const": True},
                    "productionAuthority": {"const": False},
                    "error": {
                        "enum": [
                            "gate1_fixture_unavailable",
                            "story_id_required",
                            "story_not_found",
                        ]
                    },
                },
            },
        ],
    }
    operation = lambda value: {  # noqa: E731 - compact deterministic schema factory
        "type": "object",
        "additionalProperties": False,
        "required": ["operation"],
        "properties": {"operation": {"const": value}},
    }
    story_operation = lambda value: {  # noqa: E731
        "type": "object",
        "additionalProperties": False,
        "required": ["operation", "storyId"],
        "properties": {
            "operation": {"const": value},
            "storyId": {"type": "string", "minLength": 3, "maxLength": 128},
        },
    }
    return {
        "manifestSchemaVersion": "livewire.capability.v2",
        "productId": "live-wire",
        "capabilityId": "livewire.editorial.read",
        "version": "2.0.0-gate1",
        "inputSchema": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": "Live Wire editorial read input",
            "oneOf": [
                operation("story.index"),
                story_operation("story.get"),
                story_operation("receipt.list"),
                operation("correction.list"),
            ],
        },
        "artifactSchema": artifact_schema,
        "requiredAuthority": [],
        "sideEffects": [],
        "supportsPreview": True,
        "supportsRollback": False,
        "eventTypes": [],
        "health": "degraded",
        "healthReason": (
            "Local fixture-backed preview is inspectable; it reads bundled sample "
            "data only and is not a production newsroom."
        ),
        "dataPlacement": ["local"],
        "estimatedCost": {
            "concurrency": 1,
            "externalActions": 0,
            "modelTokens": 0,
            "moneyMicros": 0,
            "retries": 0,
        },
        "latencyEstimate": {
            "measurementRequiredBeforePromotion": True,
            "status": "unbenchmarked",
        },
        "previewBehavior": {
            "executes": False,
            "mode": "read_only_fixture",
            "productionMutation": False,
        },
        "failureSemantics": {
            "automaticRetry": False,
            "fallback": "none",
            "fixtureFailureHttpStatus": 503,
            "mode": "fail_closed",
            "recovery": "Restore data/gate1/preview.json from version control.",
        },
        "gate1Truth": {
            "fixtureOnly": True,
            "productionAuthority": False,
            "readOnly": True,
            "runtimeMutation": False,
        },
        "artifactOwnership": {
            "custody": "user_repository",
            "exportable": True,
            "ownerProduct": "live-wire",
            "paths": [
                "capabilities/v2/livewire-editorial-read.manifest.json",
                "data/gate1/preview.json",
                "data/gate1/source_wire.json",
                "editorial/contracts/v2",
            ],
        },
        "provenance": {
            "editorialSchemaVersion": "livewire.editorial.v2",
            "evidenceBundle": "data/gate1",
            "fixture": "data/gate1/preview.json",
            "fixtureId": "livewire-gate1-read-only-preview-v2",
            "generatedBy": "Live Wire Gate 1 deterministic local builders",
            "sourceCaptureSha256": "09181c5edde7f1b2e9fd718b3d0d449f432e792aadb2d6fd9ba09f848b0bef8a",
        },
        "semanticValidation": {
            "editorialSchemaVersion": "livewire.editorial.v2",
            "policyModule": "editorial.policy",
            "schemaOnlyAuthoritative": False,
            "requiredTests": [
                "tests.test_gate1_contracts",
                "tests.test_gate1_server",
                "tests.test_gate1_platform_boundary",
                "tests.test_public_edition",
            ],
        },
        "routine": {
            "activationAuthorized": False,
            "candidateOnly": True,
            "certified": False,
        },
    }


def render() -> str:
    return json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(render(), encoding="utf-8")
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
