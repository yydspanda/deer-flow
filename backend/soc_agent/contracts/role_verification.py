"""Contracts for conditional second-pass role adjudication verification."""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class RoleVerificationClaimType(StrEnum):
    NETWORK_DIRECTION = "network_direction"
    ROLE_ASSIGNMENT = "role_assignment"
    RESPONSE_TARGET = "response_target"


class RoleVerificationTriggerReason(StrEnum):
    PRIMARY_DIRECTION_CONFLICTED = "primary_direction_conflicted"
    PRIMARY_DIRECTION_INFERRED = "primary_direction_inferred"
    PRIMARY_DIRECTION_INDETERMINATE = "primary_direction_indeterminate"
    PRIMARY_ROLE_TENTATIVE = "primary_role_tentative"
    PRIMARY_ROLE_CONFLICTED = "primary_role_conflicted"
    PRIMARY_ROLE_UNRESOLVED = "primary_role_unresolved"
    PRIMARY_EVIDENCE_GAP = "primary_evidence_gap"
    PRIMARY_LOW_CONFIDENCE = "primary_low_confidence"
    NETWORK_INTERMEDIARY_PRESENT = "network_intermediary_present"
    RESPONSE_TARGET_PROPOSED = "response_target_proposed"
    UPSTREAM_ROLE_CONFLICT = "upstream_role_conflict"
    PRIMARY_GROUNDING_DEGRADED = "primary_grounding_degraded"


class RoleVerificationClaimStatus(StrEnum):
    SUPPORTED = "supported"
    CHALLENGED = "challenged"
    UNRESOLVED = "unresolved"


class RoleVerificationStatus(StrEnum):
    CONFIRMED = "confirmed"
    CHALLENGED = "challenged"
    UNRESOLVED = "unresolved"
    UNAVAILABLE = "unavailable"


class RoleVerificationFailureKind(StrEnum):
    PROVIDER_ERROR = "provider_error"
    OUTPUT_INVALID = "output_invalid"


RoleVerificationScalar = str | int | float | bool | None


class RoleVerificationClaim(BaseModel):
    """One atomic first-pass assertion shown to the verifier as untrusted input."""

    model_config = ConfigDict(extra="forbid")

    claim_ref: str = Field(pattern=r"^RC-(?:ND|R|T)-[0-9]{2}$")
    claim_type: RoleVerificationClaimType
    assertion: dict[str, RoleVerificationScalar] = Field(min_length=1, max_length=12)

    @field_validator("assertion")
    @classmethod
    def validate_assertion(cls, value: dict[str, RoleVerificationScalar]) -> dict[str, RoleVerificationScalar]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in value):
            raise ValueError("role verification assertion keys must use lower_snake_case")
        return value


class RoleVerificationTriggerDecision(BaseModel):
    """Deterministic decision controlling whether a second model call is allowed."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.role_verification_trigger.v1"] = "soc.role_verification_trigger.v1"
    policy_version: Literal[
        "soc.role_verification_trigger_policy.v1",
        "soc.role_verification_trigger_policy.v2",
    ] = "soc.role_verification_trigger_policy.v2"
    triggered: bool
    reasons: list[RoleVerificationTriggerReason] = Field(default_factory=list)
    claim_count: int = Field(ge=0)
    claims_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    minimum_confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_trigger(self) -> RoleVerificationTriggerDecision:
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("role verification trigger reasons must be unique")
        if self.triggered and (not self.reasons or self.claim_count == 0):
            raise ValueError("triggered role verification requires reasons and reviewable claims")
        if not self.triggered and self.reasons:
            raise ValueError("non-triggered role verification cannot retain trigger reasons")
        return self


class RoleVerificationAlternative(BaseModel):
    """Structured replacement assertion suggested by the verifier."""

    model_config = ConfigDict(extra="forbid")

    assertion: dict[str, RoleVerificationScalar] = Field(min_length=1, max_length=12)

    @field_validator("assertion")
    @classmethod
    def validate_assertion(cls, value: dict[str, RoleVerificationScalar]) -> dict[str, RoleVerificationScalar]:
        if any(not re.fullmatch(r"[a-z][a-z0-9_]*", key) for key in value):
            raise ValueError("role verification alternative keys must use lower_snake_case")
        return value


class RoleVerificationClaimReview(BaseModel):
    """Evidence-bound adversarial review of one first-pass assertion."""

    model_config = ConfigDict(extra="forbid")

    claim_ref: str = Field(pattern=r"^RC-(?:ND|R|T)-[0-9]{2}$")
    status: RoleVerificationClaimStatus
    supporting_evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    contradicting_evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    context_refs: list[str] = Field(default_factory=list, max_length=20)
    alternative: RoleVerificationAlternative | None = None
    rationale: str = Field(min_length=1, max_length=3000)
    counterevidence_assessment: str = Field(min_length=1, max_length=3000)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_review(self) -> RoleVerificationClaimReview:
        reference_groups = (
            (self.supporting_evidence_refs, r"E-[A-F0-9]{12}", "supporting evidence"),
            (self.contradicting_evidence_refs, r"E-[A-F0-9]{12}", "contradicting evidence"),
            (self.context_refs, r"(?:S|A|M|C|T)-[A-F0-9]{12}", "context"),
        )
        for values, pattern, label in reference_groups:
            if len(values) != len(set(values)):
                raise ValueError(f"role verification {label} references must be unique")
            if any(not re.fullmatch(pattern, value) for value in values):
                raise ValueError(f"role verification {label} references are invalid")
        if set(self.supporting_evidence_refs) & set(self.contradicting_evidence_refs):
            raise ValueError("one E-* fact cannot be both supporting and contradicting for the same claim")
        if self.status is RoleVerificationClaimStatus.SUPPORTED and not self.supporting_evidence_refs:
            raise ValueError("supported role verification claim requires supporting evidence")
        if self.status is RoleVerificationClaimStatus.CHALLENGED and not self.contradicting_evidence_refs:
            raise ValueError("challenged role verification claim requires contradicting evidence")
        if self.status is RoleVerificationClaimStatus.UNRESOLVED and not self.evidence_gaps:
            raise ValueError("unresolved role verification claim requires an evidence gap")
        if self.status is RoleVerificationClaimStatus.SUPPORTED and self.alternative is not None:
            raise ValueError("supported role verification claim cannot include an alternative")
        return self


class RoleVerificationCandidate(BaseModel):
    """Strict model output before Runtime adds trigger and provider provenance."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.role_verification_candidate.v1"] = "soc.role_verification_candidate.v1"
    claim_reviews: list[RoleVerificationClaimReview] = Field(min_length=1, max_length=80)

    @field_validator("claim_reviews")
    @classmethod
    def validate_unique_claims(cls, value: list[RoleVerificationClaimReview]) -> list[RoleVerificationClaimReview]:
        refs = [item.claim_ref for item in value]
        if len(refs) != len(set(refs)):
            raise ValueError("role verification candidate must review each claim once")
        return value


