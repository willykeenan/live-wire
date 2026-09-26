"""Cross-object editorial authority and deterministic state projection.

Pydantic proves that individual records are well shaped.  This module proves
that their references and authority agree before any consequential transition.
It is deliberately pure: no I/O, clock reads, environment reads, or generation.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TypeVar
from urllib.parse import urlparse

from pydantic import ValidationError

from .models import (
    Claim,
    Correction,
    CorrectionType,
    EditorialState,
    EditorialTransition,
    Evidence,
    EvidenceRelation,
    HumanReview,
    PublicationState,
    Receipt,
    ReceiptUpdate,
    RiskLevel,
    Source,
    SourceType,
    Story,
    TransitionAction,
    Verification,
    VerificationOutcome,
    CorroborationRule,
)


class PolicyViolation(ValueError):
    """A fail-closed editorial decision with a stable machine-readable code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


T = TypeVar("T")


def _unique_index(items: Iterable[T], attr: str, kind: str) -> dict[str, T]:
    result: dict[str, T] = {}
    for item in items:
        key = getattr(item, attr)
        if key in result:
            raise PolicyViolation("DUPLICATE_ID", f"duplicate {kind} id {key}")
        result[key] = item
    return result


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise PolicyViolation(code, message)


def _same_human(left: HumanReview, right: HumanReview) -> bool:
    return (
        left.reviewer_id == right.reviewer_id
        and left.reviewer_name == right.reviewer_name
        and left.reviewer_role == right.reviewer_role
        and left.human is True
        and right.human is True
    )


def _source_hostname(source: Source) -> str:
    hostname = (urlparse(source.url).hostname or "").casefold().rstrip(".")
    return hostname.removeprefix("www.")


def _transition_shape(transition: EditorialTransition) -> None:
    same_editorial = transition.from_editorial_state is transition.to_editorial_state
    same_publication = transition.from_publication_state is transition.to_publication_state
    action = transition.action

    valid = False
    if action is TransitionAction.REPORT:
        valid = (
            transition.from_editorial_state is EditorialState.DRAFT
            and transition.to_editorial_state is EditorialState.REPORTED
            and same_publication
        )
    elif action is TransitionAction.DEVELOP:
        valid = (
            transition.from_editorial_state is EditorialState.REPORTED
            and transition.to_editorial_state is EditorialState.DEVELOPING
            and same_publication
        )
    elif action is TransitionAction.CONFIRM:
        valid = (
            transition.from_editorial_state in {EditorialState.REPORTED, EditorialState.DEVELOPING}
            and transition.to_editorial_state is EditorialState.CONFIRMED
            and same_publication
        )
    elif action is TransitionAction.CORRECT:
        valid = (
            transition.from_editorial_state
            in {
                EditorialState.REPORTED,
                EditorialState.DEVELOPING,
                EditorialState.CONFIRMED,
                EditorialState.CORRECTED,
            }
            and transition.to_editorial_state is EditorialState.CORRECTED
            and same_publication
        )
    elif action is TransitionAction.RETRACT:
        valid = (
            transition.from_editorial_state
            in {
                EditorialState.REPORTED,
                EditorialState.DEVELOPING,
                EditorialState.CONFIRMED,
                EditorialState.CORRECTED,
            }
            and transition.to_editorial_state is EditorialState.RETRACTED
            and same_publication
        )
    elif action is TransitionAction.PUBLISH:
        valid = (
            same_editorial
            and transition.from_editorial_state not in {EditorialState.DRAFT, EditorialState.RETRACTED}
            and transition.from_publication_state is PublicationState.WITHHELD
            and transition.to_publication_state is PublicationState.PUBLISHED
        )
    elif action is TransitionAction.GLOBAL_PROMOTE:
        valid = (
            same_editorial
            and transition.from_editorial_state not in {EditorialState.DRAFT, EditorialState.RETRACTED}
            and transition.from_publication_state is PublicationState.PUBLISHED
            and transition.to_publication_state is PublicationState.GLOBALLY_PROMOTED
        )
    _require(valid, "INVALID_TRANSITION_SHAPE", f"action {action.value} does not match from/to states")


def _validate_confirmation(
    claim: Claim,
    verification: Verification,
    evidence_by_id: dict[str, Evidence],
    source_by_id: dict[str, Source],
    transition: EditorialTransition,
) -> None:
    _require(
        verification.claim_id == claim.claim_id,
        "VERIFICATION_CLAIM_MISMATCH",
        f"verification {verification.verification_id} does not resolve to {claim.claim_id}",
    )
    _require(
        verification.outcome is VerificationOutcome.SUPPORTED,
        "UNSUPPORTED_CONFIRMATION",
        f"claim {claim.claim_id} lacks a supported verification",
    )
    _require(not verification.lexical_overlap_only, "LEXICAL_ONLY", "lexical overlap cannot confirm")
    _require(
        all(
            value is True
            for value in (
                verification.claim_agreement,
                verification.event_agreement,
                verification.entity_agreement,
                verification.location_agreement,
                verification.time_agreement,
            )
        ),
        "DIMENSION_MISMATCH",
        "confirmation requires claim/event/entity/location/time agreement",
    )
    _require(
        verification.human_review is not None
        and transition.human_review is not None
        and _same_human(verification.human_review, transition.human_review),
        "REVIEWER_MISMATCH",
        "verification and transition require the same named human reviewer",
    )
    _require(
        verification.verified_at is not None
        and verification.verified_at <= transition.occurred_at,
        "FUTURE_VERIFICATION",
        "verification must exist before the transition it authorizes",
    )
    assert verification.human_review is not None
    _require(
        verification.human_review.reviewed_at <= transition.occurred_at,
        "FUTURE_REVIEW",
        "verification review must exist before the transition it authorizes",
    )
    _require(
        verification.correction_path == transition.correction_path,
        "CORRECTION_PATH_MISMATCH",
        "verification and transition correction paths differ",
    )

    verification_evidence: list[Evidence] = []
    for evidence_id in verification.evidence_ids:
        item = evidence_by_id.get(evidence_id)
        _require(item is not None, "UNKNOWN_EVIDENCE", f"unknown evidence {evidence_id}")
        assert item is not None
        _require(item.claim_id == claim.claim_id, "EVIDENCE_CLAIM_MISMATCH", evidence_id)
        _require(
            item.target_text == claim.text,
            "EVIDENCE_TARGET_MISMATCH",
            evidence_id,
        )
        _require(item.attributable, "UNATTRIBUTABLE_EVIDENCE", evidence_id)
        _require(item.relation is EvidenceRelation.SUPPORTS, "NON_SUPPORTING_EVIDENCE", evidence_id)
        _require(
            all(
                value is True
                for value in (
                    item.claim_agreement,
                    item.event_agreement,
                    item.entity_agreement,
                    item.location_agreement,
                    item.time_agreement,
                )
            ),
            "EVIDENCE_DIMENSION_MISMATCH",
            evidence_id,
        )
        _require(evidence_id in transition.evidence_ids, "EVIDENCE_NOT_AUTHORIZED", evidence_id)
        assert verification.human_review is not None
        _require(
            item.captured_at <= verification.human_review.reviewed_at,
            "EVIDENCE_AFTER_VERIFICATION_REVIEW",
            evidence_id,
        )
        verification_evidence.append(item)

    source_ids = {item.source_id for item in verification_evidence}
    sources: list[Source] = []
    for source_id in source_ids:
        source = source_by_id.get(source_id)
        _require(source is not None, "UNKNOWN_SOURCE", f"unknown source {source_id}")
        assert source is not None
        _require(source.attributable, "UNATTRIBUTABLE_SOURCE", source_id)
        assert verification.human_review is not None
        _require(
            source.retrieved_at <= verification.human_review.reviewed_at,
            "SOURCE_AFTER_VERIFICATION_REVIEW",
            source_id,
        )
        sources.append(source)

    if verification.corroboration is CorroborationRule.OFFICIAL_PRIMARY:
        source = source_by_id.get(verification.official_primary_source_id or "")
        _require(source is not None, "UNKNOWN_OFFICIAL_SOURCE", "official source does not resolve")
        assert source is not None
        _require(source.source_id in source_ids, "OFFICIAL_SOURCE_NOT_EVIDENCE", source.source_id)
        _require(
            source.source_type is SourceType.OFFICIAL_PRIMARY,
            "NOT_OFFICIAL_PRIMARY",
            source.source_id,
        )
        _require(
            verification.independent_source_count == len({s.independence_key for s in sources}),
            "FORGED_SOURCE_COUNT",
            "independentSourceCount does not match resolved evidence",
        )
    elif verification.corroboration is CorroborationRule.TWO_INDEPENDENT:
        independent = {source.independence_key for source in sources}
        _require(len(independent) >= 2, "NOT_INDEPENDENT", "fewer than two independence keys")
        hostnames = {_source_hostname(source) for source in sources}
        _require(
            "" not in hostnames and len(hostnames) >= 2,
            "NOT_HOST_DIVERSE",
            "two-independent corroboration also requires distinct normalized source hosts",
        )
        _require(
            verification.independent_source_count == len(independent),
            "FORGED_SOURCE_COUNT",
            "independentSourceCount does not match resolved evidence",
        )
    else:
        raise PolicyViolation("INSUFFICIENT_CORROBORATION", "accepted corroboration rule required")


