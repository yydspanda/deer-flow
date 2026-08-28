from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.gateway.routers.soc_effectiveness import (
    get_effectiveness_snapshot,
    get_rule_effectiveness_detail,
)
from soc_agent.contracts import (
    SocOperationsAvailability,
    SocRuleOptimizationPolicy,
    SocRuleRecommendationKind,
)
from soc_agent.core import SocEffectivenessService, SocServiceNotImplementedError
from soc_agent.db import SqlAlchemySocEffectivenessRepository, create_soc_tables
from soc_agent.db.models import (
    SocAlertSummaryRow,
    SocAnalysisRunRow,
    SocDecisionAuditLogRow,
    SocDecisionTransitionRow,
    SocDispositionOutcomeRow,
    SocDispositionTransitionRow,
    SocMemoryCandidateRow,
    SocMemoryFeedbackRow,
    SocMemoryPatternObservationRow,
    SocMemoryRecordRow,
    SocMemoryUseRow,
)

NOW = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)


def test_effectiveness_snapshot_counts_latest_run_and_exposes_denominators() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    _seed_effectiveness_rows(session_factory)
    service = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        policy=SocRuleOptimizationPolicy(
            minimum_labeled_alerts=2,
            minimum_label_coverage=0.2,
            high_volume_alert_count=100,
        ),
        clock=lambda: NOW,
    )

    snapshot = service.get_snapshot(window_days=30, tenant_id="pingan")

    assert snapshot.availability is SocOperationsAvailability.AVAILABLE
    assert snapshot.coverage is not None
    assert snapshot.coverage.total_alert_count == 2
    assert snapshot.coverage.superseded_run_count == 1
    assert snapshot.coverage.high_trust_labeled_alert_count == 2
    assert snapshot.coverage.high_trust_label_coverage.value == 1.0

    assert snapshot.summary is not None
    assert snapshot.summary.triage_accuracy.numerator == 0
    assert snapshot.summary.triage_accuracy.denominator == 2
    assert snapshot.summary.detection_miss_rate.value == 1.0
    assert snapshot.summary.operational_miss_rate.value == 1.0
    assert snapshot.summary.transfer_precision.value == 0.0
    assert snapshot.summary.attack_transfer_recall.value == 0.0
    assert snapshot.summary.auto_ignore_rate.value == 0.5
    assert snapshot.summary.wrong_auto_ignore_rate.value == 1.0
    assert snapshot.summary.human_touch_rate.value == 1.0

    assert snapshot.compute is not None
    assert snapshot.compute.provider_run_count == 2
    assert snapshot.compute.provider_call_count == 3
    assert snapshot.compute.token_measured_run_count == 1
    assert snapshot.compute.total_tokens == 100
    assert snapshot.compute.average_total_duration_ms == 1500.0
    assert snapshot.compute.repair_run_count == 1
    assert snapshot.compute.fallback_run_count == 1
    assert snapshot.compute.degraded_run_count == 1

    assert len(snapshot.rules) == 1
    rule = snapshot.rules[0]
    assert rule.rule_code == "RC-MIXED-1"
    assert rule.confirmed_risk_rate == 0.5
    assert rule.false_positive_rate == 0.5
    assert rule.triage_accuracy == 0.0
    assert rule.memory_context_use_count == 2
    assert rule.memory_directive_use_count == 1
    assert rule.memory_contradiction_count == 1
    assert rule.recommendation.kind is SocRuleRecommendationKind.DETECTION_GAP
    assert rule.recommendation.authority == "advisory"


def test_effectiveness_snapshot_does_not_invent_accuracy_without_truth_labels() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as session:
        session.add(_run("RUN-UNLABELED", "ALT-UNLABELED", NOW - timedelta(hours=1)))
        session.add(_summary("RUN-UNLABELED", "ALT-UNLABELED", NOW - timedelta(hours=1)))
        session.add(
            SocDecisionAuditLogRow(
                audit_id="AUD-LOW-TRUST",
                action="correction",
                run_id="RUN-UNLABELED",
                alert_id="ALT-UNLABELED",
                actor_id="model-reviewer",
                actor_type="service",
                actor_surface="daemon",
                occurred_at=NOW - timedelta(minutes=30),
                previous_verdict="suspicious",
                final_verdict="false_positive",
                confidence=0.9,
                confidence_source="llm_self_report",
                record_payload={},
            )
        )
        session.commit()

    snapshot = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        clock=lambda: NOW,
    ).get_snapshot()

    assert snapshot.summary is not None
    assert snapshot.summary.triage_accuracy.availability is SocOperationsAvailability.NOT_MEASURED
    assert snapshot.summary.triage_accuracy.denominator == 0
    assert snapshot.summary.triage_accuracy.value is None
    assert snapshot.summary.detection_miss_rate.availability is SocOperationsAvailability.NOT_MEASURED
    assert snapshot.rules[0].recommendation.kind is SocRuleRecommendationKind.INSUFFICIENT_LABELS


