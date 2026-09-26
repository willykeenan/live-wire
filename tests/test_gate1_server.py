"""Credential/import isolation and HTTP checks for the Gate 1 reader process."""

from __future__ import annotations

from http.client import HTTPConnection
from http.server import HTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from app import gate1_capability, gate1_server
from app.gate1_read_model import Gate1ReadModel
from app.gate1_server import Gate1Handler


ROOT = Path(__file__).resolve().parents[1]


class Gate1ReaderServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = HTTPServer(("127.0.0.1", 0), Gate1Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path)
        response = connection.getresponse()
        payload = response.read()
        headers = dict(response.getheaders())
        connection.close()
        return response.status, headers, payload

    def test_reader_serves_only_fixture_reads_static_ui_and_closed_legacy_shapes(self):
        status, _, payload = self.request("GET", "/api/health")
        health = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(health["mode"], "gate1_read_only")
        self.assertIs(health["credentialsLoaded"], False)
        self.assertIs(health["legacyRuntimeImported"], False)
        self.assertEqual(health["sideEffects"], [])
        self.assertTrue(all(value == 0 for key, value in health["estimatedCost"].items() if key != "concurrency"))
        self.assertEqual(health["capabilityId"], "livewire.editorial.read")
        self.assertEqual(health["status"], "degraded")

        status, _, payload = self.request("GET", "/api/capabilities")
        capability = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(capability["capabilityId"], "livewire.editorial.read")
        self.assertEqual(capability["health"], "degraded")
        self.assertIs(capability["gate1Truth"]["productionAuthority"], False)

        status, headers, payload = self.request("GET", "/api/stories")
        stories = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual(stories["count"], 3)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        status, _, payload = self.request("GET", "/api/analysts")
        self.assertEqual((status, json.loads(payload)), (200, []))
        status, _, payload = self.request("GET", "/api/rundown?channel=world")
        self.assertEqual(json.loads(payload)["segments"], [])
        status, _, payload = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Stories that show their work", payload)
        self.assertNotIn(b"https://unpkg.com", payload)
        status, headers, _ = self.request("GET", "/index.html")
        self.assertEqual(status, 200)
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])
        for correction in gate1_server.READ_MODEL.corrections()["corrections"]:
            status, _, payload = self.request("GET", correction["correctionPath"])
            self.assertEqual(status, 200)
            self.assertIn(correction["correctionId"].encode("utf-8"), payload)
            self.assertIn(b"no newsroom, publication, or production authority", payload)
            for value in (
                correction["storyId"],
                correction["claimId"],
                correction["receiptIds"][0],
                correction["evidenceIds"][0],
                correction["humanReview"]["reviewerName"],
                correction["humanReview"]["reviewerRole"],
                correction["humanReview"]["reviewedAt"],
            ):
                self.assertIn(str(value).encode("utf-8"), payload)

    def test_every_mutation_and_unknown_legacy_capability_fails_closed(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                status, _, payload = self.request(method, "/api/stories")
                self.assertEqual(status, 405)
                self.assertEqual(json.loads(payload), {"error": "read_only_surface", "readOnly": True})
        status, _, payload = self.request("GET", "/api/generate")
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"], "capability_closed")

    def test_real_bounded_read_errors_are_declared_by_the_artifact_schema(self):
        status, _, missing_payload = self.request("GET", "/api/story")
        self.assertEqual(status, 400)
        status, _, unknown_payload = self.request("GET", "/api/story?id=story-does-not-exist")
        self.assertEqual(status, 404)
        error_payloads = [json.loads(missing_payload), json.loads(unknown_payload)]
        status, _, capability_payload = self.request("GET", "/api/capabilities")
        self.assertEqual(status, 200)
        schema = json.loads(capability_payload)["artifactSchema"]
        error_branch = next(
            branch for branch in schema["oneOf"] if branch.get("title") == "bounded read error"
        )
        allowed_errors = set(error_branch["properties"]["error"]["enum"])
        for payload in error_payloads:
            with self.subTest(error=payload["error"]):
                self.assertTrue(set(error_branch["required"]).issubset(payload))
                self.assertIn(payload["error"], allowed_errors)
                for field, contract in error_branch["properties"].items():
                    if "const" in contract:
                        self.assertEqual(payload[field], contract["const"])

    def test_corrupt_fixture_degrades_isolated_health_and_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preview.json"
            path.write_text("{}", encoding="utf-8")
            with mock.patch.object(gate1_server, "READ_MODEL", Gate1ReadModel(path)):
                health_status, _, health_payload = self.request("GET", "/api/health")
                capability_status, _, capability_payload = self.request("GET", "/api/capabilities")
        health = json.loads(health_payload)
        capability = json.loads(capability_payload)
        self.assertEqual(health_status, 200)
        self.assertIs(health["ok"], False)
        self.assertEqual(health["status"], "unavailable")
        self.assertIs(health["fixtureAvailable"], False)
        self.assertEqual(capability_status, 503)
        self.assertEqual(capability["health"], "unavailable")

    def test_corrupt_manifest_degrades_health_even_when_fixture_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text('{"gate1Truth": []}', encoding="utf-8")
            with mock.patch.object(
                gate1_capability, "CAPABILITY_MANIFEST_PATH", manifest_path
            ):
                health_status, _, health_payload = self.request("GET", "/api/health")
                capability_status, _, capability_payload = self.request("GET", "/api/capabilities")
        health = json.loads(health_payload)
        capability = json.loads(capability_payload)
        self.assertEqual(health_status, 200)
        self.assertIs(health["fixtureAvailable"], False)
        self.assertIs(health["manifestAvailable"], False)
        self.assertIs(health["ok"], False)
        self.assertEqual(health["status"], "unavailable")
        self.assertEqual(capability_status, 503)
        self.assertEqual(capability["health"], "unavailable")

    def test_deeply_nested_manifest_uses_the_same_unavailable_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_text("[" * 1_500 + "]" * 1_500, encoding="utf-8")
            with mock.patch.object(
                gate1_capability, "CAPABILITY_MANIFEST_PATH", manifest_path
            ):
                health_status, _, health_payload = self.request("GET", "/api/health")
                capability_status, _, capability_payload = self.request("GET", "/api/capabilities")
        self.assertEqual(health_status, 200)
        self.assertEqual(json.loads(health_payload)["status"], "unavailable")
        self.assertEqual(capability_status, 503)
        self.assertEqual(json.loads(capability_payload)["health"], "unavailable")

    def test_import_succeeds_with_poisoned_legacy_modules_and_minimal_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            poison = Path(tmp)
            for name in ("dotenv.py", "requests.py", "yfinance.py"):
                (poison / name).write_text("raise RuntimeError('forbidden import')\n", encoding="utf-8")
            for package in ("broadcast", "ingestion"):
                directory = poison / package
                directory.mkdir()
                (directory / "__init__.py").write_text("raise RuntimeError('forbidden import')\n", encoding="utf-8")
            code = (
                "import sys; import app.gate1_server; "
                "assert not {'dotenv','requests','yfinance','broadcast','ingestion'} & set(sys.modules)"
            )
            env = {
                "PATH": os.environ.get("PATH", ""),
                "PYTHONPATH": os.pathsep.join((str(poison), str(ROOT))),
                "PYTHONDONTWRITEBYTECODE": "1",
            }
            result = subprocess.run(
                [sys.executable, "-c", code],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)

        source = (ROOT / "app" / "gate1_server.py").read_text(encoding="utf-8")
        for forbidden in ("dotenv", "os.environ", "requests", "yfinance", "from broadcast", "from ingestion"):
            self.assertNotIn(forbidden, source)

    def test_documented_direct_server_entry_point_is_runnable(self):
        result = subprocess.run(
            [sys.executable, "app/gate1_server.py", "--help"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--port", result.stdout)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(".venv/bin/python app/gate1_server.py --port 8901", readme)


if __name__ == "__main__":
    unittest.main()