def _validate_current_verification(
    claim: Claim,
    verification: Verification,
    evidence_by_id: dict[str, Evidence],
    source_by_id: dict[str, Source],
    story: Story,
) -> None:
    """Validate inherited confirmed authority before any later transition."""

    _require(
        verification.claim_id == claim.claim_id
        and verification.outcome is VerificationOutcome.SUPPORTED
        and not verification.lexical_overlap_only
        and verification.human_review is not None
        and verification.verified_at is not None
        and verification.verified_at <= story.updated_at
        and verification.correction_path == story.correction_path,
        "CURRENT_VERIFICATION_MISMATCH",
        verification.verification_id,
    )
    _require(
        all(
            value is True
            for value in (
                verification.claim_agreement,
                verification.event_agreement,
                verification.entity_agreement,
                verification.location_agreement,
                verification.time_agreement,
            )
        ),
        "CURRENT_VERIFICATION_DIMENSION_MISMATCH",
        verification.verification_id,
    )
    assert verification.human_review is not None
    resolved_sources: list[Source] = []
    for evidence_id in verification.evidence_ids:
        item = evidence_by_id.get(evidence_id)
        _require(
            item is not None
            and evidence_id in story.evidence_ids
            and item.claim_id == claim.claim_id
            and item.target_text == claim.text
            and item.attributable
            and item.relation is EvidenceRelation.SUPPORTS
            and all(
                value is True
                for value in (
                    item.claim_agreement,
                    item.event_agreement,
                    item.entity_agreement,
                    item.location_agreement,
                    item.time_agreement,
                )
            )
            and item.captured_at <= verification.human_review.reviewed_at,
            "CURRENT_VERIFICATION_EVIDENCE_MISMATCH",
            evidence_id,
        )
        assert item is not None
        source = source_by_id.get(item.source_id)
        _require(
            source is not None
            and source.attributable
            and source.retrieved_at <= verification.human_review.reviewed_at,
            "CURRENT_VERIFICATION_SOURCE_MISMATCH",
            item.source_id,
        )
        assert source is not None
        resolved_sources.append(source)
    independent = {source.independence_key for source in resolved_sources}
    _require(
        verification.independent_source_count == len(independent),
        "CURRENT_VERIFICATION_SOURCE_COUNT_MISMATCH",
        verification.verification_id,
    )
    if verification.corroboration is CorroborationRule.OFFICIAL_PRIMARY:
        official = source_by_id.get(verification.official_primary_source_id or "")
        _require(
            official is not None
            and official.source_type is SourceType.OFFICIAL_PRIMARY
            and official in resolved_sources,
            "CURRENT_VERIFICATION_CORROBORATION_MISMATCH",
            verification.verification_id,
        )
    else:
        _require(
            verification.corroboration is CorroborationRule.TWO_INDEPENDENT
            and len(independent) >= 2,
            "CURRENT_VERIFICATION_CORROBORATION_MISMATCH",
            verification.verification_id,
        )
        hostnames = {_source_hostname(source) for source in resolved_sources}
        _require(
            "" not in hostnames and len(hostnames) >= 2,
            "CURRENT_VERIFICATION_CORROBORATION_MISMATCH",
            verification.verification_id,
        )


