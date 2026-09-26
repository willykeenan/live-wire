#!/usr/bin/env python3
"""Deterministically export the Live Wire Gate 1 root JSON Schemas."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from editorial.models import ROOT_CONTRACTS, SCHEMA_VERSION, contract_schema  # noqa: E402


OUTPUT = ROOT / "editorial" / "contracts" / "v2"


def generated_schema_texts() -> dict[str, str]:
    rendered: dict[str, str] = {}
    for name, model in sorted(ROOT_CONTRACTS.items()):
        schema = contract_schema(model)
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["$id"] = f"https://livewire.show/schemas/editorial/v2/{name}.schema.json"
        schema["x-livewire-schema-version"] = SCHEMA_VERSION
        text = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        rendered[f"{name}.schema.json"] = text
    return rendered


def render() -> dict[str, str]:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rendered = generated_schema_texts()
    for name, text in rendered.items():
        (OUTPUT / name).write_text(text, encoding="utf-8")
    return rendered


if __name__ == "__main__":
    files = render()
    print(f"exported {len(files)} {SCHEMA_VERSION} schemas to {OUTPUT}")
