"""Schema and domain validation for SOC Agent runtime outputs."""

from __future__ import annotations

from soc_agent.contracts import AnalysisResult, Decision


def validate_analysis_result(result: AnalysisResult) -> AnalysisResult:
    """Materialize stable decision support before policy consumes a result."""

    evidence_refs = list(result.decision_evidence_refs)
    if not evidence_refs:
        evidence_refs = list(dict.fromkeys(reference for item in result.reasoning for reference in item.evidence_refs))[:20]
    reasoning_refs = list(result.decision_reasoning_refs)
    if not reasoning_refs:
        reasoning_refs = [item.reasoning_id for item in result.reasoning[:20]]
    if not evidence_refs or not reasoning_refs:
        raise ValueError("analysis result requires explicit decision support references")
    return result.model_copy(
        update={
            "decision_evidence_refs": evidence_refs,
            "decision_reasoning_refs": reasoning_refs,
        }
    )


def validate_decision(decision: Decision) -> Decision:
    """Enforce Alpha domain rules on final decisions."""

    if decision.automation_allowed:
        raise ValueError("Alpha never allows automated production actions")
    return decision