def authorize_transition(
    story: Story,
    claims: Sequence[Claim],
    sources: Sequence[Source],
    evidence: Sequence[Evidence],
    verifications: Sequence[Verification],
    transition: EditorialTransition,
    corrections: Sequence[Correction] = (),
) -> bool:
    """Prove cross-object authority or raise ``PolicyViolation``.

    Counts and labels in a Verification are never trusted without resolving the
    underlying evidence and source independence keys supplied here.
    """

    _transition_shape(transition)
    _require(
        len(transition.reason) <= 500,
        "PROJECTION_INVALID",
        "transition reason exceeds the Receipt history summary limit",
    )
    _require(transition.story_id == story.story_id, "STORY_MISMATCH", transition.transition_id)
    _require(
        transition.from_editorial_state is story.editorial_state
        and transition.from_publication_state is story.publication_state,
        "STALE_TRANSITION",
        "from state does not match current Story",
    )
    _require(transition.occurred_at >= story.updated_at, "BACKDATED_TRANSITION", transition.transition_id)
    _require(transition.correction_path == story.correction_path, "CORRECTION_PATH_MISMATCH", story.story_id)
    if transition.action in {TransitionAction.CORRECT, TransitionAction.RETRACT}:
        if story.editorial_state is EditorialState.REPORTED:
            _require(
                not story.transition_ids and not story.correction_ids,
                "PRIOR_TRANSITION_REPLAY_REQUIRED",
                "reported correction input must be an origin-state snapshot",
            )
        else:
            raise PolicyViolation(
                "PRIOR_TRANSITION_REPLAY_REQUIRED",
                "correction from this state requires a complete prior-transition replay ledger",
            )
    else:
        _require(
            not story.transition_ids,
            "PRIOR_TRANSITION_REPLAY_REQUIRED",
            "Gate 1 authorizes only an origin-state snapshot; chained non-correction history requires a complete replay ledger",
        )
        if transition.action in {
            TransitionAction.REPORT,
            TransitionAction.DEVELOP,
            TransitionAction.CONFIRM,
        }:
            _require(
                story.editorial_state in {EditorialState.DRAFT, EditorialState.REPORTED},
                "ORIGIN_STATE_UNSUPPORTED",
                "Gate 1 origin-snapshot projection supports only draft or reported input",
            )

    claim_by_id = _unique_index(claims, "claim_id", "claim")
    source_by_id = _unique_index(sources, "source_id", "source")
    evidence_by_id = _unique_index(evidence, "evidence_id", "evidence")
    verification_by_id = _unique_index(verifications, "verification_id", "verification")
    correction_by_id = _unique_index(corrections, "correction_id", "correction")
    correction_by_claim: dict[str, Correction] = {}
    for correction in correction_by_id.values():
        _require(
            correction.claim_id not in correction_by_claim,
            "DUPLICATE_CLAIM_CORRECTION",
            correction.claim_id,
        )
        correction_by_claim[correction.claim_id] = correction

    _require(
        set(claim_by_id) == set(story.claim_ids),
        "STORY_CLAIM_SET_MISMATCH",
        "the canonical Claim set must resolve exactly before a Story transition",
    )
    _require(
        set(transition.claim_ids) == set(story.claim_ids),
        "PARTIAL_STORY_TRANSITION",
        "Gate 1 aggregate Story state cannot change for only a Claim subset",
    )
    _require(
        len(story.claim_ids) == 1,
        "BOUNDED_ONE_CLAIM_REQUIRED",
        "Gate 1 projection supports one-Claim Stories until aggregate/Receipt clock semantics are versioned",
    )
    if transition.action not in {TransitionAction.PUBLISH, TransitionAction.GLOBAL_PROMOTE}:
        _require(
            story.publication_state is PublicationState.WITHHELD
            and transition.from_publication_state is PublicationState.WITHHELD
            and transition.to_publication_state is PublicationState.WITHHELD
            and story.live_wire_published_at is None,
            "UNREACHABLE_PUBLICATION_STATE",
            "Gate 1 has no authorized prior publication path",
        )
    _require(
        not story.breaking and story.entered_breaking_at is None,
        "UNREACHABLE_BREAKING_STATE",
        "Gate 1 has no breaking-state transition",
    )
    required_evidence_ids = set(story.evidence_ids) | set(transition.evidence_ids)
    _require(
        required_evidence_ids.issubset(evidence_by_id),
        "STORY_EVIDENCE_SET_MISMATCH",
        "existing and transition Evidence must resolve before projection",
    )
    required_source_ids = set(story.source_ids) | {
        evidence_by_id[evidence_id].source_id for evidence_id in required_evidence_ids
    }
    _require(
        required_source_ids.issubset(source_by_id),
        "STORY_SOURCE_SET_MISMATCH",
        "every existing and transition Evidence Source must resolve",
    )
    for evidence_id in story.evidence_ids:
        _require(
            evidence_by_id[evidence_id].claim_id in claim_by_id,
            "STORY_EVIDENCE_CLAIM_MISMATCH",
            evidence_id,
        )
    _require(
        set(story.source_ids)
        == {evidence_by_id[evidence_id].source_id for evidence_id in story.evidence_ids},
        "STORY_SOURCE_EVIDENCE_SET_MISMATCH",
        "current Story Sources must be exactly those resolved by current Story Evidence",
    )
    _require(
        len(required_source_ids) <= 1_000
        and len(required_evidence_ids) <= 5_000
        and len(story.transition_ids) < 5_000,
        "PROJECTION_CAPACITY_EXCEEDED",
        "projected Story identifier collections exceed contract capacity",
    )
    for evidence_id in story.evidence_ids:
        _require(
            evidence_by_id[evidence_id].captured_at <= story.updated_at,
            "BACKFILLED_STORY_EVIDENCE",
            f"existing evidence {evidence_id} was captured after the current Story state",
        )
    for source_id in story.source_ids:
        source = source_by_id.get(source_id)
        _require(source is not None, "UNKNOWN_SOURCE", source_id)
        assert source is not None
        _require(
            source.retrieved_at <= story.updated_at,
            "BACKFILLED_STORY_SOURCE",
            f"existing source {source_id} was retrieved after the current Story state",
        )
    for evidence_id in required_evidence_ids:
        item = evidence_by_id[evidence_id]
        source = source_by_id[item.source_id]
        _require(
            item.captured_at >= source.retrieved_at,
            "EVIDENCE_PRECEDES_SOURCE_RETRIEVAL",
            evidence_id,
        )

    referenced_claims: list[Claim] = []
    for claim_id in transition.claim_ids:
        claim = claim_by_id.get(claim_id)
        _require(claim is not None, "UNKNOWN_CLAIM", f"unknown claim {claim_id}")
        assert claim is not None
        _require(claim.story_id == story.story_id, "CLAIM_STORY_MISMATCH", claim_id)
        _require(claim_id in story.claim_ids, "CLAIM_NOT_IN_STORY", claim_id)
        _require(claim.editorial_state is transition.from_editorial_state, "CLAIM_STATE_MISMATCH", claim_id)
        _require(
            claim.updated_at == story.updated_at,
            "CLAIM_STORY_CLOCK_MISMATCH",
            claim_id,
        )
        referenced_claims.append(claim)

    if story.editorial_state in {
        EditorialState.DRAFT,
        EditorialState.REPORTED,
        EditorialState.DEVELOPING,
    }:
        _require(
            not story.verification_ids
            and not story.correction_ids
            and all(not claim.correction_ids for claim in referenced_claims),
            "UNREACHABLE_CURRENT_STATE",
            "lower editorial states cannot carry privileged lineage",
        )
    for verification_id in story.verification_ids:
        verification = verification_by_id.get(verification_id)
        _require(
            verification is not None,
            "UNKNOWN_CURRENT_VERIFICATION",
            verification_id,
        )
        assert verification is not None
        current_claim = claim_by_id.get(verification.claim_id)
        _require(current_claim is not None, "CURRENT_VERIFICATION_MISMATCH", verification_id)
        assert current_claim is not None
        _validate_current_verification(
            current_claim, verification, evidence_by_id, source_by_id, story
        )
    if story.editorial_state is EditorialState.CONFIRMED:
        _require(
            not story.correction_ids
            and all(not claim.correction_ids for claim in referenced_claims),
            "UNREACHABLE_CURRENT_STATE",
            "confirmed state cannot already carry correction lineage",
        )
        current_verified_claims = {
            verification_by_id[verification_id].claim_id
            for verification_id in story.verification_ids
        }
        _require(
            current_verified_claims == set(story.claim_ids),
            "CURRENT_VERIFICATION_SET_MISMATCH",
            "confirmed Story requires a resolved Verification for every Claim",
        )

    derived_high_risk = any(
        claim.risk_level is RiskLevel.HIGH for claim in referenced_claims
    )
    _require(
        story.high_risk is derived_high_risk,
        "STORY_RISK_MISMATCH",
        "Story highRisk must equal the resolved canonical Claim risk",
    )
    _require(
        transition.high_risk is derived_high_risk,
        "RISK_FLAG_MISMATCH",
        "transition highRisk must equal resolved Story/Claim risk",
    )

    # Every state change needs attributable Evidence. Reported means an
    # attributable source reported the wording; developing means the evidence
    # changed. Gate 1 has no governed RiskAssessment artifact, so every action
    # requires the named human record; a caller-supplied standard label cannot
    # reduce authority.
    _require(bool(transition.evidence_ids), "MISSING_EVIDENCE", transition.transition_id)
    for evidence_id in transition.evidence_ids:
        item = evidence_by_id.get(evidence_id)
        _require(item is not None, "UNKNOWN_EVIDENCE", evidence_id)
        assert item is not None
        _require(item.attributable, "UNATTRIBUTABLE_EVIDENCE", evidence_id)
        _require(item.claim_id in transition.claim_ids, "EVIDENCE_CLAIM_MISMATCH", evidence_id)
        if transition.action is not TransitionAction.CORRECT:
            target_claim = claim_by_id[item.claim_id]
            _require(
                item.target_text == target_claim.text,
                "EVIDENCE_TARGET_MISMATCH",
                evidence_id,
            )
        source = source_by_id.get(item.source_id)
        _require(source is not None, "UNKNOWN_SOURCE", item.source_id)
        assert source is not None
        _require(source.attributable, "UNATTRIBUTABLE_SOURCE", source.source_id)
        _require(
            item.captured_at <= transition.occurred_at,
            "FUTURE_EVIDENCE",
            f"evidence {evidence_id} was captured after the transition",
        )
        _require(
            source.retrieved_at <= transition.occurred_at,
            "FUTURE_SOURCE",
            f"source {source.source_id} was retrieved after the transition",
        )
    for claim in referenced_claims:
        _require(
            any(evidence_by_id[eid].claim_id == claim.claim_id for eid in transition.evidence_ids),
            "CLAIM_WITHOUT_EVIDENCE",
            claim.claim_id,
        )
    if transition.action is TransitionAction.DEVELOP:
        _require(
            any(evidence_id not in story.evidence_ids for evidence_id in transition.evidence_ids),
            "NO_NEW_EVIDENCE",
            "developing requires evidence not already recorded on the Story",
        )
    _require(transition.human_review is not None, "MISSING_HUMAN_REVIEW", transition.transition_id)
    assert transition.human_review is not None
    _require(
        transition.human_review.reviewed_at <= transition.occurred_at,
        "FUTURE_REVIEW",
        "human review must exist before the transition it authorizes",
    )
    for evidence_id in transition.evidence_ids:
        item = evidence_by_id[evidence_id]
        source = source_by_id[item.source_id]
        _require(
            item.captured_at <= transition.human_review.reviewed_at,
            "EVIDENCE_AFTER_TRANSITION_REVIEW",
            evidence_id,
        )
        _require(
            source.retrieved_at <= transition.human_review.reviewed_at,
            "SOURCE_AFTER_TRANSITION_REVIEW",
            source.source_id,
        )

    if transition.action is TransitionAction.CONFIRM:
        verification_for_claim: dict[str, Verification] = {}
        for verification_id in transition.verification_ids:
            verification = verification_by_id.get(verification_id)
            _require(verification is not None, "UNKNOWN_VERIFICATION", verification_id)
            assert verification is not None
            _require(
                verification.claim_id not in verification_for_claim,
                "DUPLICATE_CLAIM_VERIFICATION",
                verification.claim_id,
            )
            verification_for_claim[verification.claim_id] = verification
        for claim in referenced_claims:
            verification = verification_for_claim.get(claim.claim_id)
            _require(verification is not None, "MISSING_VERIFICATION", claim.claim_id)
            assert verification is not None
            _validate_confirmation(claim, verification, evidence_by_id, source_by_id, transition)
        _require(
            set(verification_for_claim) == set(transition.claim_ids),
            "VERIFICATION_SET_MISMATCH",
            "confirmation requires exactly one Verification per Story Claim",
        )
    else:
        _require(
            not transition.verification_ids,
            "UNEXPECTED_VERIFICATION",
            "only a confirmation transition may add Verification IDs",
        )

    if transition.action in {TransitionAction.CORRECT, TransitionAction.RETRACT}:
        _require(
            len(story.claim_ids) == 1,
            "DERIVED_LINEAGE_REQUIRED",
            "Gate 1 correction projection supports one-Claim Stories only",
        )
        _require(
            set(transition.evidence_ids).isdisjoint(story.evidence_ids),
            "CORRECTION_EVIDENCE_NOT_NEW",
            "correction/retraction Evidence must be newly added by the atomic transition",
        )
        _require(
            set(correction_by_claim) == set(transition.claim_ids),
            "CORRECTION_SET_MISMATCH",
            "correct/retract requires exactly one Correction for every referenced Claim",
        )
        _require(
            set(transition.evidence_ids)
            == {
                evidence_id
                for correction in correction_by_claim.values()
                for evidence_id in correction.evidence_ids
            },
            "CORRECTION_EVIDENCE_SET_MISMATCH",
            "the atomic transition Evidence set must exactly equal its Correction Evidence set",
        )
        for claim in referenced_claims:
            _require(
                list(story.correction_ids) == list(claim.correction_ids),
                "CORRECTION_CHAIN_MISMATCH",
                "Story and Claim correction chains must agree exactly",
            )
            prior_evidence = [
                evidence_by_id[evidence_id]
                for evidence_id in story.evidence_ids
                if evidence_by_id[evidence_id].claim_id == claim.claim_id
                and evidence_by_id[evidence_id].target_text == claim.text
                and evidence_by_id[evidence_id].attributable
                and source_by_id[evidence_by_id[evidence_id].source_id].attributable
            ]
            _require(
                bool(prior_evidence),
                "PRIOR_REPORTED_EVIDENCE_INVALID",
                "correction origin requires attributable Evidence and Source bound to the prior wording",
            )
            correction = correction_by_claim.get(claim.claim_id)
            _require(correction is not None, "MISSING_CORRECTION", claim.claim_id)
            assert correction is not None
            _require(correction.story_id == story.story_id, "CORRECTION_STORY_MISMATCH", claim.claim_id)
            _require(correction.prior_text == claim.text, "CORRECTION_PRIOR_MISMATCH", claim.claim_id)
            _require(
                correction.correction_id not in story.correction_ids
                and correction.correction_id not in claim.correction_ids,
                "DUPLICATE_CORRECTION",
                correction.correction_id,
            )
            expected_superseded = claim.correction_ids[-1] if claim.correction_ids else None
            if expected_superseded is not None:
                _require(
                    bool(story.correction_ids)
                    and story.correction_ids[-1] == expected_superseded,
                    "CORRECTION_CHAIN_MISMATCH",
                    "Story and Claim correction heads must agree",
                )
            _require(
                correction.supersedes_correction_id == expected_superseded,
                "CORRECTION_CHAIN_MISMATCH",
                "Correction must supersede the Claim's latest correction exactly",
            )
            _require(correction.correction_path == story.correction_path, "CORRECTION_PATH_MISMATCH", claim.claim_id)
            _require(
                correction.issued_at == transition.occurred_at,
                "CORRECTION_TRANSITION_CLOCK_MISMATCH",
                "Correction issuedAt must equal the atomic transition occurredAt",
            )
            _require(
                transition.human_review is not None and _same_human(correction.human_review, transition.human_review),
                "REVIEWER_MISMATCH",
                claim.claim_id,
            )
            _require(
                set(correction.evidence_ids).issubset(transition.evidence_ids),
                "CORRECTION_EVIDENCE_NOT_AUTHORIZED",
                claim.claim_id,
            )
            for evidence_id in correction.evidence_ids:
                item = evidence_by_id[evidence_id]
                source = source_by_id[item.source_id]
                _require(
                    item.captured_at <= correction.human_review.reviewed_at,
                    "EVIDENCE_AFTER_CORRECTION_REVIEW",
                    evidence_id,
                )
                _require(
                    source.retrieved_at <= correction.human_review.reviewed_at,
                    "SOURCE_AFTER_CORRECTION_REVIEW",
                    source.source_id,
                )
            if transition.action is TransitionAction.RETRACT:
                _require(correction.correction_type is CorrectionType.RETRACTION, "WRONG_CORRECTION_TYPE", claim.claim_id)
                for evidence_id in correction.evidence_ids:
                    item = evidence_by_id[evidence_id]
                    _require(
                        item.relation is EvidenceRelation.REFUTES,
                        "RETRACTION_REQUIRES_REFUTING_EVIDENCE",
                        evidence_id,
                    )
                    _require(
                        item.target_text == correction.prior_text,
                        "RETRACTION_TARGET_MISMATCH",
                        evidence_id,
                    )
                    _require(
                        item.claim_agreement is False
                        and all(
                            value is True
                            for value in (
                                item.event_agreement,
                                item.entity_agreement,
                                item.location_agreement,
                                item.time_agreement,
                            )
                        ),
                        "RETRACTION_EVIDENCE_DIMENSION_MISMATCH",
                        evidence_id,
                    )
            else:
                _require(correction.correction_type is not CorrectionType.RETRACTION, "WRONG_CORRECTION_TYPE", claim.claim_id)
                _require(
                    correction.corrected_text is not None
                    and len(correction.corrected_text) <= 500,
                    "PROJECTION_INVALID",
                    "correctedText must fit the derived Story title",
                )
                for evidence_id in correction.evidence_ids:
                    item = evidence_by_id[evidence_id]
                    _require(
                        item.relation is EvidenceRelation.SUPPORTS,
                        "UNSUPPORTED_CORRECTED_TEXT",
                        evidence_id,
                    )
                    _require(
                        item.target_text == correction.corrected_text,
                        "CORRECTION_TARGET_MISMATCH",
                        evidence_id,
                    )
                    _require(
                        all(
                            value is True
                            for value in (
                                item.claim_agreement,
                                item.event_agreement,
                                item.entity_agreement,
                                item.location_agreement,
                                item.time_agreement,
                            )
                        ),
                        "CORRECTION_EVIDENCE_DIMENSION_MISMATCH",
                        evidence_id,
                    )
            _require(
                len(correction.reason) <= 500,
                "PROJECTION_INVALID",
                "Correction reason must fit the Receipt history summary",
            )
    else:
        _require(not correction_by_id, "UNEXPECTED_CORRECTION", "non-correction transition received Corrections")

    if transition.action is TransitionAction.GLOBAL_PROMOTE:
        _require(story.live_wire_published_at is not None, "NOT_PUBLISHED", story.story_id)
    if transition.action in {TransitionAction.PUBLISH, TransitionAction.GLOBAL_PROMOTE}:
        raise PolicyViolation(
            "CAPABILITY_CLOSED",
            "Gate 1 has no atomic Story/Receipt/preview/outbox publication projector",
        )

    return True


