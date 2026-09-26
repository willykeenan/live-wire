#!/usr/bin/env python3
"""Credential-isolated loopback server for the read-only editorial evidence preview.

It serves the committed fixture in data/gate1 only: no keys, no model, no network
adapters, no writes. Run: python3 app/gate1_server.py --port 8901
"""

from __future__ import annotations

import argparse
from html import escape
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.gate1_capability import (
    CAPABILITY_ID,
    CAPABILITY_MANIFEST,
    CAPABILITY_VERSION,
    capability_manifest_state,
)
from app.gate1_read_model import FixtureReadError, Gate1ReadModel
from editorial.models import SCHEMA_VERSION


HOST = "127.0.0.1"
DEFAULT_PORT = 8901
READ_MODEL = Gate1ReadModel()
CORRECTION_PATH = "/corrections/2026-07-11-automated-rumor-confirmations/"
STATIC_FILES = {
    "/broadcast.js": (ROOT / "app" / "broadcast.js", "text/javascript; charset=utf-8"),
    "/avatar.js": (ROOT / "app" / "avatar.js", "text/javascript; charset=utf-8"),
    "/lipsync.js": (ROOT / "app" / "lipsync.js", "text/javascript; charset=utf-8"),
    "/assets/logo.svg": (ROOT / "assets" / "logo.svg", "image/svg+xml"),
    CORRECTION_PATH: (
        ROOT / "site" / "corrections" / "2026-07-11-automated-rumor-confirmations" / "index.html",
        "text/html; charset=utf-8",
    ),
}
GATE1_HTML = ROOT / "app" / "broadcast.html"
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; base-uri 'none'; connect-src 'self'; frame-ancestors 'none'; "
    "frame-src 'none'; form-action 'none'; img-src 'self' data:; media-src 'self'; "
    "object-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'"
)


def surface_error(error: str) -> dict[str, object]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "available": False,
        "ownerProduct": "live-wire",
        "readOnly": True,
        "fixtureOnly": True,
        "productionAuthority": False,
        "error": error,
    }


class Gate1Handler(BaseHTTPRequestHandler):
    """Serve only immutable files, fixture reads, and fixed closed responses."""

    server_version = "LiveWireGate1Reader/1.0"

    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, code: int, body: bytes, content_type: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, value: object, code: int = 200) -> None:
        payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        self._send(code, payload, "application/json; charset=utf-8")

    def _file(self, path: Path, content_type: str) -> None:
        try:
            payload = path.read_bytes()
        except OSError:
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        self._send(200, payload, content_type)

    def _gate1_html(self) -> None:
        """Serve the shared studio shell; refuse it if it ever grows an external script."""
        try:
            payload = GATE1_HTML.read_bytes()
        except OSError:
            return self._send(404, b"not found\n", "text/plain; charset=utf-8")
        if b'<script src="http://' in payload or b'<script src="https://' in payload:
            return self._send(503, b"external script blocked\n", "text/plain; charset=utf-8")
        self._send(200, payload, "text/html; charset=utf-8")

    def _correction_page(self, path: str) -> bool:
        """Render an issued fixture correction at its exact contract path."""
        try:
            ledger = READ_MODEL.corrections()
        except FixtureReadError:
            self._json(READ_MODEL.unavailable(), 503)
            return True
        correction = next(
            (
                item
                for item in ledger["corrections"]
                if item.get("correctionPath") == path
            ),
            None,
        )
        if correction is None:
            return False
        replacement = correction.get("correctedText") or "Withdrawn; no replacement wording."
        review = correction.get("humanReview") if isinstance(correction.get("humanReview"), dict) else {}
        receipt_ids = ", ".join(str(item) for item in correction.get("receiptIds", []))
        evidence_ids = ", ".join(str(item) for item in correction.get("evidenceIds", []))
        body = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Live Wire fixture correction</title>
