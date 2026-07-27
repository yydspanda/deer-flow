from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    AnalysisRun,
    ConfidenceCalibrationSample,
    ConfidenceLabelReviewStatus,
    EntrySurface,
    NormalizationBaselineAcceptCommand,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceIssueType,
    NormalizationMaintenanceIssueUpdateCommand,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocNormalizationMaintenanceService, SocServiceError
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.eval import calibrate_confidence
from soc_agent.llm import LLMChatResponse
from soc_agent.normalizers import (
    build_normalization_suggestion_prompt,
    build_normalization_suggestion_report,
    normalize_alert_payload,
    run_live_normalization_suggestion,
)


class _CollectingEventSink:
    def __init__(self) -> None:
        self.events: list[SocEvent] = []

    def emit(self, event: SocEvent) -> None:
        self.events.append(event)


def _repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _payload(*, extra: str = "") -> dict:
    message = f'skyeye|!{{"attack_type":"恶意外联","sip":"30.1.1.10","dip":"30.2.2.20"{extra}' + "}"
    return {
        "alert": {
            "alertId": "PINGAN-MAINT-001",
            "alertCode": "PIE-MAINT-001",
            "alertName": "Normalization maintenance fixture",
            "hitLog": [
                {
                    "topic": "sec_guard_apt",
                    "topicName": "SkyEye APT",
                    "zeusRawLogs": [{"message": message}],
                }
            ],
        }
    }


def _context(*, roles: list[str] | None = None) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="normalization-test",
            surface=EntrySurface.TEST,
            roles=roles or [],
        )
    )


def _maintenance_service(
    repository: SqlAlchemyAlertRepository,
    *,
    event_sink: _CollectingEventSink | None = None,
) -> SocNormalizationMaintenanceService:
    return SocNormalizationMaintenanceService(
        baseline_repository=repository,
        issue_repository=repository,
        event_sink=event_sink,
    )


def _analysis_service(
    repository: SqlAlchemyAlertRepository,
    maintenance: SocNormalizationMaintenanceService,
) -> SocAnalysisService:
    return SocAnalysisService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        normalization_maintenance_monitor=maintenance,
    )


def _baseline_command(run: AnalysisRun) -> NormalizationBaselineAcceptCommand:
    assert run.normalization_report is not None
    observation = run.normalization_report.message_schemas[0]
    assert observation.parser_name
    assert observation.parser_version
    assert observation.schema_fingerprint
    return NormalizationBaselineAcceptCommand(
        source_system=run.normalization_report.source_system,
        adapter=run.normalization_report.adapter,
        parser_name=observation.parser_name,
        parser_version=observation.parser_version,
        accepted_fingerprints=[observation.schema_fingerprint],
        reason="Reviewed onboarding fixture",
    )


def test_baseline_acceptance_is_role_gated_and_persisted() -> None:
    repository = _repository()
    run = SocAnalysisService().analyze(_payload())
    service = _maintenance_service(repository)

    with pytest.raises(SocServiceError, match="requires one of roles"):
        service.accept_baseline(_baseline_command(run), context=_context())

    baseline = service.accept_baseline(
        _baseline_command(run),
        context=_context(roles=["soc_engineer"]),
    )

    assert repository.get_normalization_baseline(baseline.baseline_id) == baseline
    assert service.list_baselines() == [baseline]