def _validated_copy(model: T, **updates: object) -> T:
    values = model.model_dump(mode="python")  # type: ignore[attr-defined]
    values.update(updates)
    return type(model).model_validate(values)


def apply_transition(
    story: Story,
    claims: Sequence[Claim],
    sources: Sequence[Source],
    evidence: Sequence[Evidence],
    verifications: Sequence[Verification],
    transition: EditorialTransition,
    receipts: Sequence[Receipt],
    corrections: Sequence[Correction] = (),
) -> tuple[Story, list[Claim], list[Receipt]]:
    """Authorize and atomically project Story, Claims, and every Receipt."""

    _require(
        not story.transition_ids,
        "PRIOR_TRANSITION_REPLAY_REQUIRED",
        "Gate 1 projects only an origin-state snapshot; chained non-correction history requires a complete replay ledger",
    )
    _require(
        transition.action not in {TransitionAction.CORRECT, TransitionAction.RETRACT},
        "USE_ATOMIC_CORRECTION_TRANSITION",
        "correction and retraction require apply_correction_transition",
    )
    authorize_transition(story, claims, sources, evidence, verifications, transition, corrections)
    try:
        projected_story, projected_claims = _project_story_claim_transition(
            story, claims, sources, evidence, transition, corrections
        )
        projected_receipts = _project_transition_receipts(
            story,
            claims,
            sources,
            evidence,
            verifications,
            transition,
            receipts,
        )
    except ValidationError as exc:
        raise PolicyViolation("PROJECTION_INVALID", "authorized transition cannot satisfy its derived contracts") from exc
    return projected_story, projected_claims, projected_receipts


