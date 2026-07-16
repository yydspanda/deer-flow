"""Versioned, read-only evaluation for deterministic alert correlation."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from soc_agent.contracts import (
    CORRELATION_SCORING_POLICY_VERSION,
    AlertSummary,
    CorrelationQuery,
    InvestigationEvidence,
)
from soc_agent.core import (
    InMemoryAlertSummaryRepository,
    InMemoryInvestigationEvidenceRepository,
    SocCorrelationService,
)

DEFAULT_CORRELATION_EVAL_FIXTURE = Path(__file__).resolve().parents[2] / "samples" / "eval" / "correlation" / "vendor_neutral_baseline_v1.json"


class CorrelationRelationship(StrEnum):
    """Human label separating retrieval relevance from duplicate identity."""

    SAME_INCIDENT = "same_incident"
    RELATED_DISTINCT = "related_distinct"
    UNRELATED = "unrelated"


class CorrelationEvalCandidateFixture(BaseModel):
    """One labeled historical candidate and its run-scoped evidence."""

    model_config = ConfigDict(extra="forbid")

    relationship: CorrelationRelationship
    rationale: str = Field(min_length=1)
    summary: AlertSummary
    evidence: list[InvestigationEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence_lineage(self) -> CorrelationEvalCandidateFixture:
        for item in self.evidence:
            if item.run_id != self.summary.run_id:
                raise ValueError("candidate evidence run_id must equal candidate summary run_id")
            if item.alert_id != self.summary.alert_id:
                raise ValueError("candidate evidence alert_id must equal candidate summary alert_id")
        return self


class CorrelationEvalCaseFixture(BaseModel):
    """One current subject and its labeled historical candidate set."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    scenario: str = Field(min_length=1)
    subject: AlertSummary
    subject_evidence: list[InvestigationEvidence] = Field(default_factory=list)
    candidates: list[CorrelationEvalCandidateFixture] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_lineage(self) -> CorrelationEvalCaseFixture:
        candidate_run_ids = [item.summary.run_id for item in self.candidates]
        if len(candidate_run_ids) != len(set(candidate_run_ids)):
            raise ValueError("candidate run_id values must be unique within a correlation eval case")
        if self.subject.run_id in candidate_run_ids:
            raise ValueError("subject run_id cannot also be a candidate run_id")
        for item in self.subject_evidence:
            if item.run_id != self.subject.run_id:
                raise ValueError("subject evidence run_id must equal subject summary run_id")
            if item.alert_id != self.subject.alert_id:
                raise ValueError("subject evidence alert_id must equal subject summary alert_id")
        return self


