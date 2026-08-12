from __future__ import annotations

from soc_agent.contracts import (
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryTargetArtifact,
)
from soc_agent.memory import MemoryAdmissionService


def _command(
    *,
    source_type: SocMemoryCandidateSourceType,
    source_metadata: dict | None = None,
    metadata: dict | None = None,
    facets: dict[str, list[str]] | None = None,
) -> SocMemoryCandidateCreateCommand:
    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary="Reviewed detection lesson",
        content="A human-reviewed conclusion proposed for later reuse.",
        source=SocMemoryCandidateSource(
            source_type=source_type,
            source_id="SRC-ADMISSION-1",
            metadata=source_metadata or {},
        ),
        evidence_refs=["review:SRC-ADMISSION-1"],
        validity=SocMemoryCandidateValidity(notes="Requires memory review."),
        facets=facets or {},
        metadata=metadata or {},
    )


def test_changed_verdict_with_reason_and_anchor_is_admitted() -> None:
    decision = MemoryAdmissionService().evaluate(
        _command(
            source_type=SocMemoryCandidateSourceType.CORRECTION,
            source_metadata={
                "previous_verdict": "unknown",
                "corrected_verdict": "true_positive",
            },
            metadata={"correction_reason_length": 48},
            facets={"detection_key": ["nids:reverse-shell"]},
        )
    )

    assert decision.status.value == "admitted"
    assert decision.quality_score == 1.0
    assert decision.reusable_facets == {"detection_key": ["nids:reverse-shell"]}


def test_confirmation_only_does_not_create_one_candidate_per_alert() -> None:
    decision = MemoryAdmissionService().evaluate(
        _command(
            source_type=SocMemoryCandidateSourceType.CORRECTION,
            source_metadata={
                "previous_verdict": "true_positive",
                "corrected_verdict": "true_positive",
            },
            metadata={"correction_reason_length": 48},
            facets={"detection_key": ["nids:reverse-shell"]},
        )
    )

    assert decision.status.value == "observed_only"
    assert "confirmation_only" in {reason.value for reason in decision.reason_codes}


def test_unpromoted_note_and_unanchored_feedback_remain_observations() -> None:
    service = MemoryAdmissionService()
    ordinary_note = service.evaluate(
        _command(
            source_type=SocMemoryCandidateSourceType.REVIEW_NOTE,
            source_metadata={"origin": "analyst_note"},
            metadata={"note_length": 80},
            facets={"scenario_key": ["reverse_shell"]},
        )
    )
    unanchored = service.evaluate(
        _command(
            source_type=SocMemoryCandidateSourceType.REVIEW_NOTE,
            source_metadata={
                "origin": "analyst_note",
                "promote_to_memory": True,
            },
            metadata={"note_length": 80},
        )
    )

    assert ordinary_note.status.value == "observed_only"
    assert unanchored.status.value == "observed_only"
    assert "no_reusable_anchor" in {reason.value for reason in unanchored.reason_codes}


def test_lead_agent_acceptance_requires_substantive_human_reason() -> None:
    decision = MemoryAdmissionService().evaluate(
        _command(
            source_type=SocMemoryCandidateSourceType.REVIEW_NOTE,
            source_metadata={
                "origin": "accepted_lead_agent_conclusion",
                "note_length": 500,
                "acceptance_reason_length": 4,
            },
            facets={"scenario_key": ["reverse_shell"]},
        )
    )

    assert decision.status.value == "observed_only"
    assert "explicit_lead_agent_acceptance" in {reason.value for reason in decision.reason_codes}
    assert "weak_or_missing_reason" in {reason.value for reason in decision.reason_codes}