def _project_story_claim_transition(
    story: Story,
    claims: Sequence[Claim],
    sources: Sequence[Source],
    evidence: Sequence[Evidence],
    transition: EditorialTransition,
    corrections: Sequence[Correction] = (),
) -> tuple[Story, list[Claim]]:
    """Project an already-authorized transition across Story and Claims."""

    source_by_id = {item.source_id: item for item in sources}
    evidence_by_id = {item.evidence_id: item for item in evidence}
    transition_source_ids = [
        source_by_id[evidence_by_id[evidence_id].source_id].source_id
        for evidence_id in transition.evidence_ids
    ]
    editorial_state = transition.to_editorial_state
    publication_state = transition.to_publication_state
    correction_ids = list(story.correction_ids)
    verification_ids = list(story.verification_ids)
    correction_by_claim = {item.claim_id: item for item in corrections}
    for item in corrections:
        if item.correction_id not in correction_ids:
            correction_ids.append(item.correction_id)
    for verification_id in transition.verification_ids:
        if verification_id not in verification_ids:
            verification_ids.append(verification_id)

    title = story.title
    summary = story.summary
    if transition.action is TransitionAction.CORRECT:
        _require(
            len(story.claim_ids) == 1,
            "DERIVED_LINEAGE_REQUIRED",
            "Gate 1 can rewrite derived Story text only for a one-Claim Story",
        )
        for correction in corrections:
            if correction.corrected_text is not None:
                title = correction.corrected_text
                summary = summary.replace(correction.prior_text, correction.corrected_text)

    new_story = _validated_copy(
        story,
        title=title,
        summary=summary,
        editorial_state=editorial_state,
        publication_state=publication_state,
        source_ids=list(dict.fromkeys([*story.source_ids, *transition_source_ids])),
        evidence_ids=list(dict.fromkeys([*story.evidence_ids, *transition.evidence_ids])),
        verification_ids=verification_ids,
        transition_ids=[*story.transition_ids, transition.transition_id],
        correction_ids=correction_ids,
        live_wire_published_at=(
            transition.occurred_at
            if transition.action is TransitionAction.PUBLISH and story.live_wire_published_at is None
            else story.live_wire_published_at
        ),
        updated_at=transition.occurred_at,
    )

    projected_claims: list[Claim] = []
    for claim in claims:
        if claim.claim_id not in transition.claim_ids:
            projected_claims.append(claim)
            continue
        correction = correction_by_claim.get(claim.claim_id)
        claim_corrections = list(claim.correction_ids)
        text = claim.text
        if correction is not None:
            claim_corrections.append(correction.correction_id)
            if correction.corrected_text is not None:
                text = correction.corrected_text
        projected_claims.append(
            _validated_copy(
                claim,
                text=text,
                editorial_state=editorial_state,
                updated_at=transition.occurred_at,
                correction_ids=claim_corrections,
            )
        )
    return new_story, projected_claims