def test_monitor_deduplicates_novel_schema_and_reopens_resolved_issue() -> None:
    repository = _repository()
    event_sink = _CollectingEventSink()
    maintenance = _maintenance_service(repository, event_sink=event_sink)
    baseline_run = SocAnalysisService().analyze(_payload())
    maintenance.accept_baseline(
        _baseline_command(baseline_run),
        context=_context(roles=["soc_engineer"]),
    )
    service = _analysis_service(repository, maintenance)

    known = service.analyze(_payload(), context=_context())
    assert known.normalization_monitoring_result is not None
    assert known.normalization_monitoring_result.issues == []

    changed = service.analyze(_payload(extra=',"campaign":"fixture"'), context=_context())
    assert changed.normalization_monitoring_result is not None
    novel = next(item for item in changed.normalization_monitoring_result.issues if item.issue_type is NormalizationMaintenanceIssueType.NOVEL_SCHEMA)
    assert novel.occurrence_count == 1

    repeated = service.analyze(_payload(extra=',"campaign":"fixture"'), context=_context())
    assert repeated.normalization_monitoring_result is not None
    repeated_novel = next(item for item in repeated.normalization_monitoring_result.issues if item.issue_type is NormalizationMaintenanceIssueType.NOVEL_SCHEMA)
    assert repeated_novel.issue_id == novel.issue_id
    assert repeated_novel.occurrence_count == 2

    resolved = maintenance.update_issue(
        NormalizationMaintenanceIssueUpdateCommand(
            issue_id=novel.issue_id,
            status="resolved",
            reason="Parser mapping updated in test",
        ),
        context=_context(roles=["soc_engineer"]),
    )
    assert resolved.status is NormalizationMaintenanceIssueStatus.RESOLVED

    recurring = service.analyze(_payload(extra=',"campaign":"fixture"'), context=_context())
    assert recurring.normalization_monitoring_result is not None
    reopened = next(item for item in recurring.normalization_monitoring_result.issues if item.issue_id == novel.issue_id)
    assert reopened.status is NormalizationMaintenanceIssueStatus.OPEN
    assert reopened.occurrence_count == 3
    assert any(event.event_type is SocEventType.NORMALIZATION_DRIFT_DETECTED for event in event_sink.events)


