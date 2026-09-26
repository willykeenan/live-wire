"""Fail-closed, fixture-backed read model for the Gate 1 product slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from pydantic import ValidationError

from editorial import (
    Claim,
    Correction,
    EditorialState,
    EditorialTransition,
    Evidence,
    PolicyViolation,
    PublicationState,
    Receipt,
    RiskLevel,
    Source,
    Story,
    TransitionAction,
    Verification,
    apply_correction_transition,
)
from editorial.models import SCHEMA_VERSION


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = ROOT / "data" / "gate1" / "preview.json"
EXPECTED_FIXTURE_ID = "livewire-gate1-read-only-preview-v2"
EXPECTED_SOURCE_CAPTURE = "data/gate1/source_wire.json"
EXPECTED_SOURCE_CAPTURE_SHA256 = "09181c5edde7f1b2e9fd718b3d0d449f432e792aadb2d6fd9ba09f848b0bef8a"
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_SOURCE_CAPTURE_BYTES = 4 * 1024 * 1024
MAX_STORIES = 12
ALLOWED_CATEGORIES = {"business", "markets", "politics", "tech", "us", "world"}
RESERVED_CORRECTION_PATHS = {
    "/corrections/2026-07-11-automated-rumor-confirmations/",
}
SURFACE_BOUNDARY = {
    "ownerProduct": "live-wire",
    "readOnly": True,
    "fixtureOnly": True,
    "productionAuthority": False,
}


class FixtureReadError(ValueError):
    """The preview fixture cannot be served without weakening its contract."""


@dataclass(frozen=True)
class StoryBundle:
    category: str
    story: Story
    claims: tuple[Claim, ...]
    sources: tuple[Source, ...]
    evidence: tuple[Evidence, ...]
    verifications: tuple[Verification, ...]
    transitions: tuple[EditorialTransition, ...]
    receipts: tuple[Receipt, ...]
    corrections: tuple[Correction, ...]

    def public_json(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "story": public_story(self),
            "claims": [_dump(item) for item in self.claims],
            "sources": [_dump(item) for item in self.sources],
            "evidence": [_dump(item) for item in self.evidence],
            "verifications": [_dump(item) for item in self.verifications],
            "transitions": [_dump(item) for item in self.transitions],
            "receipts": [_dump(item) for item in self.receipts],
            "corrections": [_dump(item) for item in self.corrections],
            "previewSegments": [preview_segment(self)],
        }


@dataclass(frozen=True)
class FixtureSnapshot:
    fixture_id: str
    generated_at: str
    source_capture: str
    fixture_sha256: str
    source_capture_sha256: str
    bundles: tuple[StoryBundle, ...]

    def public_json(self) -> dict[str, Any]:
        return {
            "snapshot": {
                "fixtureId": self.fixture_id,
                "generatedAt": self.generated_at,
                "sourceCapture": self.source_capture,
                "fixtureSha256": self.fixture_sha256,
                "sourceCaptureSha256": self.source_capture_sha256,
                "fixtureOnly": True,
                "productionAuthority": False,
                "artifactCustody": "user_repository",
                "ownerProduct": "live-wire",
            }
        }


def _dump(model: Any) -> dict[str, Any]:
    return model.model_dump(mode="json", by_alias=True)


def _unique(items: tuple[Any, ...], attr: str, label: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for item in items:
        key = getattr(item, attr)
        if key in result:
            raise FixtureReadError(f"duplicate {label} identifier")
        result[key] = item
    return result


def _exact(actual: set[str], expected: set[str], label: str) -> None:
    if actual != expected:
        raise FixtureReadError(f"{label} references do not resolve exactly")


def _validated_copy(model: Any, **updates: Any) -> Any:
    values = model.model_dump(mode="python")
    values.update(updates)
    return type(model).model_validate(values)


def _same_contract(actual: Any, expected: Any, label: str) -> None:
    if _dump(actual) != _dump(expected):
        raise FixtureReadError(f"{label} diverges from its canonical bundle object")


def _parse_capture_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise FixtureReadError("sourceCapture contains an invalid publication time")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FixtureReadError("sourceCapture contains an invalid publication time") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FixtureReadError("sourceCapture publication time must include a timezone")
    return parsed


def _validate_capture_membership(source_payload: bytes, bundles: tuple[StoryBundle, ...]) -> None:
    try:
        rows = json.loads(source_payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError, RecursionError, MemoryError) as exc:
        raise FixtureReadError("sourceCapture is not valid JSON") from exc
    if not isinstance(rows, list) or not rows:
        raise FixtureReadError("sourceCapture must contain captured source rows")
    captured_sources: set[tuple[str, datetime]] = set()
    captured_claims: set[tuple[str, datetime, str, str]] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise FixtureReadError("sourceCapture row must be an object")
        url = row.get("url")
        headline = row.get("headline")
        category = row.get("category")
        if (
            not isinstance(url, str)
            or not isinstance(headline, str)
            or category not in ALLOWED_CATEGORIES
        ):
            raise FixtureReadError("sourceCapture row lacks URL, headline, or category")
        published_at = _parse_capture_time(row.get("published_at"))
        captured_sources.add((url, published_at))
        captured_claims.add((url, published_at, headline, category))
    for bundle in bundles:
        claim = bundle.claims[0]
        source_keys = {
            (source.url, source.source_published_at) for source in bundle.sources
        }
        if not source_keys.issubset(captured_sources):
            raise FixtureReadError("fixture Source is absent from its declared sourceCapture")
        if not any(
            (*key, claim.text, bundle.category) in captured_claims
            for key in source_keys
        ):
            raise FixtureReadError(
                "fixture Claim wording/category is absent from its declared sourceCapture"
            )


def _parse_bundle(raw: Any) -> StoryBundle:
    if not isinstance(raw, dict):
        raise FixtureReadError("story bundle must be an object")
    allowed = {
        "category", "story", "claims", "sources", "evidence", "verifications",
        "transitions", "receipts", "corrections",
    }
    if set(raw) != allowed:
        raise FixtureReadError("story bundle field set is not closed")
    category = raw.get("category")
    if category not in ALLOWED_CATEGORIES:
        raise FixtureReadError("unsupported story category")
    try:
        bundle = StoryBundle(
            category=category,
            story=Story.model_validate(raw["story"]),
            claims=tuple(Claim.model_validate(item) for item in raw["claims"]),
            sources=tuple(Source.model_validate(item) for item in raw["sources"]),
            evidence=tuple(Evidence.model_validate(item) for item in raw["evidence"]),
            verifications=tuple(Verification.model_validate(item) for item in raw["verifications"]),
            transitions=tuple(EditorialTransition.model_validate(item) for item in raw["transitions"]),
            receipts=tuple(Receipt.model_validate(item) for item in raw["receipts"]),
            corrections=tuple(Correction.model_validate(item) for item in raw["corrections"]),
        )
    except (KeyError, TypeError, ValueError, RecursionError, MemoryError) as exc:
        raise FixtureReadError("one or more editorial contracts are invalid") from exc
    _validate_bundle(bundle)
    return bundle


def _validate_bundle(bundle: StoryBundle) -> None:
    story = bundle.story
    claims = _unique(bundle.claims, "claim_id", "Claim")
    sources = _unique(bundle.sources, "source_id", "Source")
    evidence = _unique(bundle.evidence, "evidence_id", "Evidence")
    verifications = _unique(bundle.verifications, "verification_id", "Verification")
    transitions = _unique(bundle.transitions, "transition_id", "Transition")
    receipts = _unique(bundle.receipts, "receipt_id", "Receipt")
    corrections = _unique(bundle.corrections, "correction_id", "Correction")
    _exact(set(story.claim_ids), set(claims), "Story Claim")
    _exact(set(story.source_ids), set(sources), "Story Source")
    _exact(set(story.evidence_ids), set(evidence), "Story Evidence")
    _exact(set(story.verification_ids), set(verifications), "Story Verification")
    _exact(set(story.transition_ids), set(transitions), "Story Transition")
    _exact(set(story.correction_ids), set(corrections), "Story Correction")
    _exact(set(story.receipt_ids), set(receipts), "Story Receipt")
    _exact(
        set(sources),
        {item.source_id for item in evidence.values()},
        "Story Evidence Source",
    )
    if len(claims) != 1:
        raise FixtureReadError("bounded Gate 1 preview supports exactly one Claim per Story")
    _exact({item.claim_id for item in receipts.values()}, set(claims), "Receipt Claim")
    only_claim = next(iter(claims.values()))
    if (
        only_claim.risk_level is not RiskLevel.HIGH
        or story.high_risk is not True
        or story.title != only_claim.text
        or story.updated_at != only_claim.updated_at
    ):
        raise FixtureReadError("bounded Story, Claim, risk, or clock truth diverges")
    if story.publication_state is not PublicationState.WITHHELD:
        raise FixtureReadError("Gate 1 preview cannot contain published Story state")
    if story.live_wire_published_at is not None or story.entered_breaking_at is not None:
        raise FixtureReadError("Gate 1 preview cannot imply prior publication or breaking state")
    if story.correction_path in RESERVED_CORRECTION_PATHS:
        raise FixtureReadError("fixture correctionPath collides with a reserved static route")
    if story.breaking:
        raise FixtureReadError("Gate 1 preview cannot contain breaking state")
    if story.editorial_state not in {
        EditorialState.REPORTED,
        EditorialState.CORRECTED,
        EditorialState.RETRACTED,
    }:
        raise FixtureReadError("Gate 1 preview contains an unauthorized editorial state")
    derived_high_risk = any(claim.risk_level is RiskLevel.HIGH for claim in claims.values())
    if story.high_risk is not derived_high_risk:
        raise FixtureReadError("Story and Claim risk states diverge")

    for claim in claims.values():
        if claim.story_id != story.story_id:
            raise FixtureReadError("Claim resolves to a different Story")
        if claim.editorial_state is not story.editorial_state:
            raise FixtureReadError("Claim and Story editorial states diverge")
    for item in evidence.values():
        if item.claim_id not in claims or item.source_id not in sources:
            raise FixtureReadError("Evidence references do not resolve")
        if item.captured_at < sources[item.source_id].retrieved_at:
            raise FixtureReadError("Evidence predates retrieval of its Source")
        if item.captured_at > story.updated_at:
            raise FixtureReadError("Evidence was captured after the Story state it supports")
    for item in sources.values():
        if item.retrieved_at > story.updated_at:
            raise FixtureReadError("Source was retrieved after the Story state it supports")
    if any(not item.attributable for item in sources.values()) or any(
        not item.attributable for item in evidence.values()
    ):
        raise FixtureReadError("bounded preview requires attributable Sources and Evidence")
    for item in verifications.values():
        if item.claim_id not in claims or not set(item.evidence_ids).issubset(evidence):
            raise FixtureReadError("Verification references do not resolve")
        if (
            item.verified_at is None
            or item.verified_at > story.updated_at
            or item.human_review is None
            or item.human_review.reviewed_at > story.updated_at
        ):
            raise FixtureReadError("Verification authority occurs after the Story state")
    for item in transitions.values():
        if item.story_id != story.story_id or not set(item.claim_ids).issubset(claims):
            raise FixtureReadError("Transition references do not resolve")
        if not set(item.evidence_ids).issubset(evidence):
            raise FixtureReadError("Transition Evidence references do not resolve")
        if not set(item.verification_ids).issubset(verifications):
            raise FixtureReadError("Transition Verification references do not resolve")
        if item.occurred_at > story.updated_at:
            raise FixtureReadError("Transition occurs after the current Story state")
    for item in receipts.values():
        if item.story_id != story.story_id or item.claim_id not in claims:
            raise FixtureReadError("Receipt references do not resolve")
        if item.editorial_state is not story.editorial_state:
            raise FixtureReadError("Receipt and Story editorial states diverge")
        if item.publication_state is not story.publication_state:
            raise FixtureReadError("Receipt and Story publication states diverge")
        claim = claims[item.claim_id]
        if item.claim_text != claim.text:
            raise FixtureReadError("Receipt and canonical Claim text diverge")
        if item.correction_path != story.correction_path:
            raise FixtureReadError("Receipt and Story correction paths diverge")
        if (
            item.source_published_at != story.source_published_at
            or item.first_seen_at != story.first_seen_at
            or item.live_wire_published_at != story.live_wire_published_at
            or item.updated_at != story.updated_at
        ):
            raise FixtureReadError("Receipt and Story clocks diverge")

        receipt_source_ids = {source.source_id for source in item.sources}
        receipt_evidence_ids = {entry.evidence_id for entry in item.evidence}
        expected_evidence_ids = {
            entry.evidence_id for entry in evidence.values() if entry.claim_id == item.claim_id
        }
        if receipt_evidence_ids != expected_evidence_ids:
            raise FixtureReadError("Receipt does not contain the canonical Claim evidence set")
        if receipt_source_ids != {entry.source_id for entry in item.evidence}:
            raise FixtureReadError("Receipt Source set is not exactly its Evidence Source set")
        for nested in item.sources:
            canonical = sources.get(nested.source_id)
            if canonical is None:
                raise FixtureReadError("Receipt contains an unknown Source")
            _same_contract(nested, canonical, "Receipt Source")
        for nested in item.evidence:
            canonical = evidence.get(nested.evidence_id)
            if canonical is None:
                raise FixtureReadError("Receipt contains unknown Evidence")
            _same_contract(nested, canonical, "Receipt Evidence")

        if item.verification is not None:
            canonical = verifications.get(item.verification.verification_id)
            if canonical is None:
                raise FixtureReadError("Receipt contains an unknown Verification")
            _same_contract(item.verification, canonical, "Receipt Verification")
        expected_corrections = {
            correction.correction_id: correction
            for correction in corrections.values()
            if correction.claim_id == item.claim_id and item.receipt_id in correction.receipt_ids
        }
        if {correction.correction_id for correction in item.corrections} != set(expected_corrections):
            raise FixtureReadError("Receipt correction set diverges from the canonical ledger")
        for nested in item.corrections:
            _same_contract(nested, expected_corrections[nested.correction_id], "Receipt Correction")
        authorized_reviewers = [
            review
            for review in [
                item.verification.human_review if item.verification is not None else None,
                *[correction.human_review for correction in item.corrections],
            ]
            if review is not None
        ]
        if item.reviewer is None and authorized_reviewers:
            raise FixtureReadError("Receipt omits the canonical human reviewer")
        if item.reviewer is not None and not any(item.reviewer == review for review in authorized_reviewers):
            raise FixtureReadError("Receipt reviewer diverges from canonical review authority")
        for update in item.update_history:
            if update.transition_id is not None and update.transition_id not in transitions:
                raise FixtureReadError("Receipt history contains an unknown Transition")
            if update.correction_id is not None and update.correction_id not in corrections:
                raise FixtureReadError("Receipt history contains an unknown Correction")
        latest_update = item.update_history[-1]
        if (
            latest_update.state is not item.editorial_state
            or latest_update.publication_state is not item.publication_state
            or latest_update.at != item.updated_at
        ):
            raise FixtureReadError("Receipt history does not end at its current state and clock")
    for item in corrections.values():
        if item.story_id != story.story_id or item.claim_id not in claims:
            raise FixtureReadError("Correction references do not resolve")
        if not set(item.receipt_ids).issubset(receipts) or not set(item.evidence_ids).issubset(evidence):
            raise FixtureReadError("Correction references do not resolve")
        if item.issued_at > story.updated_at:
            raise FixtureReadError("Correction occurs after the current Story state")

    _validate_bounded_history(story, claims, sources, evidence, verifications, transitions, receipts, corrections)


def _validate_bounded_history(
    story: Story,
    claims: dict[str, Claim],
    sources: dict[str, Source],
    evidence: dict[str, Evidence],
    verifications: dict[str, Verification],
    transitions: dict[str, EditorialTransition],
    receipts: dict[str, Receipt],
    corrections: dict[str, Correction],
) -> None:
    """Replay the only privileged history this bounded preview may expose.

    Gate 1 intentionally has no confirmed/published path. Reported Stories carry
    no transition history. A corrected/retracted fixture must be a one-Claim,
    one-Receipt, one-transition projection that reproduces byte-for-byte from a
    prior reported state through the real policy and correction projector.
    """

    if story.editorial_state is EditorialState.REPORTED:
        if transitions or corrections or verifications:
            raise FixtureReadError("reported preview Story cannot carry privileged history")
        if any(item.target_text != claims[item.claim_id].text for item in evidence.values()):
            raise FixtureReadError("reported preview Evidence must bind the exact current Claim text")
        if any(receipt.verification is not None or receipt.reviewer is not None for receipt in receipts.values()):
            raise FixtureReadError("reported preview cannot imply verification or review")
        for receipt in receipts.values():
            if len(receipt.update_history) != 1:
                raise FixtureReadError("reported preview must have one reported history origin")
            origin = receipt.update_history[0]
            if (
                origin.state is not EditorialState.REPORTED
                or origin.publication_state is not PublicationState.WITHHELD
                or origin.at != receipt.updated_at
                or origin.transition_id is not None
                or origin.correction_id is not None
            ):
                raise FixtureReadError("reported preview contains unauthorized history")
        return
    if verifications or story.verification_ids or any(
        receipt.verification is not None for receipt in receipts.values()
    ):
        raise FixtureReadError(
            "bounded corrected-from-reported preview cannot carry prior Verification authority"
        )
    if len(claims) != 1 or len(receipts) != 1 or len(transitions) != 1 or len(corrections) != 1:
        raise FixtureReadError("corrected preview history exceeds the bounded replay shape")

    claim = next(iter(claims.values()))
    receipt = next(iter(receipts.values()))
    transition = next(iter(transitions.values()))
    correction = next(iter(corrections.values()))
    expected_action = (
        TransitionAction.RETRACT
        if story.editorial_state is EditorialState.RETRACTED
        else TransitionAction.CORRECT
    )
    if transition.action is not expected_action:
        raise FixtureReadError("preview state does not match its privileged transition")
    if transition.from_editorial_state is not EditorialState.REPORTED:
        raise FixtureReadError("bounded correction history must begin at reported")
    if (
        transition.to_editorial_state is not story.editorial_state
        or transition.to_publication_state is not story.publication_state
        or correction.claim_id != claim.claim_id
        or correction.story_id != story.story_id
    ):
        raise FixtureReadError("privileged history does not resolve to the current Story")
    if len(receipt.update_history) != 2:
        raise FixtureReadError("bounded correction Receipt must preserve exactly two history entries")
    first, last = receipt.update_history
    if (
        first.state is not transition.from_editorial_state
        or first.publication_state is not transition.from_publication_state
        or first.transition_id is not None
        or first.correction_id is not None
        or last.transition_id != transition.transition_id
        or last.correction_id != correction.correction_id
    ):
        raise FixtureReadError("Receipt history cannot reconstruct the prior reported state")

    transition_evidence_ids = set(transition.evidence_ids)
    prior_evidence_ids = [
        evidence_id
        for evidence_id in story.evidence_ids
        if evidence_id not in transition_evidence_ids
    ]
    if not prior_evidence_ids:
        raise FixtureReadError("correction replay has no prior reported Evidence")
    if any(evidence[evidence_id].target_text != correction.prior_text for evidence_id in prior_evidence_ids):
        raise FixtureReadError("prior reported Evidence does not bind the superseded Claim text")
    prior_source_ids = list(dict.fromkeys(
        evidence[evidence_id].source_id for evidence_id in prior_evidence_ids
    ))

    prior_title = story.title
    prior_summary = story.summary
    if correction.corrected_text is not None:
        if story.title == correction.corrected_text:
            prior_title = correction.prior_text
        prior_summary = story.summary.replace(correction.corrected_text, correction.prior_text)
    try:
        prior_story = _validated_copy(
            story,
            title=prior_title,
            summary=prior_summary,
            editorial_state=transition.from_editorial_state,
            publication_state=transition.from_publication_state,
            source_ids=prior_source_ids,
            evidence_ids=prior_evidence_ids,
            transition_ids=[item for item in story.transition_ids if item != transition.transition_id],
            correction_ids=[item for item in story.correction_ids if item != correction.correction_id],
            updated_at=first.at,
        )
        prior_claim = _validated_copy(
            claim,
            text=correction.prior_text,
            editorial_state=transition.from_editorial_state,
            correction_ids=[item for item in claim.correction_ids if item != correction.correction_id],
            updated_at=first.at,
        )
        prior_receipt = _validated_copy(
            receipt,
            claim_text=correction.prior_text,
            editorial_state=transition.from_editorial_state,
            publication_state=transition.from_publication_state,
            sources=[sources[source_id] for source_id in prior_source_ids],
            evidence=[evidence[evidence_id] for evidence_id in prior_evidence_ids],
            reviewer=None,
            updated_at=first.at,
            update_history=[first],
            corrections=[],
            superseded_texts=[],
        )
        projected_story, projected_claims, projected_receipts = apply_correction_transition(
            prior_story,
            [prior_claim],
            list(sources.values()),
            list(evidence.values()),
            list(verifications.values()),
            transition,
            [correction],
            [prior_receipt],
        )
    except (PolicyViolation, ValidationError) as exc:
        raise FixtureReadError("privileged preview history failed policy replay") from exc
    _same_contract(projected_story, story, "replayed Story")
    _same_contract(projected_claims[0], claim, "replayed Claim")
    _same_contract(projected_receipts[0], receipt, "replayed Receipt")


def preview_segment(bundle: StoryBundle) -> dict[str, Any]:
    story = bundle.story
    receipt = bundle.receipts[0]
    source = receipt.sources[0]
    retracted = story.editorial_state is EditorialState.RETRACTED
    return {
        "segmentId": f"segment-{story.story_id}",
        "storyId": story.story_id,
        "receiptId": receipt.receipt_id,
        "headline": (
            "Story retracted — open Receipts for the withdrawn wording"
            if retracted
            else receipt.claim_text
        ),
        "summary": (
            "This Story was retracted. Open Receipts for the reason, evidence, and preserved prior wording."
            if retracted
            else "This Story was corrected. Open Receipts for current wording and preserved prior text."
            if bundle.corrections
            else "Attributed source report preserved for inspection. Live Wire has not evaluated this Claim as true."
        ),
        "category": bundle.category,
        "editorialState": story.editorial_state.value,
        "publicationState": story.publication_state.value,
        "sourceName": source.name,
        "sourceUrl": source.url,
        "sourcePublishedAt": source.source_published_at.isoformat().replace("+00:00", "Z"),
        "firstSeenAt": story.first_seen_at.isoformat().replace("+00:00", "Z"),
        "updatedAt": story.updated_at.isoformat().replace("+00:00", "Z"),
        "correctionCount": len(bundle.corrections),
        "breaking": False,
    }


def public_story(bundle: StoryBundle) -> dict[str, Any]:
    """Expose the canonical, hash-reconstructable Story unchanged."""
    return _dump(bundle.story)


def _latest_bundle_time(bundle: StoryBundle) -> datetime:
    times: list[datetime] = [
        bundle.story.source_published_at,
        bundle.story.first_seen_at,
        bundle.story.updated_at,
    ]
    for optional in (
        bundle.story.live_wire_published_at,
        bundle.story.entered_breaking_at,
    ):
        if optional is not None:
            times.append(optional)
    for item in bundle.claims:
        times.extend([item.time_start, item.created_at, item.updated_at])
        if item.time_end is not None:
            times.append(item.time_end)
    for item in bundle.sources:
        times.extend([
            item.source_published_at,
            item.first_seen_at,
            item.retrieved_at,
        ])
    times.extend(item.captured_at for item in bundle.evidence)
    for item in bundle.verifications:
        if item.verified_at is not None:
            times.append(item.verified_at)
        if item.human_review is not None:
            times.append(item.human_review.reviewed_at)
    for item in bundle.transitions:
        times.append(item.occurred_at)
        if item.human_review is not None:
            times.append(item.human_review.reviewed_at)
    for item in bundle.corrections:
        times.extend([item.issued_at, item.human_review.reviewed_at])
    for item in bundle.receipts:
        times.extend([
            item.source_published_at,
            item.first_seen_at,
            item.updated_at,
            *[update.at for update in item.update_history],
        ])
        if item.live_wire_published_at is not None:
            times.append(item.live_wire_published_at)
        if item.reviewer is not None:
            times.append(item.reviewer.reviewed_at)
    return max(times)


class Gate1ReadModel:
    """Loads and validates a tiny immutable fixture anew for every read."""

    def __init__(self, fixture_path: Path = DEFAULT_FIXTURE):
        self.fixture_path = Path(fixture_path)

    def _load(self) -> FixtureSnapshot:
        try:
            if self.fixture_path.stat().st_size > MAX_FIXTURE_BYTES:
                raise FixtureReadError("fixture exceeds byte limit")
            payload = self.fixture_path.read_bytes()
            raw = json.loads(payload.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, MemoryError) as exc:
            raise FixtureReadError("fixture cannot be read") from exc
        if not isinstance(raw, dict):
            raise FixtureReadError("fixture root must be an object")
        required = {
            "schemaVersion", "fixtureId", "fixtureOnly", "productionAuthority",
            "generatedAt", "sourceCapture", "stories",
        }
        if set(raw) != required:
            raise FixtureReadError("fixture root field set is not closed")
        if raw.get("schemaVersion") != SCHEMA_VERSION:
            raise FixtureReadError("fixture schema version mismatch")
        if raw.get("fixtureOnly") is not True or raw.get("productionAuthority") is not False:
            raise FixtureReadError("fixture authority boundary is invalid")
        fixture_id = raw.get("fixtureId")
        if not isinstance(fixture_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._:-]{2,127}", fixture_id):
            raise FixtureReadError("fixtureId is invalid")
        if fixture_id != EXPECTED_FIXTURE_ID:
            raise FixtureReadError("fixtureId is not the governed Gate 1 v2 artifact")
        generated_at = raw.get("generatedAt")
        if not isinstance(generated_at, str):
            raise FixtureReadError("generatedAt is invalid")
        try:
            parsed_generated = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise FixtureReadError("generatedAt is invalid") from exc
        if parsed_generated.tzinfo is None or parsed_generated.utcoffset() is None:
            raise FixtureReadError("generatedAt must include a timezone")
        source_capture = raw.get("sourceCapture")
        if not isinstance(source_capture, str) or not source_capture or Path(source_capture).is_absolute():
            raise FixtureReadError("sourceCapture is invalid")
        if source_capture != EXPECTED_SOURCE_CAPTURE:
            raise FixtureReadError("sourceCapture is not the governed Gate 1 v2 input")
        source_path = (ROOT / source_capture).resolve()
        try:
            source_path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise FixtureReadError("sourceCapture escapes the repository") from exc
        try:
            if source_path.stat().st_size > MAX_SOURCE_CAPTURE_BYTES:
                raise FixtureReadError("sourceCapture exceeds byte limit")
            source_payload = source_path.read_bytes()
        except OSError as exc:
            raise FixtureReadError("sourceCapture cannot be read") from exc
        source_capture_sha256 = hashlib.sha256(source_payload).hexdigest()
        if source_capture_sha256 != EXPECTED_SOURCE_CAPTURE_SHA256:
            raise FixtureReadError("sourceCapture checksum is not the governed frozen input")
        stories = raw.get("stories")
        if not isinstance(stories, list) or not 1 <= len(stories) <= MAX_STORIES:
            raise FixtureReadError("fixture Story count is outside the bound")
        bundles = tuple(_parse_bundle(item) for item in stories)
        ids = [bundle.story.story_id for bundle in bundles]
        if len(ids) != len(set(ids)):
            raise FixtureReadError("duplicate Story identifier")
        global_ids = (
            ("Claim", "claim_id", [item for bundle in bundles for item in bundle.claims]),
            ("Source", "source_id", [item for bundle in bundles for item in bundle.sources]),
            ("Evidence", "evidence_id", [item for bundle in bundles for item in bundle.evidence]),
            ("Verification", "verification_id", [item for bundle in bundles for item in bundle.verifications]),
            ("Transition", "transition_id", [item for bundle in bundles for item in bundle.transitions]),
            ("Receipt", "receipt_id", [item for bundle in bundles for item in bundle.receipts]),
            ("Correction", "correction_id", [item for bundle in bundles for item in bundle.corrections]),
        )
        for label, attribute, items in global_ids:
            identifiers = [getattr(item, attribute) for item in items]
            if len(identifiers) != len(set(identifiers)):
                raise FixtureReadError(f"duplicate snapshot-wide {label} identifier")
        correction_paths = [bundle.story.correction_path for bundle in bundles]
        if len(correction_paths) != len(set(correction_paths)):
            raise FixtureReadError("duplicate correctionPath would make correction routing ambiguous")
        if any(_latest_bundle_time(bundle) > parsed_generated for bundle in bundles):
            raise FixtureReadError("generatedAt predates fixture content")
        _validate_capture_membership(source_payload, bundles)
        return FixtureSnapshot(
            fixture_id=fixture_id,
            generated_at=generated_at,
            source_capture=source_capture,
            fixture_sha256=hashlib.sha256(payload).hexdigest(),
            source_capture_sha256=source_capture_sha256,
            bundles=bundles,
        )

    @staticmethod
    def unavailable() -> dict[str, Any]:
        return {
            "schemaVersion": SCHEMA_VERSION,
            "available": False,
            **SURFACE_BOUNDARY,
            "error": "gate1_fixture_unavailable",
        }

    def story_index(self) -> dict[str, Any]:
        snapshot = self._load()
        bundles = snapshot.bundles
        return {
            "schemaVersion": SCHEMA_VERSION,
            "available": True,
            **SURFACE_BOUNDARY,
            **snapshot.public_json(),
            "count": len(bundles),
            "stories": [public_story(bundle) for bundle in bundles],
            "previews": [preview_segment(bundle) for bundle in bundles],
        }

    def story_detail(self, story_id: str) -> dict[str, Any] | None:
        snapshot = self._load()
        for bundle in snapshot.bundles:
            if bundle.story.story_id == story_id:
                return {
                    "schemaVersion": SCHEMA_VERSION,
                    "available": True,
                    **SURFACE_BOUNDARY,
                    **snapshot.public_json(),
                    **bundle.public_json(),
                }
        return None

    def receipts(self, story_id: str) -> dict[str, Any] | None:
        detail = self.story_detail(story_id)
        if detail is None:
            return None
        return {
            "schemaVersion": SCHEMA_VERSION,
            "available": True,
            **SURFACE_BOUNDARY,
            "snapshot": detail["snapshot"],
            "storyId": story_id,
            "receipts": detail["receipts"],
        }

    def corrections(self) -> dict[str, Any]:
        snapshot = self._load()
        items = [item for bundle in snapshot.bundles for item in bundle.corrections]
        items.sort(key=lambda item: (item.issued_at, item.correction_id), reverse=True)
        return {
            "schemaVersion": SCHEMA_VERSION,
            "available": True,
            **SURFACE_BOUNDARY,
            **snapshot.public_json(),
            "count": len(items),
            "corrections": [_dump(item) for item in items],
        }
