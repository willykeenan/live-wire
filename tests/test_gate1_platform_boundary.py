"""Boundary checks for the read-only editorial preview capability manifest."""

from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest import mock

from app import gate1_capability, server
from app.gate1_read_model import FixtureReadError, Gate1ReadModel
from editorial import Claim, Correction, EditorialTransition, Evidence, Receipt, Source, Story, Verification
from scripts.build_gate1_capability import OUTPUT, build, render


ROOT = Path(__file__).resolve().parents[1]


class Gate1PlatformBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads(OUTPUT.read_text(encoding="utf-8"))

    def test_capability_manifest_is_byte_reproducible_and_complete(self):
        self.assertEqual(OUTPUT.read_text(encoding="utf-8"), render())
        self.assertEqual(self.manifest, build())
        core = {
            "productId", "capabilityId", "version", "inputSchema",
            "artifactSchema", "requiredAuthority", "sideEffects",
            "supportsPreview", "supportsRollback", "eventTypes", "health",
            "dataPlacement", "estimatedCost",
        }
        self.assertTrue(core.issubset(self.manifest))

        refs: list[str] = []
        def collect(value):
            if isinstance(value, dict):
                refs.extend(item for key, item in value.items() if key == "$ref")
                for item in value.values():
                    collect(item)
            elif isinstance(value, list):
                for item in value:
                    collect(item)
        collect(self.manifest["artifactSchema"])
        self.assertGreaterEqual(len(refs), 3)
        for ref in refs:
            self.assertTrue((OUTPUT.parent / ref).resolve().is_file(), ref)

    def test_candidate_is_product_owned_local_zero_side_effect_and_not_integrated(self):
        self.assertEqual(self.manifest["productId"], "live-wire")
        self.assertEqual(self.manifest["capabilityId"], "livewire.editorial.read")
        self.assertEqual(self.manifest["health"], "degraded")
        self.assertEqual(self.manifest["dataPlacement"], ["local"])
        self.assertEqual(self.manifest["requiredAuthority"], [])
        self.assertEqual(self.manifest["sideEffects"], [])
        self.assertEqual(self.manifest["eventTypes"], [])
        self.assertTrue(all(value == 0 for key, value in self.manifest["estimatedCost"].items() if key != "concurrency"))
        self.assertEqual(self.manifest["estimatedCost"]["concurrency"], 1)
        self.assertEqual(self.manifest["latencyEstimate"]["status"], "unbenchmarked")
        self.assertIs(self.manifest["gate1Truth"]["productionAuthority"], False)
        self.assertIs(self.manifest["gate1Truth"]["runtimeMutation"], False)
        self.assertIs(self.manifest["routine"]["activationAuthorized"], False)
        self.assertIs(self.manifest["semanticValidation"]["schemaOnlyAuthoritative"], False)
        self.assertEqual(self.manifest["semanticValidation"]["policyModule"], "editorial.policy")
        self.assertEqual(
            {
                branch["properties"]["operation"]["const"]
                for branch in self.manifest["inputSchema"]["oneOf"]
            },
            {"story.index", "story.get", "receipt.list", "correction.list"},
        )
        for relative in self.manifest["artifactOwnership"]["paths"]:
            self.assertTrue((ROOT / relative).exists(), relative)
        boundary = (OUTPUT.parent / "README.md").read_text(encoding="utf-8")
        self.assertIn("no production authority", boundary)
        self.assertIn("not a certified Routine", boundary)
        self.assertIn("grants no invocation", boundary)

    def test_manifest_and_readme_carry_no_internal_process_vocabulary(self):
        texts = {
            "manifest": OUTPUT.read_text(encoding="utf-8"),
            "readme": (OUTPUT.parent / "README.md").read_text(encoding="utf-8"),
            "builder": (ROOT / "scripts" / "build_gate1_capability.py").read_text(encoding="utf-8"),
        }
        internal = re.compile(
            r"\bdirector\b|\bKE\b|authority lease|mission event|\bgovernor\b|docs/gate1|LW-GATE1",
            re.IGNORECASE,
        )
        for name, text in texts.items():
            self.assertIsNone(internal.search(text), f"internal term in {name}")

    def test_real_read_artifacts_match_every_declared_outer_branch_and_contract(self):
        model = Gate1ReadModel()
        story_id = model.story_index()["stories"][0]["storyId"]
        artifacts = {
            "story.index": model.story_index(),
            "story.get": model.story_detail(story_id),
            "receipt.list": model.receipts(story_id),
            "correction.list": model.corrections(),
        }
        branches = {
            branch["title"]: branch
            for branch in self.manifest["artifactSchema"]["oneOf"]
        }
        models = {
            "claims": Claim,
            "corrections": Correction,
            "evidence": Evidence,
            "receipts": Receipt,
            "sources": Source,
            "stories": Story,
            "transitions": EditorialTransition,
            "verifications": Verification,
        }
        for title, payload in artifacts.items():
            assert payload is not None
            branch = branches[title]
            properties = branch["properties"]
            with self.subTest(title=title):
                self.assertEqual(set(payload), set(properties))
                self.assertEqual(set(branch["required"]), set(properties))
                for field, contract in properties.items():
                    if "const" in contract:
                        self.assertEqual(payload[field], contract["const"])
                snapshot_contract = properties["snapshot"]
                self.assertEqual(set(payload["snapshot"]), set(snapshot_contract["properties"]))
                for field, contract in snapshot_contract["properties"].items():
                    if "const" in contract:
                        self.assertEqual(payload["snapshot"][field], contract["const"])
                for field, model_type in models.items():
                    if field in payload:
                        for item in payload[field]:
                            model_type.model_validate(item)
                if "story" in payload:
                    Story.model_validate(payload["story"])

        error = Gate1ReadModel.unavailable()
        error_branch = branches["bounded read error"]
        self.assertEqual(set(error), set(error_branch["properties"]))
        self.assertEqual(set(error_branch["required"]), set(error_branch["properties"]))
        self.assertIn(error["error"], error_branch["properties"]["error"]["enum"])

    def test_server_health_reports_the_same_non_authorizing_boundary(self):
        state = server.capability_state()
        self.assertEqual(state["gate1_capability_id"], self.manifest["capabilityId"])
        self.assertEqual(state["gate1_capability_version"], self.manifest["version"])
        self.assertEqual(state["gate1_capability_health"], "degraded")
        self.assertEqual(state["gate1_capability_manifest"], "capabilities/v2/livewire-editorial-read.manifest.json")
        self.assertIs(state["gate1_fixture_only"], True)
        self.assertIs(state["gate1_mutation_enabled"], False)
        self.assertIs(state["gate1_production_authority"], False)
        self.assertFalse([key for key in state if "director" in key.lower()])
        self.assertEqual(state["gate1_side_effects"], [])
        self.assertEqual(state["gate1_estimated_cost"], self.manifest["estimatedCost"])
        served = server.capability_manifest_state()
        self.assertEqual(served["capabilityId"], self.manifest["capabilityId"])
        self.assertEqual(served["runtimeHealth"]["status"], "degraded")
        self.assertIs(served["runtimeHealth"]["fixtureAvailable"], True)

    def test_corrupt_fixture_marks_health_and_capability_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(server, "GATE1_READ_MODEL", Gate1ReadModel(path)):
                state = server.capability_state()
                manifest = server.capability_manifest_state()
        self.assertEqual(state["status"], "degraded")
        self.assertEqual(state["gate1_capability_health"], "unavailable")
        self.assertIs(state["gate1_fixture_available"], False)
        self.assertEqual(manifest["health"], "unavailable")
        self.assertIs(manifest["runtimeHealth"]["fixtureAvailable"], False)

    def test_manifest_loader_rejects_forged_authority_and_semantic_shortcuts(self):
        for path, value in (
            (("gate1Truth", "productionAuthority"), True),
            (("gate1Truth", "runtimeMutation"), True),
            (("routine", "activationAuthorized"), True),
            (("semanticValidation", "schemaOnlyAuthoritative"), True),
            (("semanticValidation", "requiredTests"), []),
            (("artifactOwnership", "paths"), []),
            (("failureSemantics", "fixtureFailureHttpStatus"), 200),
            (("inputSchema",), {}),
            (("artifactSchema",), {}),
        ):
            with self.subTest(path=path):
                forged = json.loads(json.dumps(self.manifest))
                if len(path) == 1:
                    forged[path[0]] = value
                else:
                    forged[path[0]][path[1]] = value
                with tempfile.TemporaryDirectory() as tmp:
                    manifest_path = Path(tmp) / "manifest.json"
                    manifest_path.write_text(json.dumps(forged), encoding="utf-8")
                    with mock.patch.object(
                        gate1_capability, "CAPABILITY_MANIFEST_PATH", manifest_path
                    ):
                        with self.assertRaises(FixtureReadError):
                            gate1_capability.capability_manifest_state(Gate1ReadModel())


if __name__ == "__main__":
    unittest.main()