def test_missing_baseline_and_unsupported_message_create_operational_issues() -> None:
    repository = _repository()
    maintenance = _maintenance_service(repository)
    service = _analysis_service(repository, maintenance)

    parsed = service.analyze(_payload(), context=_context())
    parsed_types = {item.issue_type for item in parsed.normalization_monitoring_result.issues}
    assert NormalizationMaintenanceIssueType.BASELINE_MISSING in parsed_types
    missing_issue = next(item for item in parsed.normalization_monitoring_result.issues if item.issue_type is NormalizationMaintenanceIssueType.BASELINE_MISSING)
    maintenance.accept_baseline(
        _baseline_command(parsed),
        context=_context(roles=["soc_engineer"]),
    )
    assert repository.get_normalization_issue(missing_issue.issue_id).status is NormalizationMaintenanceIssueStatus.RESOLVED

    unsupported_payload = _payload()
    unsupported_payload["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] = "opaque vendor format"
    unsupported = service.analyze(unsupported_payload, context=_context())
    unsupported_types = {item.issue_type for item in unsupported.normalization_monitoring_result.issues}
    assert NormalizationMaintenanceIssueType.UNSUPPORTED_SCHEMA in unsupported_types


def test_configured_field_importance_rule_detects_vendor_neutral_gap() -> None:
    alert = normalize_alert_payload(_payload())
    alert.extensions["field_importance_rules"] = [
        {
            "rule_id": "campaign-to-user-id-test",
            "source_patterns": ["parsed.attack_type"],
            "expected_target": "entities.user.user_id",
            "importance": "critical",
            "reason": "test rule requires a canonical user id",
        }
    ]

    request = build_analysis_request_for_payload(alert.model_dump(mode="json"))

    gap = next(item for item in request.evidence_coverage.high_value_gaps if item.rule_id == "campaign-to-user-id-test")
    assert gap.expected_target == "entities.user.user_id"
    assert gap.importance == "critical"


def test_configured_field_importance_rule_ignores_empty_source_value() -> None:
    payload = _payload()
    payload["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] = 'skyeye|!{"attack_type":"","sip":"30.1.1.10"}'
    alert = normalize_alert_payload(payload)
    alert.extensions["field_importance_rules"] = [
        {
            "rule_id": "empty-campaign-to-user-id-test",
            "source_patterns": ["parsed.attack_type"],
            "expected_target": "entities.user.user_id",
            "importance": "critical",
            "reason": "empty source values must not create mapping gaps",
        }
    ]

    request = build_analysis_request_for_payload(alert.model_dump(mode="json"))

    assert not any(item.rule_id == "empty-campaign-to-user-id-test" for item in request.evidence_coverage.high_value_gaps)


def test_offline_suggestion_rejects_unobserved_paths_and_never_auto_applies() -> None:
    alert = normalize_alert_payload(_payload())
    alert.extensions["field_importance_rules"] = [
        {
            "rule_id": "test-gap",
            "source_patterns": ["parsed.attack_type"],
            "expected_target": "entities.user.user_id",
            "reason": "test mapping gap",
        }
    ]
    run = SocAnalysisService().analyze(alert.model_dump(mode="json"))
    prompt = build_normalization_suggestion_prompt(run)
    observed_path = next(path for path in prompt.observed_source_paths if path.endswith("#parsed.attack_type"))
    report = build_normalization_suggestion_report(
        run,
        model_name="fixture-model",
        response_content={
            "suggestions": [
                {
                    "target_path": "entities.user.user_id",
                    "source_paths": [observed_path],
                    "confidence": 0.72,
                    "rationale": "Observed field may carry the identity in this fixture.",
                },
                {
                    "target_path": "entities.network.source_ip",
                    "source_paths": ["invented.path"],
                    "confidence": 0.99,
                    "rationale": "Invented path must be rejected.",
                },
            ]
        },
    )

    assert report.generated_by == "llm_replay"
    assert report.auto_apply_allowed is False
    assert any(item.status.value == "candidate" for item in report.suggestions)
    assert any(item.status.value == "rejected" for item in report.suggestions)


def test_live_suggestion_uses_model_but_keeps_auto_apply_disabled() -> None:
    run = SocAnalysisService().analyze(_payload())
    prompt = build_normalization_suggestion_prompt(run)
    observed_path = prompt.observed_source_paths[0]

    class Client:
        def complete(self, messages, *, model_name):
            assert messages[0]["role"] == "system"
            assert model_name == "deepseek-v4-pro"
            return LLMChatResponse(
                content={
                    "suggestions": [
                        {
                            "target_path": "entities.network.source_ip",
                            "source_paths": [observed_path],
                            "confidence": 0.61,
                            "rationale": "Observed path is a candidate source for analyst review.",
                        }
                    ]
                },
                model_name="deepseek-v4-pro",
            )

    report = run_live_normalization_suggestion(
        run,
        client=Client(),
        model_name="deepseek-v4-pro",
    )

    assert report.generated_by == "llm"
    assert report.model_name == "deepseek-v4-pro"
    assert report.auto_apply_allowed is False
    assert report.suggestions[0].status.value == "candidate"


def test_confidence_calibration_outputs_review_only_profile() -> None:
    samples = [
        ConfidenceCalibrationSample(
            sample_id=f"sample-{index}",
            run_id=f"run-{index}",
            alert_id=f"alert-{index}",
            input_hash=f"hash-{index}",
            predicted_verdict=Verdict.TRUE_POSITIVE if index < 10 else Verdict.FALSE_POSITIVE,
            actual_verdict=(Verdict.TRUE_POSITIVE if index < 9 else Verdict.FALSE_POSITIVE),
            confidence=0.9 if index < 10 else 0.4,
            model_name="test-model",
            prompt_version="test-prompt-v1",
            pipeline_version="test-pipeline-v1",
            summary="Reviewed test prediction.",
            recommended_action="Keep review-only.",
            review_status=ConfidenceLabelReviewStatus.ACCEPTED,
            reviewer_id="analyst-1",
            reviewed_at="2026-07-15T00:00:00Z",
            review_reason="Golden test label.",
        )
        for index in range(20)
    ]

    report = calibrate_confidence(
        samples,
        bin_count=5,
        minimum_samples=30,
        minimum_threshold_samples=5,
    )

    assert report.sample_count == 20
    assert report.brier_score >= 0
    assert report.expected_calibration_error >= 0
    assert report.threshold_profile.auto_action_allowed is False
    assert report.threshold_profile.review_below == 0.9
    assert "provisional" in " ".join(report.warnings)
