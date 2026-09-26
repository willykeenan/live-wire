"""Release hygiene: no trading code, no internal names, working live mode,
local-only request handling, and maintenance scripts that run on a clean copy."""

from __future__ import annotations

from fnmatch import fnmatch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import unittest
from unittest import mock

from app import server
from broadcast import anchor, llm, schedule, voice
from ingestion import intel


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".py", ".js", ".html", ".md", ".json", ".txt", ".yml", ".sh", ".svg", ".example", ""}
SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules"}


def repo_text_files():
    ignored_notes = "RELEASE-NOTES-FOR-*.md"
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if not path.is_file() or path.suffix not in TEXT_SUFFIXES:
            continue
        if fnmatch(path.name, ignored_notes):
            continue
        # Test modules name the forbidden terms as patterns; fixtures are still scanned.
        if path.parent == ROOT / "tests" and path.suffix == ".py":
            continue
        yield path


class NoTradingCodeTests(unittest.TestCase):
    def test_trading_modules_are_deleted(self):
        for rel in (
            "ingestion/options_flow_scanner.py", "ingestion/scorer.py", "ingestion/bundler.py",
            "ingestion/analysis.py", "ingestion/news_feed.py", "broadcast/copilot.py",
        ):
            self.assertFalse((ROOT / rel).exists(), rel)

    def test_nothing_imports_market_data_libraries(self):
        pattern = re.compile(r"^\s*(?:import|from)\s+(?:yfinance|lightweight_charts)\b", re.M)
        for path in ROOT.rglob("*.py"):
            if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
                continue
            self.assertIsNone(pattern.search(path.read_text(encoding="utf-8")), str(path))
        self.assertNotIn("yfinance", (ROOT / "requirements.txt").read_text(encoding="utf-8"))

    def test_no_markets_channel_or_program(self):
        self.assertFalse(hasattr(schedule, "MARKETS_PROGRAM"))
        self.assertFalse(hasattr(schedule, "channel_program"))
        self.assertNotIn("day-traders", (ROOT / "broadcast" / "schedule.py").read_text(encoding="utf-8"))

    def test_intel_has_no_prediction_market_feeds(self):
        names = {fn.__name__ for fn in intel._SOURCES}
        self.assertFalse({"kalshi", "polymarket"} & names)
        src = (ROOT / "ingestion" / "intel.py").read_text(encoding="utf-8").lower()
        for term in ("kalshi", "gamma-api.polymarket", "trade-api"):
            self.assertNotIn(term, src)

    def test_studio_js_and_html_have_no_chart_or_markets_code(self):
        js = (ROOT / "app" / "broadcast.js").read_text(encoding="utf-8")
        html = (ROOT / "app" / "broadcast.html").read_text(encoding="utf-8")
        for term in ("LightweightCharts", "openChart", "/api/signals", "/api/bars", "renderMarkets",
                     "Polymarket", "channel=markets"):
            self.assertNotIn(term, js)
        for term in ("lightweight-charts", "mkt-lock", 'id="chart"', 'data-channel="markets"'):
            self.assertNotIn(term, html)

    def test_rumor_vetting_asks_for_no_trading_thesis(self):
        src = (ROOT / "ingestion" / "rumors.py").read_text(encoding="utf-8")
        for term in ("primary_ticker", "expected_move_pct", "tradeable thesis"):
            self.assertNotIn(term, src)

    def test_writing_prompts_forbid_trading_advice(self):
        program = schedule.current_program()
        prompt = anchor._system(program, schedule.program_anchors(program))
        self.assertIn("Never give trading, investment, or betting advice", prompt)


class InternalNamesTests(unittest.TestCase):
    INTERNAL = re.compile(
        r"impact wire|castingiron|KE Agent Capability|Director invocation|Authority Lease|"
        r"Mission event|LW-GATE1|gate1_director|directorRegistration|VISION_MEMO",
        re.IGNORECASE,
    )

    def test_no_internal_product_or_process_names_ship(self):
        hits = []
        for path in repo_text_files():
            text = path.read_text(encoding="utf-8", errors="ignore")
            for match in self.INTERNAL.finditer(text):
                hits.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
        self.assertEqual(hits, [])

    def test_health_exposes_no_internal_authority_fields(self):
        state = server.capability_state()
        self.assertFalse([key for key in state if "director" in key.lower()])


