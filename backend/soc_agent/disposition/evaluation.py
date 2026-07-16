"""Deterministic shadow-disposition sampling and gate evaluation."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence

from soc_agent.contracts import (
    AuthorizationEnrichmentRecord,
    AuthorizationSourceFreshness,
    SocDispositionEvaluationGatePolicy,
    SocDispositionEvaluationGateStatus,
    SocDispositionEvaluationMetric,
    SocDispositionEvaluationRecommendation,
    SocDispositionEvaluationReport,
    SocDispositionEvaluationScope,
    SocDispositionFactFanout,
    SocDispositionOutcomeRecord,
    SocDispositionOutcomeReviewKind,
    SocDispositionOutcomeSource,
    SocDispositionOutcomeStatus,
    SocDispositionProposalRecord,
    SocDispositionSampleManifest,
)
from soc_agent.utils.hashing import stable_hash


def disposition_evaluation_scope_hash(scope: SocDispositionEvaluationScope) -> str:
    """Return the stable identity of one tenant/version/time cohort."""

    return stable_hash(scope.model_dump(mode="json"))


def scoped_disposition_proposals(
    proposals: Sequence[SocDispositionProposalRecord],
    enrichments: Mapping[str, AuthorizationEnrichmentRecord],
    scope: SocDispositionEvaluationScope,
) -> tuple[list[SocDispositionProposalRecord], list[str]]:
    """Select one exact cohort and expose broken proposal/enrichment lineage."""

    selected: list[SocDispositionProposalRecord] = []
    warnings: list[str] = []
    for proposal in proposals:
        if proposal.policy_version != scope.proposal_policy_version:
            continue
        if not scope.window_start <= proposal.created_at < scope.window_end:
            continue
        enrichment = enrichments.get(proposal.source_enrichment_id)
        if enrichment is None:
            warnings.append(f"proposal {proposal.proposal_id} is missing source enrichment {proposal.source_enrichment_id}")
            continue
        if enrichment.matcher_policy_version != scope.matcher_policy_version:
            continue
        if enrichment.query.tenant_id != scope.tenant_id or enrichment.query.environment != scope.environment:
            continue
        selected.append(proposal)
    return sorted(selected, key=lambda item: (item.created_at, item.proposal_id)), warnings


def select_disposition_review_sample(
    proposals: Sequence[SocDispositionProposalRecord],
    *,
    sample_size: int,
    selection_seed_hash: str,
) -> list[SocDispositionProposalRecord]:
    """Select a reproducible sample through hash ranking, independent of input order."""

    if sample_size < 1:
        raise ValueError("sample_size must be positive")
    unique = {proposal.proposal_id: proposal for proposal in proposals}
    if sample_size > len(unique):
        raise ValueError("sample_size cannot exceed scoped proposal population")
    return sorted(
        unique.values(),
        key=lambda proposal: (stable_hash({"seed": selection_seed_hash, "proposal_id": proposal.proposal_id}), proposal.proposal_id),
    )[:sample_size]


def evaluate_disposition_gate(
    proposals: Sequence[SocDispositionProposalRecord],
    enrichments: Mapping[str, AuthorizationEnrichmentRecord],
    outcomes: Sequence[SocDispositionOutcomeRecord],
    manifests: Sequence[SocDispositionSampleManifest],
    policy: SocDispositionEvaluationGatePolicy,
    *,
    dataset_complete: bool,
    warnings: Sequence[str] = (),
) -> SocDispositionEvaluationReport:
    """Measure EV-01 shadow quality without enabling or applying automation."""

    scoped, lineage_warnings = scoped_disposition_proposals(proposals, enrichments, policy.scope)
    proposal_by_id = {proposal.proposal_id: proposal for proposal in scoped}
    scoped_ids = set(proposal_by_id)
    scope_hash = disposition_evaluation_scope_hash(policy.scope)

    primary = _latest_outcomes(
        outcomes,
        review_kind=SocDispositionOutcomeReviewKind.ANALYST_RESOLUTION,
        proposal_ids=scoped_ids,
        accepted_sources=set(policy.accepted_primary_sources),
    )
    matching_manifests = [manifest for manifest in manifests if manifest.scope_hash == scope_hash]
    manifest_by_id = {manifest.sample_id: manifest for manifest in matching_manifests}
    sampled_population_ids = {proposal_id for manifest in matching_manifests for proposal_id in manifest.selected_proposal_ids if proposal_id in scoped_ids}
    sampled = _latest_sampled_outcomes(
        outcomes,
        proposal_ids=scoped_ids,
        manifests=manifest_by_id,
        accepted_sources=set(policy.accepted_sample_sources),
    )
    non_independent_sample_ids = {proposal_id for proposal_id, sampled_outcome in sampled.items() if proposal_id in primary and primary[proposal_id].reviewed_by.actor_id == sampled_outcome.reviewed_by.actor_id}
    sampled = {proposal_id: outcome for proposal_id, outcome in sampled.items() if proposal_id not in non_independent_sample_ids}

    confirmed_count = sum(item.outcome_status is SocDispositionOutcomeStatus.CONFIRMED for item in primary.values())
    overridden_count = sum(item.outcome_status is SocDispositionOutcomeStatus.OVERRIDDEN for item in primary.values())
    inconclusive_count = sum(item.outcome_status is SocDispositionOutcomeStatus.INCONCLUSIVE for item in primary.values())
    resolved_count = confirmed_count + overridden_count
    pending_count = len(scoped) - len(primary)
    resolution_rate = _ratio(resolved_count, len(scoped))
    shadow_precision = _ratio(confirmed_count, resolved_count)
    override_rate = _ratio(overridden_count, resolved_count)

    sampled_resolved = {proposal_id: item for proposal_id, item in sampled.items() if item.outcome_status in {SocDispositionOutcomeStatus.CONFIRMED, SocDispositionOutcomeStatus.OVERRIDDEN}}
    sampled_confirmed = sum(item.outcome_status is SocDispositionOutcomeStatus.CONFIRMED for item in sampled_resolved.values())
    sampled_precision = _ratio(sampled_confirmed, len(sampled_resolved))
    sample_coverage_rate = _ratio(len(sampled_resolved), len(sampled_population_ids))

    agreement_pairs = [
        (primary[proposal_id], sampled_item)
        for proposal_id, sampled_item in sampled_resolved.items()
        if proposal_id in primary and primary[proposal_id].outcome_status in {SocDispositionOutcomeStatus.CONFIRMED, SocDispositionOutcomeStatus.OVERRIDDEN}
    ]
    agreement_count = len(agreement_pairs)
    agreement_matches = sum(left.observed_disposition is right.observed_disposition for left, right in agreement_pairs)
    agreement_rate = _ratio(agreement_matches, agreement_count)

    freshness_pass_count = sum(_proposal_freshness_passes(proposal, enrichments) for proposal in scoped)
    freshness_pass_rate = _ratio(freshness_pass_count, len(scoped))
    fanout = _fact_fanout(scoped)
    maximum_fanout = max((item.proposal_count for item in fanout), default=0)

    metrics = [
        _metric("proposal_count", len(scoped), policy.minimum_proposal_count, ">="),
        _metric("resolved_count", resolved_count, policy.minimum_resolved_count, ">="),
        _metric("resolution_rate", resolution_rate, policy.minimum_resolution_rate, ">="),
        _metric("shadow_precision", shadow_precision, policy.minimum_shadow_precision, ">="),
        _metric("override_rate", override_rate, policy.maximum_override_rate, "<="),
        _metric("sampled_review_count", len(sampled_resolved), policy.minimum_sampled_review_count, ">="),
        _metric("sampled_precision", sampled_precision, policy.minimum_sampled_precision, ">="),
        _metric("sample_coverage_rate", sample_coverage_rate, policy.minimum_sample_coverage_rate, ">="),
        _metric("sample_agreement_count", agreement_count, policy.minimum_sample_agreement_count, ">="),
        _metric("sample_agreement_rate", agreement_rate, policy.minimum_sample_agreement_rate, ">="),
        _metric("freshness_pass_rate", freshness_pass_rate, policy.minimum_freshness_pass_rate, ">="),
        _metric("maximum_fact_version_fanout", maximum_fanout, policy.maximum_fact_version_fanout, "<="),
    ]
    count_metrics = {
        "proposal_count",
        "resolved_count",
        "sampled_review_count",
        "sample_agreement_count",
    }
    effective_dataset_complete = dataset_complete and not lineage_warnings
    insufficient = not effective_dataset_complete or any(not item.passed for item in metrics if item.name in count_metrics)
    failed = any(not item.passed for item in metrics)
    if insufficient:
        gate_status = SocDispositionEvaluationGateStatus.INSUFFICIENT_DATA
    elif failed:
        gate_status = SocDispositionEvaluationGateStatus.FAILED
    else:
        gate_status = SocDispositionEvaluationGateStatus.PASSED_SHADOW_EVALUATION
    eligible = gate_status is SocDispositionEvaluationGateStatus.PASSED_SHADOW_EVALUATION
    independence_warnings = [f"proposal {proposal_id} sampled review is not independent from primary reviewer" for proposal_id in sorted(non_independent_sample_ids)]
    all_warnings = [*warnings, *lineage_warnings, *independence_warnings]
    if not dataset_complete:
        all_warnings.append("repository result limit was reached; the evaluation dataset is incomplete")
    rollback_signals = [item.name for item in metrics if not item.passed and item.name not in count_metrics]

    dataset_hash = stable_hash(
        {
            "scope_hash": scope_hash,
            "proposal_ids": sorted(scoped_ids),
            "proposal_keys": sorted(item.proposal_key for item in scoped),
            "primary_outcome_ids": sorted(item.outcome_id for item in primary.values()),
            "sampled_outcome_ids": sorted(item.outcome_id for item in sampled.values()),
            "sample_manifest_ids": sorted(manifest_by_id),
        }
    )
    return SocDispositionEvaluationReport(
        policy=policy,
        scope_hash=scope_hash,
        dataset_hash=dataset_hash,
        dataset_complete=effective_dataset_complete,
        proposal_count=len(scoped),
        resolved_count=resolved_count,
        confirmed_count=confirmed_count,
        overridden_count=overridden_count,
        inconclusive_count=inconclusive_count,
        pending_count=pending_count,
        resolution_rate=resolution_rate,
        shadow_precision=shadow_precision,
        override_rate=override_rate,
        sampled_population_count=len(sampled_population_ids),
        sampled_review_count=len(sampled_resolved),
        sampled_precision=sampled_precision,
        sample_coverage_rate=sample_coverage_rate,
        sample_agreement_count=agreement_count,
        sample_agreement_rate=agreement_rate,
        freshness_pass_count=freshness_pass_count,
        freshness_pass_rate=freshness_pass_rate,
        maximum_fact_version_fanout=maximum_fanout,
        fact_fanout=fanout,
        metrics=metrics,
        gate_status=gate_status,
        recommendation=(SocDispositionEvaluationRecommendation.ELIGIBLE_FOR_GOVERNED_ROLLOUT_REVIEW if eligible else SocDispositionEvaluationRecommendation.HOLD_SHADOW),
        rollout_review_eligible=eligible,
        rollback_signals=rollback_signals,
        warnings=list(dict.fromkeys(all_warnings)),
    )


def _latest_outcomes(
    outcomes: Sequence[SocDispositionOutcomeRecord],
    *,
    review_kind: SocDispositionOutcomeReviewKind,
    proposal_ids: set[str],
    accepted_sources: set[SocDispositionOutcomeSource],
) -> dict[str, SocDispositionOutcomeRecord]:
    selected: dict[str, SocDispositionOutcomeRecord] = {}
    for outcome in outcomes:
        if outcome.review_kind is not review_kind or outcome.proposal_id not in proposal_ids or outcome.source not in accepted_sources:
            continue
        existing = selected.get(outcome.proposal_id)
        if existing is None or (outcome.observed_at, outcome.created_at, outcome.outcome_id) > (
            existing.observed_at,
            existing.created_at,
            existing.outcome_id,
        ):
            selected[outcome.proposal_id] = outcome
    return selected


def _latest_sampled_outcomes(
    outcomes: Sequence[SocDispositionOutcomeRecord],
    *,
    proposal_ids: set[str],
    manifests: Mapping[str, SocDispositionSampleManifest],
    accepted_sources: set[SocDispositionOutcomeSource],
) -> dict[str, SocDispositionOutcomeRecord]:
    candidates = []
    for outcome in outcomes:
        if outcome.review_kind is not SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW:
            continue
        manifest = manifests.get(outcome.sample_id or "")
        if manifest is None or outcome.proposal_id not in proposal_ids:
            continue
        if outcome.proposal_id not in manifest.selected_proposal_ids:
            continue
        candidates.append(outcome)
    return _latest_outcomes(
        candidates,
        review_kind=SocDispositionOutcomeReviewKind.SAMPLED_QUALITY_REVIEW,
        proposal_ids=proposal_ids,
        accepted_sources=accepted_sources,
    )


def _proposal_freshness_passes(
    proposal: SocDispositionProposalRecord,
    enrichments: Mapping[str, AuthorizationEnrichmentRecord],
) -> bool:
    enrichment = enrichments.get(proposal.source_enrichment_id)
    if enrichment is None or not enrichment.match_result.source_freshness:
        return False
    return all(status in {AuthorizationSourceFreshness.FRESH, AuthorizationSourceFreshness.NOT_REQUIRED} for status in enrichment.match_result.source_freshness)


def _fact_fanout(proposals: Sequence[SocDispositionProposalRecord]) -> list[SocDispositionFactFanout]:
    counts: Counter[tuple[str, str]] = Counter()
    for proposal in proposals:
        counts.update({(ref.fact_id, ref.fact_version_id) for ref in proposal.source_fact_refs})
    return [
        SocDispositionFactFanout(
            fact_id=fact_id,
            fact_version_id=fact_version_id,
            proposal_count=count,
        )
        for (fact_id, fact_version_id), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metric(
    name: str,
    value: float | int | None,
    threshold: float | int,
    comparator: str,
) -> SocDispositionEvaluationMetric:
    numeric_value = float(value) if value is not None else None
    numeric_threshold = float(threshold)
    passed = numeric_value is not None and (numeric_value >= numeric_threshold if comparator == ">=" else numeric_value <= numeric_threshold)
    return SocDispositionEvaluationMetric(
        name=name,
        value=numeric_value,
        threshold=numeric_threshold,
        comparator=comparator,
        passed=passed,
        reason=(f"{name}={numeric_value} must be {comparator} {numeric_threshold}" if numeric_value is not None else f"{name} is unavailable and cannot satisfy {comparator} {numeric_threshold}"),
    )


__all__ = [
    "disposition_evaluation_scope_hash",
    "evaluate_disposition_gate",
    "scoped_disposition_proposals",
    "select_disposition_review_sample",
]