def test_rule_effectiveness_uses_detection_identity_not_mutable_rule_name() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    first = NOW - timedelta(hours=2)
    second = NOW - timedelta(hours=1)
    with session_factory() as session:
        session.add_all(
            [
                _run("RUN-RULE-NAME-A", "ALT-RULE-NAME-A", first),
                _summary(
                    "RUN-RULE-NAME-A",
                    "ALT-RULE-NAME-A",
                    first,
                    rule_name="旧规则名称",
                ),
                _run("RUN-RULE-NAME-B", "ALT-RULE-NAME-B", second),
                _summary(
                    "RUN-RULE-NAME-B",
                    "ALT-RULE-NAME-B",
                    second,
                    rule_name="新规则名称",
                ),
            ]
        )
        session.commit()

    snapshot = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        clock=lambda: NOW,
    ).get_snapshot(tenant_id="pingan")

    assert len(snapshot.rules) == 1
    assert snapshot.rules[0].alert_count == 2
    assert snapshot.rules[0].rule_code == "RC-MIXED-1"


def test_effectiveness_router_uses_the_same_core_service() -> None:
    service = SocEffectivenessService(
        repository=None,
        database_error_code="soc.database.not_configured",
        clock=lambda: NOW,
    )

    snapshot = get_effectiveness_snapshot(
        service=service,
        window_days=7,
        tenant_id=None,
        source_type=None,
    )

    assert snapshot.availability is SocOperationsAvailability.NOT_CONFIGURED
    assert snapshot.scope.window_start == NOW - timedelta(days=7)
    assert snapshot.error_code == "soc.database.not_configured"

    with pytest.raises(SocServiceNotImplementedError):
        service.get_rule_detail("0123456789abcdef")

    with pytest.raises(HTTPException) as exc_info:
        get_rule_effectiveness_detail(
            group_key="0123456789abcdef",
            service=service,
            window_days=30,
            tenant_id=None,
            source_type=None,
        )
    assert exc_info.value.status_code == 503


def test_effectiveness_uses_independent_sample_truth_without_overriding_primary_outcome() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    started_at = NOW - timedelta(hours=1)
    with session_factory() as session:
        session.add(_run("RUN-SAMPLED", "ALT-SAMPLED", started_at, verdict="false_positive"))
        session.add(_summary("RUN-SAMPLED", "ALT-SAMPLED", started_at))
        session.add(_run("RUN-SAMPLE-ONLY", "ALT-SAMPLE-ONLY", started_at, verdict="false_positive"))
        session.add(_summary("RUN-SAMPLE-ONLY", "ALT-SAMPLE-ONLY", started_at))
        session.add(
            _outcome(
                "OUT-SAMPLE",
                "RUN-SAMPLED",
                "ALT-SAMPLED",
                observed_at=started_at + timedelta(minutes=10),
                review_kind="sampled_quality_review",
                disposition="closed_true_positive",
            )
        )
        session.add(
            _outcome(
                "OUT-PRIMARY",
                "RUN-SAMPLED",
                "ALT-SAMPLED",
                observed_at=started_at + timedelta(minutes=5),
                review_kind="analyst_resolution",
                disposition="closed_false_positive",
            )
        )
        session.add(
            _outcome(
                "OUT-SAMPLE-ONLY",
                "RUN-SAMPLE-ONLY",
                "ALT-SAMPLE-ONLY",
                observed_at=started_at + timedelta(minutes=15),
                review_kind="sampled_quality_review",
                disposition="closed_true_positive",
            )
        )
        session.commit()

    snapshot = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        policy=SocRuleOptimizationPolicy(minimum_labeled_alerts=1),
        clock=lambda: NOW,
    ).get_snapshot()

    assert snapshot.coverage is not None
    assert snapshot.coverage.high_trust_labeled_alert_count == 2
    assert snapshot.summary is not None
    assert snapshot.summary.triage_accuracy.value == 0.5
    assert snapshot.rules[0].final_false_positive_count == 1
    assert snapshot.rules[0].final_risk_count == 1