class CorrelationEvalFixtureSet(BaseModel):
    """Versioned, human-labeled corpus for one scoring policy."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.correlation_eval_fixture_set.v1"] = "soc.correlation_eval_fixture_set.v1"
    fixture_set_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scoring_policy_version: str = Field(min_length=1)
    dedup_score_threshold: float = Field(gt=0)
    query_limit: int = Field(default=10, ge=1, le=100)
    candidate_limit: int = Field(default=200, ge=1, le=1000)
    evidence_limit_per_match: int = Field(default=5, ge=0, le=50)
    cases: list[CorrelationEvalCaseFixture] = Field(min_length=1)
    fixture_path: str | None = None

    @model_validator(mode="after")
    def validate_corpus_shape(self) -> CorrelationEvalFixtureSet:
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("correlation eval case_id values must be unique")
        labels = {candidate.relationship for case in self.cases for candidate in case.candidates}
        missing = set(CorrelationRelationship) - labels
        if missing:
            missing_values = ", ".join(sorted(item.value for item in missing))
            raise ValueError(f"correlation eval corpus is missing relationship labels: {missing_values}")
        largest_case = max(len(case.candidates) for case in self.cases)
        if self.query_limit < largest_case or self.candidate_limit < largest_case:
            raise ValueError("query_limit and candidate_limit must cover every labeled candidate in each eval case")
        return self


class CorrelationEvalBinaryMetrics(BaseModel):
    """Pairwise binary classification metrics with explicit confusion counts."""

    true_positive: int = Field(ge=0)
    false_positive: int = Field(ge=0)
    false_negative: int = Field(ge=0)
    true_negative: int = Field(ge=0)
    positive_support: int = Field(ge=0)
    negative_support: int = Field(ge=0)
    precision: float = Field(ge=0.0, le=1.0)
    recall: float = Field(ge=0.0, le=1.0)
    f1: float = Field(ge=0.0, le=1.0)


class CorrelationEvalPairResult(BaseModel):
    """Observed scorer/service behavior for one labeled candidate pair."""

    candidate_run_id: str
    candidate_alert_id: str
    relationship: CorrelationRelationship
    rationale: str
    expected_relevant: bool
    expected_duplicate: bool
    retrieved: bool
    score: float = Field(ge=0.0)
    matched_reasons: list[str] = Field(default_factory=list)
    predicted_duplicate_at_threshold: bool = False
    reusable_evidence_ids: list[str] = Field(default_factory=list)
    reusable_evidence_run_ids: list[str] = Field(default_factory=list)
    evidence_lineage_leakage_ids: list[str] = Field(default_factory=list)


class CorrelationEvalCaseResult(BaseModel):
    """Per-subject retrieval, dedup, fan-out, and lineage measurements."""

    case_id: str
    scenario: str
    subject_run_id: str
    candidate_count: int = Field(ge=0)
    retrieved_count: int = Field(ge=0)
    expected_relevant_count: int = Field(ge=0)
    expected_same_incident_count: int = Field(ge=0)
    reusable_evidence_count: int = Field(ge=0)
    unrelated_evidence_exposure_count: int = Field(ge=0)
    evidence_lineage_leakage_count: int = Field(ge=0)
    unexpected_match_run_ids: list[str] = Field(default_factory=list)
    retrieval_metrics: CorrelationEvalBinaryMetrics
    dedup_metrics: CorrelationEvalBinaryMetrics
    pairs: list[CorrelationEvalPairResult] = Field(default_factory=list)


class CorrelationEvalFanOut(BaseModel):
    """How many historical candidates one subject exposes downstream."""

    total_retrieved: int = Field(ge=0)
    minimum_per_case: int = Field(ge=0)
    maximum_per_case: int = Field(ge=0)
    mean_per_case: float = Field(ge=0.0)
    excess_unrelated_count: int = Field(ge=0)


class CorrelationEvalDiff(BaseModel):
    """Replay diff that ignores timestamps and preserves policy/corpus changes."""

    baseline_schema_version: str
    baseline_fixture_set_id: str
    baseline_scoring_policy_version: str
    fixture_set_changed: bool = False
    scoring_policy_changed: bool = False
    pair_count_delta: int = 0
    retrieval_precision_delta: float = 0.0
    retrieval_recall_delta: float = 0.0
    dedup_precision_delta: float = 0.0
    dedup_recall_delta: float = 0.0
    maximum_fan_out_delta: int = 0
    excess_unrelated_count_delta: int = 0
    evidence_lineage_leakage_count_delta: int = 0
    unrelated_evidence_exposure_count_delta: int = 0
    reason_distribution_delta: dict[str, int] = Field(default_factory=dict)
    added_pair_keys: list[str] = Field(default_factory=list)
    removed_pair_keys: list[str] = Field(default_factory=list)
    changed_pair_keys: list[str] = Field(default_factory=list)
    changed: bool = False


class CorrelationEvalReport(BaseModel):
    """Read-only baseline report; never a rollout or suppression approval."""

    schema_version: str = "soc.correlation_eval_report.v1"
    fixture_schema_version: str
    fixture_set_id: str
    fixture_path: str | None = None
    scoring_policy_version: str
    dedup_score_threshold: float
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    case_count: int = Field(ge=0)
    pair_count: int = Field(ge=0)
    label_counts: dict[str, int] = Field(default_factory=dict)
    retrieved_pair_count: int = Field(ge=0)
    retrieval_metrics: CorrelationEvalBinaryMetrics
    dedup_metrics: CorrelationEvalBinaryMetrics
    candidate_fan_out: CorrelationEvalFanOut
    reason_distribution: dict[str, int] = Field(default_factory=dict)
    reason_distribution_by_relationship: dict[str, dict[str, int]] = Field(default_factory=dict)
    reusable_evidence_count: int = Field(ge=0)
    evidence_lineage_leakage_count: int = Field(ge=0)
    unrelated_evidence_exposure_count: int = Field(ge=0)
    integrity_passed: bool = False
    shadow_dedup_allowed: Literal[False] = False
    decision_impact: Literal["none"] = "none"
    limitations: list[str] = Field(default_factory=list)
    diff: CorrelationEvalDiff | None = None
    results: list[CorrelationEvalCaseResult] = Field(default_factory=list)


def load_correlation_eval_fixture(
    path: str | Path = DEFAULT_CORRELATION_EVAL_FIXTURE,
) -> CorrelationEvalFixtureSet:
    """Load and validate one versioned correlation evaluation corpus."""

    fixture_path = Path(path)
    try:
        data = json.loads(fixture_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read correlation eval fixture {fixture_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid correlation eval fixture JSON {fixture_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"correlation eval fixture must be a JSON object: {fixture_path}")
    fixture = CorrelationEvalFixtureSet.model_validate(data)
    if fixture.scoring_policy_version != CORRELATION_SCORING_POLICY_VERSION:
        raise ValueError(f"correlation eval fixture scoring_policy_version does not match the current scorer: {fixture.scoring_policy_version} != {CORRELATION_SCORING_POLICY_VERSION}")
    return fixture.model_copy(update={"fixture_path": str(fixture_path)})


def load_correlation_eval_report(path: str | Path) -> CorrelationEvalReport:
    """Load a prior correlation report for deterministic replay diff."""

    report_path = Path(path)
    try:
        data = json.loads(report_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read correlation eval baseline {report_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid correlation eval baseline JSON {report_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"correlation eval baseline must be a JSON object: {report_path}")
    return CorrelationEvalReport.model_validate(data)


def run_correlation_eval(
    fixture: CorrelationEvalFixtureSet,
    *,
    baseline: CorrelationEvalReport | None = None,
) -> CorrelationEvalReport:
    """Measure current correlation behavior without writing production state."""

    if fixture.scoring_policy_version != CORRELATION_SCORING_POLICY_VERSION:
        raise ValueError("correlation eval fixture targets a different scoring policy version")
    results = [_run_case(fixture, case) for case in fixture.cases]
    pairs = [pair for result in results for pair in result.pairs]
    label_counts = Counter(pair.relationship.value for pair in pairs)
    retrieved_counts = [result.retrieved_count for result in results]
    reason_distribution = _reason_distribution(pairs)
    reason_distribution_by_relationship = {relationship.value: _reason_distribution([pair for pair in pairs if pair.relationship is relationship]) for relationship in CorrelationRelationship}
    lineage_leakage_count = sum(result.evidence_lineage_leakage_count for result in results)
    unexpected_match_count = sum(len(result.unexpected_match_run_ids) for result in results)
    report = CorrelationEvalReport(
        fixture_schema_version=fixture.schema_version,
        fixture_set_id=fixture.fixture_set_id,
        fixture_path=fixture.fixture_path,
        scoring_policy_version=fixture.scoring_policy_version,
        dedup_score_threshold=fixture.dedup_score_threshold,
        case_count=len(results),
        pair_count=len(pairs),
        label_counts=dict(sorted(label_counts.items())),
        retrieved_pair_count=sum(pair.retrieved for pair in pairs),
        retrieval_metrics=_binary_metrics([(pair.expected_relevant, pair.retrieved) for pair in pairs]),
        dedup_metrics=_binary_metrics([(pair.expected_duplicate, pair.predicted_duplicate_at_threshold) for pair in pairs]),
        candidate_fan_out=CorrelationEvalFanOut(
            total_retrieved=sum(retrieved_counts),
            minimum_per_case=min(retrieved_counts),
            maximum_per_case=max(retrieved_counts),
            mean_per_case=sum(retrieved_counts) / len(retrieved_counts),
            excess_unrelated_count=sum(pair.retrieved and not pair.expected_relevant for pair in pairs),
        ),
        reason_distribution=reason_distribution,
        reason_distribution_by_relationship=reason_distribution_by_relationship,
        reusable_evidence_count=sum(result.reusable_evidence_count for result in results),
        evidence_lineage_leakage_count=lineage_leakage_count,
        unrelated_evidence_exposure_count=sum(result.unrelated_evidence_exposure_count for result in results),
        integrity_passed=lineage_leakage_count == 0 and unexpected_match_count == 0,
        limitations=[
            "The corpus is controlled and does not estimate production prevalence or analyst workload.",
            "The current scorer uses deterministic summary-field overlap and has no incident-identity proof.",
            "The dedup threshold is offline diagnostic input only; it cannot suppress alerts or close ReviewQueue items.",
        ],
        results=results,
    )
    if baseline is not None:
        return report.model_copy(update={"diff": _correlation_eval_diff(baseline, report)})
    return report


def _run_case(
    fixture: CorrelationEvalFixtureSet,
    case: CorrelationEvalCaseFixture,
) -> CorrelationEvalCaseResult:
    summaries = [case.subject, *(candidate.summary for candidate in case.candidates)]
    evidence = [
        *case.subject_evidence,
        *(item for candidate in case.candidates for item in candidate.evidence),
    ]
    service = SocCorrelationService(
        summary_repository=InMemoryAlertSummaryRepository(summaries),
        evidence_repository=InMemoryInvestigationEvidenceRepository(evidence),
    )
    correlation = service.correlate(
        CorrelationQuery(
            run_id=case.subject.run_id,
            limit=fixture.query_limit,
            candidate_limit=fixture.candidate_limit,
            evidence_limit_per_match=fixture.evidence_limit_per_match,
        )
    )
    if correlation.scoring_policy_version != fixture.scoring_policy_version:
        raise ValueError("correlation result did not report the fixture scoring policy version")

    matches_by_run = {match.summary.run_id: match for match in correlation.matches}
    candidate_run_ids = {candidate.summary.run_id for candidate in case.candidates}
    unexpected_match_run_ids = sorted(set(matches_by_run) - candidate_run_ids)
    pairs: list[CorrelationEvalPairResult] = []
    for candidate in case.candidates:
        match = matches_by_run.get(candidate.summary.run_id)
        reusable_evidence = match.reusable_evidence if match is not None else []
        leakage_ids = [item.evidence_id for item in reusable_evidence if item.run_id != candidate.summary.run_id]
        relationship = candidate.relationship
        score = match.score if match is not None else 0.0
        pairs.append(
            CorrelationEvalPairResult(
                candidate_run_id=candidate.summary.run_id,
                candidate_alert_id=candidate.summary.alert_id,
                relationship=relationship,
                rationale=candidate.rationale,
                expected_relevant=relationship is not CorrelationRelationship.UNRELATED,
                expected_duplicate=relationship is CorrelationRelationship.SAME_INCIDENT,
                retrieved=match is not None,
                score=score,
                matched_reasons=match.matched_reasons if match is not None else [],
                predicted_duplicate_at_threshold=(match is not None and score >= fixture.dedup_score_threshold),
                reusable_evidence_ids=[item.evidence_id for item in reusable_evidence],
                reusable_evidence_run_ids=sorted({item.run_id for item in reusable_evidence if item.run_id is not None}),
                evidence_lineage_leakage_ids=leakage_ids,
            )
        )

    return CorrelationEvalCaseResult(
        case_id=case.case_id,
        scenario=case.scenario,
        subject_run_id=case.subject.run_id,
        candidate_count=len(case.candidates),
        retrieved_count=len(correlation.matches),
        expected_relevant_count=sum(pair.expected_relevant for pair in pairs),
        expected_same_incident_count=sum(pair.expected_duplicate for pair in pairs),
        reusable_evidence_count=sum(len(pair.reusable_evidence_ids) for pair in pairs),
        unrelated_evidence_exposure_count=sum(len(pair.reusable_evidence_ids) for pair in pairs if pair.relationship is CorrelationRelationship.UNRELATED),
        evidence_lineage_leakage_count=sum(len(pair.evidence_lineage_leakage_ids) for pair in pairs),
        unexpected_match_run_ids=unexpected_match_run_ids,
        retrieval_metrics=_binary_metrics([(pair.expected_relevant, pair.retrieved) for pair in pairs]),
        dedup_metrics=_binary_metrics([(pair.expected_duplicate, pair.predicted_duplicate_at_threshold) for pair in pairs]),
        pairs=pairs,
    )


def _binary_metrics(
    observations: Sequence[tuple[bool, bool]],
) -> CorrelationEvalBinaryMetrics:
    true_positive = sum(expected and predicted for expected, predicted in observations)
    false_positive = sum(not expected and predicted for expected, predicted in observations)
    false_negative = sum(expected and not predicted for expected, predicted in observations)
    true_negative = sum(not expected and not predicted for expected, predicted in observations)
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    return CorrelationEvalBinaryMetrics(
        true_positive=true_positive,
        false_positive=false_positive,
        false_negative=false_negative,
        true_negative=true_negative,
        positive_support=true_positive + false_negative,
        negative_support=true_negative + false_positive,
        precision=precision,
        recall=recall,
        f1=_safe_ratio(2 * precision * recall, precision + recall),
    )


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def _reason_distribution(
    pairs: Sequence[CorrelationEvalPairResult],
) -> dict[str, int]:
    counts = Counter(reason.partition(":")[0] or "unknown" for pair in pairs if pair.retrieved for reason in pair.matched_reasons)
    return dict(sorted(counts.items()))


def _correlation_eval_diff(
    baseline: CorrelationEvalReport,
    current: CorrelationEvalReport,
) -> CorrelationEvalDiff:
    baseline_pairs = _pair_snapshots(baseline)
    current_pairs = _pair_snapshots(current)
    shared_pair_keys = set(baseline_pairs).intersection(current_pairs)
    reason_keys = set(baseline.reason_distribution).union(current.reason_distribution)
    reason_distribution_delta = {key: current.reason_distribution.get(key, 0) - baseline.reason_distribution.get(key, 0) for key in sorted(reason_keys) if current.reason_distribution.get(key, 0) != baseline.reason_distribution.get(key, 0)}
    diff = CorrelationEvalDiff(
        baseline_schema_version=baseline.schema_version,
        baseline_fixture_set_id=baseline.fixture_set_id,
        baseline_scoring_policy_version=baseline.scoring_policy_version,
        fixture_set_changed=baseline.fixture_set_id != current.fixture_set_id,
        scoring_policy_changed=(baseline.scoring_policy_version != current.scoring_policy_version),
        pair_count_delta=current.pair_count - baseline.pair_count,
        retrieval_precision_delta=(current.retrieval_metrics.precision - baseline.retrieval_metrics.precision),
        retrieval_recall_delta=(current.retrieval_metrics.recall - baseline.retrieval_metrics.recall),
        dedup_precision_delta=(current.dedup_metrics.precision - baseline.dedup_metrics.precision),
        dedup_recall_delta=current.dedup_metrics.recall - baseline.dedup_metrics.recall,
        maximum_fan_out_delta=(current.candidate_fan_out.maximum_per_case - baseline.candidate_fan_out.maximum_per_case),
        excess_unrelated_count_delta=(current.candidate_fan_out.excess_unrelated_count - baseline.candidate_fan_out.excess_unrelated_count),
        evidence_lineage_leakage_count_delta=(current.evidence_lineage_leakage_count - baseline.evidence_lineage_leakage_count),
        unrelated_evidence_exposure_count_delta=(current.unrelated_evidence_exposure_count - baseline.unrelated_evidence_exposure_count),
        reason_distribution_delta=reason_distribution_delta,
        added_pair_keys=sorted(set(current_pairs) - set(baseline_pairs)),
        removed_pair_keys=sorted(set(baseline_pairs) - set(current_pairs)),
        changed_pair_keys=sorted(key for key in shared_pair_keys if baseline_pairs[key] != current_pairs[key]),
    )
    changed_values = [
        diff.fixture_set_changed,
        diff.scoring_policy_changed,
        diff.pair_count_delta,
        diff.retrieval_precision_delta,
        diff.retrieval_recall_delta,
        diff.dedup_precision_delta,
        diff.dedup_recall_delta,
        diff.maximum_fan_out_delta,
        diff.excess_unrelated_count_delta,
        diff.evidence_lineage_leakage_count_delta,
        diff.unrelated_evidence_exposure_count_delta,
        diff.reason_distribution_delta,
        diff.added_pair_keys,
        diff.removed_pair_keys,
        diff.changed_pair_keys,
    ]
    return diff.model_copy(update={"changed": any(changed_values)})


def _pair_snapshots(report: CorrelationEvalReport) -> dict[str, tuple[object, ...]]:
    return {
        f"{case.case_id}:{pair.candidate_run_id}": (
            pair.relationship.value,
            pair.expected_relevant,
            pair.expected_duplicate,
            pair.retrieved,
            pair.score,
            tuple(pair.matched_reasons),
            pair.predicted_duplicate_at_threshold,
            tuple(pair.reusable_evidence_ids),
            tuple(pair.evidence_lineage_leakage_ids),
        )
        for case in report.results
        for pair in case.pairs
    }


__all__ = [
    "DEFAULT_CORRELATION_EVAL_FIXTURE",
    "CorrelationEvalBinaryMetrics",
    "CorrelationEvalCandidateFixture",
    "CorrelationEvalCaseFixture",
    "CorrelationEvalCaseResult",
    "CorrelationEvalDiff",
    "CorrelationEvalFanOut",
    "CorrelationEvalFixtureSet",
    "CorrelationEvalPairResult",
    "CorrelationEvalReport",
    "CorrelationRelationship",
    "load_correlation_eval_fixture",
    "load_correlation_eval_report",
    "run_correlation_eval",
]
