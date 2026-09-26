# Live Wire editorial contracts v2

These eight generated JSON Schemas describe the shape of the local Gate 1 v2
editorial artifacts. V2 is a breaking successor to the locked v1 evidence:
`Evidence.targetText` is required so every evidence record names the exact
proposition it evaluates.

Regenerate the files deterministically with:

```sh
./.venv/bin/python scripts/export_editorial_schemas.py
```

Shape validation alone never authorizes an editorial transition. Cross-object
identity, exact target binding, clocks, human review, corroboration, correction
lineage, and atomic Story/Claim/Receipt projection are enforced by
`editorial.policy` and the required test suites. The historical v1 directory
is frozen and kept for reference.
