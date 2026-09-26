"""Strict, versioned editorial contracts for the Live Wire Gate 1 candidate.

These models are deliberately deterministic and side-effect free.  They parse
and validate records; they do not fetch sources, invoke a model, publish, or
grant newsroom authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import ipaddress
import json
import re
from typing import Annotated, Any, Literal, Optional
from urllib.parse import urlparse

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeInt,
    StrictBool,
    StrictStr,
    StringConstraints,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "livewire.editorial.v2"


def _require_utc(value: datetime) -> datetime:
    """Reject ambiguous/local timestamps and normalize a real instant to UTC."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return value.astimezone(timezone.utc)


def _safe_correction_path(value: str) -> str:
    """Allow only a stable, local public correction route."""
    if not value.startswith("/corrections/") or not value.endswith("/"):
        raise ValueError("correctionPath must start with /corrections/")
    if any(token in value for token in ("%", "?", "#", "\\", "//")):
        raise ValueError("correctionPath contains an unsafe path component")
    segments = value.strip("/").split("/")
    if (
        len(segments) < 2
        or segments[0] != "corrections"
        or any(
            segment in {".", ".."}
            or re.fullmatch(r"[a-z0-9][a-z0-9._~-]*", segment) is None
            for segment in segments
        )
    ):
        raise ValueError("correctionPath must be a lowercase local URL path")
    return value


ContractId = Annotated[
    StrictStr,
    StringConstraints(
        min_length=3,
        max_length=128,
        pattern=r"^[a-z0-9][a-z0-9._:-]*$",
    ),
]
NonEmptyText = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=20_000),
]
ShortText = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
ReasonText = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=12, max_length=4_000),
]
Slug = Annotated[
    StrictStr,
    StringConstraints(min_length=3, max_length=160, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]
HttpUrlText = Annotated[
    StrictStr,
    StringConstraints(min_length=10, max_length=2_048, pattern=r"^https?://[^\s]+$"),
]
CorrectionPath = Annotated[StrictStr, AfterValidator(_safe_correction_path)]
UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]


class EditorialState(str, Enum):
    DRAFT = "draft"
    REPORTED = "reported"
    DEVELOPING = "developing"
    CONFIRMED = "confirmed"
    CORRECTED = "corrected"
    RETRACTED = "retracted"


class PublicationState(str, Enum):
    WITHHELD = "withheld"
    PUBLISHED = "published"
    GLOBALLY_PROMOTED = "globally_promoted"


class RiskLevel(str, Enum):
    STANDARD = "standard"
    HIGH = "high"


class SourceType(str, Enum):
    OFFICIAL_PRIMARY = "official_primary"
    PRIMARY = "primary"
    NEWSWIRE = "newswire"
    PUBLISHER = "publisher"
    DOCUMENT = "document"
    EYEWITNESS = "eyewitness"
    SOCIAL = "social"
    OTHER = "other"


class EvidenceRelation(str, Enum):
    SUPPORTS = "supports"
    REFUTES = "refutes"
    CONTEXT = "context"


class VerificationOutcome(str, Enum):
    UNVERIFIED = "unverified"
    INCONCLUSIVE = "inconclusive"
    SUPPORTED = "supported"
    REFUTED = "refuted"


class CorroborationRule(str, Enum):
    INSUFFICIENT = "insufficient"
    OFFICIAL_PRIMARY = "official_primary"
    TWO_INDEPENDENT = "two_independent"


class TransitionAction(str, Enum):
    REPORT = "report"
    DEVELOP = "develop"
    CONFIRM = "confirm"
    CORRECT = "correct"
    RETRACT = "retract"
    PUBLISH = "publish"
    GLOBAL_PROMOTE = "global_promote"


class CorrectionType(str, Enum):
    CORRECTION = "correction"
    CLARIFICATION = "clarification"
    RETRACTION = "retraction"