def _project_transition_receipts(
    story: Story,
    claims: Sequence[Claim],
    sources: Sequence[Source],
    evidence: Sequence[Evidence],
    verifications: Sequence[Verification],
    transition: EditorialTransition,
    receipts: Sequence[Receipt],
) -> list[Receipt]:
    """Project one authorized report/develop/confirm decision through Receipts."""

    claim_by_id = _unique_index(claims, "claim_id", "claim")
    source_by_id = _unique_index(sources, "source_id", "source")
    evidence_by_id = _unique_index(evidence, "evidence_id", "evidence")
    verification_by_id = _unique_index(verifications, "verification_id", "verification")
    receipt_by_id = _unique_index(receipts, "receipt_id", "receipt")
    _require(
        set(receipt_by_id) == set(story.receipt_ids),
        "STORY_RECEIPT_SET_MISMATCH",
        "the complete canonical Story Receipt set must be supplied",
    )
    _require(
        {receipt.claim_id for receipt in receipts} == set(story.claim_ids),
        "RECEIPT_CLAIM_SET_MISMATCH",
        "every Story Claim requires at least one canonical Receipt",
    )

    projected: list[Receipt] = []
    for receipt in receipts:
        claim = claim_by_id.get(receipt.claim_id)
        _require(claim is not None, "UNKNOWN_CLAIM", receipt.claim_id)
        assert claim is not None
        _require(
            receipt.story_id == story.story_id
            and receipt.claim_text == claim.text
            and receipt.editorial_state is transition.from_editorial_state
            and receipt.publication_state is transition.from_publication_state
            and receipt.updated_at == story.updated_at,
            "STALE_RECEIPT",
            receipt.receipt_id,
        )
        _require(
            receipt.source_published_at == story.source_published_at
            and receipt.first_seen_at == story.first_seen_at
            and receipt.live_wire_published_at == story.live_wire_published_at,
            "RECEIPT_STORY_CLOCK_MISMATCH",
            receipt.receipt_id,
        )
        history_transition_ids = [
            update.transition_id
            for update in receipt.update_history
            if update.transition_id is not None
        ]
        origin = receipt.update_history[0]
        history_head = receipt.update_history[-1]
        _require(
            len(receipt.update_history) == 1 + len(story.transition_ids)
            and origin.transition_id is None
            and origin.correction_id is None
            and origin.state in {EditorialState.DRAFT, EditorialState.REPORTED}
            and origin.publication_state is PublicationState.WITHHELD
            and history_transition_ids == list(story.transition_ids)
            and all(update.correction_id is None for update in receipt.update_history),
            "RECEIPT_HISTORY_MISMATCH",
            receipt.receipt_id,
        )
        _require(
            history_head.state is receipt.editorial_state
            and history_head.publication_state is receipt.publication_state
            and history_head.at == receipt.updated_at
            and all(
                update.publication_state is PublicationState.WITHHELD
                for update in receipt.update_history
            ),
            "RECEIPT_HISTORY_HEAD_MISMATCH",
            receipt.receipt_id,
        )
        _require(
            [item.correction_id for item in receipt.corrections]
            == list(story.correction_ids)
            == list(claim.correction_ids)
            and len(receipt.superseded_texts) == len(receipt.corrections),
            "RECEIPT_CORRECTION_CHAIN_MISMATCH",
            receipt.receipt_id,
        )
        if not story.transition_ids and not story.verification_ids and not story.correction_ids:
            _require(
                receipt.reviewer is None,
                "UNREACHABLE_RECEIPT_REVIEWER",
                receipt.receipt_id,
            )

        current_evidence_ids = {
            evidence_id
            for evidence_id in story.evidence_ids
            if evidence_by_id[evidence_id].claim_id == claim.claim_id
        }
        receipt_evidence_ids = {item.evidence_id for item in receipt.evidence}
        _require(
            receipt_evidence_ids == current_evidence_ids,
            "RECEIPT_EVIDENCE_SET_MISMATCH",
            receipt.receipt_id,
        )
        current_source_ids = {
            evidence_by_id[evidence_id].source_id for evidence_id in current_evidence_ids
        }
        receipt_source_ids = {item.source_id for item in receipt.sources}
        _require(
            receipt_source_ids == current_source_ids,
            "RECEIPT_SOURCE_SET_MISMATCH",
            receipt.receipt_id,
        )
        for nested in receipt.sources:
            _require(
                source_by_id.get(nested.source_id) == nested
                and nested.retrieved_at <= receipt.updated_at,
                "RECEIPT_SOURCE_DIVERGENCE",
                nested.source_id,
            )
        for nested in receipt.evidence:
            _require(
                evidence_by_id.get(nested.evidence_id) == nested
                and nested.captured_at <= receipt.updated_at,
                "RECEIPT_EVIDENCE_DIVERGENCE",
                nested.evidence_id,
            )

        next_evidence_ids = list(dict.fromkeys([
            *[item.evidence_id for item in receipt.evidence],
            *[
                evidence_id
                for evidence_id in transition.evidence_ids
                if evidence_by_id[evidence_id].claim_id == claim.claim_id
            ],
        ]))
        next_source_ids = list(dict.fromkeys(
            evidence_by_id[evidence_id].source_id for evidence_id in next_evidence_ids
        ))
        next_sources = [source_by_id[source_id] for source_id in next_source_ids]
        next_evidence = [evidence_by_id[evidence_id] for evidence_id in next_evidence_ids]
        _require(
            min(item.source_published_at for item in next_sources) == story.source_published_at
            and min(item.first_seen_at for item in next_sources) == story.first_seen_at,
            "CLOCK_REBASE_REQUIRED",
            "new transition Sources would rewrite preserved Story/Receipt clocks",
        )

        next_verification = receipt.verification
        if receipt.verification is None:
            _require(
                not any(
                    verification_by_id[verification_id].claim_id == claim.claim_id
                    for verification_id in story.verification_ids
                ),
                "RECEIPT_VERIFICATION_SET_MISMATCH",
                receipt.receipt_id,
            )
        if receipt.verification is not None:
            canonical = verification_by_id.get(receipt.verification.verification_id)
            _require(
                canonical == receipt.verification
                and receipt.verification.verification_id in story.verification_ids,
                "RECEIPT_VERIFICATION_DIVERGENCE",
                receipt.receipt_id,
            )
        if transition.action is TransitionAction.CONFIRM:
            matching = [
                verification_by_id[verification_id]
                for verification_id in transition.verification_ids
                if verification_by_id[verification_id].claim_id == claim.claim_id
            ]
            _require(
                len(matching) == 1,
                "RECEIPT_VERIFICATION_SET_MISMATCH",
                receipt.receipt_id,
            )
            next_verification = matching[0]

        projected.append(_validated_copy(
            receipt,
            editorial_state=transition.to_editorial_state,
            publication_state=transition.to_publication_state,
            sources=next_sources,
            evidence=next_evidence,
            verification=next_verification,
            reviewer=transition.human_review,
            updated_at=transition.occurred_at,
            update_history=[
                *receipt.update_history,
                ReceiptUpdate(
                    state=transition.to_editorial_state,
                    publicationState=transition.to_publication_state,
                    at=transition.occurred_at,
                    summary=transition.reason,
                    transitionId=transition.transition_id,
                    correctionId=None,
                ),
            ],
        ))
    return projected


