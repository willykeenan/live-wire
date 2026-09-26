#!/usr/bin/env python3
"""Build the bounded, contract-valid Gate 1 read-only preview fixture."""

from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from editorial import (  # noqa: E402
    Claim,
    Correction,
    EditorialTransition,
    Evidence,
    HumanReview,
    Receipt,
    Source,
    Story,
    apply_correction_transition,
)
from editorial.models import SCHEMA_VERSION  # noqa: E402


OUTPUT = ROOT / "data" / "gate1" / "preview.json"
SOURCE_CAPTURE = "data/gate1/source_wire.json"
FIRST_SEEN = "2026-07-12T06:41:14Z"


ITEMS = (
    {
        "key": "harbor-grid-whitepaper",
        "category": "tech",
        "title": "Harbor Grid publishes a battery monitoring white paper",
        "summary": "A source-linked essay captured from the public wire for this read-only preview.",
        "sourceName": "Fixture Wire",
        "sourceUrl": "https://example.invalid/harbor-grid-whitepaper",
        "sourcePublishedAt": "2026-07-12T04:15:27Z",
        "independenceKey": "example.invalid:harbor-grid",
    },
    {
        "key": "aster-helix-runtime",
        "category": "tech",
        "title": "Aster Labs open-sources the Helix runtime",
        "summary": "A publisher headline captured from the public wire; Live Wire has not evaluated its truth.",
        "sourceName": "Aster Labs Notes",
        "sourceUrl": "https://example.invalid/aster-helix",
        "sourcePublishedAt": "2026-07-11T22:38:57Z",
        "independenceKey": "example.invalid:aster-labs",
    },
    {
        "key": "harbor-console",
        "category": "tech",
        "title": "Harbor Grid is an open-source portable game console, designed from scratch",
        "correctedTitle": "Harbor Grid is an open-source portable games console, designed from scratch",
        "summary": "A project headline preserved with an attributable Live Wire wording correction.",
        "sourceName": "Harbor Grid Repository",
        "sourceUrl": "https://example.invalid/harbor-console",
        "sourcePublishedAt": "2026-07-11T21:58:00Z",
        "independenceKey": "example.invalid:harbor-console",
    },
)


def review() -> HumanReview:
    return HumanReview.model_validate({
        "reviewerId": "fixture-reviewer-morgan-ellis",
        "reviewerName": "Morgan Ellis",
        "reviewerRole": "Synthetic fixture reviewer; no production authority",
        "human": True,
        "reviewedAt": "2026-07-12T06:59:00Z",
        "reason": "Compared the frozen source headline with the preview wording in this synthetic fixture.",
    })


def base_bundle(item: dict[str, str]) -> dict[str, object]:
    key = item["key"]
    story_id = f"story-preview-{key}"
    claim_id = f"claim-preview-{key}"
    source_id = f"source-preview-{key}"
    evidence_id = f"evidence-preview-{key}"
    receipt_id = f"receipt-preview-{key}"
    correction_path = f"/corrections/gate1-preview-{key}/"
    src = Source.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "source",
        "sourceId": source_id,
        "name": item["sourceName"],
        "url": item["sourceUrl"],
        "sourceType": "publisher",
        "independenceKey": item["independenceKey"],
        "attributable": True,
        "sourcePublishedAt": item["sourcePublishedAt"],
        "firstSeenAt": FIRST_SEEN,
        "retrievedAt": FIRST_SEEN,
    })
    ev = Evidence.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "evidence",
        "evidenceId": evidence_id,
        "claimId": claim_id,
        "sourceId": source_id,
        "targetText": item["title"],
        "relation": "context",
        "description": (
            "Superseded Live Wire preview wording retained as contextual comparison."
            if "correctedTitle" in item
            else "Frozen source headline retained for the read-only Gate 1 preview."
        ),
        "locator": (
            "preview capture wording; not a source quotation"
            if "correctedTitle" in item
            else "source headline"
        ),
        "capturedAt": FIRST_SEEN,
        "attributable": True,
        "claimAgreement": None,
        "eventAgreement": None,
        "entityAgreement": None,
        "locationAgreement": None,
        "timeAgreement": None,
    })
    cl = Claim.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "claim",
        "claimId": claim_id,
        "storyId": story_id,
        "text": item["title"],
        "editorialState": "reported",
        # These public-wire records have no governed semantic RiskAssessment.
        # Keyword triage cannot lower authority, so the preview fails closed.
        "riskLevel": "high",
        "eventKey": f"source-report-{key}",
        "entityKeys": [f"subject-{key}"],
        "locationKey": "not-location-bound",
        "timeStart": item["sourcePublishedAt"],
        "timeEnd": None,
        "createdAt": FIRST_SEEN,
        "updatedAt": FIRST_SEEN,
        "supersedesClaimId": None,
        "correctionIds": [],
    })
    st = Story.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "story",
        "storyId": story_id,
        "slug": f"preview-{key}",
        "title": item["title"],
        "summary": item["summary"],
        "editorialState": "reported",
        "publicationState": "withheld",
        "highRisk": True,
        "breaking": False,
        "claimIds": [claim_id],
        "sourceIds": [source_id],
        "evidenceIds": [evidence_id],
        "verificationIds": [],
        "transitionIds": [],
        "correctionIds": [],
        "receiptIds": [receipt_id],
        "sourcePublishedAt": item["sourcePublishedAt"],
        "firstSeenAt": FIRST_SEEN,
        "liveWirePublishedAt": None,
        "updatedAt": FIRST_SEEN,
        "enteredBreakingAt": None,
        "correctionPath": correction_path,
    })
    rec = Receipt.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "receipt",
        "receiptId": receipt_id,
        "storyId": story_id,
        "claimId": claim_id,
        "claimText": item["title"],
        "editorialState": "reported",
        "publicationState": "withheld",
        "sources": [src.model_dump(mode="json", by_alias=True)],
        "evidence": [ev.model_dump(mode="json", by_alias=True)],
        "verification": None,
        "reviewer": None,
        "sourcePublishedAt": item["sourcePublishedAt"],
        "firstSeenAt": FIRST_SEEN,
        "liveWirePublishedAt": None,
        "updatedAt": FIRST_SEEN,
        "updateHistory": [{
            "state": "reported",
            "publicationState": "withheld",
            "at": FIRST_SEEN,
            "summary": "Captured read-only from one public source; truth was not evaluated.",
            "transitionId": None,
            "correctionId": None,
        }],
        "corrections": [],
        "supersededTexts": [],
        "correctionPath": correction_path,
    })
    return {
        "category": item["category"],
        "story": st,
        "claims": [cl],
        "sources": [src],
        "evidence": [ev],
        "verifications": [],
        "transitions": [],
        "receipts": [rec],
        "corrections": [],
    }


