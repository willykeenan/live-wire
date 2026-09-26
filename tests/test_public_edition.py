"""Public-edition contracts: demo mode, privacy, policy, empty archive."""

from __future__ import annotations

from copy import deepcopy
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import unittest
from unittest import mock

from app import server
from broadcast import demo, execution, llm, voice
from ingestion import rumors as rumor_engine


ROOT = Path(__file__).resolve().parents[1]


class DemoModeTests(unittest.TestCase):
    def test_demo_is_default_without_generation(self):
        with mock.patch.dict(os.environ, {"LIVE_WIRE_GENERATION_ENABLED": "", "LIVE_WIRE_DEMO": ""}, clear=False):
            self.assertTrue(execution.demo_mode())
            self.assertFalse(execution.generation_enabled())

    def test_demo_rundown_uses_invented_names_and_satire_labels(self):
        pack = demo.public_rundown()
        self.assertGreaterEqual(pack["count"], 3)
        self.assertEqual(pack["mode"], "demo")
        blob = json.dumps(pack).lower()
        self.assertIn("satire", blob)
        forbidden = ("trump", "biden", "musk", "obama", "putin", "ada lovelace", "ai weiwei")
        for name in forbidden:
            self.assertNotIn(name, blob)
        for invented in ("harbor grid", "aster labs", "northstar energy", "vale crossing"):
            self.assertIn(invented, blob)
        samples = [
            ROOT / "data" / "gate1" / "preview.json",
            ROOT / "data" / "gate1" / "source_wire.json",
            ROOT / "site" / "roster.json",
            ROOT / "tests" / "fixtures" / "false_confirmation_cases.json",
            ROOT / "tests" / "fixtures" / "breaking_freshness.json",
        ]
        for path in samples:
            text = path.read_text(encoding="utf-8").lower()
            for name in forbidden:
                self.assertNotIn(name, text, path.name)

    def test_demo_tts_needs_no_key_and_skips_network(self):
        with mock.patch.dict(os.environ, {"ELEVENLABS_API_KEY": "", "LIVE_WIRE_DEMO": "1"}, clear=False):
            with mock.patch("broadcast.voice.requests.post", side_effect=AssertionError("network")):
                result = voice.synthesize("Good evening from Vale Crossing.", "fixture-voice")
        self.assertTrue(result.get("demo"))
        self.assertIn("alignment", result)
        self.assertGreater(len(result["alignment"]["characters"]), 8)

    def test_demo_tts_ignores_leftover_vendor_key(self):
        with mock.patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "xi_leftover_not_for_demo",
            "LIVE_WIRE_DEMO": "1",
            "LIVE_WIRE_GENERATION_ENABLED": "0",
        }, clear=False):
            with mock.patch("broadcast.voice.requests.post", side_effect=AssertionError("network")):
                result = voice.synthesize("Good evening from Vale Crossing.", "fixture-voice")
        self.assertTrue(result.get("demo"))
        self.assertNotIn("error", result)
        self.assertIn("alignment", result)

    def test_llm_does_not_run_in_demo(self):
        with mock.patch.dict(os.environ, {"LIVE_WIRE_GENERATION_ENABLED": "0", "ANTHROPIC_API_KEY": "sk-test"}, clear=False):
            with mock.patch("broadcast.llm.subprocess.run", side_effect=AssertionError("cli")):
                self.assertFalse(llm.available())
                self.assertEqual(llm.text("system", "user"), "")

    def test_rumors_off_by_default(self):
        with mock.patch.dict(os.environ, {"LIVE_WIRE_RUMORS_ENABLED": ""}, clear=False):
            self.assertFalse(execution.rumors_enabled())
            self.assertEqual(rumor_engine.scan_rumors(), "")


class PublicHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def request(self, method: str, path: str, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        raw = json.dumps(body).encode("utf-8") if body is not None else None
        sent = {"Content-Type": "application/json"} if raw is not None else {}
        sent.update(headers or {})
        connection.request(method, path, body=raw, headers=sent)
        response = connection.getresponse()
        payload = response.read()
        connection.close()
        decoded = None
        if payload:
            text = payload.decode("utf-8")
            try:
                decoded = json.loads(text)
            except json.JSONDecodeError:
                decoded = text
        return response.status, decoded

    def test_health_reports_demo(self):
        status, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")
        self.assertTrue(body["demo_mode"])
        self.assertFalse(body["rumors_enabled"])
        self.assertFalse(body["generation_enabled"])
        self.assertTrue(body["audio_enabled"])
        self.assertEqual(body["release_state"], "public")

    def test_rundown_is_demo_satire(self):
        status, body = self.request("GET", "/api/rundown?channel=world")
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "demo")
        self.assertGreaterEqual(body["count"], 3)
        self.assertEqual(body["segments"][0]["kicker"], "SATIRE")

    def test_demo_tts_route(self):
        status, body = self.request("POST", "/api/tts", {"text": "Harbor Grid files a weather report.", "analyst_id": "vance"})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("demo"))
        self.assertIn("alignment", body)

    def test_rumor_scan_stays_off(self):
        status, body = self.request("POST", "/api/rumors/scan", {})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "rumors_disabled")

    def test_markets_routes_are_gone(self):
        for path in ("/api/signals", "/api/bars/ACME?tf=5m"):
            status, _ = self.request("GET", path)
            self.assertEqual(status, 404, path)
        # The old ?channel=markets query no longer selects a markets desk.
        status, body = self.request("GET", "/api/rundown?channel=markets")
        self.assertEqual(status, 200)
        self.assertEqual(body["mode"], "demo")
        self.assertNotIn("Global Markets", json.dumps(body))


class SurfaceAndDocsTests(unittest.TestCase):
    def test_markets_tab_removed_from_studio(self):
        html = (ROOT / "app" / "broadcast.html").read_text(encoding="utf-8")
        js = (ROOT / "app" / "broadcast.js").read_text(encoding="utf-8")
        self.assertNotIn('data-tab="markets"', html)
        self.assertNotIn("tab-markets", html)
        self.assertNotIn("data-pillar=\"markets\"", html)
        self.assertNotIn("lightweight-charts", html)
        self.assertIn("SATIRE", html)
        self.assertNotIn('data-tab="markets"', js)

    def test_site_links_github_and_policy(self):
        landing = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        live = (ROOT / "site" / "live" / "index.html").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in (landing, live, readme):
            self.assertIn("github.com/willykeenan/live-wire", text)
        self.assertIn("Satire must be labelled", readme)
        self.assertIn("Never present rumors as confirmed", readme)
        self.assertIn("Operators are responsible for what they broadcast", readme)
        self.assertIn("LICENSE", [p.name for p in ROOT.iterdir()] + ["LICENSE"])
        self.assertTrue((ROOT / "LICENSE").is_file())

    def test_no_openclaw_or_home_key_lookup(self):
        voice_src = (ROOT / "broadcast" / "voice.py").read_text(encoding="utf-8")
        llm_src = (ROOT / "broadcast" / "llm.py").read_text(encoding="utf-8")
        icon_src = (ROOT / "assets" / "make_icon.py").read_text(encoding="utf-8")
        for src in (voice_src, llm_src, icon_src):
            self.assertNotIn("openclaw", src.lower())
            self.assertNotIn("Path.home()", src)
            self.assertNotIn("expanduser", src)
            self.assertNotIn("/opt/homebrew", src)
        self.assertIn("ANTHROPIC_API_KEY", llm_src)
        self.assertIn("ELEVENLABS_API_KEY", voice_src)

    def test_archive_is_empty(self):
        archive = ROOT / "data" / "archive"
        self.assertTrue(archive.is_dir())
        leftover = [p for p in archive.iterdir() if p.name not in {".gitkeep"}]
        self.assertEqual(leftover, [])

    def test_false_confirmation_fixtures_stay_unconfirmed(self):
        cases = json.loads((ROOT / "tests" / "fixtures" / "false_confirmation_cases.json").read_text(encoding="utf-8"))
        original = deepcopy(server.STATE)
        original_containment = server.EDITORIAL_CONTAINMENT
        try:
            # Exercise the matcher with the rumor desk opted in. Shared entity
            # words must not become an automated confirmation.
            server.EDITORIAL_CONTAINMENT = False
            for case in cases:
                with server._lock:
                    server.STATE["rumor_tracks"] = {case["rumor"]["id"]: deepcopy(case["rumor"])}
                    server.STATE["rumors"] = [deepcopy(case["rumor"])]
                    server.STATE["wire"] = [deepcopy(case["wire"])]
                    server.STATE["rumorsConfirmed"] = 0
                self.assertFalse(server.close_the_loop())
                result = server.STATE["rumor_tracks"][case["rumor"]["id"]]
                self.assertNotIn(result.get("status"), {"confirmed", "debunked"})
                self.assertEqual(server.STATE["rumorsConfirmed"], 0)
        finally:
            server.EDITORIAL_CONTAINMENT = original_containment
            with server._lock:
                server.STATE.clear()
                server.STATE.update(original)


if __name__ == "__main__":
    unittest.main()