class RoleAdjudicationVerificationResult(BaseModel):
    """Persisted, non-authoritative second-pass verification result."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.role_adjudication_verification.v1"] = "soc.role_adjudication_verification.v1"
    status: RoleVerificationStatus
    trigger: RoleVerificationTriggerDecision
    claims: list[RoleVerificationClaim] = Field(min_length=1, max_length=80)
    claim_reviews: list[RoleVerificationClaimReview] = Field(default_factory=list, max_length=80)
    primary_model_name: str = Field(min_length=1, max_length=256)
    verifier_model_name: str = Field(min_length=1, max_length=256)
    same_model_verification: bool
    prompt_version: str = Field(min_length=1, max_length=128)
    parser_version: str = Field(min_length=1, max_length=128)
    repair_applied: bool = False
    repair_log: list[dict[str, Any]] = Field(default_factory=list, max_length=20)
    failure_kind: RoleVerificationFailureKind | None = None
    warnings: list[str] = Field(default_factory=list, max_length=20)
    automation_allowed: Literal[False] = False

    @model_validator(mode="after")
    def validate_result(self) -> RoleAdjudicationVerificationResult:
        if not self.trigger.triggered:
            raise ValueError("persisted role verification requires a triggered gate")
        claim_refs = [item.claim_ref for item in self.claims]
        if len(claim_refs) != len(set(claim_refs)):
            raise ValueError("persisted role verification claims must be unique")
        if len(self.claims) != self.trigger.claim_count:
            raise ValueError("persisted role verification must retain every triggered claim")
        if stable_role_verification_claims_hash(self.claims) != self.trigger.claims_hash:
            raise ValueError("persisted role verification claims must match the trigger hash")
        if self.status is RoleVerificationStatus.UNAVAILABLE:
            if self.claim_reviews or self.failure_kind is None:
                raise ValueError("unavailable role verification requires failure_kind and no claim reviews")
            return self
        if self.failure_kind is not None:
            raise ValueError("completed role verification cannot carry failure_kind")
        expected_refs = set(claim_refs)
        actual_refs = {item.claim_ref for item in self.claim_reviews}
        if actual_refs != expected_refs:
            raise ValueError("completed role verification must review every triggered claim")
        derived = derive_role_verification_status(self.claim_reviews)
        if self.status is not derived:
            raise ValueError("role verification status must be derived from claim reviews")
        return self


class RoleVerificationNodeOutput(BaseModel):
    """Runtime node output with bounded provider metadata."""

    model_config = ConfigDict(extra="forbid")

    verification: RoleAdjudicationVerificationResult
    metadata: dict[str, Any] = Field(default_factory=dict)


def derive_role_verification_status(
    reviews: list[RoleVerificationClaimReview],
) -> RoleVerificationStatus:
    if any(item.status is RoleVerificationClaimStatus.CHALLENGED for item in reviews):
        return RoleVerificationStatus.CHALLENGED
    if any(item.status is RoleVerificationClaimStatus.UNRESOLVED for item in reviews):
        return RoleVerificationStatus.UNRESOLVED
    return RoleVerificationStatus.CONFIRMED


def stable_role_verification_claims_hash(
    claims: list[RoleVerificationClaim],
) -> str:
    """Hash the exact ordered claim projection without importing Runtime helpers."""

    payload = json.dumps(
        [claim.model_dump(mode="json") for claim in claims],
        ensure_ascii=True,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "RoleAdjudicationVerificationResult",
    "RoleVerificationAlternative",
    "RoleVerificationCandidate",
    "RoleVerificationClaim",
    "RoleVerificationClaimReview",
    "RoleVerificationClaimStatus",
    "RoleVerificationClaimType",
    "RoleVerificationFailureKind",
    "RoleVerificationNodeOutput",
    "RoleVerificationStatus",
    "RoleVerificationTriggerDecision",
    "RoleVerificationTriggerReason",
    "derive_role_verification_status",
    "stable_role_verification_claims_hash",
]