def test_rule_detail_separates_behaviors_and_attributes_memory_outcomes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    first = NOW - timedelta(hours=2)
    second = NOW - timedelta(hours=1)
    cross_rule = NOW - timedelta(minutes=30)
    with session_factory() as session:
        session.add_all(
            [
                _run("RUN-BEHAVIOR-A", "ALT-BEHAVIOR-A", first, verdict="suspicious"),
                _run("RUN-BEHAVIOR-B", "ALT-BEHAVIOR-B", second, verdict="true_positive"),
                _run("RUN-CROSS-RULE", "ALT-CROSS-RULE", cross_rule, verdict="false_positive"),
                _summary("RUN-BEHAVIOR-A", "ALT-BEHAVIOR-A", first),
                _summary("RUN-BEHAVIOR-B", "ALT-BEHAVIOR-B", second),
                _summary(
                    "RUN-CROSS-RULE",
                    "ALT-CROSS-RULE",
                    cross_rule,
                    rule_code="RC-OTHER-2",
                ),
                _pattern_observation(
                    "MPO-BEHAVIOR-A",
                    "a" * 64,
                    "c" * 64,
                    "RUN-BEHAVIOR-A",
                    "ALT-BEHAVIOR-A",
                    first,
                    "OpenVPN / UDP 1194",
                    "false_positive",
                ),
                _pattern_observation(
                    "MPO-BEHAVIOR-B",
                    "b" * 64,
                    "d" * 64,
                    "RUN-BEHAVIOR-B",
                    "ALT-BEHAVIOR-B",
                    second,
                    "CVE-2017-7924 / 拒绝服务 / UDP 44818",
                    "true_positive",
                ),
                _memory_candidate_row("MC-BEHAVIOR-A", "a" * 64, first),
                _memory_record_row("MEM-BEHAVIOR-A", "MC-BEHAVIOR-A", first),
                _memory_use_with_verdicts(
                    "MU-BEHAVIOR-A",
                    "RUN-BEHAVIOR-A",
                    "ALT-BEHAVIOR-A",
                    first,
                ),
                _memory_feedback_with_verdict(
                    "MF-BEHAVIOR-A",
                    "MU-BEHAVIOR-A",
                    "RUN-BEHAVIOR-A",
                    "ALT-BEHAVIOR-A",
                    first,
                ),
                _memory_use_with_verdicts(
                    "MU-CROSS-RULE",
                    "RUN-CROSS-RULE",
                    "ALT-CROSS-RULE",
                    cross_rule,
                ),
            ]
        )
        session.commit()

    service = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        policy=SocRuleOptimizationPolicy(minimum_labeled_alerts=1),
        clock=lambda: NOW,
    )
    group_key = next(item.group_key for item in service.get_snapshot(tenant_id="pingan").rules if item.rule_code == "RC-MIXED-1")

    detail = service.get_rule_detail(
        group_key,
        tenant_id="pingan",
    )

    assert [item.behavior_label for item in detail.behavior_groups] == [
        "CVE-2017-7924 / 拒绝服务 / UDP 44818",
        "OpenVPN / UDP 1194",
    ]
    openvpn = detail.behavior_groups[1]
    assert openvpn.memory_id == "MEM-BEHAVIOR-A"
    assert openvpn.retrieval_enabled is True
    assert len(detail.memories) == 1
    memory = detail.memories[0]
    assert memory.directive_count == 1
    assert memory.helpful_correction_count == 1
    assert memory.harmful_override_count == 0
    assert memory.directive_accuracy.value == 1.0
    assert memory.directive_accuracy.metric_id == "memory.MEM-BEHAVIOR-A.v1.directive_accuracy"
    assert memory.final_outcome_coverage.value == 1.0
    assert memory.final_outcome_coverage.metric_id == "memory.MEM-BEHAVIOR-A.v1.final_outcome_coverage"
    assert memory.source_rule_codes == ["RC-MIXED-1"]
    assert memory.actual_rule_codes == ["RC-MIXED-1", "RC-OTHER-2"]

    routed = get_rule_effectiveness_detail(
        group_key=group_key,
        service=service,
        window_days=30,
        tenant_id="pingan",
        source_type=None,
    )
    assert routed.rule.group_key == group_key

    with pytest.raises(HTTPException) as exc_info:
        get_rule_effectiveness_detail(
            group_key="0" * 16,
            service=service,
            window_days=30,
            tenant_id="pingan",
            source_type=None,
        )
    assert exc_info.value.status_code == 404