<style>body{{max-width:760px;margin:48px auto;padding:0 20px;font:16px/1.6 system-ui;background:#0b0d10;color:#f5f1e8}}a{{color:#f0c780}}code{{overflow-wrap:anywhere}}.note{{border:1px solid #8b6f34;padding:14px}}</style>
<main><p><a href="/">← Live Wire evidence preview</a></p><h1>Fixture correction</h1>
<p class="note"><strong>Read-only synthetic evidence.</strong> This local page grants no newsroom, publication, or production authority.</p>
<dl><dt>Type</dt><dd>{escape(str(correction.get('correctionType') or 'correction'))}</dd>
<dt>Prior wording</dt><dd>{escape(str(correction.get('priorText') or ''))}</dd>
<dt>Replacement</dt><dd>{escape(str(replacement))}</dd>
<dt>Reason</dt><dd>{escape(str(correction.get('reason') or ''))}</dd>
<dt>Issued</dt><dd>{escape(str(correction.get('issuedAt') or ''))}</dd>
<dt>Reviewer</dt><dd>{escape(str(review.get('reviewerName') or ''))}</dd>
<dt>Reviewer role</dt><dd>{escape(str(review.get('reviewerRole') or ''))}</dd>
<dt>Reviewed</dt><dd>{escape(str(review.get('reviewedAt') or ''))}</dd>
<dt>Story ID</dt><dd><code>{escape(str(correction.get('storyId') or ''))}</code></dd>
<dt>Claim ID</dt><dd><code>{escape(str(correction.get('claimId') or ''))}</code></dd>
<dt>Receipt IDs</dt><dd><code>{escape(receipt_ids)}</code></dd>
<dt>Evidence IDs</dt><dd><code>{escape(evidence_ids)}</code></dd>
<dt>Correction ID</dt><dd><code>{escape(str(correction.get('correctionId') or ''))}</code></dd></dl></main></html>"""
        self._send(200, body.encode("utf-8"), "text/html; charset=utf-8")
        return True

    def _gate1_read(self, path: str, query: dict[str, list[str]]) -> bool:
        try:
            if path == "/api/stories":
                self._json(READ_MODEL.story_index())
                return True
            if path == "/api/story":
                story_id = (query.get("id") or [""])[0]
                if not story_id:
                    self._json(surface_error("story_id_required"), 400)
                else:
                    result = READ_MODEL.story_detail(story_id)
                    self._json(result if result is not None else surface_error("story_not_found"), 200 if result else 404)
                return True
            if path == "/api/receipts":
                story_id = (query.get("story") or [""])[0]
                if not story_id:
                    self._json(surface_error("story_id_required"), 400)
                else:
                    result = READ_MODEL.receipts(story_id)
                    self._json(result if result is not None else surface_error("story_not_found"), 200 if result else 404)
                return True
            if path == "/api/corrections":
                self._json(READ_MODEL.corrections())
                return True
        except FixtureReadError:
            self._json(READ_MODEL.unavailable(), 503)
            return True
        return False

    def _closed_legacy_read(self, path: str) -> bool:
        fixed: dict[str, object] = {
            "/api/analysts": [],
            "/api/rundown": {
                "segments": [], "count": 0, "mode": "contained", "program": None,
                "audio_enabled": False, "release_state": "gate1_read_only",
            },
            "/api/schedule": {
                "now": None, "grid_weekday": [], "grid_weekend": [], "mode": "contained",
            },
            "/api/intel": {"intel": [], "briefs": [], "status": "contained"},
            "/api/media": {},
        }
        if path in fixed:
            self._json(fixed[path])
            return True
        return False

    def _dispatch_get(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            try:
                manifest_state = capability_manifest_state(READ_MODEL)
                manifest_available = True
            except FixtureReadError:
                manifest_state = None
                manifest_available = False
            fixture_available = bool(
                manifest_state is not None
                and manifest_state["runtimeHealth"]["fixtureAvailable"]
            )
            healthy_boundary = fixture_available and manifest_available
            health = (
                manifest_state["runtimeHealth"]["status"]
                if manifest_state is not None
                else "unavailable"
            )
            self._json({
                "ok": healthy_boundary,
                "status": health,
                "mode": "gate1_read_only",
                "host": HOST,
                "ownerProduct": "live-wire",
                "fixtureOnly": True,
                "productionAuthority": False,
                "credentialsLoaded": False,
                "legacyRuntimeImported": False,
                "capabilityManifest": CAPABILITY_MANIFEST,
                "capabilityId": CAPABILITY_ID,
                "capabilityVersion": CAPABILITY_VERSION,
                "fixtureAvailable": fixture_available,
                "manifestAvailable": manifest_available,
                "manifestRuntimeHealth": (
                    manifest_state["runtimeHealth"]["status"]
                    if manifest_state is not None
                    else "unavailable"
                ),
                "sideEffects": [],
                "estimatedCost": {
                    "moneyMicros": 0, "modelTokens": 0, "concurrency": 1,
                    "retries": 0, "externalActions": 0,
                },
            })
            return
        if path == "/api/capabilities":
            try:
                manifest = capability_manifest_state(READ_MODEL)
            except FixtureReadError:
                self._json({
                    "capabilityId": CAPABILITY_ID,
                    "version": CAPABILITY_VERSION,
                    "health": "unavailable",
                    "error": "gate1_capability_unavailable",
                }, 503)
                return
            self._json(
                manifest,
                200 if manifest["runtimeHealth"]["fixtureAvailable"] else 503,
            )
            return
        if self._gate1_read(path, parse_qs(parsed.query, keep_blank_values=True)):
            return
        if self._closed_legacy_read(path):
            return
        if path in {"/", "/index.html"}:
            self._gate1_html()
            return
        if path.startswith("/corrections/") and path != CORRECTION_PATH:
            if self._correction_page(path):
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")
            return
        static = STATIC_FILES.get(path)
        if static is not None:
            self._file(*static)
            return
        if path.startswith("/api/"):
            self._json({"error": "capability_closed", "readOnly": True}, 403)
            return
        self._send(404, b"not found\n", "text/plain; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch_get()

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch_get()

    def _deny_mutation(self) -> None:
        self._json({"error": "read_only_surface", "readOnly": True}, 405)

    do_POST = _deny_mutation  # type: ignore[assignment]  # noqa: N815
    do_PUT = _deny_mutation  # type: ignore[assignment]  # noqa: N815
    do_PATCH = _deny_mutation  # type: ignore[assignment]  # noqa: N815
    do_DELETE = _deny_mutation  # type: ignore[assignment]  # noqa: N815


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.port <= 65_535:
        raise SystemExit("port must be between 1 and 65535")
    server = HTTPServer((HOST, args.port), Gate1Handler)
    print(f"Live Wire evidence preview (read-only): http://{HOST}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
