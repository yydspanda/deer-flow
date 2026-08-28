"""SQL aggregates for SOC product effectiveness and rule optimization."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from contextlib import AbstractContextManager
from typing import Any

from sqlalchemy import and_, case, func, literal, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from soc_agent.contracts import (
    SocBehaviorGroupEffectivenessAggregate,
    SocEffectivenessScope,
    SocMemoryEffectivenessAggregate,
    SocRuleEffectivenessAggregate,
    SocRuleEffectivenessSelector,
)
from soc_agent.db.models import (
    SocAlertSummaryRow,
    SocAnalysisRunRow,
    SocDecisionAuditLogRow,
    SocDecisionTransitionRow,
    SocDispositionOutcomeRow,
    SocDispositionTransitionRow,
    SocExternalDispositionRow,
    SocMemoryCandidateRow,
    SocMemoryFeedbackRow,
    SocMemoryPatternObservationRow,
    SocMemoryRecordRow,
    SocMemoryUseRow,
)
from soc_agent.protocols import SocEffectivenessRepositoryError

_TRUE_POSITIVE = "true_positive"
_FALSE_POSITIVE = "false_positive"
_RESOLVED_VERDICTS = (_TRUE_POSITIVE, _FALSE_POSITIVE)
_TRANSFERRED = "escalated"
_AUTO_IGNORE_DISPOSITIONS = ("ignored", "closed_false_positive")


class SqlAlchemySocEffectivenessRepository:
    """Read exact latest-run aggregates without loading full alert payloads."""

    def __init__(self, session_factory: Callable[[], AbstractContextManager[Session]]) -> None:
        self._session_factory = session_factory

    def read_rule_aggregates(
        self,
        scope: SocEffectivenessScope,
    ) -> list[SocRuleEffectivenessAggregate]:
        try:
            with self._session_factory() as session:
                return self._read_rule_aggregates(session, scope)
        except SQLAlchemyError as exc:
            raise SocEffectivenessRepositoryError("SOC effectiveness database query failed") from exc

    def read_behavior_group_aggregates(
        self,
        scope: SocEffectivenessScope,
        selector: SocRuleEffectivenessSelector,
    ) -> list[SocBehaviorGroupEffectivenessAggregate]:
        try:
            with self._session_factory() as session:
                return self._read_behavior_group_aggregates(
                    session,
                    scope,
                    selector,
                )
        except SQLAlchemyError as exc:
            raise SocEffectivenessRepositoryError("SOC effectiveness behavior query failed") from exc

    def read_memory_aggregates(
        self,
        scope: SocEffectivenessScope,
        selector: SocRuleEffectivenessSelector,
    ) -> list[SocMemoryEffectivenessAggregate]:
        try:
            with self._session_factory() as session:
                return self._read_memory_aggregates(session, scope, selector)
        except SQLAlchemyError as exc:
            raise SocEffectivenessRepositoryError("SOC effectiveness Memory query failed") from exc

    def _read_rule_aggregates(
        self,
        session: Session,
        scope: SocEffectivenessScope,
    ) -> list[SocRuleEffectivenessAggregate]:
        ranked_runs = (
            select(
                SocAnalysisRunRow.run_id.label("run_id"),
                SocAnalysisRunRow.alert_id.label("alert_id"),
                SocAnalysisRunRow.status.label("status"),
                SocAnalysisRunRow.analysis_verdict.label("analysis_verdict"),
                SocAnalysisRunRow.runtime_decision_verdict.label("runtime_decision_verdict"),
                SocAnalysisRunRow.total_duration_ms.label("total_duration_ms"),
                SocAnalysisRunRow.provider_call_count.label("provider_call_count"),
                SocAnalysisRunRow.input_tokens.label("input_tokens"),
                SocAnalysisRunRow.output_tokens.label("output_tokens"),
                SocAnalysisRunRow.total_tokens.label("total_tokens"),
                SocAnalysisRunRow.usage_measurement_status.label("usage_measurement_status"),
                SocAnalysisRunRow.output_quality_status.label("output_quality_status"),
                SocAnalysisRunRow.repair_applied.label("repair_applied"),
                SocAnalysisRunRow.deterministic_fallback_used.label("deterministic_fallback_used"),
                SocAnalysisRunRow.degraded_section_count.label("degraded_section_count"),
                (
                    func.row_number()
                    .over(
                        partition_by=SocAnalysisRunRow.alert_id,
                        order_by=(
                            SocAnalysisRunRow.started_at.desc(),
                            SocAnalysisRunRow.updated_at.desc(),
                            SocAnalysisRunRow.run_id.desc(),
                        ),
                    )
                    .label("run_rank")
                ),
                (func.count(SocAnalysisRunRow.run_id).over(partition_by=SocAnalysisRunRow.alert_id).label("alert_run_count")),
            )
            .where(
                SocAnalysisRunRow.started_at >= scope.window_start,
                SocAnalysisRunRow.started_at < scope.window_end,
            )
            .cte("soc_effectiveness_ranked_runs")
        )
        latest_runs = select(ranked_runs).where(ranked_runs.c.run_rank == 1).cte("soc_effectiveness_latest_runs")
        run_ids = select(latest_runs.c.run_id)

        latest_decisions = _latest_decision_cte(run_ids)
        latest_applied_dispositions = _latest_applied_disposition_cte(run_ids)
        latest_corrections = _latest_correction_cte(run_ids)
        latest_outcomes = _latest_primary_outcome_cte(run_ids)
        latest_sample_outcomes = _latest_sample_outcome_cte(run_ids)
        latest_external = _latest_external_disposition_cte(run_ids)
        memory_uses = (
            select(
                SocMemoryUseRow.run_id.label("run_id"),
                func.count(SocMemoryUseRow.use_id).label("memory_context_use_count"),
                func.sum(case((SocMemoryUseRow.directive_applied.is_(True), 1), else_=0)).label("memory_directive_use_count"),
            )
            .where(SocMemoryUseRow.run_id.in_(run_ids))
            .group_by(SocMemoryUseRow.run_id)
            .cte("soc_effectiveness_memory_uses")
        )
        memory_feedback = (
            select(
                SocMemoryFeedbackRow.run_id.label("run_id"),
                func.sum(case((SocMemoryFeedbackRow.alignment == "contradicts", 1), else_=0)).label("memory_contradiction_count"),
            )
            .where(SocMemoryFeedbackRow.run_id.in_(run_ids))
            .group_by(SocMemoryFeedbackRow.run_id)
            .cte("soc_effectiveness_memory_feedback")
        )

        effective_verdict = func.coalesce(
            latest_decisions.c.after_verdict,
            latest_runs.c.runtime_decision_verdict,
            latest_runs.c.analysis_verdict,
        )
        predicted_disposition = func.coalesce(
            latest_applied_dispositions.c.after_disposition,
            latest_decisions.c.effective_disposition,
        )
        final_operational_disposition = func.coalesce(
            latest_outcomes.c.observed_disposition,
            latest_external.c.canonical_status,
            latest_sample_outcomes.c.observed_disposition,
        )
        final_verdict = case(
            (
                latest_corrections.c.final_verdict.in_(_RESOLVED_VERDICTS),
                latest_corrections.c.final_verdict,
            ),
            (
                final_operational_disposition.in_(("closed_true_positive", "closed_benign_true_positive")),
                literal(_TRUE_POSITIVE),
            ),
            (
                final_operational_disposition == "closed_false_positive",
                literal(_FALSE_POSITIVE),
            ),
            else_=literal(None),
        )
        completed = latest_runs.c.status == "success"
        labeled = final_verdict.in_(_RESOLVED_VERDICTS)
        final_risk = final_verdict == _TRUE_POSITIVE
        final_false_positive = final_verdict == _FALSE_POSITIVE
        transferred = predicted_disposition == _TRANSFERRED
        auto_ignored = and_(
            latest_applied_dispositions.c.after_disposition.in_(_AUTO_IGNORE_DISPOSITIONS),
            latest_applied_dispositions.c.transition_kind == "applied",
        )
        human_touched = or_(
            and_(
                latest_corrections.c.final_verdict.is_not(None),
                latest_corrections.c.actor_type == "user",
            ),
            latest_outcomes.c.source == "analyst",
            latest_sample_outcomes.c.source == "analyst",
        )

        tenant_id = SocAlertSummaryRow.tenant_id
        source_type = func.coalesce(SocAlertSummaryRow.source_type, literal("unknown"))
        source_system = SocAlertSummaryRow.source_system
        detection_key = SocAlertSummaryRow.detection_key
        rule_code = SocAlertSummaryRow.rule_code
        rule_name = SocAlertSummaryRow.rule_name
        detection_identity = func.coalesce(
            detection_key,
            rule_code,
            rule_name,
            literal("unclassified:") + source_type,
        )
        filters = []
        if scope.tenant_id is not None:
            filters.append(tenant_id == scope.tenant_id)
        if scope.source_type is not None:
            filters.append(source_type == scope.source_type)

        query = (
            select(
                tenant_id.label("tenant_id"),
                source_type.label("source_type"),
                source_system.label("source_system"),
                func.max(detection_key).label("detection_key"),
                func.max(rule_code).label("rule_code"),
                func.max(rule_name).label("rule_name"),
                func.count(latest_runs.c.run_id).label("alert_count"),
                _sum_if(completed).label("completed_count"),
                func.coalesce(func.sum(latest_runs.c.alert_run_count - 1), 0).label("superseded_run_count"),
                _sum_if(labeled).label("labeled_count"),
                _sum_if(labeled).label("high_trust_labeled_count"),
                _sum_if(and_(labeled, effective_verdict == final_verdict)).label("correct_count"),
                _sum_if(final_risk).label("final_risk_count"),
                _sum_if(final_false_positive).label("final_false_positive_count"),
                _sum_if(and_(final_risk, effective_verdict == _FALSE_POSITIVE)).label("detection_miss_count"),
                _sum_if(transferred).label("transfer_count"),
                _sum_if(and_(transferred, labeled)).label("labeled_transfer_count"),
                _sum_if(and_(transferred, final_risk)).label("transferred_risk_count"),
                _sum_if(auto_ignored).label("auto_ignore_count"),
                _sum_if(and_(auto_ignored, labeled)).label("labeled_auto_ignore_count"),
                _sum_if(and_(auto_ignored, final_risk)).label("wrong_auto_ignore_count"),
                _sum_if(human_touched).label("human_touch_count"),
                _sum_if(func.coalesce(latest_runs.c.provider_call_count, 0) > 0).label("provider_run_count"),
                func.coalesce(func.sum(latest_runs.c.provider_call_count), 0).label("provider_call_count"),
                _sum_if(latest_runs.c.total_tokens.is_not(None)).label("token_measured_run_count"),
                func.coalesce(func.sum(latest_runs.c.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(latest_runs.c.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(latest_runs.c.total_tokens), 0).label("total_tokens"),
                _sum_if(latest_runs.c.total_duration_ms.is_not(None)).label("duration_measured_run_count"),
                func.coalesce(func.sum(latest_runs.c.total_duration_ms), 0).label("total_duration_ms"),
                _sum_if(latest_runs.c.repair_applied.is_(True)).label("repair_run_count"),
                _sum_if(latest_runs.c.deterministic_fallback_used.is_(True)).label("fallback_run_count"),
                _sum_if(
                    or_(
                        latest_runs.c.output_quality_status == "degraded",
                        func.coalesce(latest_runs.c.degraded_section_count, 0) > 0,
                    )
                ).label("degraded_run_count"),
                func.coalesce(func.sum(memory_uses.c.memory_context_use_count), 0).label("memory_context_use_count"),
                func.coalesce(func.sum(memory_uses.c.memory_directive_use_count), 0).label("memory_directive_use_count"),
                func.coalesce(func.sum(memory_feedback.c.memory_contradiction_count), 0).label("memory_contradiction_count"),
            )
            .select_from(latest_runs)
            .outerjoin(SocAlertSummaryRow, SocAlertSummaryRow.run_id == latest_runs.c.run_id)
            .outerjoin(latest_decisions, latest_decisions.c.run_id == latest_runs.c.run_id)
            .outerjoin(latest_applied_dispositions, latest_applied_dispositions.c.run_id == latest_runs.c.run_id)
            .outerjoin(latest_corrections, latest_corrections.c.run_id == latest_runs.c.run_id)
            .outerjoin(latest_outcomes, latest_outcomes.c.run_id == latest_runs.c.run_id)
            .outerjoin(latest_sample_outcomes, latest_sample_outcomes.c.run_id == latest_runs.c.run_id)
            .outerjoin(latest_external, latest_external.c.target_run_id == latest_runs.c.run_id)
            .outerjoin(memory_uses, memory_uses.c.run_id == latest_runs.c.run_id)
            .outerjoin(memory_feedback, memory_feedback.c.run_id == latest_runs.c.run_id)
            .group_by(
                tenant_id,
                source_type,
                source_system,
                detection_identity,
            )
            .order_by(
                func.count(latest_runs.c.run_id).desc(),
                source_type,
                detection_identity,
            )
        )
        if filters:
            query = query.where(*filters)
        rows = session.execute(query).mappings()
        return [SocRuleEffectivenessAggregate.model_validate(dict(row)) for row in rows]

    def _read_behavior_group_aggregates(
        self,
        session: Session,
        scope: SocEffectivenessScope,
        selector: SocRuleEffectivenessSelector,
    ) -> list[SocBehaviorGroupEffectivenessAggregate]:
        rows = list(
            session.execute(
                select(SocMemoryPatternObservationRow)
                .join(
                    SocAlertSummaryRow,
                    SocAlertSummaryRow.run_id == SocMemoryPatternObservationRow.run_id,
                )
                .where(
                    SocMemoryPatternObservationRow.observed_at >= scope.window_start,
                    SocMemoryPatternObservationRow.observed_at < scope.window_end,
                    *_rule_selector_filters(selector),
                )
                .order_by(
                    SocMemoryPatternObservationRow.observed_at.asc(),
                    SocMemoryPatternObservationRow.observation_id.asc(),
                )
            ).scalars()
        )
        if not rows:
            return []

        grouped: dict[str, list[SocMemoryPatternObservationRow]] = defaultdict(list)
        for row in rows:
            grouped[row.lineage_key].append(row)
        lineage_keys = set(grouped)
        pattern_source_ids = {f"memory_pattern:{row.aggregation_key}" for row in rows}
        source_run_ids = {row.run_id for row in rows}
        candidate_rows = list(
            session.execute(
                select(SocMemoryCandidateRow).where(
                    SocMemoryCandidateRow.tenant_id == selector.tenant_id,
                    or_(
                        SocMemoryCandidateRow.source_id.in_(pattern_source_ids),
                        and_(
                            SocMemoryCandidateRow.source_type == "manual_note",
                            SocMemoryCandidateRow.source_run_id.in_(source_run_ids),
                        ),
                    ),
                )
            ).scalars()
        )
        candidates_by_lineage: dict[str, list[SocMemoryCandidateRow]] = defaultdict(list)
        for row in candidate_rows:
            metadata = _json_mapping(row.candidate_payload.get("metadata"))
            lineage_key = metadata.get("lineage_key")
            if isinstance(lineage_key, str) and lineage_key in lineage_keys:
                candidates_by_lineage[lineage_key].append(row)
        candidate_ids = {row.candidate_id for candidates in candidates_by_lineage.values() for row in candidates}
        records_by_candidate: dict[str, SocMemoryRecordRow] = {}
        for chunk in _chunks(candidate_ids):
            for row in session.execute(select(SocMemoryRecordRow).where(SocMemoryRecordRow.source_candidate_id.in_(chunk))).scalars():
                records_by_candidate[row.source_candidate_id] = row

        aggregates: list[SocBehaviorGroupEffectivenessAggregate] = []
        for lineage_key, observations in grouped.items():
            first = observations[0]
            candidate, record = _select_candidate_and_record(
                candidates_by_lineage.get(lineage_key, []),
                records_by_candidate,
            )
            verdict_counts = Counter()
            for observation in observations:
                lesson = _json_mapping(observation.observation_payload.get("lesson"))
                verdict = lesson.get("verdict")
                if isinstance(verdict, str) and verdict:
                    verdict_counts[verdict] += 1
            latest = observations[-1]
            signature = _json_mapping(latest.observation_payload.get("signature"))
            behavior_label = signature.get("label")
            aggregates.append(
                SocBehaviorGroupEffectivenessAggregate(
                    lineage_key=lineage_key,
                    behavior_label=(behavior_label if isinstance(behavior_label, str) and behavior_label.strip() else first.pattern_value),
                    environment=first.environment,
                    data_class=first.data_class,
                    profile_id=first.profile_id or "soc.generic",
                    profile_version=first.profile_version or "1",
                    sample_count=len(observations),
                    distinct_alert_count=len({item.alert_id for item in observations}),
                    window_count=len({item.aggregation_key for item in observations}),
                    verdict_counts=dict(verdict_counts),
                    first_observed_at=min(item.observed_at for item in observations),
                    last_observed_at=max(item.observed_at for item in observations),
                    candidate_id=(candidate.candidate_id if candidate is not None else None),
                    candidate_status=(candidate.status if candidate is not None else None),
                    memory_id=record.memory_id if record is not None else None,
                    memory_version=(record.version if record is not None else None),
                    memory_status=(record.status if record is not None else None),
                    retrieval_enabled=(record.retrieval_enabled if record is not None else False),
                )
            )
        return sorted(
            aggregates,
            key=lambda item: (item.last_observed_at, item.lineage_key),
            reverse=True,
        )

    def _read_memory_aggregates(
        self,
        session: Session,
        scope: SocEffectivenessScope,
        selector: SocRuleEffectivenessSelector,
    ) -> list[SocMemoryEffectivenessAggregate]:
        latest_runs = _latest_runs_for_scope(scope)
        selected_rows = list(
            session.execute(
                select(
                    SocMemoryUseRow,
                    SocAlertSummaryRow.rule_code,
                    SocAlertSummaryRow.detection_key,
                )
                .join(latest_runs, latest_runs.c.run_id == SocMemoryUseRow.run_id)
                .join(
                    SocAlertSummaryRow,
                    SocAlertSummaryRow.run_id == SocMemoryUseRow.run_id,
                )
                .where(*_rule_selector_filters(selector))
                .order_by(
                    SocMemoryUseRow.created_at.desc(),
                    SocMemoryUseRow.use_id.desc(),
                )
            ).all()
        )
        latest_by_memory_alert: dict[
            tuple[str, int, str],
            tuple[SocMemoryUseRow, str | None, str | None],
        ] = {}
        for use_row, rule_code, detection_key in selected_rows:
            latest_by_memory_alert.setdefault(
                (use_row.memory_id, use_row.memory_version, use_row.alert_id),
                (use_row, rule_code, detection_key),
            )
        uses = list(latest_by_memory_alert.values())
        if not uses:
            return []

        selected_memory_versions = {(item[0].memory_id, item[0].memory_version) for item in uses}
        actual_rule_codes: dict[tuple[str, int], set[str]] = defaultdict(set)
        memory_ids = {item[0].memory_id for item in uses}
        for chunk in _chunks(memory_ids):
            query = (
                select(
                    SocMemoryUseRow.memory_id,
                    SocMemoryUseRow.memory_version,
                    SocAlertSummaryRow.rule_code,
                    SocAlertSummaryRow.detection_key,
                )
                .join(latest_runs, latest_runs.c.run_id == SocMemoryUseRow.run_id)
                .join(
                    SocAlertSummaryRow,
                    SocAlertSummaryRow.run_id == SocMemoryUseRow.run_id,
                )
                .where(SocMemoryUseRow.memory_id.in_(chunk))
            )
            query = query.where(SocAlertSummaryRow.tenant_id == selector.tenant_id if selector.tenant_id is not None else SocAlertSummaryRow.tenant_id.is_(None))
            if scope.source_type is not None:
                query = query.where(SocAlertSummaryRow.source_type == scope.source_type)
            for memory_id, memory_version, rule_code, detection_key in session.execute(query):
                key = (memory_id, memory_version)
                if key in selected_memory_versions:
                    actual_rule_codes[key].add(rule_code or detection_key or "unclassified")

        use_ids = {item[0].use_id for item in uses}
        run_ids = {item[0].run_id for item in uses}
        applied_disposition_by_run: dict[str, str] = {}
        for chunk in _chunks(run_ids):
            disposition_rows = session.execute(
                select(SocDispositionTransitionRow)
                .where(
                    SocDispositionTransitionRow.run_id.in_(chunk),
                    SocDispositionTransitionRow.transition_kind == "applied",
                )
                .order_by(
                    SocDispositionTransitionRow.created_at.desc(),
                    SocDispositionTransitionRow.transition_id.desc(),
                )
            ).scalars()
            for row in disposition_rows:
                applied_disposition_by_run.setdefault(
                    row.run_id,
                    row.after_disposition,
                )
        feedback_by_use: dict[str, SocMemoryFeedbackRow] = {}
        trust_rank = {"high": 3, "medium": 2, "low": 1}
        for chunk in _chunks(use_ids):
            feedback_rows = session.execute(select(SocMemoryFeedbackRow).where(SocMemoryFeedbackRow.use_id.in_(chunk))).scalars()
            for row in feedback_rows:
                current = feedback_by_use.get(row.use_id)
                if current is None or (
                    trust_rank.get(row.trust, 0),
                    row.created_at,
                    row.feedback_id,
                ) > (
                    trust_rank.get(current.trust, 0),
                    current.created_at,
                    current.feedback_id,
                ):
                    feedback_by_use[row.use_id] = row

        records: dict[str, SocMemoryRecordRow] = {}
        for chunk in _chunks(memory_ids):
            for row in session.execute(select(SocMemoryRecordRow).where(SocMemoryRecordRow.memory_id.in_(chunk))).scalars():
                records[row.memory_id] = row
        source_run_ids = {row.source_run_id for row in records.values() if row.source_run_id is not None}
        source_rule_codes: dict[str, str] = {}
        for chunk in _chunks(source_run_ids):
            for row in session.execute(select(SocAlertSummaryRow).where(SocAlertSummaryRow.run_id.in_(chunk))).scalars():
                source_rule_codes[row.run_id] = row.rule_code or row.detection_key or row.rule_name or "unclassified"

        grouped_uses: dict[
            tuple[str, int],
            list[tuple[SocMemoryUseRow, str | None, str | None]],
        ] = defaultdict(list)
        for item in uses:
            grouped_uses[(item[0].memory_id, item[0].memory_version)].append(item)

        aggregates: list[SocMemoryEffectivenessAggregate] = []
        for (memory_id, memory_version), items in grouped_uses.items():
            record = records.get(memory_id)
            version_record = record if record is not None and record.version == memory_version else None
            effect_counts = Counter(item[0].effect for item in items)
            actual_codes = sorted(actual_rule_codes[(memory_id, memory_version)])
            feedback_rows = [feedback_by_use[item[0].use_id] for item in items if item[0].use_id in feedback_by_use]
            high_trust = [item for item in feedback_rows if item.trust == "high"]
            alignment_counts = Counter(item.alignment for item in high_trust)
            directive_high_trust = 0
            directive_correct = 0
            helpful = 0
            harmful = 0
            wrong_auto_ignore = 0
            uses_by_id = {item[0].use_id: item[0] for item in items}
            for feedback in high_trust:
                use = uses_by_id[feedback.use_id]
                payload = _json_mapping(use.use_payload)
                final_verdict = _json_mapping(feedback.feedback_payload).get("final_verdict")
                base_verdict = payload.get("base_verdict")
                effective_verdict = payload.get("effective_verdict")
                if not use.directive_applied or final_verdict not in _RESOLVED_VERDICTS:
                    continue
                directive_high_trust += 1
                if effective_verdict == final_verdict:
                    directive_correct += 1
                if base_verdict != final_verdict and effective_verdict == final_verdict:
                    helpful += 1
                if base_verdict == final_verdict and effective_verdict != final_verdict:
                    harmful += 1
                if effective_verdict == _FALSE_POSITIVE and final_verdict == _TRUE_POSITIVE and applied_disposition_by_run.get(use.run_id) in _AUTO_IGNORE_DISPOSITIONS:
                    wrong_auto_ignore += 1
            aggregates.append(
                SocMemoryEffectivenessAggregate(
                    memory_id=memory_id,
                    memory_version=memory_version,
                    summary=(version_record.summary if version_record is not None else None),
                    record_status=(version_record.status if version_record is not None else None),
                    retrieval_enabled=(version_record.retrieval_enabled if version_record is not None else False),
                    use_alert_count=len(items),
                    context_only_count=effect_counts["context_only"],
                    directive_count=sum(int(item[0].directive_applied) for item in items),
                    reinforced_count=effect_counts["reinforced"],
                    overridden_count=effect_counts["overridden"],
                    conflicted_count=effect_counts["conflicted"],
                    feedback_count=len(feedback_rows),
                    high_trust_feedback_count=len(high_trust),
                    directive_high_trust_feedback_count=directive_high_trust,
                    directive_correct_count=directive_correct,
                    support_count=alignment_counts["supports"],
                    contradiction_count=alignment_counts["contradicts"],
                    not_applicable_count=alignment_counts["not_applicable"],
                    unknown_count=alignment_counts["unknown"],
                    helpful_correction_count=helpful,
                    harmful_override_count=harmful,
                    wrong_auto_ignore_count=wrong_auto_ignore,
                    source_rule_codes=([source_rule_codes[record.source_run_id]] if record is not None and record.source_run_id in source_rule_codes else []),
                    actual_rule_codes=actual_codes,
                    last_use_at=max(item[0].created_at for item in items),
                    last_feedback_at=(max(item.created_at for item in feedback_rows) if feedback_rows else None),
                )
            )
        return sorted(
            aggregates,
            key=lambda item: (
                item.directive_count,
                item.use_alert_count,
                item.memory_id,
                item.memory_version,
            ),
            reverse=True,
        )


def _latest_decision_cte(run_ids):
    ranked = (
        select(
            SocDecisionTransitionRow.run_id.label("run_id"),
            SocDecisionTransitionRow.after_verdict.label("after_verdict"),
            SocDecisionTransitionRow.effective_disposition.label("effective_disposition"),
            func.row_number()
            .over(
                partition_by=SocDecisionTransitionRow.run_id,
                order_by=(SocDecisionTransitionRow.created_at.desc(), SocDecisionTransitionRow.transition_id.desc()),
            )
            .label("item_rank"),
        )
        .where(SocDecisionTransitionRow.run_id.in_(run_ids))
        .cte("soc_effectiveness_ranked_decisions")
    )
    return select(ranked).where(ranked.c.item_rank == 1).cte("soc_effectiveness_latest_decisions")


def _latest_runs_for_scope(scope: SocEffectivenessScope):
    ranked = (
        select(
            SocAnalysisRunRow.run_id.label("run_id"),
            SocAnalysisRunRow.alert_id.label("alert_id"),
            func.row_number()
            .over(
                partition_by=SocAnalysisRunRow.alert_id,
                order_by=(
                    SocAnalysisRunRow.started_at.desc(),
                    SocAnalysisRunRow.updated_at.desc(),
                    SocAnalysisRunRow.run_id.desc(),
                ),
            )
            .label("run_rank"),
        )
        .where(
            SocAnalysisRunRow.started_at >= scope.window_start,
            SocAnalysisRunRow.started_at < scope.window_end,
        )
        .cte("soc_effectiveness_detail_ranked_runs")
    )
    return select(ranked.c.run_id, ranked.c.alert_id).where(ranked.c.run_rank == 1).cte("soc_effectiveness_detail_latest_runs")


def _rule_selector_filters(
    selector: SocRuleEffectivenessSelector,
) -> list[Any]:
    filters: list[Any] = [
        SocAlertSummaryRow.source_type == selector.source_type,
    ]
    for column, value in (
        (SocAlertSummaryRow.tenant_id, selector.tenant_id),
        (SocAlertSummaryRow.source_system, selector.source_system),
    ):
        filters.append(column == value if value is not None else column.is_(None))
    if selector.detection_key is not None:
        filters.append(SocAlertSummaryRow.detection_key == selector.detection_key)
    elif selector.rule_code is not None:
        filters.extend(
            [
                SocAlertSummaryRow.detection_key.is_(None),
                SocAlertSummaryRow.rule_code == selector.rule_code,
            ]
        )
    elif selector.rule_name is not None:
        filters.extend(
            [
                SocAlertSummaryRow.detection_key.is_(None),
                SocAlertSummaryRow.rule_code.is_(None),
                SocAlertSummaryRow.rule_name == selector.rule_name,
            ]
        )
    else:
        filters.extend(
            [
                SocAlertSummaryRow.detection_key.is_(None),
                SocAlertSummaryRow.rule_code.is_(None),
                SocAlertSummaryRow.rule_name.is_(None),
            ]
        )
    return filters


def _select_candidate_and_record(
    candidates: list[SocMemoryCandidateRow],
    records_by_candidate: dict[str, SocMemoryRecordRow],
) -> tuple[SocMemoryCandidateRow | None, SocMemoryRecordRow | None]:
    if not candidates:
        return None, None
    active = {"pending_review", "confirmed_candidate", "confirmed"}
    selected = max(
        candidates,
        key=lambda item: (
            item.candidate_id in records_by_candidate,
            item.status in active,
            item.updated_at,
            item.candidate_id,
        ),
    )
    return selected, records_by_candidate.get(selected.candidate_id)


def _json_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _chunks(values: Iterable[str], size: int = 500) -> Iterable[list[str]]:
    chunk: list[str] = []
    for value in values:
        chunk.append(value)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _latest_applied_disposition_cte(run_ids):
    ranked = (
        select(
            SocDispositionTransitionRow.run_id.label("run_id"),
            SocDispositionTransitionRow.after_disposition.label("after_disposition"),
            SocDispositionTransitionRow.transition_kind.label("transition_kind"),
            func.row_number()
            .over(
                partition_by=SocDispositionTransitionRow.run_id,
                order_by=(SocDispositionTransitionRow.created_at.desc(), SocDispositionTransitionRow.transition_id.desc()),
            )
            .label("item_rank"),
        )
        .where(
            SocDispositionTransitionRow.run_id.in_(run_ids),
            SocDispositionTransitionRow.transition_kind == "applied",
        )
        .cte("soc_effectiveness_ranked_applied_dispositions")
    )
    return select(ranked).where(ranked.c.item_rank == 1).cte("soc_effectiveness_latest_applied_dispositions")


def _latest_correction_cte(run_ids):
    ranked = (
        select(
            SocDecisionAuditLogRow.run_id.label("run_id"),
            SocDecisionAuditLogRow.final_verdict.label("final_verdict"),
            SocDecisionAuditLogRow.confidence_source.label("confidence_source"),
            SocDecisionAuditLogRow.actor_type.label("actor_type"),
            func.row_number()
            .over(
                partition_by=SocDecisionAuditLogRow.run_id,
                order_by=(SocDecisionAuditLogRow.occurred_at.desc(), SocDecisionAuditLogRow.audit_id.desc()),
            )
            .label("item_rank"),
        )
        .where(
            SocDecisionAuditLogRow.run_id.in_(run_ids),
            SocDecisionAuditLogRow.action == "correction",
            SocDecisionAuditLogRow.final_verdict.is_not(None),
            or_(
                SocDecisionAuditLogRow.confidence_source.in_(("human_confirmation", "external_disposition")),
                SocDecisionAuditLogRow.actor_type == "user",
            ),
        )
        .cte("soc_effectiveness_ranked_corrections")
    )
    return select(ranked).where(ranked.c.item_rank == 1).cte("soc_effectiveness_latest_corrections")


def _latest_primary_outcome_cte(run_ids):
    ranked = (
        select(
            SocDispositionOutcomeRow.run_id.label("run_id"),
            SocDispositionOutcomeRow.observed_disposition.label("observed_disposition"),
            SocDispositionOutcomeRow.source.label("source"),
            func.row_number()
            .over(
                partition_by=SocDispositionOutcomeRow.run_id,
                order_by=(SocDispositionOutcomeRow.observed_at.desc(), SocDispositionOutcomeRow.outcome_id.desc()),
            )
            .label("item_rank"),
        )
        .where(
            SocDispositionOutcomeRow.run_id.in_(run_ids),
            SocDispositionOutcomeRow.review_kind == "analyst_resolution",
            SocDispositionOutcomeRow.outcome_status.in_(("confirmed", "overridden")),
        )
        .cte("soc_effectiveness_ranked_primary_outcomes")
    )
    return select(ranked).where(ranked.c.item_rank == 1).cte("soc_effectiveness_latest_primary_outcomes")


def _latest_sample_outcome_cte(run_ids):
    ranked = (
        select(
            SocDispositionOutcomeRow.run_id.label("run_id"),
            SocDispositionOutcomeRow.observed_disposition.label("observed_disposition"),
            SocDispositionOutcomeRow.source.label("source"),
            func.row_number()
            .over(
                partition_by=SocDispositionOutcomeRow.run_id,
                order_by=(SocDispositionOutcomeRow.observed_at.desc(), SocDispositionOutcomeRow.outcome_id.desc()),
            )
            .label("item_rank"),
        )
        .where(
            SocDispositionOutcomeRow.run_id.in_(run_ids),
            SocDispositionOutcomeRow.review_kind == "sampled_quality_review",
            SocDispositionOutcomeRow.outcome_status.in_(("confirmed", "overridden")),
            SocDispositionOutcomeRow.source == "analyst",
        )
        .cte("soc_effectiveness_ranked_sample_outcomes")
    )
    return select(ranked).where(ranked.c.item_rank == 1).cte("soc_effectiveness_latest_sample_outcomes")


def _latest_external_disposition_cte(run_ids):
    ranked = (
        select(
            SocExternalDispositionRow.target_run_id.label("target_run_id"),
            SocExternalDispositionRow.canonical_status.label("canonical_status"),
            func.row_number()
            .over(
                partition_by=SocExternalDispositionRow.target_run_id,
                order_by=(SocExternalDispositionRow.created_at.desc(), SocExternalDispositionRow.disposition_id.desc()),
            )
            .label("item_rank"),
        )
        .where(
            SocExternalDispositionRow.target_run_id.in_(run_ids),
            SocExternalDispositionRow.apply_status == "mapped",
            SocExternalDispositionRow.trust_level == "high",
        )
        .cte("soc_effectiveness_ranked_external_dispositions")
    )
    return select(ranked).where(ranked.c.item_rank == 1).cte("soc_effectiveness_latest_external_dispositions")


def _sum_if(condition):
    return func.sum(case((condition, 1), else_=0))


__all__ = ["SqlAlchemySocEffectivenessRepository"]