def test_rule_detail_does_not_project_current_state_onto_an_old_memory_version() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    observed_at = NOW - timedelta(hours=1)
    with session_factory() as session:
        session.add_all(
            [
                _run("RUN-OLD-MEMORY", "ALT-OLD-MEMORY", observed_at),
                _summary("RUN-OLD-MEMORY", "ALT-OLD-MEMORY", observed_at),
                _memory_candidate_row("MC-OLD-MEMORY", "a" * 64, observed_at),
                _memory_record_row(
                    "MEM-BEHAVIOR-A",
                    "MC-OLD-MEMORY",
                    observed_at,
                    version=2,
                ),
                _memory_use_with_verdicts(
                    "MU-OLD-MEMORY",
                    "RUN-OLD-MEMORY",
                    "ALT-OLD-MEMORY",
                    observed_at,
                ),
            ]
        )
        session.commit()

    service = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        clock=lambda: NOW,
    )
    rule = service.get_snapshot(tenant_id="pingan").rules[0]

    memory = service.get_rule_detail(
        rule.group_key,
        tenant_id="pingan",
    ).memories[0]

    assert memory.memory_version == 1
    assert memory.summary is None
    assert memory.record_status is None
    assert memory.retrieval_enabled is False


def test_memory_wrong_auto_ignore_requires_an_applied_ignore_disposition() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    first = NOW - timedelta(hours=2)
    second = NOW - timedelta(hours=1)
    with session_factory() as session:
        session.add_all(
            [
                _run("RUN-HARM-NO-ACTION", "ALT-HARM-NO-ACTION", first),
                _summary("RUN-HARM-NO-ACTION", "ALT-HARM-NO-ACTION", first),
                _run("RUN-HARM-IGNORED", "ALT-HARM-IGNORED", second),
                _summary("RUN-HARM-IGNORED", "ALT-HARM-IGNORED", second),
                _memory_candidate_row("MC-HARM", "a" * 64, first),
                _memory_record_row("MEM-BEHAVIOR-A", "MC-HARM", first),
                _memory_use_with_verdicts(
                    "MU-HARM-NO-ACTION",
                    "RUN-HARM-NO-ACTION",
                    "ALT-HARM-NO-ACTION",
                    first,
                    base_verdict="true_positive",
                    effective_verdict="false_positive",
                ),
                _memory_feedback_with_verdict(
                    "MF-HARM-NO-ACTION",
                    "MU-HARM-NO-ACTION",
                    "RUN-HARM-NO-ACTION",
                    "ALT-HARM-NO-ACTION",
                    first,
                    final_verdict="true_positive",
                    alignment="contradicts",
                ),
                _memory_use_with_verdicts(
                    "MU-HARM-IGNORED",
                    "RUN-HARM-IGNORED",
                    "ALT-HARM-IGNORED",
                    second,
                    base_verdict="true_positive",
                    effective_verdict="false_positive",
                ),
                _memory_feedback_with_verdict(
                    "MF-HARM-IGNORED",
                    "MU-HARM-IGNORED",
                    "RUN-HARM-IGNORED",
                    "ALT-HARM-IGNORED",
                    second,
                    final_verdict="true_positive",
                    alignment="contradicts",
                ),
                _applied_disposition(
                    "RUN-HARM-IGNORED",
                    "ALT-HARM-IGNORED",
                    second,
                    "ignored",
                ),
            ]
        )
        session.commit()

    service = SocEffectivenessService(
        repository=SqlAlchemySocEffectivenessRepository(session_factory),
        clock=lambda: NOW,
    )
    rule = service.get_snapshot(tenant_id="pingan").rules[0]

    memory = service.get_rule_detail(
        rule.group_key,
        tenant_id="pingan",
    ).memories[0]

    assert memory.harmful_override_count == 2
    assert memory.wrong_auto_ignore_count == 1
    assert memory.contradiction_count == 2


