"""Deterministic claim projection and trigger policy for role verification."""

from __future__ import annotations

from soc_agent.contracts import (
    AdjudicatedRole,
    AdjudicatedRoleStatus,
    AdjudicatedRoleType,
    AnalysisEvidenceGroundingReport,
    AnalysisEvidenceGroundingStatus,
    AnalysisResult,
    LLMAnalysisRequest,
    NetworkBoundaryDirection,
    NetworkDirectionAssessmentStatus,
    RoleResolutionStatus,
    RoleVerificationClaim,
    RoleVerificationClaimType,
    RoleVerificationTriggerDecision,
    RoleVerificationTriggerReason,
    stable_role_verification_claims_hash,
)

ROLE_VERIFICATION_TRIGGER_POLICY_VERSION = "soc.role_verification_trigger_policy.v2"
DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE = 0.35

_CORE_ROLE_TYPES = {
    AdjudicatedRoleType.ATTACKER,
    AdjudicatedRoleType.VICTIM,
}
_PLACEHOLDER_ROLE_VALUES = {
    "unknown",
    "not_available",
    "not available",
    "n/a",
    "none",
    "未识别",
    "未知",
}

_ROLE_CONFLICT_TERMS = (
    "role",
    "attacker",
    "victim",
    "source",
    "destination",
    "direction",
    "initiator",
    "responder",
)


def build_role_verification_claims(
    analysis: AnalysisResult,
) -> list[RoleVerificationClaim]:
    """Project atomic first-pass claims without its rationale or confidence."""

    claims: list[RoleVerificationClaim] = []
    direction = analysis.network_direction
    if _direction_is_reviewable(analysis):
        direction_claims = (
            ("RC-ND-01", "observed_flow", direction.observed_flow, direction.observed_flow != "not_available"),
            (
                "RC-ND-02",
                "boundary_direction",
                direction.boundary_direction.value,
                direction.boundary_direction is not NetworkBoundaryDirection.NOT_APPLICABLE,
            ),
            ("RC-ND-03", "semantic_direction", direction.semantic_direction, direction.semantic_direction is not None),
            ("RC-ND-04", "connection_initiator", direction.connection_initiator, direction.connection_initiator is not None),
        )
        claims.extend(
            RoleVerificationClaim(
                claim_ref=claim_ref,
                claim_type=RoleVerificationClaimType.NETWORK_DIRECTION,
                assertion={field_name: value},
            )
            for claim_ref, field_name, value, present in direction_claims
            if present
        )

    claims.extend(
        RoleVerificationClaim(
            claim_ref=f"RC-R-{index:02d}",
            claim_type=RoleVerificationClaimType.ROLE_ASSIGNMENT,
            assertion={
                "role": role.role.value,
                "entity_type": role.entity_type,
                "value": role.value,
            },
        )
        for index, role in enumerate(_core_roles(analysis), start=1)
    )
    return claims


def evaluate_role_verification_trigger(
    analysis: AnalysisResult,
    *,
    request: LLMAnalysisRequest,
    grounding: AnalysisEvidenceGroundingReport,
    minimum_confidence: float = DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE,
) -> RoleVerificationTriggerDecision:
    """Trigger only when direction or attacker/victim needs a safety backstop."""

    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence must be within [0, 1]")
    claims = build_role_verification_claims(analysis)
    reasons: list[RoleVerificationTriggerReason] = []
    direction = analysis.network_direction
    core_roles = _core_roles(analysis)
    direction_reviewable = _direction_is_reviewable(analysis)

    if direction_reviewable and direction.status is NetworkDirectionAssessmentStatus.CONFLICTED:
        reasons.append(RoleVerificationTriggerReason.PRIMARY_DIRECTION_CONFLICTED)
    if direction_reviewable and direction.status is NetworkDirectionAssessmentStatus.INDETERMINATE:
        reasons.append(RoleVerificationTriggerReason.PRIMARY_DIRECTION_INDETERMINATE)
    if any(role.status is AdjudicatedRoleStatus.CONFLICTED for role in core_roles):
        reasons.append(RoleVerificationTriggerReason.PRIMARY_ROLE_CONFLICTED)
    if any(role.status is AdjudicatedRoleStatus.UNRESOLVED for role in core_roles):
        reasons.append(RoleVerificationTriggerReason.PRIMARY_ROLE_UNRESOLVED)

    if _has_upstream_role_conflict(request):
        reasons.append(RoleVerificationTriggerReason.UPSTREAM_ROLE_CONFLICT)
    if _has_core_grounding_failure(
        analysis,
        grounding=grounding,
        core_roles=core_roles,
        include_direction=direction_reviewable,
    ):
        reasons.append(RoleVerificationTriggerReason.PRIMARY_GROUNDING_DEGRADED)
    core_confidences = [role.confidence for role in core_roles]
    if direction_reviewable:
        core_confidences.append(direction.confidence)
    if reasons and core_confidences and min(core_confidences) < minimum_confidence:
        reasons.append(RoleVerificationTriggerReason.PRIMARY_LOW_CONFIDENCE)

    reasons = list(dict.fromkeys(reasons))
    triggered = bool(claims and reasons)
    if not triggered:
        reasons = []
    return RoleVerificationTriggerDecision(
        triggered=triggered,
        reasons=reasons,
        claim_count=len(claims),
        claims_hash=stable_role_verification_claims_hash(claims),
        minimum_confidence=minimum_confidence,
    )