class PrivateReleaseNotesTests(unittest.TestCase):
    def test_maintainer_release_notes_are_gitignored(self):
        patterns = [line.strip() for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
                    if line.strip() and not line.startswith("#")]
        candidates = ["RELEASE-NOTES-FOR-MAINTAINER.md"] + [
            p.name for p in ROOT.iterdir() if p.name.startswith("RELEASE-NOTES-FOR-")]
        for name in candidates:
            self.assertTrue(any(fnmatch(name, pattern) for pattern in patterns), name)


class MaintenanceScriptTests(unittest.TestCase):
    def test_audit_bundle_scripts_are_gone_and_rest_are_self_contained(self):
        for rel in ("verify_gate1_offline.py", "build_gate1_manifest.py",
                    "capture_gate1_wire.py", "build_gate1_corpus.py"):
            self.assertFalse((ROOT / "scripts" / rel).exists(), rel)
        for path in (ROOT / "scripts").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotRegex(text, r"[\"']audit[\"'/]|VISION_MEMO", path.name)
            importlib.import_module(f"scripts.{path.stem}")

    def test_preview_builder_reproduces_the_shipped_fixture(self):
        builder = importlib.import_module("scripts.build_gate1_preview")
        rendered = json.dumps(builder.build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        self.assertEqual(rendered, (ROOT / "data" / "gate1" / "preview.json").read_text(encoding="utf-8"))


class _ServerCase(unittest.TestCase):
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

    def setUp(self):
        with server._lock:
            server._TTS_HITS.clear()

    def call(self, method, path, body=None, headers=None, raw=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=5)
        payload = raw if raw is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
        sent = {"Content-Type": "application/json"} if payload is not None else {}
        sent.update(headers or {})
        connection.request(method, path, body=payload, headers=sent)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        try:
            return response.status, json.loads(data.decode("utf-8"))
        except ValueError:
            return response.status, data


LIVE_ENV = {"LIVE_WIRE_GENERATION_ENABLED": "1", "LIVE_WIRE_DEMO": "", "LIVE_WIRE_START_LOOPS": ""}


class LiveModeTests(_ServerCase):
    def test_loops_follow_generation_unless_overridden(self):
        with mock.patch.dict(os.environ, {"LIVE_WIRE_GENERATION_ENABLED": "0", "LIVE_WIRE_START_LOOPS": ""}):
            self.assertFalse(server.background_loops_enabled())
        with mock.patch.dict(os.environ, LIVE_ENV):
            self.assertTrue(server.background_loops_enabled())
            self.assertTrue(server.capability_state()["background_loops_enabled"])
        with mock.patch.dict(os.environ, {**LIVE_ENV, "LIVE_WIRE_START_LOOPS": "0"}):
            self.assertFalse(server.background_loops_enabled())

    def test_empty_wire_never_calls_the_model(self):
        program = schedule.current_program()
        # build_rundown swallows exceptions, so count calls instead of raising.
        with mock.patch.dict(os.environ, LIVE_ENV), \
                mock.patch.object(llm, "available", return_value=True), \
                mock.patch.object(llm, "json_call", return_value=None) as json_call, \
                mock.patch.object(llm, "research_json", return_value=None) as research_json:
            rundown = anchor.build_rundown(program, wire=[], rumors=[], fast=False)
        json_call.assert_not_called()
        research_json.assert_not_called()
        self.assertEqual(rundown["mode"], "template")

    def test_rundown_request_is_standby_uncached_and_marks_a_viewer(self):
        with server._lock:
            saved = dict(server.STATE["rundowns"])
            server.STATE["rundowns"].clear()
            server.STATE["lastViewer"] = 0.0
        try:
            with mock.patch.dict(os.environ, LIVE_ENV), \
                    mock.patch.object(llm, "json_call", return_value=None) as json_call, \
                    mock.patch.object(server, "build_rundown") as build:
                status, body = self.call("GET", "/api/rundown")
            json_call.assert_not_called()
            build.assert_not_called()
            self.assertEqual(status, 200)
            self.assertEqual(body["mode"], "warming_up")
            self.assertNotIn("Demo Hour", json.dumps(body))
            with server._lock:
                self.assertEqual(server.STATE["rundowns"], {})
                self.assertLess(time.time() - server.STATE["lastViewer"], server.VIEWER_TTL)
        finally:
            with server._lock:
                server.STATE["rundowns"].clear()
                server.STATE["rundowns"].update(saved)

    def test_rumors_desk_anchor_segments_are_not_dropped(self):
        program = schedule.program_public(schedule.current_program())
        rundown = {
            "mode": "live", "program": program,
            "segments": [
                {"id": "a", "analyst_id": "theo", "beat": "rumors", "kicker": "TECH",
                 "headline": "A city council votes on a very long bridge", "text": "Real news, labelled satire."},
                {"id": "b", "analyst_id": "theo", "beat": "rumors", "kicker": "UNCONFIRMED",
                 "headline": "Chatter about a merger", "text": "Unverified chatter."},
            ],
        }
        with mock.patch.dict(os.environ, LIVE_ENV):
            public = server.public_preview_rundown(rundown)
        self.assertEqual(public["mode"], "live")
        self.assertEqual([seg["id"] for seg in public["segments"]], ["a"])
        self.assertEqual(public["program"]["name"], program["name"])

    def test_env_example_and_launcher_do_not_pin_demo_mode(self):
        env_lines = [line.strip() for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()]
        self.assertNotIn("LIVE_WIRE_DEMO=1", env_lines)
        launcher = (ROOT / "launch.sh").read_text(encoding="utf-8")
        self.assertNotIn('LIVE_WIRE_DEMO="${LIVE_WIRE_DEMO:-1}"', launcher)
        self.assertNotIn('LIVE_WIRE_GENERATION_ENABLED="${LIVE_WIRE_GENERATION_ENABLED:-0}"', launcher)
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("LIVE_WIRE_START_LOOPS", "LIVE_WIRE_HOST", "LIVE_WIRE_ALLOWED_HOSTS"):
            self.assertIn(f"`{name}`", readme)


class RequestGuardTests(_ServerCase):
    def origin(self):
        return f"http://127.0.0.1:{self.port}"

    def test_tts_requires_json_content_type(self):
        status, body = self.call("POST", "/api/tts", raw=b'{"text": "hello there"}',
                                 headers={"Content-Type": "text/plain"})
        self.assertEqual((status, body["error"]), (415, "json_required"))

    def test_cross_site_requests_are_refused(self):
        for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"},
                        {"Origin": "http://127.0.0.1:1"}, {"Sec-Fetch-Site": "cross-site"}):
            with self.subTest(headers=headers):
                status, body = self.call("POST", "/api/tts", {"text": "hello there"}, headers=headers)
                self.assertEqual((status, body["error"]), (403, "cross_origin_refused"))
        status, _ = self.call("POST", "/api/rumors/scan", {}, headers={"Origin": "https://evil.example"})
        self.assertEqual(status, 403)

    def test_dns_rebinding_host_is_refused(self):
        for method, path in (("GET", "/api/health"), ("POST", "/api/tts")):
            status, body = self.call(method, path, {"text": "hi"} if method == "POST" else None,
                                     headers={"Host": f"attacker.example:{self.port}"})
            self.assertEqual((status, body["error"]), (403, "host_not_allowed"), path)

    def test_same_origin_demo_tts_still_works(self):
        status, body = self.call("POST", "/api/tts", {"text": "Vale Crossing waves hello.", "analyst_id": "dawn"},
                                 headers={"Origin": self.origin(), "Sec-Fetch-Site": "same-origin"})
        self.assertEqual(status, 200)
        self.assertTrue(body.get("demo"))

    def test_voice_id_must_be_on_the_roster_and_text_is_capped(self):
        status, body = self.call("POST", "/api/tts", {"text": "hello", "voice_id": "../../v1/user"})
        self.assertEqual((status, body["error"]), (400, "unknown_voice_id"))
        status, _ = self.call("POST", "/api/tts", {"text": "hello", "voice_id": voice.VOICES["sarah"]["voice_id"]})
        self.assertEqual(status, 200)
        status, body = self.call("POST", "/api/tts", {"text": "x" * (server.MAX_TTS_CHARS + 1)})
        self.assertEqual((status, body["error"]), (413, "text_too_long"))
        status, _ = self.call("POST", "/api/tts", raw=b'{"text": "' + b"x" * (server.MAX_BODY_BYTES + 10) + b'"}')
        self.assertEqual(status, 413)

    def test_vendor_is_never_called_cross_origin_and_only_with_roster_ids(self):
        calls = []

        class Reply:
            status_code = 200
            text = ""

            @staticmethod
            def json():
                return {"audio_base64": "AAAA", "alignment": {
                    "characters": ["h"], "character_start_times_seconds": [0.0],
                    "character_end_times_seconds": [0.1]}}

        def fake_post(url, **kwargs):
            calls.append(url)
            return Reply()

        env = {**LIVE_ENV, "ELEVENLABS_API_KEY": "test-not-a-real-key"}
        with tempfile.TemporaryDirectory() as cache, mock.patch.dict(os.environ, env), \
                mock.patch.object(voice, "CACHE_DIR", Path(cache)), \
                mock.patch.object(voice.requests, "post", side_effect=fake_post):
            self.call("POST", "/api/tts", raw=b'{"text": "spend my credits"}',
                      headers={"Content-Type": "text/plain", "Origin": "https://evil.example"})
            self.call("POST", "/api/tts", {"text": "spend my credits", "voice_id": "../../../v1/user"})
            self.assertEqual(calls, [])
            status, body = self.call("POST", "/api/tts", {"text": "A fine evening in Vale Crossing.", "analyst_id": "vance"},
                                     headers={"Origin": self.origin()})
        self.assertEqual(status, 200, body)
        self.assertEqual(len(calls), 1)
        voice_path = calls[0].split("/text-to-speech/", 1)[1].split("/", 1)[0]
        self.assertIn(voice_path, voice.VOICE_IDS)

    def test_media_lookup_is_generation_only_and_same_origin(self):
        with mock.patch.object(server, "resolve_video", side_effect=AssertionError("scraped")):
            status, body = self.call("GET", "/api/media?q=harbor+grid")
            self.assertEqual((status, body["error"]), (403, "generation_disabled"))
            with mock.patch.dict(os.environ, LIVE_ENV):
                status, body = self.call("GET", "/api/media?q=harbor+grid",
                                         headers={"Sec-Fetch-Site": "cross-site"})
            self.assertEqual((status, body["error"]), (403, "cross_origin_refused"))

    def test_allowed_hosts_extend_for_lan_use_only_when_asked(self):
        with mock.patch.object(server, "HOST", "0.0.0.0"), \
                mock.patch.dict(os.environ, {"LIVE_WIRE_ALLOWED_HOSTS": ""}):
            self.assertEqual(server.allowed_hosts(), {"127.0.0.1", "localhost", "::1"})
        with mock.patch.object(server, "HOST", "0.0.0.0"), \
                mock.patch.dict(os.environ, {"LIVE_WIRE_ALLOWED_HOSTS": "Studio.lan, 192.168.1.20:8899"}):
            self.assertTrue({"studio.lan", "192.168.1.20"} <= server.allowed_hosts())
        self.assertEqual(server._bare_host("[::1]:8899"), "::1")
        self.assertEqual(server._bare_host("LOCALHOST:8899"), "localhost")


if __name__ == "__main__":
    unittest.main()