def _seed_effectiveness_rows(session_factory) -> None:
    old = NOW - timedelta(hours=3)
    latest = NOW - timedelta(hours=2)
    second = NOW - timedelta(hours=1)
    with session_factory() as session:
        session.add_all(
            [
                _run("RUN-A-OLD", "ALT-A", old, verdict="true_positive"),
                _run(
                    "RUN-A-LATEST",
                    "ALT-A",
                    latest,
                    verdict="false_positive",
                    provider_call_count=2,
                    total_tokens=100,
                    total_duration_ms=1000,
                    repair_applied=True,
                ),
                _run(
                    "RUN-B",
                    "ALT-B",
                    second,
                    verdict="true_positive",
                    provider_call_count=1,
                    total_duration_ms=2000,
                    output_quality_status="degraded",
                    deterministic_fallback_used=True,
                    degraded_section_count=1,
                ),
                _summary("RUN-A-OLD", "ALT-A", old),
                _summary("RUN-A-LATEST", "ALT-A", latest),
                _summary("RUN-B", "ALT-B", second),
                _decision("RUN-A-LATEST", "ALT-A", latest, "false_positive", "ignored"),
                _decision("RUN-B", "ALT-B", second, "true_positive", "escalated"),
                _correction("RUN-A-LATEST", "ALT-A", latest, "true_positive"),
                _correction("RUN-B", "ALT-B", second, "false_positive"),
                _applied_disposition("RUN-A-LATEST", "ALT-A", latest, "ignored"),
                _memory_use("MU-A", "RUN-A-LATEST", "ALT-A", latest, directive=True),
                _memory_use("MU-B", "RUN-B", "ALT-B", second, directive=False),
                _memory_feedback("MF-A", "MU-A", "RUN-A-LATEST", "ALT-A", latest),
            ]
        )
        session.commit()


def _run(
    run_id: str,
    alert_id: str,
    started_at: datetime,
    *,
    verdict: str = "suspicious",
    provider_call_count: int = 0,
    total_tokens: int | None = None,
    total_duration_ms: int | None = None,
    output_quality_status: str = "valid",
    repair_applied: bool = False,
    deterministic_fallback_used: bool = False,
    degraded_section_count: int = 0,
) -> SocAnalysisRunRow:
    return SocAnalysisRunRow(
        run_id=run_id,
        alert_id=alert_id,
        status="success",
        pipeline_version="test",
        model_name="test-model",
        prompt_version="test-prompt",
        analysis_verdict=verdict,
        runtime_decision_verdict=verdict,
        total_duration_ms=total_duration_ms,
        provider_call_count=provider_call_count,
        input_tokens=total_tokens - 20 if total_tokens is not None else None,
        output_tokens=20 if total_tokens is not None else None,
        total_tokens=total_tokens,
        usage_measurement_status="reported" if total_tokens is not None else "unavailable",
        output_quality_status=output_quality_status,
        repair_applied=repair_applied,
        deterministic_fallback_used=deterministic_fallback_used,
        degraded_section_count=degraded_section_count,
        started_at=started_at,
        ended_at=started_at + timedelta(seconds=2),
        run_payload={},
        created_at=started_at,
        updated_at=started_at + timedelta(seconds=2),
    )


def _summary(
    run_id: str,
    alert_id: str,
    created_at: datetime,
    *,
    rule_code: str = "RC-MIXED-1",
    rule_name: str = "Mixed outcome detector",
) -> SocAlertSummaryRow:
    return SocAlertSummaryRow(
        run_id=run_id,
        alert_id=alert_id,
        tenant_id="pingan",
        source_type="nids",
        source_system="zeus",
        detection_key=f"pingan:nids:{rule_code}",
        rule_code=rule_code,
        rule_name=rule_name,
        entity_keys=[],
        status="success",
        verdict="suspicious",
        needs_review=False,
        created_at=created_at,
        updated_at=created_at,
        summary_payload={},
    )


