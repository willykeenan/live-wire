#!/usr/bin/env python3
"""Live Wire language adapter.

Prefers the official Anthropic API when ANTHROPIC_API_KEY is set. Otherwise
uses the local `claude` CLI if it is on PATH. Demo mode never reaches here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile

from broadcast import execution

DEFAULT_MODEL = "claude-haiku-4-5"
_NO_TOOLS = "Bash Edit Write Read Glob Grep WebSearch WebFetch Task TodoWrite NotebookEdit"


def _api_key() -> str:
    return (os.environ.get("ANTHROPIC_API_KEY") or "").strip()


def _claude_bin() -> str:
    """Resolve the claude CLI from PATH only. No hard-coded install locations."""
    return shutil.which("claude") or ""


def available():
    """Whether this process may invoke a language model."""
    if not execution.generation_enabled():
        return False
    if _api_key():
        return True
    return bool(_claude_bin())


def _run_api(system, user, model, timeout):
    key = _api_key()
    if not key:
        return ""
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=key, timeout=timeout)
        kwargs = {
            "model": model,
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": user}],
        }
        if system:
            kwargs["system"] = system
        msg = client.messages.create(**kwargs)
        parts = []
        for block in msg.content or []:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts).strip()
    except Exception:
        return ""


def _run_cli(system, user, model, timeout, tools=None):
    binary = _claude_bin()
    if not binary:
        return ""
    env = dict(os.environ)
    cmd = [binary, "-p", user, "--model", model, "--output-format", "text",
           "--no-session-persistence"]
    if tools:
        cmd += ["--allowedTools", tools, "--permission-mode", "acceptEdits"]
    else:
        cmd += ["--disallowed-tools", _NO_TOOLS]
    if system:
        cmd += ["--system-prompt", system]
    try:
        with tempfile.TemporaryDirectory(prefix="lw-claude-") as wd:
            p = subprocess.run(cmd, cwd=wd, env=env, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError):
        return ""
    if p.returncode != 0:
        return ""
    return (p.stdout or "").strip()


def _run(system, user, model=DEFAULT_MODEL, timeout=150):
    if not execution.authorize("llm.text"):
        return ""
    if not available():
        return ""
    if _api_key():
        return _run_api(system, user, model, timeout)
    return _run_cli(system, user, model, timeout)


def text(system, user, model=DEFAULT_MODEL, timeout=150):
    return _run(system, user, model, timeout)


def research(system, user, model=DEFAULT_MODEL, timeout=220):
    if not execution.authorize("llm.research"):
        return ""
    if not available():
        return ""
    if _api_key():
        return _run_api(system, user, model, timeout)
    return _run_cli(system, user, model, timeout, tools="WebSearch WebFetch")


def research_json(system, user, model=DEFAULT_MODEL, timeout=220):
    out = research(system, user + "\n\nReturn ONLY a valid JSON object — no prose, no markdown code fences.",
                   model, timeout)
    return _extract_json(out)


def _extract_json(s):
    if not s:
        return None
    s = s.strip()
    s = re.sub(r"^```(?:json)?", "", s).strip()
    s = re.sub(r"```$", "", s).strip()
    try:
        return json.loads(s)
    except Exception:
        pass
    for op, cl in (("{", "}"), ("[", "]")):
        i, j = s.find(op), s.rfind(cl)
        if 0 <= i < j:
            try:
                return json.loads(s[i:j + 1])
            except Exception:
                continue
    return None


def json_call(system, user, model=DEFAULT_MODEL, timeout=150):
    out = _run(system, user + "\n\nReturn ONLY valid JSON — no prose, no markdown code fences.",
               model, timeout)
    return _extract_json(out)


if __name__ == "__main__":
    print("claude available:", available())
    print("text:", text("You are terse.", "Reply with exactly: OK"))
    print("json:", json_call("You output JSON.", 'Give {"ok": true, "items": ["a","b"]}'))