def _project_correction(
    story: Story,
    claim: Claim,
    receipt: Receipt,
    correction: Correction,
    transition: EditorialTransition,
    next_sources: Sequence[Source],
    next_evidence: Sequence[Evidence],
) -> tuple[Story, Claim, Receipt]:
    """Project one reviewed correction through Story, Claim, and Receipt views."""

    _require(correction.story_id == story.story_id == claim.story_id == receipt.story_id, "STORY_MISMATCH", correction.correction_id)
    _require(correction.claim_id == claim.claim_id == receipt.claim_id, "CLAIM_MISMATCH", correction.correction_id)
    _require(receipt.receipt_id in correction.receipt_ids, "RECEIPT_MISMATCH", correction.correction_id)
    _require(correction.prior_text == claim.text == receipt.claim_text, "CORRECTION_PRIOR_MISMATCH", correction.correction_id)
    _require(correction.correction_path == story.correction_path == receipt.correction_path, "CORRECTION_PATH_MISMATCH", correction.correction_id)
    _require(correction.issued_at >= story.updated_at, "BACKDATED_CORRECTION", correction.correction_id)
    state = (
        EditorialState.RETRACTED
        if correction.correction_type is CorrectionType.RETRACTION
        else EditorialState.CORRECTED
    )
    corrected_text = correction.corrected_text or claim.text
    title = story.title
    summary = story.summary
    if correction.corrected_text is not None:
        title = correction.corrected_text
        summary = summary.replace(correction.prior_text, correction.corrected_text)

    projected_story = _validated_copy(
        story,
        title=title,
        summary=summary,
        editorial_state=state,
        correction_ids=[*story.correction_ids, correction.correction_id],
        updated_at=correction.issued_at,
    )
    projected_claim = _validated_copy(
        claim,
        text=corrected_text,
        editorial_state=state,
        correction_ids=[*claim.correction_ids, correction.correction_id],
        updated_at=correction.issued_at,
    )
    projected_receipt = _validated_copy(
        receipt,
        claim_text=corrected_text,
        editorial_state=state,
        sources=list(next_sources),
        evidence=list(next_evidence),
        reviewer=correction.human_review,
        updated_at=correction.issued_at,
        update_history=[
            *receipt.update_history,
            ReceiptUpdate(
                state=state,
                publicationState=receipt.publication_state,
                at=correction.issued_at,
                summary=correction.reason,
                transitionId=transition.transition_id,
                correctionId=correction.correction_id,
            ),
        ],
        corrections=[*receipt.corrections, correction],
        superseded_texts=[*receipt.superseded_texts, correction.prior_text],
    )
    return projected_story, projected_claim, projected_receipt


