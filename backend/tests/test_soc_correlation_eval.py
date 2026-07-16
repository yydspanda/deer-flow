from __future__ import annotations

import json

import pytest

from soc_agent.cli import main
from soc_agent.contracts import CORRELATION_SCORING_POLICY_VERSION
from soc_agent.eval import (
    DEFAULT_CORRELATION_EVAL_FIXTURE,
    CorrelationRelationship,
    load_correlation_eval_fixture,
    load_correlation_eval_report,
    run_correlation_eval,
)


def test_correlation_eval_fixture_covers_distinct_relationship_labels() -> None:
    fixture = load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE)

    labels = {candidate.relationship for case in fixture.cases for candidate in case.candidates}

    assert fixture.schema_version == "soc.correlation_eval_fixture_set.v1"
    assert fixture.scoring_policy_version == CORRELATION_SCORING_POLICY_VERSION
    assert labels == set(CorrelationRelationship)
    assert fixture.query_limit >= max(len(case.candidates) for case in fixture.cases)


def test_correlation_eval_reports_retrieval_dedup_fanout_and_evidence_boundaries() -> None:
    fixture = load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE)

    report = run_correlation_eval(fixture)

    assert report.schema_version == "soc.correlation_eval_report.v1"
    assert report.scoring_policy_version == CORRELATION_SCORING_POLICY_VERSION
    assert report.case_count == 2
    assert report.pair_count == 8
    assert report.label_counts == {
        "related_distinct": 2,
        "same_incident": 2,
        "unrelated": 4,
    }
    assert report.retrieval_metrics.true_positive == 4
    assert report.retrieval_metrics.false_positive == 2
    assert report.retrieval_metrics.false_negative == 0
    assert report.retrieval_metrics.true_negative == 2
    assert report.retrieval_metrics.precision == pytest.approx(2 / 3)
    assert report.retrieval_metrics.recall == 1.0
    assert report.dedup_metrics.true_positive == 2
    assert report.dedup_metrics.false_positive == 1
    assert report.dedup_metrics.false_negative == 0
    assert report.dedup_metrics.true_negative == 5
    assert report.dedup_metrics.precision == pytest.approx(2 / 3)
    assert report.dedup_metrics.recall == 1.0
    assert report.candidate_fan_out.total_retrieved == 6
    assert report.candidate_fan_out.maximum_per_case == 3
    assert report.candidate_fan_out.excess_unrelated_count == 2
    assert report.reason_distribution == {
        "category": 4,
        "detection_key": 4,
        "entity_key": 11,
        "rule_code": 4,
        "source_type": 6,
    }
    assert report.reusable_evidence_count == 6
    assert report.evidence_lineage_leakage_count == 0
    assert report.unrelated_evidence_exposure_count == 2
    assert report.integrity_passed is True
    assert report.shadow_dedup_allowed is False
    assert report.decision_impact == "none"

    endpoint = next(item for item in report.results if item.case_id == "endpoint-credential-access")
    endpoint_same = next(pair for pair in endpoint.pairs if pair.relationship is CorrelationRelationship.SAME_INCIDENT)
    endpoint_related = next(pair for pair in endpoint.pairs if pair.relationship is CorrelationRelationship.RELATED_DISTINCT)
    assert endpoint_same.score == 149
    assert endpoint_same.reusable_evidence_ids == ["EVI-EVAL-ENDPOINT-SAME"]
    assert "EVI-EVAL-ENDPOINT-CURRENT" not in endpoint_same.reusable_evidence_ids
    assert endpoint_same.reusable_evidence_run_ids == [endpoint_same.candidate_run_id]
    assert endpoint_related.score == 134
    assert endpoint_related.expected_relevant is True
    assert endpoint_related.expected_duplicate is False
    assert endpoint_related.predicted_duplicate_at_threshold is True

    network = next(item for item in report.results if item.case_id == "network-reverse-shell")
    network_related = next(pair for pair in network.pairs if pair.relationship is CorrelationRelationship.RELATED_DISTINCT)
    assert network_related.score == 119
    assert network_related.retrieved is True
    assert network_related.predicted_duplicate_at_threshold is False


def test_correlation_eval_rejects_stale_scoring_policy() -> None:
    fixture = load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE)
    stale = fixture.model_copy(update={"scoring_policy_version": "soc.correlation.scoring.stale"})

    with pytest.raises(ValueError, match="different scoring policy version"):
        run_correlation_eval(stale)


def test_correlation_eval_replay_diff_ignores_generated_at_and_detects_metric_change(
    tmp_path,
) -> None:
    fixture = load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE)
    baseline = run_correlation_eval(fixture)
    baseline_path = tmp_path / "correlation-baseline.json"
    baseline_path.write_text(baseline.model_dump_json(indent=2), encoding="utf-8")

    loaded = load_correlation_eval_report(baseline_path)
    unchanged = run_correlation_eval(fixture, baseline=loaded)

    assert unchanged.diff is not None
    assert unchanged.diff.changed is False
    assert unchanged.diff.changed_pair_keys == []
    assert unchanged.diff.reason_distribution_delta == {}

    lower_precision = baseline.model_copy(update={"retrieval_metrics": baseline.retrieval_metrics.model_copy(update={"precision": 0.5})})
    changed = run_correlation_eval(fixture, baseline=lower_precision)

    assert changed.diff is not None
    assert changed.diff.changed is True
    assert changed.diff.retrieval_precision_delta == pytest.approx(1 / 6)


def test_cli_eval_correlation_outputs_read_only_baseline(capsys) -> None:
    code = main(["eval", "correlation", "--pretty"])

    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert code == 0
    assert data["schema_version"] == "soc.correlation_eval_report.v1"
    assert data["fixture_set_id"] == "vendor-neutral-correlation-baseline-v1"
    assert data["retrieval_metrics"]["false_positive"] == 2
    assert data["dedup_metrics"]["false_positive"] == 1
    assert data["evidence_lineage_leakage_count"] == 0
    assert data["shadow_dedup_allowed"] is False
    assert data["decision_impact"] == "none"


def test_cli_eval_correlation_can_diff_prior_report(tmp_path, capsys) -> None:
    fixture = load_correlation_eval_fixture(DEFAULT_CORRELATION_EVAL_FIXTURE)
    baseline_path = tmp_path / "correlation-baseline.json"
    baseline_path.write_text(
        run_correlation_eval(fixture).model_dump_json(indent=2),
        encoding="utf-8",
    )

    code = main(
        [
            "eval",
            "correlation",
            "--baseline-json",
            str(baseline_path),
            "--pretty",
        ]
    )

    data = json.loads(capsys.readouterr().out)

    assert code == 0
    assert data["diff"]["changed"] is False
    assert data["diff"]["changed_pair_keys"] == []
