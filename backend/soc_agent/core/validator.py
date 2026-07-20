"""Schema and domain validation for SOC Agent runtime outputs."""

from __future__ import annotations

from soc_agent.contracts import AnalysisResult, Decision


def validate_analysis_result(result: AnalysisResult) -> AnalysisResult:
    """Validate LLM/stub analysis before decision logic consumes it."""

    if result.verdict == "false_positive" and result.confidence >= 0.9:
        if "review" not in result.recommended_action.lower():
            raise ValueError("high-confidence false positives still require review")
    return result


def validate_decision(decision: Decision) -> Decision:
    """Enforce Alpha domain rules on final decisions."""

    if decision.automation_allowed:
        raise ValueError("Alpha never allows automated production actions")
    return decision
