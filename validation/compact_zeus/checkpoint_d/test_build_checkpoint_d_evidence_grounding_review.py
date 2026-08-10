from __future__ import annotations

from copy import deepcopy

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_analyzer_output_review import (
    build_analyzer_output_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    canonical_sha256,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_evidence_grounding_review import (
    build_evidence_grounding_review,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_analyzer_output_review import (
    _Client,
    _d5_review,
)

from soc_agent.contracts import AnalysisEvidenceGroundingStatus
from soc_agent.llm import JsonLLMAnalyzer


def _d5_and_d7_reviews() -> tuple[dict, dict]:
    d5_review = _d5_review()
    d7_review = build_analyzer_output_review(
        d5_review,
        alert_id=1,
        analyzer=JsonLLMAnalyzer(
            client=_Client(),
            model_name="test-live-model",
        ),
    )
    return d5_review, d7_review


def test_grounding_review_accepts_fully_grounded_d7_output() -> None:
    d5_review, d7_review = _d5_and_d7_reviews()

    review = build_evidence_grounding_review(
        d5_review,
        d7_review,
        alert_id=1,
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert review["quality_gate"]["status"] == "ready"
    assert review["grounding_report"]["grounded_count"] == 1
    assert review["grounding_report"]["ungrounded_count"] == 0
    assert review["scenario_support_review"][0]["all_references_grounded"]
    assert "decision_policy" in review["scope"]["not_performed"]


def test_grounding_review_blocks_changed_fact_without_repairing_it() -> None:
    d5_review, d7_review = _d5_and_d7_reviews()
    mutated_d7 = deepcopy(d7_review)
    mutated_d7["analysis_result"]["evidence"][0]["value"] = "2"
    mutated_d7["analysis_result_sha256"] = canonical_sha256(
        mutated_d7["analysis_result"]
    )

    review = build_evidence_grounding_review(
        d5_review,
        mutated_d7,
        alert_id=1,
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["quality_gate"]["status"] == "blocked"
    assert review["quality_gate"]["blocking_reasons"] == [
        "ungrounded_analysis_evidence",
        "ungrounded_analysis_reasoning",
    ]
    assert review["grounding_report"]["description_leakage_count"] == 0
    grounding_item = review["evidence_review"][0]["grounding"]
    assert (
        grounding_item["status"]
        == AnalysisEvidenceGroundingStatus.VALUE_NOT_FOUND.value
    )
    assert review["scenario_support_review"][0]["rejected_evidence_refs"]
    assert review["scenario_support_review"][0]["rejected_reasoning_refs"] == ["R-01"]
