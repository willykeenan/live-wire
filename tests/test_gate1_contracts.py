"""Contract, authority, and correction regression tests for Gate 1."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from pydantic import ValidationError

from editorial import (
    Claim,
    Correction,
    EditorialTransition,
    Evidence,
    HumanReview,
    PolicyViolation,
    Receipt,
    Source,
    Story,
    Verification,
    apply_correction_transition,
    apply_transition,
    authorize_transition,
)
from editorial.models import ROOT_CONTRACTS, SCHEMA_VERSION
from scripts.export_editorial_schemas import generated_schema_texts


ROOT = Path(__file__).resolve().parents[1]
T0 = "2026-07-12T04:00:00Z"
T1 = "2026-07-12T05:00:00Z"
T2 = "2026-07-12T05:10:00Z"
T3 = "2026-07-12T05:20:00Z"
T4 = "2026-07-12T05:30:00Z"
T5 = "2026-07-12T05:40:00Z"
CORRECTION_PATH = "/corrections/gate1-fixture/"
CORRECTED_TEXT = "The fixture project published an open-source portable console design."


def reviewer(at: str = T3) -> HumanReview:
    return HumanReview.model_validate({
        "reviewerId": "reviewer-morgan-ellis",
        "reviewerName": "Morgan Ellis",
        "reviewerRole": "Standards editor",
        "human": True,
        "reviewedAt": at,
        "reason": "Reviewed the complete frozen evidence packet and source provenance.",
    })


def source(
    source_id: str = "source-one",
    independence_key: str = "publisher-one",
    source_type: str = "publisher",
) -> Source:
    return Source.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "source",
        "sourceId": source_id,
        "name": f"Publisher {source_id}",
        "url": f"https://{independence_key}.example/report",
        "sourceType": source_type,
        "independenceKey": independence_key,
        "attributable": True,
        "sourcePublishedAt": T0,
        "firstSeenAt": T1,
        "retrievedAt": T2,
    })


def claim(state: str = "reported", risk: str = "standard") -> Claim:
    return Claim.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "claim",
        "claimId": "claim-one",
        "storyId": "story-one",
        "text": "The fixture project published a portable console design.",
        "editorialState": state,
        "riskLevel": risk,
        "eventKey": "project-publication",
        "entityKeys": ["fixture-project"],
        "locationKey": "global",
        "timeStart": T0,
        "timeEnd": None,
        "createdAt": T1,
        "updatedAt": T2,
        "supersedesClaimId": None,
        "correctionIds": [],
    })


def evidence(
    evidence_id: str = "evidence-one",
    source_id: str = "source-one",
    relation: str = "supports",
    agreement: bool | None = True,
    target_text: str | None = None,
) -> Evidence:
    return Evidence.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "evidence",
        "evidenceId": evidence_id,
        "claimId": "claim-one",
        "sourceId": source_id,
        "targetText": target_text or claim().text,
        "relation": relation,
        "description": "Frozen source excerpt and locator for the fixture claim.",
        "locator": "headline and first paragraph",
        "capturedAt": T2,
        "attributable": True,
        "claimAgreement": agreement,
        "eventAgreement": agreement,
        "entityAgreement": agreement,
        "locationAgreement": agreement,
        "timeAgreement": agreement,
    })


def correction_evidence(
    target_text: str = CORRECTED_TEXT,
    evidence_id: str = "evidence-correction",
) -> Evidence:
    return evidence(evidence_id=evidence_id, target_text=target_text)


def correction_packet(*additional: Evidence) -> list[Evidence]:
    return [evidence(), correction_evidence(), *additional]


def story(
    state: str = "reported",
    publication: str = "withheld",
    high_risk: bool = False,
    source_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    verification_ids: list[str] | None = None,
) -> Story:
    return Story.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "story",
        "storyId": "story-one",
        "slug": "fixture-project-report",
        "title": "The fixture project published a portable console design.",
        "summary": "An attributed source report preserved for contract testing.",
        "editorialState": state,
        "publicationState": publication,
        "highRisk": high_risk,
        "breaking": False,
        "claimIds": ["claim-one"],
        "sourceIds": source_ids or ["source-one"],
        "evidenceIds": evidence_ids or ["evidence-one"],
        "verificationIds": verification_ids or [],
        "transitionIds": [],
        "correctionIds": [],
        "receiptIds": ["receipt-one"],
        "sourcePublishedAt": T0,
        "firstSeenAt": T1,
        "liveWirePublishedAt": None,
        "updatedAt": T2,
        "enteredBreakingAt": None,
        "correctionPath": CORRECTION_PATH,
    })


def receipt() -> Receipt:
    src = source()
    item = evidence()
    return Receipt.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "receipt",
        "receiptId": "receipt-one",
        "storyId": "story-one",
        "claimId": "claim-one",
        "claimText": claim().text,
        "editorialState": "reported",
        "publicationState": "withheld",
        "sources": [src.model_dump(mode="json", by_alias=True)],
        "evidence": [item.model_dump(mode="json", by_alias=True)],
        "verification": None,
        "reviewer": None,
        "sourcePublishedAt": T0,
        "firstSeenAt": T1,
        "liveWirePublishedAt": None,
        "updatedAt": T2,
        "updateHistory": [{
            "state": "reported",
            "publicationState": "withheld",
            "at": T2,
            "summary": "Captured from one source; truth was not evaluated.",
            "transitionId": None,
            "correctionId": None,
        }],
        "corrections": [],
        "supersededTexts": [],
        "correctionPath": CORRECTION_PATH,
    })


def receipt_with(sources: list[Source], items: list[Evidence]) -> Receipt:
    return Receipt.model_validate({
        **receipt().model_dump(mode="json", by_alias=True),
        "sources": [item.model_dump(mode="json", by_alias=True) for item in sources],
        "evidence": [item.model_dump(mode="json", by_alias=True) for item in items],
        "sourcePublishedAt": min(item.source_published_at for item in sources),
        "firstSeenAt": min(item.first_seen_at for item in sources),
    })


def supported_verification(items: list[Evidence], count: int = 2) -> Verification:
    return Verification.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "verification",
        "verificationId": "verification-one",
        "claimId": "claim-one",
        "outcome": "supported",
        "evidenceIds": [item.evidence_id for item in items],
        "claimAgreement": True,
        "eventAgreement": True,
        "entityAgreement": True,
        "locationAgreement": True,
        "timeAgreement": True,
        "corroboration": "two_independent",
        "independentSourceCount": count,
        "officialPrimarySourceId": None,
        "lexicalOverlapOnly": False,
        "humanReview": reviewer().model_dump(mode="json", by_alias=True),
        "verifiedAt": T4,
        "reason": "Two independent attributable records support every claim dimension.",
        "correctionPath": CORRECTION_PATH,
    })


def confirm_transition(items: list[Evidence], high_risk: bool = False) -> EditorialTransition:
    return EditorialTransition.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "editorial_transition",
        "transitionId": "transition-confirm",
        "storyId": "story-one",
        "claimIds": ["claim-one"],
        "action": "confirm",
        "fromEditorialState": "reported",
        "toEditorialState": "confirmed",
        "fromPublicationState": "withheld",
        "toPublicationState": "withheld",
        "evidenceIds": [item.evidence_id for item in items],
        "verificationIds": ["verification-one"],
        "humanReview": reviewer().model_dump(mode="json", by_alias=True),
        "occurredAt": T4,
        "reason": "Standards approved confirmation after resolving all evidence links.",
        "correctionPath": CORRECTION_PATH,
        "highRisk": high_risk,
    })


def correction() -> Correction:
    return Correction.model_validate({
        "schemaVersion": SCHEMA_VERSION,
        "objectType": "correction",
        "correctionId": "correction-one",
        "storyId": "story-one",
        "claimId": "claim-one",
        "receiptIds": ["receipt-one"],
        "evidenceIds": ["evidence-correction"],
        "correctionType": "correction",
        "priorText": claim().text,
        "correctedText": CORRECTED_TEXT,
        "reason": "The original wording omitted the source's open-source qualifier.",
        "issuedAt": T4,
        "humanReview": reviewer().model_dump(mode="json", by_alias=True),
        "correctionPath": CORRECTION_PATH,
        "supersedesCorrectionId": None,
    })


class SchemaContractTests(unittest.TestCase):
    def test_all_eight_v2_schemas_are_checked_in_and_byte_reproducible(self):
        expected = generated_schema_texts()
        directory = ROOT / "editorial" / "contracts" / "v2"
        self.assertEqual(len(expected), 8)
        self.assertEqual(
            sorted(expected),
            sorted(path.name for path in directory.glob("*.schema.json")),
        )
        for name, text in expected.items():
            with self.subTest(name=name):
                self.assertEqual((directory / name).read_text(encoding="utf-8"), text)
                schema = json.loads(text)
                self.assertEqual(schema["x-livewire-schema-version"], SCHEMA_VERSION)
                self.assertTrue(schema["$id"].endswith(name))

    def test_root_contracts_forbid_extra_fields(self):
        raw = source().model_dump(mode="json", by_alias=True)
        raw["modelConfidence"] = 0.99
        with self.assertRaises(ValidationError):
            Source.model_validate(raw)

    def test_timestamps_must_be_zoned_and_keep_observation_order(self):
        raw = source().model_dump(mode="json", by_alias=True)
        raw["firstSeenAt"] = "2026-07-11T23:00:00"
        with self.assertRaises(ValidationError):
            Source.model_validate(raw)

    def test_source_url_requires_a_real_host_and_no_embedded_credentials(self):
        raw = source().model_dump(mode="json", by_alias=True)
        for value in (
            "https://?x",
            "https://#x",
            "https://user@example.com/report",
            "https://user@:80/path",
            "https://-invalid.example/report",
        ):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                Source.model_validate({**raw, "url": value})
        raw["firstSeenAt"] = "2026-07-11T23:00:00Z"
        with self.assertRaises(ValidationError):
            Source.model_validate(raw)

    def test_reviewer_must_be_a_named_human(self):
        raw = reviewer().model_dump(mode="json", by_alias=True)
        for label in ("AI system", "Anonymous editor", "Standards Bot"):
            with self.subTest(label=label), self.assertRaises(ValidationError):
                HumanReview.model_validate({**raw, "reviewerName": label})
        for name in ("Ren Vale", "Ai Marlow", "李小舟"):
            with self.subTest(name=name):
                parsed = HumanReview.model_validate({**raw, "reviewerName": name})
                self.assertEqual(parsed.reviewer_name, name)

    def test_correction_path_rejects_fragment_query_and_traversal(self):
        raw = story().model_dump(mode="json", by_alias=True)
        for path in (
            "https://example.com/corrections/x/",
            "/corrections/x#y/",
            "/corrections/../x/",
            "/corrections/./private/",
            "/corrections/%2e%2e/private/",
            "/corrections/%2fadmin/",
            "/corrections/",
        ):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                Story.model_validate({**raw, "correctionPath": path})


class AuthorityPolicyTests(unittest.TestCase):
    def setUp(self):
        self.sources = [source("source-one", "origin-one"), source("source-two", "origin-two")]
        self.evidence = [
            evidence("evidence-one", "source-one", "supports", True),
            evidence("evidence-two", "source-two", "supports", True),
        ]
        self.verification = supported_verification(self.evidence)
        self.transition = confirm_transition(self.evidence)
        self.story = story(source_ids=[s.source_id for s in self.sources], evidence_ids=[e.evidence_id for e in self.evidence])
        self.claim = claim()

    def test_two_resolved_independent_sources_can_be_authorized(self):
        self.assertTrue(authorize_transition(
            self.story, [self.claim], self.sources, self.evidence,
            [self.verification], self.transition,
        ))

    def test_duplicate_independence_keys_fail_even_with_claimed_count_two(self):
        sources = [source("source-one", "same-origin"), source("source-two", "same-origin")]
        with self.assertRaisesRegex(PolicyViolation, "NOT_INDEPENDENT"):
            authorize_transition(
                self.story, [self.claim], sources, self.evidence,
                [self.verification], self.transition,
            )

        same_host_second = Source.model_validate({
            **self.sources[1].model_dump(mode="json", by_alias=True),
            "url": "https://origin-one.example/another-report",
        })
        with self.assertRaisesRegex(PolicyViolation, "NOT_HOST_DIVERSE"):
            authorize_transition(
                self.story,
                [self.claim],
                [self.sources[0], same_host_second],
                self.evidence,
                [self.verification],
                self.transition,
            )

        invalid_host = self.sources[1].model_copy(update={"url": "https://?x"})
        with self.assertRaisesRegex(PolicyViolation, "NOT_HOST_DIVERSE"):
            authorize_transition(
                self.story,
                [self.claim],
                [self.sources[0], invalid_host],
                self.evidence,
                [self.verification],
                self.transition,
            )

    def test_forged_independent_count_fails_against_resolved_sources(self):
        verification = supported_verification(self.evidence, count=3)
        with self.assertRaisesRegex(PolicyViolation, "FORGED_SOURCE_COUNT"):
            authorize_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [verification], self.transition,
            )

    def test_context_evidence_cannot_be_counted_as_support(self):
        items = [self.evidence[0], evidence("evidence-two", "source-two", "context", True)]
        verification = supported_verification(items)
        transition = confirm_transition(items)
        with self.assertRaisesRegex(PolicyViolation, "NON_SUPPORTING_EVIDENCE"):
            authorize_transition(self.story, [self.claim], self.sources, items, [verification], transition)

    def test_lexical_only_or_dimension_mismatch_cannot_form_supported_verification(self):
        raw = self.verification.model_dump(mode="json", by_alias=True)
        with self.assertRaises(ValidationError):
            Verification.model_validate({**raw, "lexicalOverlapOnly": True})
        for field in ("claimAgreement", "eventAgreement", "entityAgreement", "locationAgreement", "timeAgreement"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                Verification.model_validate({**raw, field: False})

    def test_low_risk_privileged_and_all_high_risk_transitions_need_controls(self):
        base = self.transition.model_dump(mode="json", by_alias=True)
        with self.assertRaises(ValidationError):
            EditorialTransition.model_validate({**base, "evidenceIds": [], "humanReview": None})
        report = {
            **base,
            "transitionId": "transition-report",
            "action": "report",
            "fromEditorialState": "draft",
            "toEditorialState": "reported",
            "verificationIds": [],
            "highRisk": True,
            "evidenceIds": [],
            "humanReview": None,
        }
        with self.assertRaises(ValidationError):
            EditorialTransition.model_validate(report)

    def test_report_and_develop_require_attributable_resolved_evidence(self):
        report_raw = {
            **self.transition.model_dump(mode="json", by_alias=True),
            "transitionId": "transition-report",
            "action": "report",
            "fromEditorialState": "draft",
            "toEditorialState": "reported",
            "verificationIds": [],
            "evidenceIds": [],
            "humanReview": None,
            "highRisk": False,
        }
        report = EditorialTransition.model_validate(report_raw)
        draft_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "editorialState": "draft",
        })
        draft_claim = Claim.model_validate({
            **self.claim.model_dump(mode="json", by_alias=True),
            "editorialState": "draft",
        })
        with self.assertRaisesRegex(PolicyViolation, "MISSING_EVIDENCE"):
            authorize_transition(
                draft_story, [draft_claim], self.sources, self.evidence,
                [], report,
            )
        valid_report = EditorialTransition.model_validate({
            **report_raw,
            "evidenceIds": ["evidence-one"],
            "humanReview": reviewer().model_dump(mode="json", by_alias=True),
        })
        self.assertTrue(authorize_transition(
            draft_story, [draft_claim], self.sources, self.evidence,
            [], valid_report,
        ))
        no_human_report = EditorialTransition.model_validate({
            **valid_report.model_dump(mode="json", by_alias=True),
            "humanReview": None,
        })
        with self.assertRaisesRegex(PolicyViolation, "MISSING_HUMAN_REVIEW"):
            authorize_transition(
                draft_story, [draft_claim], self.sources, self.evidence,
                [], no_human_report,
            )
        future_review_report = EditorialTransition.model_validate({
            **valid_report.model_dump(mode="json", by_alias=True),
            "humanReview": reviewer(T5).model_dump(mode="json", by_alias=True),
        })
        with self.assertRaisesRegex(PolicyViolation, "FUTURE_REVIEW"):
            authorize_transition(
                draft_story, [draft_claim], self.sources, self.evidence,
                [], future_review_report,
            )
        verification_smuggling = EditorialTransition.model_validate({
            **valid_report.model_dump(mode="json", by_alias=True),
            "verificationIds": ["verification-one"],
        })
        with self.assertRaisesRegex(PolicyViolation, "UNEXPECTED_VERIFICATION"):
            authorize_transition(
                draft_story, [draft_claim], self.sources, self.evidence,
                [self.verification], verification_smuggling,
            )

        develop_raw = {
            **report_raw,
            "transitionId": "transition-develop",
            "action": "develop",
            "fromEditorialState": "reported",
            "toEditorialState": "developing",
            "occurredAt": T4,
        }
        develop = EditorialTransition.model_validate(develop_raw)
        with self.assertRaisesRegex(PolicyViolation, "MISSING_EVIDENCE"):
            authorize_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [], develop,
            )
        no_change = EditorialTransition.model_validate({
            **develop_raw,
            "evidenceIds": ["evidence-one"],
            "humanReview": reviewer().model_dump(mode="json", by_alias=True),
        })
        with self.assertRaisesRegex(PolicyViolation, "NO_NEW_EVIDENCE"):
            authorize_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [], no_change,
            )

    def test_develop_projects_new_evidence_and_its_source(self):
        new_source = source("source-three", "origin-three")
        new_evidence = evidence("evidence-three", "source-three", "context", None)
        transition = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "transitionId": "transition-develop",
            "action": "develop",
            "fromEditorialState": "reported",
            "toEditorialState": "developing",
            "verificationIds": [],
            "evidenceIds": ["evidence-three"],
            "humanReview": reviewer().model_dump(mode="json", by_alias=True),
            "highRisk": False,
        })
        projected, _, projected_receipts = apply_transition(
            self.story,
            [self.claim],
            [*self.sources, new_source],
            [*self.evidence, new_evidence],
            [],
            transition,
            [receipt_with(self.sources, self.evidence)],
        )
        self.assertIn("evidence-three", projected.evidence_ids)
        self.assertIn("source-three", projected.source_ids)
        self.assertEqual(projected_receipts[0].editorial_state.value, "developing")
        self.assertIn("evidence-three", {item.evidence_id for item in projected_receipts[0].evidence})
        self.assertEqual(projected_receipts[0].update_history[-1].transition_id, "transition-develop")

    def test_partial_story_transition_and_future_authority_inputs_fail_closed(self):
        second_claim = Claim.model_validate({
            **self.claim.model_dump(mode="json", by_alias=True),
            "claimId": "claim-two",
            "text": "A second proposition remains independently unresolved.",
            "entityKeys": ["fixture-project-two"],
        })
        multi_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "claimIds": ["claim-one", "claim-two"],
            "receiptIds": ["receipt-one", "receipt-two"],
        })
        with self.assertRaisesRegex(PolicyViolation, "PARTIAL_STORY_TRANSITION"):
            authorize_transition(
                multi_story,
                [self.claim, second_claim],
                self.sources,
                self.evidence,
                [self.verification],
                self.transition,
            )
        complete_multi_transition = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "claimIds": ["claim-one", "claim-two"],
        })
        with self.assertRaisesRegex(PolicyViolation, "BOUNDED_ONE_CLAIM_REQUIRED"):
            authorize_transition(
                multi_story,
                [self.claim, second_claim],
                self.sources,
                self.evidence,
                [self.verification],
                complete_multi_transition,
            )
        with self.assertRaisesRegex(PolicyViolation, "BOUNDED_ONE_CLAIM_REQUIRED"):
            apply_transition(
                multi_story,
                [self.claim, second_claim],
                self.sources,
                self.evidence,
                [self.verification],
                complete_multi_transition,
                [receipt()],
            )

        future_evidence = Evidence.model_validate({
            **self.evidence[0].model_dump(mode="json", by_alias=True),
            "capturedAt": T5,
        })
        temporal_evidence = [future_evidence, self.evidence[1]]
        temporal_verification = supported_verification(temporal_evidence)
        temporal_transition = confirm_transition(temporal_evidence)
        with self.assertRaisesRegex(PolicyViolation, "BACKFILLED_STORY_EVIDENCE"):
            authorize_transition(
                self.story,
                [self.claim],
                self.sources,
                temporal_evidence,
                [temporal_verification],
                temporal_transition,
            )

        future_source = Source.model_validate({
            **self.sources[0].model_dump(mode="json", by_alias=True),
            "retrievedAt": T5,
        })
        with self.assertRaisesRegex(PolicyViolation, "BACKFILLED_STORY_SOURCE"):
            authorize_transition(
                self.story,
                [self.claim],
                [future_source, self.sources[1]],
                self.evidence,
                [self.verification],
                self.transition,
            )

        transition_only_source = source("source-three", "origin-three")
        transition_only_future = Evidence.model_validate({
            **evidence("evidence-three", "source-three").model_dump(
                mode="json", by_alias=True
            ),
            "capturedAt": T5,
        })
        develop = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "transitionId": "transition-future-develop",
            "action": "develop",
            "fromEditorialState": "reported",
            "toEditorialState": "developing",
            "verificationIds": [],
            "evidenceIds": ["evidence-three"],
        })
        with self.assertRaisesRegex(PolicyViolation, "FUTURE_EVIDENCE"):
            authorize_transition(
                self.story,
                [self.claim],
                [*self.sources, transition_only_source],
                [*self.evidence, transition_only_future],
                [],
                develop,
            )

        future_verification = Verification.model_validate({
            **self.verification.model_dump(mode="json", by_alias=True),
            "verifiedAt": T5,
        })
        with self.assertRaisesRegex(PolicyViolation, "FUTURE_VERIFICATION"):
            authorize_transition(
                self.story,
                [self.claim],
                self.sources,
                self.evidence,
                [future_verification],
                self.transition,
            )

        premature_verification = Verification.model_validate({
            **self.verification.model_dump(mode="json", by_alias=True),
            "humanReview": reviewer(T1).model_dump(mode="json", by_alias=True),
            "verifiedAt": T1,
        })
        with self.assertRaisesRegex(PolicyViolation, "EVIDENCE_AFTER_VERIFICATION_REVIEW"):
            authorize_transition(
                self.story,
                [self.claim],
                self.sources,
                self.evidence,
                [premature_verification],
                self.transition,
            )

    def test_publication_and_global_promotion_remain_capability_closed(self):
        publish = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "transitionId": "transition-publish",
            "action": "publish",
            "fromEditorialState": "reported",
            "toEditorialState": "reported",
            "fromPublicationState": "withheld",
            "toPublicationState": "published",
            "verificationIds": [],
        })
        with self.assertRaisesRegex(PolicyViolation, "CAPABILITY_CLOSED"):
            authorize_transition(
                self.story, [self.claim], self.sources, self.evidence, [], publish
            )

    def test_transition_cannot_understate_resolved_risk(self):
        risky_story = story(
            high_risk=True,
            source_ids=[s.source_id for s in self.sources],
            evidence_ids=[e.evidence_id for e in self.evidence],
        )
        risky_claim = claim(risk="high")
        with self.assertRaisesRegex(PolicyViolation, "RISK_FLAG_MISMATCH"):
            authorize_transition(
                risky_story, [risky_claim], self.sources, self.evidence,
                [self.verification], self.transition,
            )

        understated_story = Story.model_validate({
            **risky_story.model_dump(mode="json", by_alias=True),
            "highRisk": False,
        })
        high_transition = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "highRisk": True,
        })
        with self.assertRaisesRegex(PolicyViolation, "STORY_RISK_MISMATCH"):
            apply_transition(
                understated_story,
                [risky_claim],
                self.sources,
                self.evidence,
                [self.verification],
                high_transition,
                [receipt_with(self.sources, self.evidence)],
            )

    def test_apply_transition_preserves_every_prior_clock(self):
        projected, claims, receipts = apply_transition(
            self.story, [self.claim], self.sources, self.evidence,
            [self.verification], self.transition,
            [receipt_with(self.sources, self.evidence)],
        )
        self.assertEqual(projected.editorial_state.value, "confirmed")
        self.assertEqual(claims[0].editorial_state.value, "confirmed")
        self.assertEqual(projected.source_published_at, self.story.source_published_at)
        self.assertEqual(projected.first_seen_at, self.story.first_seen_at)
        self.assertEqual(projected.live_wire_published_at, self.story.live_wire_published_at)
        self.assertEqual(projected.entered_breaking_at, self.story.entered_breaking_at)
        self.assertEqual(projected.updated_at.isoformat(), "2026-07-12T05:30:00+00:00")
        self.assertEqual(receipts[0].editorial_state.value, "confirmed")
        self.assertEqual(receipts[0].verification, self.verification)
        self.assertEqual(receipts[0].reviewer, self.transition.human_review)
        self.assertEqual(receipts[0].update_history[-1].transition_id, "transition-confirm")

    def test_atomic_transition_rejects_forged_receipt_history_reviewer_and_corrections(self):
        canonical = receipt_with(self.sources, self.evidence)
        raw = canonical.model_dump(mode="json", by_alias=True)
        forged_origin = Receipt.model_validate({
            **raw,
            "updateHistory": [{
                **raw["updateHistory"][0],
                "state": "confirmed",
                "publicationState": "globally_promoted",
            }],
        })
        with self.assertRaisesRegex(PolicyViolation, "RECEIPT_HISTORY_MISMATCH"):
            apply_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition, [forged_origin],
            )

        forged_reviewer = Receipt.model_validate({
            **raw,
            "reviewer": reviewer().model_dump(mode="json", by_alias=True),
        })
        with self.assertRaisesRegex(PolicyViolation, "UNREACHABLE_RECEIPT_REVIEWER"):
            apply_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition, [forged_reviewer],
            )

        forged_correction = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "receiptIds": [canonical.receipt_id],
            "evidenceIds": ["evidence-one"],
        })
        smuggled = Receipt.model_validate({
            **raw,
            "reviewer": forged_correction.human_review.model_dump(mode="json", by_alias=True),
            "corrections": [forged_correction.model_dump(mode="json", by_alias=True)],
            "supersededTexts": [forged_correction.prior_text],
        })
        with self.assertRaisesRegex(PolicyViolation, "RECEIPT_CORRECTION_CHAIN_MISMATCH"):
            apply_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition, [smuggled],
            )

        forged_clock = Receipt.model_validate({
            **raw,
            "liveWirePublishedAt": T1,
        })
        with self.assertRaisesRegex(PolicyViolation, "RECEIPT_STORY_CLOCK_MISMATCH"):
            apply_transition(
                self.story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition, [forged_clock],
            )

    def test_noncorrection_projection_closes_without_a_complete_prior_replay_ledger(self):
        forged_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "transitionIds": ["transition-unresolved-prior"],
        })
        with self.assertRaisesRegex(PolicyViolation, "PRIOR_TRANSITION_REPLAY_REQUIRED"):
            authorize_transition(
                forged_story,
                [self.claim],
                self.sources,
                self.evidence,
                [self.verification],
                self.transition,
            )
        with self.assertRaisesRegex(PolicyViolation, "PRIOR_TRANSITION_REPLAY_REQUIRED"):
            apply_transition(
                forged_story,
                [self.claim],
                self.sources,
                self.evidence,
                [self.verification],
                self.transition,
                [receipt_with(self.sources, self.evidence)],
            )

    def test_developing_origin_is_closed_consistently_by_authorize_and_apply(self):
        developing_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "editorialState": "developing",
        })
        developing_claim = Claim.model_validate({
            **self.claim.model_dump(mode="json", by_alias=True),
            "editorialState": "developing",
        })
        transition = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "fromEditorialState": "developing",
        })
        developing_receipt = Receipt.model_validate({
            **receipt_with(self.sources, self.evidence).model_dump(mode="json", by_alias=True),
            "editorialState": "developing",
            "updateHistory": [{
                **receipt().update_history[0].model_dump(mode="json", by_alias=True),
                "state": "developing",
            }],
        })
        with self.assertRaisesRegex(PolicyViolation, "ORIGIN_STATE_UNSUPPORTED"):
            authorize_transition(
                developing_story, [developing_claim], self.sources, self.evidence,
                [self.verification], transition,
            )
        with self.assertRaisesRegex(PolicyViolation, "ORIGIN_STATE_UNSUPPORTED"):
            apply_transition(
                developing_story, [developing_claim], self.sources, self.evidence,
                [self.verification], transition, [developing_receipt],
            )

    def test_unknown_evidence_and_stale_state_fail_closed(self):
        with self.assertRaisesRegex(PolicyViolation, "STORY_EVIDENCE_SET_MISMATCH"):
            authorize_transition(
                self.story, [self.claim], self.sources, [self.evidence[0]],
                [self.verification], self.transition,
            )

        ghost_evidence = Evidence.model_validate({
            **self.evidence[0].model_dump(mode="json", by_alias=True),
            "evidenceId": "evidence-ghost",
            "claimId": "claim-ghost",
        })
        ghost_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "evidenceIds": [*self.story.evidence_ids, ghost_evidence.evidence_id],
        })
        with self.assertRaisesRegex(PolicyViolation, "STORY_EVIDENCE_CLAIM_MISMATCH"):
            authorize_transition(
                ghost_story,
                [self.claim],
                self.sources,
                [*self.evidence, ghost_evidence],
                [self.verification],
                self.transition,
            )
        with self.assertRaisesRegex(PolicyViolation, "STORY_EVIDENCE_CLAIM_MISMATCH"):
            apply_transition(
                ghost_story,
                [self.claim],
                self.sources,
                [*self.evidence, ghost_evidence],
                [self.verification],
                self.transition,
                [receipt_with(self.sources, self.evidence)],
            )

    def test_unreachable_current_publication_breaking_lineage_and_claim_clock_fail(self):
        future_claim = Claim.model_validate({
            **self.claim.model_dump(mode="json", by_alias=True),
            "updatedAt": T5,
        })
        with self.assertRaisesRegex(PolicyViolation, "CLAIM_STORY_CLOCK_MISMATCH"):
            authorize_transition(
                self.story, [future_claim], self.sources, self.evidence,
                [self.verification], self.transition,
            )

        published_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "publicationState": "published",
            "liveWirePublishedAt": T2,
        })
        published_transition = EditorialTransition.model_validate({
            **self.transition.model_dump(mode="json", by_alias=True),
            "fromPublicationState": "published",
            "toPublicationState": "published",
        })
        with self.assertRaisesRegex(PolicyViolation, "UNREACHABLE_PUBLICATION_STATE"):
            authorize_transition(
                published_story, [self.claim], self.sources, self.evidence,
                [self.verification], published_transition,
            )

        breaking_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "breaking": True,
            "enteredBreakingAt": T2,
        })
        with self.assertRaisesRegex(PolicyViolation, "UNREACHABLE_BREAKING_STATE"):
            authorize_transition(
                breaking_story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition,
            )

        ghost_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "verificationIds": ["verification-one"],
        })
        with self.assertRaisesRegex(PolicyViolation, "UNREACHABLE_CURRENT_STATE"):
            authorize_transition(
                ghost_story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition,
            )
        stale_story = Story.model_validate({
            **self.story.model_dump(mode="json", by_alias=True),
            "editorialState": "developing",
        })
        with self.assertRaisesRegex(PolicyViolation, "STALE_TRANSITION"):
            authorize_transition(
                stale_story, [self.claim], self.sources, self.evidence,
                [self.verification], self.transition,
            )


class CorrectionProjectionTests(unittest.TestCase):
    @staticmethod
    def transition(
        *,
        transition_id: str = "transition-correct",
        from_state: str = "reported",
        at: str = T4,
    ) -> EditorialTransition:
        return EditorialTransition.model_validate({
            "schemaVersion": SCHEMA_VERSION,
            "objectType": "editorial_transition",
            "transitionId": transition_id,
            "storyId": "story-one",
            "claimIds": ["claim-one"],
            "action": "correct",
            "fromEditorialState": from_state,
            "toEditorialState": "corrected",
            "fromPublicationState": "withheld",
            "toPublicationState": "withheld",
            "evidenceIds": ["evidence-correction"],
            "verificationIds": [],
            "humanReview": reviewer().model_dump(mode="json", by_alias=True),
            "occurredAt": at,
            "reason": "Standards authorized an attributable wording correction.",
            "correctionPath": CORRECTION_PATH,
            "highRisk": False,
        })

    def test_correction_propagates_and_preserves_superseded_text_and_clocks(self):
        original_story = story()
        original_claim = claim()
        original_receipt = receipt()
        transition = self.transition()
        projected_story, projected_claims, projected_receipts = apply_correction_transition(
            original_story,
            [original_claim],
            [source()],
            correction_packet(),
            [],
            transition,
            [correction()],
            [original_receipt],
        )
        projected_claim = projected_claims[0]
        projected_receipt = projected_receipts[0]
        corrected = correction().corrected_text
        self.assertEqual(projected_story.title, corrected)
        self.assertEqual(projected_claim.text, corrected)
        self.assertEqual(projected_receipt.claim_text, corrected)
        self.assertEqual(projected_story.editorial_state.value, "corrected")
        self.assertEqual(projected_receipt.editorial_state.value, "corrected")
        self.assertEqual(projected_receipt.superseded_texts, [original_claim.text])
        self.assertEqual(projected_receipt.corrections[0].correction_id, "correction-one")
        self.assertEqual(projected_receipt.update_history[-1].correction_id, "correction-one")
        self.assertEqual(
            [item.evidence_id for item in projected_receipt.evidence],
            ["evidence-one", "evidence-correction"],
        )
        self.assertEqual(
            [item.evidence_id for item in original_receipt.evidence],
            ["evidence-one"],
            "correction Evidence must be added only by the atomic projection",
        )
        for name in ("source_published_at", "first_seen_at", "live_wire_published_at"):
            self.assertEqual(getattr(projected_story, name), getattr(original_story, name))
            self.assertEqual(getattr(projected_receipt, name), getattr(original_receipt, name))
        self.assertEqual(original_story.editorial_state.value, "reported", "input must remain immutable")

    def test_correction_requires_atomic_api_and_updates_every_story_receipt(self):
        transition = self.transition()
        with self.assertRaisesRegex(PolicyViolation, "USE_ATOMIC_CORRECTION_TRANSITION"):
            apply_transition(
                story(), [claim()], [source()], [correction_evidence()], [], transition, [correction()]
            )

        second_receipt = Receipt.model_validate({
            **receipt().model_dump(mode="json", by_alias=True),
            "receiptId": "receipt-two",
        })
        two_receipt_story = Story.model_validate({
            **story().model_dump(mode="json", by_alias=True),
            "receiptIds": ["receipt-one", "receipt-two"],
        })
        two_receipt_correction = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "receiptIds": ["receipt-one", "receipt-two"],
        })
        _, _, projected_receipts = apply_correction_transition(
            two_receipt_story,
            [claim()],
            [source()],
            correction_packet(),
            [],
            transition,
            [two_receipt_correction],
            [receipt(), second_receipt],
        )
        self.assertEqual(
            {item.receipt_id for item in projected_receipts},
            {"receipt-one", "receipt-two"},
        )
        self.assertTrue(all(item.corrections[-1].correction_id == "correction-one" for item in projected_receipts))

    def test_correction_prior_text_and_receipt_reference_must_match(self):
        transition = self.transition()
        raw = correction().model_dump(mode="json", by_alias=True)
        wrong_prior = Correction.model_validate({**raw, "priorText": "Different prior wording."})
        with self.assertRaisesRegex(PolicyViolation, "CORRECTION_PRIOR_MISMATCH"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], transition,
                [wrong_prior], [receipt()],
            )
        wrong_receipt = Correction.model_validate({**raw, "receiptIds": ["receipt-other"]})
        with self.assertRaisesRegex(PolicyViolation, "RECEIPT_SET_MISMATCH"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], transition,
                [wrong_receipt], [receipt()],
            )

    def test_correction_from_privileged_state_closes_without_prior_replay_ledger(self):
        confirmed_story = Story.model_validate({
            **story().model_dump(mode="json", by_alias=True),
            "editorialState": "confirmed",
            "verificationIds": ["verification-one"],
        })
        confirmed_claim = Claim.model_validate({
            **claim().model_dump(mode="json", by_alias=True),
            "editorialState": "confirmed",
        })
        transition = EditorialTransition.model_validate({
            **self.transition().model_dump(mode="json", by_alias=True),
            "fromEditorialState": "confirmed",
        })
        with self.assertRaisesRegex(PolicyViolation, "PRIOR_TRANSITION_REPLAY_REQUIRED"):
            authorize_transition(
                confirmed_story,
                [confirmed_claim],
                [source()],
                correction_packet(),
                [],
                transition,
                [correction()],
            )
        with self.assertRaisesRegex(PolicyViolation, "PRIOR_TRANSITION_REPLAY_REQUIRED"):
            apply_correction_transition(
                confirmed_story,
                [confirmed_claim],
                [source()],
                correction_packet(),
                [],
                transition,
                [correction()],
                [receipt()],
            )

    def test_correction_requires_exact_authorized_set_and_canonical_receipt_evidence(self):
        transition = self.transition()
        extra_raw = correction().model_dump(mode="json", by_alias=True)
        extra = Correction.model_validate({**extra_raw, "correctionId": "correction-extra"})
        with self.assertRaisesRegex(PolicyViolation, "DUPLICATE_CLAIM_CORRECTION"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], transition,
                [correction(), extra], [receipt()],
            )

        bad_source = Source.model_validate({
            **source().model_dump(mode="json", by_alias=True),
            "url": "https://different.example/forged",
        })
        bad_receipt = Receipt.model_validate({
            **receipt().model_dump(mode="json", by_alias=True),
            "sources": [bad_source.model_dump(mode="json", by_alias=True)],
        })
        with self.assertRaisesRegex(PolicyViolation, "RECEIPT_SOURCE_DIVERGENCE"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], transition,
                [correction()], [bad_receipt],
            )

        bad_clock_receipt = Receipt.model_validate({
            **receipt().model_dump(mode="json", by_alias=True),
            "liveWirePublishedAt": T1,
        })
        with self.assertRaisesRegex(PolicyViolation, "RECEIPT_STORY_CLOCK_MISMATCH"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], transition,
                [correction()], [bad_clock_receipt],
            )

    def test_replacement_needs_support_and_retraction_needs_same_event_refutation(self):
        reused_id_correction = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "evidenceIds": ["evidence-one"],
        })
        reused_id_transition = EditorialTransition.model_validate({
            **self.transition().model_dump(mode="json", by_alias=True),
            "evidenceIds": ["evidence-one"],
        })
        with self.assertRaisesRegex(PolicyViolation, "CORRECTION_EVIDENCE_NOT_NEW"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], reused_id_transition,
                [reused_id_correction], [receipt()],
            )

        premature_review = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "humanReview": reviewer(T1).model_dump(mode="json", by_alias=True),
        })
        with self.assertRaisesRegex(PolicyViolation, "EVIDENCE_AFTER_CORRECTION_REVIEW"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], self.transition(),
                [premature_review], [receipt()],
            )

        context = evidence(
            "evidence-correction",
            relation="context",
            agreement=None,
            target_text=CORRECTED_TEXT,
        )
        with self.assertRaisesRegex(PolicyViolation, "UNSUPPORTED_CORRECTED_TEXT"):
            apply_correction_transition(
                story(), [claim()], [source()], [evidence(), context], [], self.transition(),
                [correction()], [receipt()],
            )

        mismatch = Evidence.model_validate({
            **correction_evidence().model_dump(mode="json", by_alias=True),
            "entityAgreement": False,
        })
        with self.assertRaisesRegex(PolicyViolation, "CORRECTION_EVIDENCE_DIMENSION_MISMATCH"):
            apply_correction_transition(
                story(), [claim()], [source()], [evidence(), mismatch], [], self.transition(),
                [correction()], [receipt()],
            )

        unrelated_replacement = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "correctedText": "The moon is made of green cheese.",
        })
        with self.assertRaisesRegex(PolicyViolation, "CORRECTION_TARGET_MISMATCH"):
            apply_correction_transition(
                story(), [claim()], [source()], correction_packet(), [], self.transition(),
                [unrelated_replacement], [receipt()],
            )

        refuting = Evidence.model_validate({
            **evidence(
                "evidence-retraction", target_text=claim().text
            ).model_dump(mode="json", by_alias=True),
            "relation": "refutes",
            "claimAgreement": False,
        })
        retraction = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "evidenceIds": ["evidence-retraction"],
            "correctionType": "retraction",
            "correctedText": None,
        })
        transition = EditorialTransition.model_validate({
            **self.transition().model_dump(mode="json", by_alias=True),
            "action": "retract",
            "toEditorialState": "retracted",
            "evidenceIds": ["evidence-retraction"],
        })
        projected_story, _, projected_receipts = apply_correction_transition(
            story(), [claim()], [source()], [evidence(), refuting], [], transition,
            [retraction], [receipt()],
        )
        self.assertEqual(projected_story.editorial_state.value, "retracted")
        self.assertEqual(projected_receipts[0].editorial_state.value, "retracted")

    def test_repeat_correction_closes_without_complete_prior_replay(self):
        first_story, first_claims, first_receipts = apply_correction_transition(
            story(), [claim()], [source()], correction_packet(), [], self.transition(),
            [correction()], [receipt()],
        )
        second_text = "A second, attributable correction."
        second_evidence = correction_evidence(second_text, "evidence-correction-two")
        second = Correction.model_validate({
            **correction().model_dump(mode="json", by_alias=True),
            "correctionId": "correction-two",
            "evidenceIds": [second_evidence.evidence_id],
            "priorText": correction().corrected_text,
            "correctedText": second_text,
            "issuedAt": T5,
            "supersedesCorrectionId": "correction-one",
        })
        transition = self.transition(
            transition_id="transition-correct-again", from_state="corrected", at=T5
        )
        transition = EditorialTransition.model_validate({
            **transition.model_dump(mode="json", by_alias=True),
            "evidenceIds": [second_evidence.evidence_id],
        })
        with self.assertRaisesRegex(PolicyViolation, "PRIOR_TRANSITION_REPLAY_REQUIRED"):
            authorize_transition(
                first_story,
                first_claims,
                [source()],
                correction_packet(second_evidence),
                [],
                transition,
                [second],
            )
        with self.assertRaisesRegex(PolicyViolation, "PRIOR_TRANSITION_REPLAY_REQUIRED"):
            apply_correction_transition(
                first_story,
                first_claims,
                [source()],
                correction_packet(second_evidence),
                [],
                transition,
                [second],
                first_receipts,
            )

    def test_raw_correction_projection_is_not_publicly_exported(self):
        import editorial

        self.assertFalse(hasattr(editorial, "propagate_correction"))


if __name__ == "__main__":
    unittest.main()
