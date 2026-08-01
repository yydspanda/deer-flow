from __future__ import annotations

from copy import deepcopy

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_analyzer_output_review import (
    build_analyzer_output_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    canonical_sha256,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_decision_policy_review import (
    build_decision_policy_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_evidence_grounding_review import (
    build_evidence_grounding_review,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_analyzer_output_review import (
    _Client,
    _d5_review,
)

from soc_agent.llm import JsonLLMAnalyzer


def _d5_d7_d8_reviews(*, leak_description: bool) -> tuple[dict, dict, dict]:
    d5_review = _d5_review()
    d7_review = build_analyzer_output_review(
        d5_review,
        alert_id=1,
        analyzer=JsonLLMAnalyzer(
            client=_Client(),
            model_name="test-live-model",
        ),
    )
    if leak_description:
        d7_review = deepcopy(d7_review)
        foreign_rule_code = d5_review["llm_analysis_request"]["detection"]["rule_code"]
        d7_review["analysis_result"]["evidence"][0]["description"] += (
            f"，并命中规则 {foreign_rule_code}"
        )
        d7_review["analysis_result_sha256"] = canonical_sha256(
            d7_review["analysis_result"]
        )
    d8_review = build_evidence_grounding_review(
        d5_review,
        d7_review,
        alert_id=1,
    )
    return d5_review, d7_review, d8_review


def test_decision_review_proves_blocked_grounding_is_fail_closed() -> None:
    d5_review, d7_review, d8_review = _d5_d7_d8_reviews(leak_description=True)

    review = build_decision_policy_review(
        d5_review,
        d7_review,
        d8_review,
        alert_id=1,
    )

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert review["decision_gate"]["status"] == "guarded_review_required"
    assert review["decision"]["verdict"] == d7_review["analysis_result"]["verdict"]
    assert review["decision"]["evidence_state"] == "degraded"
    assert review["decision"]["needs_review"] is True
    assert "ungrounded_analysis_evidence" in review["decision"]["review_reasons"]
    assert "confidence_not_calibrated" in review["decision"]["review_reasons"]
    assert review["decision"]["automation_allowed"] is False
    assert review["decision_gate"]["tenant_disposition_evaluated"] is False
    assert "llm_call" in review["scope"]["not_performed"]


def test_decision_review_rejects_broken_d8_lineage() -> None:
    d5_review, d7_review, d8_review = _d5_d7_d8_reviews(leak_description=False)
    broken_d8 = deepcopy(d8_review)
    broken_d8["input"]["d7_analysis_result_sha256"] = "wrong-hash"

    review = build_decision_policy_review(
        d5_review,
        d7_review,
        broken_d8,
        alert_id=1,
    )

    assert review["acceptance"]["status"] == "failed"
    assert review["acceptance"]["failed_checks"] == ["d8_links_exact_d7_analysis"]