def apply_correction_transition(
    story: Story,
    claims: Sequence[Claim],
    sources: Sequence[Source],
    evidence: Sequence[Evidence],
    verifications: Sequence[Verification],
    transition: EditorialTransition,
    corrections: Sequence[Correction],
    receipts: Sequence[Receipt],
) -> tuple[Story, list[Claim], list[Receipt]]:
    """Authorize and atomically project a correction/retraction across all views.

    The raw projection helper is deliberately private. Callers cannot move a
    public Story, Claim, or Receipt into a corrected state without the exact
    EditorialTransition, canonical Evidence/Sources, human authority, and complete
    Correction/Receipt set passing the same policy boundary.
    """

    if story.editorial_state is EditorialState.REPORTED:
        _require(
            not story.transition_ids and not story.correction_ids,
            "PRIOR_TRANSITION_REPLAY_REQUIRED",
            "reported correction input must be an origin-state snapshot",
        )
    else:
        raise PolicyViolation(
            "PRIOR_TRANSITION_REPLAY_REQUIRED",
            "correction from this state requires a complete prior-transition replay ledger",
        )
    _require(
        transition.action in {TransitionAction.CORRECT, TransitionAction.RETRACT},
        "NOT_CORRECTION_TRANSITION",
        transition.transition_id,
    )
    authorize_transition(story, claims, sources, evidence, verifications, transition, corrections)
    try:
        projected_story, projected_claims = _project_story_claim_transition(
            story, claims, sources, evidence, transition, corrections
        )
    except ValidationError as exc:
        raise PolicyViolation("PROJECTION_INVALID", "correction cannot satisfy its derived contracts") from exc
    claim_by_id = _unique_index(claims, "claim_id", "claim")
    receipt_by_id = _unique_index(receipts, "receipt_id", "receipt")
    source_by_id = _unique_index(sources, "source_id", "source")
    evidence_by_id = _unique_index(evidence, "evidence_id", "evidence")
    _require(
        set(receipt_by_id) == set(story.receipt_ids),
        "STORY_RECEIPT_SET_MISMATCH",
        "the complete canonical Story Receipt set must be supplied",
    )
    expected_receipt_ids = {
        receipt_id for correction in corrections for receipt_id in correction.receipt_ids
    }
    _require(
        set(receipt_by_id) == expected_receipt_ids,
        "RECEIPT_SET_MISMATCH",
        "every corrected Receipt, and no unrelated Receipt, must be projected",
    )
    correction_by_receipt: dict[str, Correction] = {}
    for correction in corrections:
        for receipt_id in correction.receipt_ids:
            _require(
                receipt_id not in correction_by_receipt,
                "DUPLICATE_RECEIPT_CORRECTION",
                receipt_id,
            )
            correction_by_receipt[receipt_id] = correction

    prior_correction_truth: dict[str, Correction] = {}
    prior_correction_transition_ids: dict[str, str] = {}

    projected_claim_by_id = {claim.claim_id: claim for claim in projected_claims}
    projected_receipts: list[Receipt] = []
    for receipt_id, receipt in receipt_by_id.items():
        correction = correction_by_receipt[receipt_id]
        claim = claim_by_id.get(correction.claim_id)
        _require(claim is not None, "UNKNOWN_CLAIM", correction.claim_id)
        assert claim is not None
        _require(receipt.story_id == story.story_id, "STORY_MISMATCH", receipt_id)
        _require(receipt.claim_id == claim.claim_id, "CLAIM_MISMATCH", receipt_id)
        _require(
            receipt.claim_text == claim.text and story.title == claim.text,
            "CORRECTION_CURRENT_TEXT_MISMATCH",
            receipt_id,
        )
        _require(
            receipt.editorial_state is transition.from_editorial_state
            and receipt.publication_state is transition.from_publication_state,
            "STALE_RECEIPT",
            receipt_id,
        )
        _require(
            receipt.updated_at == story.updated_at,
            "RECEIPT_STORY_CLOCK_MISMATCH",
            receipt_id,
        )
        _require(
            receipt.source_published_at == story.source_published_at
            and receipt.first_seen_at == story.first_seen_at
            and receipt.live_wire_published_at == story.live_wire_published_at,
            "RECEIPT_STORY_CLOCK_MISMATCH",
            receipt_id,
        )
        receipt_chain = [item.correction_id for item in receipt.corrections]
        _require(
            receipt_chain == list(claim.correction_ids),
            "RECEIPT_CORRECTION_CHAIN_MISMATCH",
            receipt_id,
        )
        _require(
            len(receipt.superseded_texts) == len(receipt.corrections),
            "RECEIPT_CORRECTION_CHAIN_MISMATCH",
            receipt_id,
        )
        prior_head = None
        history_correction_ids = [
            item.correction_id
            for item in receipt.update_history
            if item.correction_id is not None
        ]
        first_correction_index = next(
            (
                index
                for index, item in enumerate(receipt.update_history)
                if item.correction_id is not None
            ),
            len(receipt.update_history),
        )
        origin = receipt.update_history[0]
        history_transition_ids = [
            item.transition_id for item in receipt.update_history[1:]
        ]
        _require(
            len(receipt.update_history) == 1 + len(story.transition_ids)
            and history_correction_ids == receipt_chain
            and origin.state in {EditorialState.DRAFT, EditorialState.REPORTED}
            and origin.publication_state is PublicationState.WITHHELD
            and origin.transition_id is None
            and origin.correction_id is None
            and history_transition_ids == list(story.transition_ids)
            and all(item is not None for item in history_transition_ids)
            and all(
                item.correction_id is None
                for item in receipt.update_history[:first_correction_index]
            )
            and all(
                item.correction_id is not None
                for item in receipt.update_history[first_correction_index:]
            ),
            "RECEIPT_CORRECTION_HISTORY_MISMATCH",
            receipt_id,
        )
        correction_updates = {
            item.correction_id: item
            for item in receipt.update_history
            if item.correction_id is not None
        }
        _require(
            len(correction_updates) == len(receipt_chain)
            and list(correction_updates) == receipt_chain,
            "RECEIPT_CORRECTION_HISTORY_MISMATCH",
            receipt_id,
        )
        transition_ids = [
            item.transition_id for item in receipt.update_history if item.transition_id is not None
        ]
        _require(
            len(transition_ids) == len(set(transition_ids)),
            "RECEIPT_HISTORY_TRANSITION_REUSE",
            receipt_id,
        )
        for index, prior_correction in enumerate(receipt.corrections):
            known = prior_correction_truth.get(prior_correction.correction_id)
            _require(
                known is None or known == prior_correction,
                "RECEIPT_CORRECTION_OBJECT_DIVERGENCE",
                prior_correction.correction_id,
            )
            prior_correction_truth[prior_correction.correction_id] = prior_correction
            _require(
                prior_correction.supersedes_correction_id == prior_head,
                "RECEIPT_CORRECTION_CHAIN_MISMATCH",
                prior_correction.correction_id,
            )
            _require(
                receipt.superseded_texts[index] == prior_correction.prior_text,
                "RECEIPT_CORRECTION_CHAIN_MISMATCH",
                prior_correction.correction_id,
            )
            update = correction_updates[prior_correction.correction_id]
            expected_state = (
                EditorialState.RETRACTED
                if prior_correction.correction_type is CorrectionType.RETRACTION
                else EditorialState.CORRECTED
            )
            _require(
                update.state is expected_state
                and update.at == prior_correction.issued_at
                and update.summary == prior_correction.reason
                and update.transition_id is not None,
                "RECEIPT_CORRECTION_HISTORY_MISMATCH",
                prior_correction.correction_id,
            )
            known_transition_id = prior_correction_transition_ids.get(
                prior_correction.correction_id
            )
            _require(
                known_transition_id is None or known_transition_id == update.transition_id,
                "RECEIPT_CORRECTION_HISTORY_DIVERGENCE",
                prior_correction.correction_id,
            )
            assert update.transition_id is not None
            prior_correction_transition_ids[prior_correction.correction_id] = update.transition_id
            prior_head = prior_correction.correction_id
        if receipt.corrections:
            latest_text = (
                receipt.corrections[-1].corrected_text
                or receipt.corrections[-1].prior_text
            )
            _require(
                claim.text == receipt.claim_text == latest_text,
                "CORRECTION_CURRENT_TEXT_MISMATCH",
                receipt_id,
            )
            _require(
                receipt.reviewer == receipt.corrections[-1].human_review,
                "RECEIPT_REVIEWER_MISMATCH",
                receipt_id,
            )
        expected_verifications = [
            verification_by_id[verification_id]
            for verification_id in story.verification_ids
            if verification_by_id[verification_id].claim_id == claim.claim_id
        ]
        if expected_verifications:
            _require(
                len(expected_verifications) == 1
                and receipt.verification == expected_verifications[0]
                and (
                    bool(receipt.corrections)
                    or receipt.reviewer == expected_verifications[0].human_review
                ),
                "RECEIPT_VERIFICATION_DIVERGENCE",
                receipt_id,
            )
        else:
            _require(
                receipt.verification is None,
                "RECEIPT_VERIFICATION_DIVERGENCE",
                receipt_id,
            )
        history_head = receipt.update_history[-1]
        _require(
            history_head.state is receipt.editorial_state
            and history_head.publication_state is receipt.publication_state
            and history_head.at == receipt.updated_at
            and history_head.correction_id == prior_head,
            "RECEIPT_HISTORY_HEAD_MISMATCH",
            receipt_id,
        )
        expected_evidence_ids = {
            evidence_id
            for evidence_id in story.evidence_ids
            if evidence_by_id[evidence_id].claim_id == claim.claim_id
        }
        receipt_evidence_ids = {item.evidence_id for item in receipt.evidence}
        _require(
            receipt_evidence_ids == expected_evidence_ids,
            "RECEIPT_EVIDENCE_SET_MISMATCH",
            receipt_id,
        )
        expected_source_ids = {
            evidence_by_id[evidence_id].source_id for evidence_id in expected_evidence_ids
        }
        receipt_source_ids = {item.source_id for item in receipt.sources}
        _require(
            receipt_source_ids == expected_source_ids,
            "RECEIPT_SOURCE_SET_MISMATCH",
            receipt_id,
        )
        for nested in receipt.sources:
            canonical = source_by_id.get(nested.source_id)
            _require(canonical is not None, "UNKNOWN_SOURCE", nested.source_id)
            assert canonical is not None
            _require(nested == canonical, "RECEIPT_SOURCE_DIVERGENCE", nested.source_id)
            _require(
                nested.retrieved_at <= receipt.updated_at,
                "FUTURE_RECEIPT_SOURCE",
                nested.source_id,
            )
        for nested in receipt.evidence:
            canonical = evidence_by_id.get(nested.evidence_id)
            _require(canonical is not None, "UNKNOWN_EVIDENCE", nested.evidence_id)
            assert canonical is not None
            _require(nested == canonical, "RECEIPT_EVIDENCE_DIVERGENCE", nested.evidence_id)
            _require(
                nested.captured_at <= receipt.updated_at,
                "FUTURE_RECEIPT_EVIDENCE",
                nested.evidence_id,
            )
        next_evidence_ids = list(dict.fromkeys([
            *[item.evidence_id for item in receipt.evidence],
            *[
                evidence_id
                for evidence_id in transition.evidence_ids
                if evidence_by_id[evidence_id].claim_id == claim.claim_id
            ],
        ]))
        next_source_ids = list(dict.fromkeys(
            evidence_by_id[evidence_id].source_id for evidence_id in next_evidence_ids
        ))
        next_sources = [source_by_id[source_id] for source_id in next_source_ids]
        next_evidence = [evidence_by_id[evidence_id] for evidence_id in next_evidence_ids]
        _require(
            min(item.source_published_at for item in next_sources) == story.source_published_at
            and min(item.first_seen_at for item in next_sources) == story.first_seen_at,
            "CLOCK_REBASE_REQUIRED",
            "new correction Sources would rewrite preserved Story/Receipt clocks",
        )
        try:
            _, projected_claim, projected_receipt = _project_correction(
                story,
                claim,
                receipt,
                correction,
                transition,
                next_sources,
                next_evidence,
            )
        except ValidationError as exc:
            raise PolicyViolation("PROJECTION_INVALID", "correction cannot satisfy its Receipt contract") from exc
        _require(
            projected_claim == projected_claim_by_id[claim.claim_id],
            "CORRECTION_PROJECTION_DIVERGENCE",
            claim.claim_id,
        )
        projected_receipts.append(projected_receipt)
    return projected_story, projected_claims, projected_receipts
