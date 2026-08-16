from __future__ import annotations

import json
from datetime import UTC, datetime

from soc_agent.contracts import (
    AlertClassification,
    AlertSourceRef,
    AlertSourceType,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    Decision,
    DecisionConfidenceSource,
    DecisionEvidenceState,
    DetectionRuleRef,
    EvidenceItem,
    LLMAnalysisRequest,
    Verdict,
)
from validation.compact_zeus.memory.simulate_pattern_memory_lifecycle import (
    simulate_pattern_memory_lifecycle,
)

NOW = datetime(2026, 8, 16, 1, 0, tzinfo=UTC)


def _base_run() -> AnalysisRun:
    evidence_ref = "E-000000000001"
    return AnalysisRun(
        run_id="RUN-PINGAN-MEMORY-BASE",
        alert_id="PINGAN-REVERSE-SHELL-BASE",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        model_name="stub",
        prompt_version="stub",
        input_payload={
            "alert_id": "PINGAN-REVERSE-SHELL-BASE",
            "event_time": NOW.isoformat(),
        },
        input_hash="a" * 64,
        started_at=NOW,
        llm_analysis_request=LLMAnalysisRequest(
            alert_id="PINGAN-REVERSE-SHELL-BASE",
            tenant_id="pingan",
            environment="prd",
            source=AlertSourceRef(
                source_type=AlertSourceType.NIDS,
                source_system="zeus",
                product="ndr",
                integration_name="pingan_legacy_alert_platform",
            ),
            detection=DetectionRuleRef(
                rule_code="RULE-REVERSE-SHELL",
                rule_name="Reverse shell detector",
                detection_key="pingan:ndr:reverse-shell",
            ),
            classification=AlertClassification(
                category="command_and_control",
                severity="high",
                technique=["T1059", "T1071"],
            ),
        ),
        analysis=AnalysisResult(
            verdict=Verdict.UNKNOWN,
            confidence=0.45,
            summary="Base fixture requires review.",
            evidence=[
                EvidenceItem(
                    evidence_ref=evidence_ref,
                    source="fixture",
                    description="Detector hit",
                    value="RULE-REVERSE-SHELL",
                )
            ],
            reasoning=[
                AnalysisReasoningItem(
                    reasoning_id="R-01",
                    statement="The base fixture has not yet used reviewed Memory.",
                    basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                    evidence_refs=[evidence_ref],
                    confidence=0.45,
                )
            ],
            decision_evidence_refs=[evidence_ref],
            decision_reasoning_refs=["R-01"],
            reason="No prior reviewed Memory was available.",
            recommended_action="needs_human_review",
        ),
        decision=Decision(
            verdict=Verdict.UNKNOWN,
            confidence=0.45,
            confidence_source=DecisionConfidenceSource.STUB_HEURISTIC,
            evidence_state=DecisionEvidenceState.PARTIAL,
            suggested_action="needs_human_review",
            needs_review=True,
            reason="No prior reviewed Memory was available.",
        ),
    )


def test_simulation_runs_one_candidate_review_retrieval_and_decision_lineage(
    tmp_path,
) -> None:
    base = _base_run()
    output_dir = tmp_path / "pattern-memory-lifecycle"

    summary = simulate_pattern_memory_lifecycle(
        base,
        output_dir=output_dir,
        tenant_id="pingan",
        environment="prd",
        support_count=3,
        confirmed_verdict=Verdict.SUSPICIOUS,
        now=NOW,
    )

    assert summary["status"] == "passed"
    assert summary["pattern"]["observation_count"] == 3
    assert summary["pattern"]["distinct_source_count"] == 3
    assert summary["pattern"]["candidate_count"] == 1
    assert summary["memory"]["record_count"] == 1
    assert summary["memory"]["retrieval_enabled"] is True
    assert summary["held_out"]["retrieval_match_count"] == 1
    assert summary["held_out"]["base_verdict"] == "unknown"
    assert summary["held_out"]["effective_verdict"] == "suspicious"
    assert summary["held_out"]["effective_needs_review"] is False
    assert all(summary["checks"].values())
    assert base.decision is not None
    assert base.decision.verdict is Verdict.UNKNOWN

    candidate = json.loads(
        (output_dir / "02-pattern-candidate.json").read_text(encoding="utf-8")
    )
    assert candidate["status"] == "pending_review"
    assert candidate["metadata"]["support_count_at_creation"] == 3
    assert candidate["metadata"]["mocked"] is True

    confirmed = json.loads(
        (output_dir / "04-confirmed-memory.json").read_text(encoding="utf-8")
    )
    assert confirmed["decision_directive"]["target_verdict"] == "suspicious"
    assert confirmed["decision_directive"]["suggested_action"] == (
        "apply reviewed tenant response policy"
    )
    assert confirmed["retrieval_enabled"] is True

    lineage = json.loads(
        (output_dir / "06-decision-lineage.json").read_text(encoding="utf-8")
    )
    transition = lineage["evaluation"]["decision_transition"]
    assert transition["before"]["verdict"] == "unknown"
    assert transition["after"]["verdict"] == "suspicious"
    assert transition["after"]["suggested_action"] == (
        "apply reviewed tenant response policy"
    )
    assert transition["transition_kind"] == "overridden"
    assert lineage["evaluation"].get("authorization") is None
    assert lineage["evaluation"].get("execution") is None