def _decision(
    run_id: str,
    alert_id: str,
    created_at: datetime,
    verdict: str,
    disposition: str,
) -> SocDecisionTransitionRow:
    return SocDecisionTransitionRow(
        transition_id=f"DT-{run_id}",
        transition_key=f"decision:{run_id}",
        run_id=run_id,
        alert_id=alert_id,
        tenant_id="pingan",
        before_verdict="suspicious",
        after_verdict=verdict,
        before_needs_review=True,
        after_needs_review=False,
        transition_kind="effective",
        effective_disposition=disposition,
        policy_id="test",
        policy_version="1",
        policy_hash="hash",
        created_by_actor_id="runtime",
        created_at=created_at,
        transition_payload={},
    )


def _correction(
    run_id: str,
    alert_id: str,
    occurred_at: datetime,
    verdict: str,
) -> SocDecisionAuditLogRow:
    return SocDecisionAuditLogRow(
        audit_id=f"AUD-{run_id}",
        action="correction",
        run_id=run_id,
        alert_id=alert_id,
        actor_id="analyst-1",
        actor_type="user",
        actor_surface="web",
        occurred_at=occurred_at,
        previous_verdict="suspicious",
        final_verdict=verdict,
        confidence=1.0,
        confidence_source="human_confirmation",
        record_payload={},
    )


def _applied_disposition(
    run_id: str,
    alert_id: str,
    created_at: datetime,
    disposition: str,
) -> SocDispositionTransitionRow:
    return SocDispositionTransitionRow(
        transition_id=f"DST-{run_id}",
        transition_key=f"disposition:{run_id}",
        run_id=run_id,
        alert_id=alert_id,
        tenant_id="pingan",
        decision_transition_id=f"DT-{run_id}",
        after_disposition=disposition,
        transition_kind="applied",
        policy_id="test",
        policy_version="1",
        created_by_actor_id="runtime",
        created_at=created_at,
        transition_payload={},
    )


def _outcome(
    outcome_id: str,
    run_id: str,
    alert_id: str,
    *,
    observed_at: datetime,
    review_kind: str,
    disposition: str,
) -> SocDispositionOutcomeRow:
    return SocDispositionOutcomeRow(
        outcome_id=outcome_id,
        lineage_key=f"lineage:{outcome_id}",
        proposal_id=f"PROP-{alert_id}",
        run_id=run_id,
        alert_id=alert_id,
        queue_id=f"RQ-{alert_id}",
        review_kind=review_kind,
        outcome_status="confirmed",
        observed_disposition=disposition,
        source="analyst",
        sample_id="SAMPLE-1" if review_kind == "sampled_quality_review" else None,
        idempotency_key=f"idempotency:{outcome_id}",
        reviewed_by_actor_id="analyst-1",
        observed_at=observed_at,
        created_at=observed_at,
        outcome_payload={},
    )


def _memory_use(
    use_id: str,
    run_id: str,
    alert_id: str,
    created_at: datetime,
    *,
    directive: bool,
) -> SocMemoryUseRow:
    return SocMemoryUseRow(
        use_id=use_id,
        idempotency_key=f"memory-use:{use_id}",
        memory_id="MEM-QUALITY-1",
        memory_version=1,
        run_id=run_id,
        alert_id=alert_id,
        tenant_id="pingan",
        effect="directive" if directive else "context_only",
        directive_applied=directive,
        created_at=created_at,
        use_payload={},
    )


def _memory_feedback(
    feedback_id: str,
    use_id: str,
    run_id: str,
    alert_id: str,
    created_at: datetime,
) -> SocMemoryFeedbackRow:
    return SocMemoryFeedbackRow(
        feedback_id=feedback_id,
        idempotency_key=f"memory-feedback:{feedback_id}",
        use_id=use_id,
        memory_id="MEM-QUALITY-1",
        memory_version=1,
        run_id=run_id,
        alert_id=alert_id,
        source="analyst_correction",
        trust="high",
        alignment="contradicts",
        created_at=created_at,
        feedback_payload={},
    )


