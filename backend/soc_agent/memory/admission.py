"""Deterministic admission gate between workflow signals and memory review."""

from __future__ import annotations

from soc_agent.contracts import (
    MemoryAdmissionDecision,
    MemoryAdmissionReasonCode,
    MemoryAdmissionStatus,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSourceType,
)
from soc_agent.utils.hashing import stable_hash

MEMORY_ADMISSION_POLICY_VERSION = "soc.memory_admission_policy.v1"

_REUSABLE_FACET_KEYS = frozenset(
    {
        "detection_key",
        "rule_code",
        "rule_name",
        "scenario_key",
        "scenario_name",
        "category",
        "domain",
        "product",
        "source_system",
        "skill",
        "capability_card",
        "conflict_type",
        "behavior_fingerprint",
        "role_entity",
        "environment",
    }
)


class MemoryAdmissionService:
    """Admit only explicitly human-promoted, reusable learning signals."""

    def evaluate(
        self,
        command: SocMemoryCandidateCreateCommand,
    ) -> MemoryAdmissionDecision:
        reusable_facets = {key: list(dict.fromkeys(value for value in values if value)) for key, values in command.facets.items() if key in _REUSABLE_FACET_KEYS and any(values)}
        reasons: list[MemoryAdmissionReasonCode] = []
        if reusable_facets:
            reasons.append(MemoryAdmissionReasonCode.REUSABLE_ANCHOR_PRESENT)
        else:
            reasons.append(MemoryAdmissionReasonCode.NO_REUSABLE_ANCHOR)

        source_type = command.source.source_type
        promotion_signal = False
        human_signal = False
        reason_is_strong = True

        if source_type is SocMemoryCandidateSourceType.CORRECTION:
            previous = command.source.metadata.get("previous_verdict")
            corrected = command.source.metadata.get("corrected_verdict")
            reason_length = int(command.metadata.get("correction_reason_length") or 0)
            human_signal = True
            reason_is_strong = reason_length >= 20
            if previous != corrected:
                promotion_signal = True
                reasons.append(MemoryAdmissionReasonCode.VERDICT_CHANGED)
            else:
                reasons.append(MemoryAdmissionReasonCode.CONFIRMATION_ONLY)
        elif source_type is SocMemoryCandidateSourceType.REVIEW_NOTE:
            origin = command.source.metadata.get("origin")
            accepted = origin == "accepted_lead_agent_conclusion"
            explicit = command.source.metadata.get("promote_to_memory") is True
            human_signal = True
            promotion_signal = accepted or explicit
            if accepted:
                reasons.append(MemoryAdmissionReasonCode.EXPLICIT_LEAD_AGENT_ACCEPTANCE)
            if explicit:
                reasons.append(MemoryAdmissionReasonCode.EXPLICIT_PROMOTION_REQUESTED)
            if not promotion_signal:
                reasons.append(MemoryAdmissionReasonCode.NO_HUMAN_PROMOTION_SIGNAL)
            reason_length_key = "acceptance_reason_length" if accepted else "note_length"
            reason_is_strong = int(command.source.metadata.get(reason_length_key) or 0) >= 20
        elif source_type is SocMemoryCandidateSourceType.DOMAIN_FINDING:
            feedback = command.metadata.get("analyst_feedback_present") is True
            human_signal = feedback
            promotion_signal = feedback
            if feedback:
                reasons.append(MemoryAdmissionReasonCode.ANALYST_FEEDBACK_PRESENT)
            else:
                reasons.append(MemoryAdmissionReasonCode.NO_HUMAN_PROMOTION_SIGNAL)
            reason_is_strong = int(command.metadata.get("analyst_feedback_length") or 0) >= 20
        else:
            explicit = command.source.metadata.get("promote_to_memory") is True
            human_signal = explicit
            promotion_signal = explicit
            if explicit:
                reasons.append(MemoryAdmissionReasonCode.EXPLICIT_PROMOTION_REQUESTED)
            else:
                reasons.append(MemoryAdmissionReasonCode.NO_HUMAN_PROMOTION_SIGNAL)

        if not reason_is_strong:
            reasons.append(MemoryAdmissionReasonCode.WEAK_OR_MISSING_REASON)

        admitted = bool(human_signal and promotion_signal and reason_is_strong and reusable_facets)
        quality_score = min(
            1.0,
            (0.25 if human_signal else 0.0) + (0.30 if promotion_signal else 0.0) + (0.25 if reason_is_strong else 0.0) + (0.20 if reusable_facets else 0.0),
        )
        return MemoryAdmissionDecision(
            status=(MemoryAdmissionStatus.ADMITTED if admitted else MemoryAdmissionStatus.OBSERVED_ONLY),
            source_type=source_type,
            candidate_type=command.candidate_type,
            quality_score=quality_score,
            reason_codes=list(dict.fromkeys(reasons)),
            reusable_facets=reusable_facets,
            command_hash=stable_hash(command.model_dump(mode="json")),
        )


__all__ = ["MEMORY_ADMISSION_POLICY_VERSION", "MemoryAdmissionService"]