def corrected_bundle(item: dict[str, str]) -> dict[str, object]:
    bundle = base_bundle(item)
    st = bundle["story"]
    cl = bundle["claims"][0]
    src = bundle["sources"][0]
    ev = bundle["evidence"][0]
    rec = bundle["receipts"][0]
    assert isinstance(st, Story) and isinstance(cl, Claim)
    assert isinstance(src, Source) and isinstance(ev, Evidence) and isinstance(rec, Receipt)
    correction_ev = Evidence.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "evidence",
        "evidenceId": "evidence-preview-harbor-console-correction",
        "claimId": cl.claim_id,
        "sourceId": src.source_id,
        "targetText": item["correctedTitle"],
        "relation": "supports",
        "description": "Frozen source headline supporting the exact corrected preview wording.",
        "locator": "source headline",
        "capturedAt": "2026-07-12T06:58:00Z",
        "attributable": True,
        "claimAgreement": True,
        "eventAgreement": True,
        "entityAgreement": True,
        "locationAgreement": True,
        "timeAgreement": True,
    })
    correction = Correction.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "correction",
        "correctionId": "correction-preview-harbor-console",
        "storyId": st.story_id,
        "claimId": cl.claim_id,
        "receiptIds": [rec.receipt_id],
        "evidenceIds": [correction_ev.evidence_id],
        "correctionType": "correction",
        "priorText": item["title"],
        "correctedText": item["correctedTitle"],
        "reason": "The preview used singular 'game'; the frozen source headline uses 'games'.",
        "issuedAt": "2026-07-12T07:00:00Z",
        "humanReview": review().model_dump(mode="json", by_alias=True),
        "correctionPath": st.correction_path,
        "supersedesCorrectionId": None,
    })
    transition = EditorialTransition.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "editorial_transition",
        "transitionId": "transition-preview-harbor-console-correction",
        "storyId": st.story_id,
        "claimIds": [cl.claim_id],
        "action": "correct",
        "fromEditorialState": "reported",
        "toEditorialState": "corrected",
        "fromPublicationState": "withheld",
        "toPublicationState": "withheld",
        "evidenceIds": [correction_ev.evidence_id],
        "verificationIds": [],
        "humanReview": review().model_dump(mode="json", by_alias=True),
        "occurredAt": "2026-07-12T07:00:00Z",
        "reason": "Issued an attributable wording correction and preserved the superseded text.",
        "correctionPath": st.correction_path,
        "highRisk": True,
    })
    projected_story, projected_claims, projected_receipts = apply_correction_transition(
        st, [cl], [src], [ev, correction_ev], [], transition, [correction], [rec]
    )
    bundle.update({
        "story": projected_story,
        "claims": projected_claims,
        "evidence": [ev, correction_ev],
        "transitions": [transition],
        "receipts": projected_receipts,
        "corrections": [correction],
    })
    return bundle


def json_bundle(bundle: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {"category": bundle["category"]}
    result["story"] = bundle["story"].model_dump(mode="json", by_alias=True)
    for key in ("claims", "sources", "evidence", "verifications", "transitions", "receipts", "corrections"):
        result[key] = [item.model_dump(mode="json", by_alias=True) for item in bundle[key]]
    return result


def build() -> dict[str, object]:
    bundles = [base_bundle(dict(item)) for item in ITEMS[:2]]
    bundles.append(corrected_bundle(dict(ITEMS[2])))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "fixtureId": "livewire-gate1-read-only-preview-v2",
        "fixtureOnly": True,
        "productionAuthority": False,
        "generatedAt": "2026-07-12T07:00:00Z",
        "sourceCapture": SOURCE_CAPTURE,
        "stories": [json_bundle(bundle) for bundle in bundles],
    }


def main() -> int:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(ITEMS)} contract-valid preview Stories to {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