def _direction_is_reviewable(analysis: AnalysisResult) -> bool:
    direction = analysis.network_direction
    if direction.status in {
        NetworkDirectionAssessmentStatus.NOT_ASSESSED,
    }:
        return False
    if direction.status is NetworkDirectionAssessmentStatus.CONFLICTED:
        return True
    return any(
        (
            direction.observed_flow != "not_available",
            direction.boundary_direction
            not in {
                NetworkBoundaryDirection.NOT_APPLICABLE,
                NetworkBoundaryDirection.INDETERMINATE,
            },
            direction.semantic_direction is not None,
            direction.connection_initiator is not None,
        )
    )


def _core_roles(analysis: AnalysisResult) -> list[AdjudicatedRole]:
    return [role for role in analysis.role_adjudication.roles if role.role in _CORE_ROLE_TYPES and role.value is not None and role.value.strip().casefold() not in _PLACEHOLDER_ROLE_VALUES]


def _has_core_grounding_failure(
    analysis: AnalysisResult,
    *,
    grounding: AnalysisEvidenceGroundingReport,
    core_roles: list[AdjudicatedRole],
    include_direction: bool,
) -> bool:
    evidence_refs: set[str] = set()
    reasoning_refs: set[str] = set()
    if include_direction:
        evidence_refs.update(analysis.network_direction.evidence_refs)
        reasoning_refs.update(analysis.network_direction.reasoning_refs)
    for role in core_roles:
        evidence_refs.update(role.evidence_refs)
        reasoning_refs.update(role.reasoning_refs)
    if not evidence_refs and not reasoning_refs:
        return False

    evidence_statuses = {item.evidence_ref: item.status for item in grounding.items}
    reasoning_statuses = {item.reasoning_id: item.status for item in grounding.reasoning_items}
    return any(evidence_statuses.get(ref) is not AnalysisEvidenceGroundingStatus.GROUNDED for ref in evidence_refs) or any(reasoning_statuses.get(ref) is not AnalysisEvidenceGroundingStatus.GROUNDED for ref in reasoning_refs)


def _has_upstream_role_conflict(request: LLMAnalysisRequest) -> bool:
    if any(resolution.status is RoleResolutionStatus.CONFLICTED for resolution in request.fact_reconstruction.role_resolutions):
        return True
    for conflict in request.fact_reconstruction.conflict_reports:
        if not conflict.blocks_automation:
            continue
        searchable = " ".join(
            [
                conflict.conflict_type,
                *conflict.involved_fields,
            ]
        ).casefold()
        if any(term in searchable for term in _ROLE_CONFLICT_TERMS):
            return True
    return False


__all__ = [
    "DEFAULT_ROLE_VERIFICATION_MIN_CONFIDENCE",
    "ROLE_VERIFICATION_TRIGGER_POLICY_VERSION",
    "build_role_verification_claims",
    "evaluate_role_verification_trigger",
]
