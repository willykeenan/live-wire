# Live Wire editorial contracts v1

These eight JSON Schemas are the machine-readable public shape of
`livewire.editorial.v1`. They are generated deterministically from the strict
Pydantic models in `editorial/models.py`:

- Story
- Claim
- Source
- Evidence
- Verification
- EditorialTransition
- Correction
- Receipt

These v1 files are frozen. `scripts/export_editorial_schemas.py` now generates
the v2 schemas in `../v2/`; do not regenerate or edit this directory.

The schemas validate individual records. Cross-record authority—including real
source independence, evidence resolution, human identity, state order, and
correction propagation—is enforced by `editorial/policy.py`. A schema-valid model
output is data, never an authority envelope.

All timestamps are timezone-aware UTC instants. `sourcePublishedAt`, `firstSeenAt`,
`liveWirePublishedAt`, `updatedAt`, and `enteredBreakingAt` have different meanings
and may not be collapsed or silently restamped.