def _pattern_observation(
    observation_id: str,
    lineage_key: str,
    aggregation_key: str,
    run_id: str,
    alert_id: str,
    observed_at: datetime,
    label: str,
    verdict: str,
) -> SocMemoryPatternObservationRow:
    return SocMemoryPatternObservationRow(
        observation_id=observation_id,
        idempotency_key=f"pattern:{observation_id}",
        aggregation_key=aggregation_key,
        lineage_key=lineage_key,
        content_hash="e" * 64,
        tenant_id="pingan",
        environment="dev",
        data_class="simulation",
        profile_id="pingan.soc",
        profile_version="7",
        feature_schema_version="pingan.soc.memory_features.v5",
        occurrence_key="f" * 64,
        source_type="analysis_run",
        source_id=f"source:{alert_id}",
        run_id=run_id,
        alert_id=alert_id,
        pattern_dimension="compound",
        pattern_value=f"compound:{lineage_key}",
        mocked=True,
        observed_at=observed_at,
        window_start=observed_at - timedelta(days=1),
        window_end=observed_at + timedelta(days=29),
        created_at=observed_at,
        observation_payload={
            "signature": {"label": label},
            "lesson": {"verdict": verdict},
        },
    )


def _memory_candidate_row(
    candidate_id: str,
    lineage_key: str,
    created_at: datetime,
) -> SocMemoryCandidateRow:
    return SocMemoryCandidateRow(
        candidate_id=candidate_id,
        candidate_type="benign_pattern",
        target_artifact="tenant_memory",
        status="confirmed",
        tenant_scope="pingan",
        tenant_id="pingan",
        source_type="repeated_pattern",
        source_id="memory_pattern:" + "c" * 64,
        source_run_id="RUN-BEHAVIOR-A",
        source_alert_id="ALT-BEHAVIOR-A",
        idempotency_key=f"candidate:{candidate_id}",
        confidence=0.9,
        decision_impact="detection_decision",
        runtime_decision_allowed=False,
        review_required=False,
        reviewed_by_actor_id="analyst-1",
        reviewed_at=created_at,
        summary="OpenVPN 内部业务访问误报经验",
        content="Reviewed business lesson",
        created_at=created_at,
        updated_at=created_at,
        candidate_payload={"metadata": {"lineage_key": lineage_key}},
    )


def _memory_record_row(
    memory_id: str,
    candidate_id: str,
    created_at: datetime,
    *,
    version: int = 1,
) -> SocMemoryRecordRow:
    return SocMemoryRecordRow(
        memory_id=memory_id,
        version=version,
        memory_type="benign_pattern",
        target_artifact="tenant_memory",
        status="confirmed",
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id=candidate_id,
        source_type="repeated_pattern",
        source_run_id="RUN-BEHAVIOR-A",
        source_alert_id="ALT-BEHAVIOR-A",
        content_hash="1" * 64,
        facets_hash="2" * 64,
        retrieval_enabled=True,
        confidence=0.9,
        created_by_actor_id="analyst-1",
        summary="OpenVPN 内部业务访问误报经验",
        content="Reviewed business lesson",
        created_at=created_at,
        updated_at=created_at,
        record_payload={},
    )


def _memory_use_with_verdicts(
    use_id: str,
    run_id: str,
    alert_id: str,
    created_at: datetime,
    *,
    base_verdict: str = "suspicious",
    effective_verdict: str = "false_positive",
) -> SocMemoryUseRow:
    return SocMemoryUseRow(
        use_id=use_id,
        idempotency_key=f"memory-use:{use_id}",
        memory_id="MEM-BEHAVIOR-A",
        memory_version=1,
        run_id=run_id,
        alert_id=alert_id,
        tenant_id="pingan",
        effect="overridden",
        directive_applied=True,
        created_at=created_at,
        use_payload={
            "base_verdict": base_verdict,
            "effective_verdict": effective_verdict,
        },
    )


def _memory_feedback_with_verdict(
    feedback_id: str,
    use_id: str,
    run_id: str,
    alert_id: str,
    created_at: datetime,
    *,
    final_verdict: str = "false_positive",
    alignment: str = "supports",
) -> SocMemoryFeedbackRow:
    return SocMemoryFeedbackRow(
        feedback_id=feedback_id,
        idempotency_key=f"memory-feedback:{feedback_id}",
        use_id=use_id,
        memory_id="MEM-BEHAVIOR-A",
        memory_version=1,
        run_id=run_id,
        alert_id=alert_id,
        source="analyst_correction",
        trust="high",
        alignment=alignment,
        created_at=created_at,
        feedback_payload={"final_verdict": final_verdict},
    )