class ContractModel(BaseModel):
    """Base for immutable contract values with a closed field set."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        populate_by_name=True,
        str_strip_whitespace=True,
        validate_default=True,
    )

    def canonical_json(self) -> str:
        """Return stable UTF-8 JSON suitable for hashing and evidence bundles."""
        return json.dumps(
            self.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )


class HumanReview(ContractModel):
    reviewer_id: ContractId = Field(alias="reviewerId")
    reviewer_name: ShortText = Field(alias="reviewerName")
    reviewer_role: ShortText = Field(alias="reviewerRole")
    human: Literal[True]
    reviewed_at: UtcDatetime = Field(alias="reviewedAt")
    reason: ReasonText

    @field_validator("reviewer_name")
    @classmethod
    def reviewer_must_be_named_human(cls, value: str) -> str:
        normalized = " ".join(value.casefold().split())
        generic_service_labels = {
            "ai system",
            "anonymous editor",
            "automation service",
            "model reviewer",
            "standards bot",
            "unknown reviewer",
        }
        if normalized in generic_service_labels:
            raise ValueError("reviewerName cannot be a generic service label")
        return value


class Source(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["source"] = Field(alias="objectType")
    source_id: ContractId = Field(alias="sourceId")
    name: ShortText
    url: HttpUrlText
    source_type: SourceType = Field(alias="sourceType")
    independence_key: ContractId = Field(alias="independenceKey")
    attributable: StrictBool
    source_published_at: UtcDatetime = Field(alias="sourcePublishedAt")
    first_seen_at: UtcDatetime = Field(alias="firstSeenAt")
    retrieved_at: UtcDatetime = Field(alias="retrievedAt")

    @field_validator("url")
    @classmethod
    def url_has_a_real_public_host(cls, value: str) -> str:
        parsed = urlparse(value)
        hostname = (parsed.hostname or "").casefold().rstrip(".")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("source URL has an invalid port") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("source URL requires a real host and cannot contain credentials")
        try:
            ipaddress.ip_address(hostname)
        except ValueError:
            labels = hostname.split(".")
            if any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                for label in labels
            ):
                raise ValueError("source URL hostname is invalid")
        return value

    @model_validator(mode="after")
    def timestamps_follow_observation_order(self) -> "Source":
        if self.first_seen_at < self.source_published_at:
            raise ValueError("firstSeenAt cannot predate sourcePublishedAt")
        if self.retrieved_at < self.first_seen_at:
            raise ValueError("retrievedAt cannot predate firstSeenAt")
        return self


class Claim(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["claim"] = Field(alias="objectType")
    claim_id: ContractId = Field(alias="claimId")
    story_id: ContractId = Field(alias="storyId")
    text: NonEmptyText
    editorial_state: EditorialState = Field(alias="editorialState")
    risk_level: RiskLevel = Field(alias="riskLevel")
    event_key: ShortText = Field(alias="eventKey")
    entity_keys: list[ContractId] = Field(alias="entityKeys", min_length=1, max_length=100)
    location_key: ShortText = Field(alias="locationKey")
    time_start: UtcDatetime = Field(alias="timeStart")
    time_end: Optional[UtcDatetime] = Field(alias="timeEnd")
    created_at: UtcDatetime = Field(alias="createdAt")
    updated_at: UtcDatetime = Field(alias="updatedAt")
    supersedes_claim_id: Optional[ContractId] = Field(alias="supersedesClaimId")
    correction_ids: list[ContractId] = Field(alias="correctionIds", max_length=1_000)

    @field_validator("entity_keys", "correction_ids")
    @classmethod
    def ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("identifier lists cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def claim_time_is_coherent(self) -> "Claim":
        if self.time_end is not None and self.time_end < self.time_start:
            raise ValueError("timeEnd cannot predate timeStart")
        if self.updated_at < self.created_at:
            raise ValueError("updatedAt cannot predate createdAt")
        if self.supersedes_claim_id == self.claim_id:
            raise ValueError("a claim cannot supersede itself")
        return self


class Evidence(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["evidence"] = Field(alias="objectType")
    evidence_id: ContractId = Field(alias="evidenceId")
    claim_id: ContractId = Field(alias="claimId")
    source_id: ContractId = Field(alias="sourceId")
    target_text: NonEmptyText = Field(alias="targetText")
    relation: EvidenceRelation
    description: NonEmptyText
    locator: NonEmptyText
    captured_at: UtcDatetime = Field(alias="capturedAt")
    attributable: StrictBool
    claim_agreement: Optional[StrictBool] = Field(alias="claimAgreement")
    event_agreement: Optional[StrictBool] = Field(alias="eventAgreement")
    entity_agreement: Optional[StrictBool] = Field(alias="entityAgreement")
    location_agreement: Optional[StrictBool] = Field(alias="locationAgreement")
    time_agreement: Optional[StrictBool] = Field(alias="timeAgreement")


class Verification(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["verification"] = Field(alias="objectType")
    verification_id: ContractId = Field(alias="verificationId")
    claim_id: ContractId = Field(alias="claimId")
    outcome: VerificationOutcome
    evidence_ids: list[ContractId] = Field(alias="evidenceIds", min_length=1, max_length=1_000)
    claim_agreement: Optional[StrictBool] = Field(alias="claimAgreement")
    event_agreement: Optional[StrictBool] = Field(alias="eventAgreement")
    entity_agreement: Optional[StrictBool] = Field(alias="entityAgreement")
    location_agreement: Optional[StrictBool] = Field(alias="locationAgreement")
    time_agreement: Optional[StrictBool] = Field(alias="timeAgreement")
    corroboration: CorroborationRule
    independent_source_count: NonNegativeInt = Field(alias="independentSourceCount")
    official_primary_source_id: Optional[ContractId] = Field(alias="officialPrimarySourceId")
    lexical_overlap_only: StrictBool = Field(alias="lexicalOverlapOnly")
    human_review: Optional[HumanReview] = Field(alias="humanReview")
    verified_at: Optional[UtcDatetime] = Field(alias="verifiedAt")
    reason: Optional[ReasonText]
    correction_path: Optional[CorrectionPath] = Field(alias="correctionPath")

    @field_validator("evidence_ids")
    @classmethod
    def evidence_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidenceIds cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def supported_verification_is_complete(self) -> "Verification":
        if self.corroboration is CorroborationRule.OFFICIAL_PRIMARY:
            if self.official_primary_source_id is None:
                raise ValueError("officialPrimarySourceId is required for official-primary corroboration")
        elif self.official_primary_source_id is not None:
            raise ValueError("officialPrimarySourceId is valid only for official-primary corroboration")
        if self.corroboration is CorroborationRule.TWO_INDEPENDENT:
            if self.independent_source_count < 2:
                raise ValueError("two-independent corroboration requires at least two sources")

        if self.outcome is VerificationOutcome.SUPPORTED:
            if self.lexical_overlap_only:
                raise ValueError("lexical overlap alone can never support a claim")
            agreements = (
                self.claim_agreement,
                self.event_agreement,
                self.entity_agreement,
                self.location_agreement,
                self.time_agreement,
            )
            if any(value is not True for value in agreements):
                raise ValueError("supported outcome requires claim/event/entity/location/time agreement")
            if self.corroboration is CorroborationRule.INSUFFICIENT:
                raise ValueError("supported outcome requires an accepted corroboration rule")
            if any(
                value is None
                for value in (
                    self.human_review,
                    self.verified_at,
                    self.reason,
                    self.correction_path,
                )
            ):
                raise ValueError("supported outcome requires human review, time, reason, and correction path")
            if self.human_review is not None and self.verified_at is not None:
                if self.human_review.reviewed_at > self.verified_at:
                    raise ValueError("verifiedAt cannot predate its human review")
        return self


class Story(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["story"] = Field(alias="objectType")
    story_id: ContractId = Field(alias="storyId")
    slug: Slug
    title: ShortText
    summary: NonEmptyText
    editorial_state: EditorialState = Field(alias="editorialState")
    publication_state: PublicationState = Field(alias="publicationState")
    high_risk: StrictBool = Field(alias="highRisk")
    breaking: StrictBool
    claim_ids: list[ContractId] = Field(alias="claimIds", min_length=1, max_length=1_000)
    source_ids: list[ContractId] = Field(alias="sourceIds", min_length=1, max_length=1_000)
    evidence_ids: list[ContractId] = Field(alias="evidenceIds", min_length=1, max_length=5_000)
    verification_ids: list[ContractId] = Field(alias="verificationIds", max_length=1_000)
    transition_ids: list[ContractId] = Field(alias="transitionIds", max_length=5_000)
    correction_ids: list[ContractId] = Field(alias="correctionIds", max_length=1_000)
    receipt_ids: list[ContractId] = Field(alias="receiptIds", min_length=1, max_length=1_000)
    source_published_at: UtcDatetime = Field(alias="sourcePublishedAt")
    first_seen_at: UtcDatetime = Field(alias="firstSeenAt")
    live_wire_published_at: Optional[UtcDatetime] = Field(alias="liveWirePublishedAt")
    updated_at: UtcDatetime = Field(alias="updatedAt")
    entered_breaking_at: Optional[UtcDatetime] = Field(alias="enteredBreakingAt")
    correction_path: CorrectionPath = Field(alias="correctionPath")

    @field_validator(
        "claim_ids",
        "source_ids",
        "evidence_ids",
        "verification_ids",
        "transition_ids",
        "correction_ids",
        "receipt_ids",
    )
    @classmethod
    def aggregate_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("aggregate identifier lists cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def story_timestamps_are_distinct_fields_with_order(self) -> "Story":
        if self.first_seen_at < self.source_published_at:
            raise ValueError("firstSeenAt cannot predate sourcePublishedAt")
        if self.updated_at < self.first_seen_at:
            raise ValueError("updatedAt cannot predate firstSeenAt")
        if self.live_wire_published_at is not None:
            if self.live_wire_published_at < self.first_seen_at:
                raise ValueError("liveWirePublishedAt cannot predate firstSeenAt")
            if self.updated_at < self.live_wire_published_at:
                raise ValueError("updatedAt cannot predate liveWirePublishedAt")
        if self.publication_state is not PublicationState.WITHHELD:
            if self.live_wire_published_at is None:
                raise ValueError("published stories require liveWirePublishedAt")
        if self.entered_breaking_at is not None:
            if self.entered_breaking_at < self.first_seen_at:
                raise ValueError("enteredBreakingAt cannot predate firstSeenAt")
            if self.updated_at < self.entered_breaking_at:
                raise ValueError("updatedAt cannot predate enteredBreakingAt")
        if self.breaking and self.entered_breaking_at is None:
            raise ValueError("breaking stories require enteredBreakingAt")
        if self.editorial_state is EditorialState.CONFIRMED and not self.verification_ids:
            raise ValueError("confirmed stories require verificationIds")
        if self.editorial_state in {EditorialState.CORRECTED, EditorialState.RETRACTED}:
            if not self.correction_ids:
                raise ValueError("corrected and retracted stories require correctionIds")
        return self


class EditorialTransition(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["editorial_transition"] = Field(alias="objectType")
    transition_id: ContractId = Field(alias="transitionId")
    story_id: ContractId = Field(alias="storyId")
    claim_ids: list[ContractId] = Field(alias="claimIds", min_length=1, max_length=1_000)
    action: TransitionAction
    from_editorial_state: EditorialState = Field(alias="fromEditorialState")
    to_editorial_state: EditorialState = Field(alias="toEditorialState")
    from_publication_state: PublicationState = Field(alias="fromPublicationState")
    to_publication_state: PublicationState = Field(alias="toPublicationState")
    evidence_ids: list[ContractId] = Field(alias="evidenceIds", max_length=5_000)
    verification_ids: list[ContractId] = Field(alias="verificationIds", max_length=1_000)
    human_review: Optional[HumanReview] = Field(alias="humanReview")
    occurred_at: UtcDatetime = Field(alias="occurredAt")
    reason: ReasonText
    correction_path: CorrectionPath = Field(alias="correctionPath")
    high_risk: StrictBool = Field(alias="highRisk")

    @field_validator("claim_ids", "evidence_ids", "verification_ids")
    @classmethod
    def transition_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("transition identifier lists cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def privileged_transition_has_controls(self) -> "EditorialTransition":
        privileged = {
            TransitionAction.CONFIRM,
            TransitionAction.CORRECT,
            TransitionAction.RETRACT,
            TransitionAction.PUBLISH,
            TransitionAction.GLOBAL_PROMOTE,
        }
        if self.high_risk or self.action in privileged:
            if not self.evidence_ids:
                raise ValueError("privileged and high-risk transitions require evidenceIds")
            if self.human_review is None:
                raise ValueError("privileged and high-risk transitions require named human review")
            if self.human_review is not None and self.human_review.reviewed_at > self.occurred_at:
                raise ValueError("occurredAt cannot predate its human review")
        if self.action is TransitionAction.CONFIRM and not self.verification_ids:
            raise ValueError("confirmation transitions require verificationIds")
        return self


class Correction(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["correction"] = Field(alias="objectType")
    correction_id: ContractId = Field(alias="correctionId")
    story_id: ContractId = Field(alias="storyId")
    claim_id: ContractId = Field(alias="claimId")
    receipt_ids: list[ContractId] = Field(alias="receiptIds", min_length=1, max_length=1_000)
    evidence_ids: list[ContractId] = Field(alias="evidenceIds", min_length=1, max_length=5_000)
    correction_type: CorrectionType = Field(alias="correctionType")
    prior_text: NonEmptyText = Field(alias="priorText")
    corrected_text: Optional[NonEmptyText] = Field(alias="correctedText")
    reason: ReasonText
    issued_at: UtcDatetime = Field(alias="issuedAt")
    human_review: HumanReview = Field(alias="humanReview")
    correction_path: CorrectionPath = Field(alias="correctionPath")
    supersedes_correction_id: Optional[ContractId] = Field(alias="supersedesCorrectionId")

    @field_validator("receipt_ids", "evidence_ids")
    @classmethod
    def correction_ids_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("correction identifier lists cannot contain duplicates")
        return value

    @model_validator(mode="after")
    def correction_is_attributable_and_non_silent(self) -> "Correction":
        if self.correction_type is CorrectionType.RETRACTION:
            if self.corrected_text is not None:
                raise ValueError("a retraction cannot replace the claim with new asserted text")
        else:
            if self.corrected_text is None:
                raise ValueError("corrections and clarifications require correctedText")
            if self.corrected_text == self.prior_text:
                raise ValueError("correctedText must differ from priorText")
        if self.human_review.reviewed_at > self.issued_at:
            raise ValueError("issuedAt cannot predate its human review")
        if self.supersedes_correction_id == self.correction_id:
            raise ValueError("a correction cannot supersede itself")
        return self


class ReceiptUpdate(ContractModel):
    state: EditorialState
    publication_state: PublicationState = Field(alias="publicationState")
    at: UtcDatetime
    summary: ShortText
    transition_id: Optional[ContractId] = Field(alias="transitionId")
    correction_id: Optional[ContractId] = Field(alias="correctionId")


class Receipt(ContractModel):
    schema_version: Literal[SCHEMA_VERSION] = Field(alias="schemaVersion")
    object_type: Literal["receipt"] = Field(alias="objectType")
    receipt_id: ContractId = Field(alias="receiptId")
    story_id: ContractId = Field(alias="storyId")
    claim_id: ContractId = Field(alias="claimId")
    claim_text: NonEmptyText = Field(alias="claimText")
    editorial_state: EditorialState = Field(alias="editorialState")
    publication_state: PublicationState = Field(alias="publicationState")
    sources: list[Source] = Field(min_length=1, max_length=100)
    evidence: list[Evidence] = Field(min_length=1, max_length=1_000)
    verification: Optional[Verification]
    reviewer: Optional[HumanReview]
    source_published_at: UtcDatetime = Field(alias="sourcePublishedAt")
    first_seen_at: UtcDatetime = Field(alias="firstSeenAt")
    live_wire_published_at: Optional[UtcDatetime] = Field(alias="liveWirePublishedAt")
    updated_at: UtcDatetime = Field(alias="updatedAt")
    update_history: list[ReceiptUpdate] = Field(alias="updateHistory", min_length=1, max_length=5_000)
    corrections: list[Correction] = Field(max_length=1_000)
    superseded_texts: list[NonEmptyText] = Field(alias="supersededTexts", max_length=1_000)
    correction_path: CorrectionPath = Field(alias="correctionPath")

    @model_validator(mode="after")
    def receipt_is_a_complete_claim_level_view(self) -> "Receipt":
        source_ids = [source.source_id for source in self.sources]
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("receipt sources cannot contain duplicates")
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("receipt evidence cannot contain duplicates")
        if any(item.claim_id != self.claim_id for item in self.evidence):
            raise ValueError("all receipt evidence must resolve to its claimId")
        if any(item.source_id not in source_ids for item in self.evidence):
            raise ValueError("all receipt evidence sourceIds must resolve in sources")
        if self.source_published_at != min(source.source_published_at for source in self.sources):
            raise ValueError("sourcePublishedAt must preserve the earliest source timestamp")
        if self.first_seen_at != min(source.first_seen_at for source in self.sources):
            raise ValueError("firstSeenAt must preserve the earliest observation timestamp")
        if self.updated_at < self.first_seen_at:
            raise ValueError("updatedAt cannot predate firstSeenAt")
        if self.live_wire_published_at is not None:
            if self.live_wire_published_at < self.first_seen_at:
                raise ValueError("liveWirePublishedAt cannot predate firstSeenAt")
            if self.updated_at < self.live_wire_published_at:
                raise ValueError("updatedAt cannot predate liveWirePublishedAt")
        if self.publication_state is not PublicationState.WITHHELD:
            if self.live_wire_published_at is None:
                raise ValueError("published receipts require liveWirePublishedAt")
        if self.verification is not None:
            if self.verification.claim_id != self.claim_id:
                raise ValueError("receipt verification must resolve to its claimId")
            if any(item not in evidence_ids for item in self.verification.evidence_ids):
                raise ValueError("verification evidenceIds must resolve in receipt evidence")
        if self.editorial_state is EditorialState.CONFIRMED:
            if self.verification is None or self.verification.outcome is not VerificationOutcome.SUPPORTED:
                raise ValueError("confirmed receipt requires a supported verification")
            if self.reviewer is None:
                raise ValueError("confirmed receipt requires its named human reviewer")
        if self.editorial_state in {EditorialState.CORRECTED, EditorialState.RETRACTED}:
            if not self.corrections:
                raise ValueError("corrected and retracted receipts require correction history")
        if any(
            correction.story_id != self.story_id or correction.claim_id != self.claim_id
            for correction in self.corrections
        ):
            raise ValueError("receipt corrections must resolve to its story and claim")
        if any(self.receipt_id not in correction.receipt_ids for correction in self.corrections):
            raise ValueError("receipt corrections must explicitly reference this receiptId")
        if any(
            evidence_id not in evidence_ids
            for correction in self.corrections
            for evidence_id in correction.evidence_ids
        ):
            raise ValueError("correction evidenceIds must resolve in receipt evidence")
        if any(correction.correction_path != self.correction_path for correction in self.corrections):
            raise ValueError("receipt corrections must use its public correctionPath")
        if len(self.superseded_texts) != len(set(self.superseded_texts)):
            raise ValueError("supersededTexts cannot contain duplicates")
        history_times = [item.at for item in self.update_history]
        if history_times != sorted(history_times):
            raise ValueError("updateHistory must be chronological")
        return self


ROOT_CONTRACTS: dict[str, type[ContractModel]] = {
    "story": Story,
    "claim": Claim,
    "source": Source,
    "evidence": Evidence,
    "verification": Verification,
    "editorial-transition": EditorialTransition,
    "correction": Correction,
    "receipt": Receipt,
}


def contract_from_json(object_type: str, raw: str | bytes) -> ContractModel:
    """Parse one root contract without accepting an unregistered object type."""
    model = ROOT_CONTRACTS.get(object_type)
    if model is None:
        raise ValueError(f"unknown editorial contract type: {object_type}")
    return model.model_validate_json(raw)


def contract_schema(model: type[ContractModel]) -> dict[str, Any]:
    """Export the public camelCase JSON Schema for a root contract."""
    return model.model_json_schema(by_alias=True, mode="validation")
