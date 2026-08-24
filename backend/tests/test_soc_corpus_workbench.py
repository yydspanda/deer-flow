from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.gateway.routers import soc_corpus_workbench
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    AnalysisProviderPurpose,
    AnalysisRequestJournal,
    AnalysisRunStatus,
    AuditAction,
    EntrySurface,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    ServiceRequestContext,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryTargetArtifact,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.core import SocAnalysisService, SocMemoryPatternService, SocMemoryService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.demo.corpus_workbench import (
    SocCorpusWorkbenchService,
    _compare_operational_label,
    _project_operational_outcome,
)
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings

_CORPUS = Path(__file__).resolve().parents[2] / "datas" / "source" / "full_alert_2026_month_forth_sample_200.pkl"


class _FakeRequest:
    def __init__(self, *, system_role: str = "admin") -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.state = SimpleNamespace(
            auth_source="session",
            user=SimpleNamespace(
                id="corpus-workbench-test",
                system_role=system_role,
            ),
        )
        self.headers = {
            "x-soc-surface": "web",
            "x-request-id": "corpus-workbench-request",
            "x-trace-id": "corpus-workbench-trace",
        }


class _FakeWorkbenchService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def process_alert(self, alert_id: str, *, context):
        self.calls.append(alert_id)
        return SimpleNamespace(alert_id=alert_id, actor=context.actor)

    def get_execution(self, alert_id: str):
        self.calls.append(f"execution:{alert_id}")
        return SimpleNamespace(alert_id=alert_id, status="running")

    def get_audit_bundle(self, alert_id: str, *, context):
        self.calls.append(f"audit:{alert_id}")
        return SimpleNamespace(alert_id=alert_id, actor=context.actor)


def _repository(tmp_path: Path) -> SqlAlchemyAlertRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'soc-corpus-workbench.sqlite'}")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


@pytest.mark.skipif(not _CORPUS.is_file(), reason="local PingAn corpus unavailable")
def test_corpus_workbench_projects_all_real_alerts_and_memory_readiness(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = SocCorpusWorkbenchService(
        repository=repository,
        analysis_service=SimpleNamespace(),
        pattern_service=SocMemoryPatternService(
            repository=repository,
            candidate_repository=repository,
            profile_registry=build_soc_memory_profile_registry(),
        ),
        source_path=_CORPUS,
        settings=SocLLMSettings(
            mode=SocAnalyzerMode.LLM,
            model_name="fixture-model",
        ),
        database_file="soc-corpus-workbench.sqlite",
    )

    state = service.get_state()

    assert state.source.alert_count == 210
    assert len(state.alerts) == 210
    assert state.readiness.fingerprint_coverage_count == 192
    assert state.readiness.decision_eligible_alert_count == 121
    # Feature schema v5 splits same-rule network alerts by canonical service,
    # vulnerability and behavior family instead of counting a broad rule cohort.
    assert state.readiness.recurrent_group_count == 14
    assert state.readiness.recurrent_alert_count == 59
    assert state.readiness.candidate_window_group_count == 2
    assert state.readiness.candidate_window_alert_count == 18
    galaxy = next(item for item in state.groups if item.rule_name == "GalaxyLab_T1003-SAM-Dumping")
    assert galaxy.alert_count == 14
    assert galaxy.decision_eligible is True
    assert galaxy.max_window_alert_count == 11
    assert all(item.workflow_state == "ready" for item in state.alerts)
    assert [item.sequence_number for item in state.alerts] == list(range(1, len(state.alerts) + 1))
    assert [item.observed_at for item in state.alerts] == sorted(item.observed_at for item in state.alerts)
    galaxy_alerts = [item for item in state.alerts if item.group_id == galaxy.group_id]
    assert sum(item.can_process for item in galaxy_alerts) == 1
    assert galaxy_alerts[0].can_process is True
    assert all(item.blocked_by_alert_id == galaxy_alerts[0].alert_id for item in galaxy_alerts[1:])
    serialized = state.model_dump_json()
    assert "zeusRawLogs" not in serialized
    assert "raw_message" not in serialized


def test_operational_projection_keeps_detection_and_disposition_separate() -> None:
    suspicious = SimpleNamespace(verdict=Verdict.SUSPICIOUS)
    false_positive = SimpleNamespace(verdict=Verdict.FALSE_POSITIVE)

    assert _project_operational_outcome(
        decision=suspicious,
        disposition=None,
    ) == ("transfer", "verdict:suspicious")
    assert _project_operational_outcome(
        decision=suspicious,
        disposition=SocOperationalDisposition.IGNORED,
    ) == ("ignore", "disposition:ignored")
    assert _project_operational_outcome(
        decision=false_positive,
        disposition=None,
    ) == ("ignore", "verdict:false_positive")

    valid_label = SimpleNamespace(
        operational_label_available=True,
        operational_label="忽略",
        label_temporal_status="valid",
    )
    invalid_time_label = SimpleNamespace(
        operational_label_available=True,
        operational_label="忽略",
        label_temporal_status="label_precedes_alert",
    )
    assert (
        _compare_operational_label(
            valid_label,
            projection="ignore",
            decision_available=True,
        )
        == "matched"
    )
    assert (
        _compare_operational_label(
            invalid_time_label,
            projection="ignore",
            decision_available=True,
        )
        == "unscored"
    )


@pytest.mark.skipif(not _CORPUS.is_file(), reason="local PingAn corpus unavailable")
def test_corpus_workbench_execution_projects_runtime_then_pattern_persistence(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    pattern_service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        profile_registry=build_soc_memory_profile_registry(),
    )
    service = SocCorpusWorkbenchService(
        repository=repository,
        analysis_service=SimpleNamespace(),
        pattern_service=pattern_service,
        source_path=_CORPUS,
        settings=SocLLMSettings(mode=SocAnalyzerMode.STUB),
        database_file="soc-corpus-workbench.sqlite",
    )
    case = next(iter(service._cases.values()))
    run = SocAnalysisService().analyze(service._payload_for_case(case))
    assert run.llm_analysis_request is not None
    run.llm_analysis_request = run.llm_analysis_request.model_copy(update={"environment": "dev-corpus-eval"})
    repository.save_run(run)

    analysis_complete = service.get_execution(case.alert_id)

    assert analysis_complete.status == "analysis_complete"
    assert analysis_complete.run_id == run.run_id
    assert analysis_complete.current_phase == "memory"
    assert next(item for item in analysis_complete.phases if item.phase == "reasoning").status == "success"
    assert next(item for item in analysis_complete.phases if item.phase == "memory").status == "running"

    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="corpus-test",
            surface=EntrySurface.API,
            roles=["soc_admin"],
            auth_source=ActorAuthSource.SESSION,
        )
    )
    aggregation = pattern_service.observe_run(
        run,
        source_type=MemoryPatternSourceType.BATCH_ALERT,
        transport_ref=f"test:{case.alert_id}",
        environment="dev-corpus-eval",
        data_class=MemoryPatternDataClass.OPERATIONAL,
        context=context,
    )

    completed = service.get_execution(case.alert_id)

    assert completed.status == "completed"
    assert completed.observation_id is not None
    memory_phase = next(item for item in completed.phases if item.phase == "memory")
    assert memory_phase.status == "success"
    assert memory_phase.metrics["window_days"] == 30.0

    candidate = SocMemoryService(candidate_repository=repository).propose_candidate(
        SocMemoryCandidateCreateCommand(
            candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
            target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
            summary="Corpus workbench regression candidate",
            content="Persisted pattern candidates must remain visible in workbench state.",
            tenant_scope="pingan",
            tenant_id="pingan",
            source=SocMemoryCandidateSource(
                source_type=SocMemoryCandidateSourceType.REPEATED_PATTERN,
                source_id=f"memory_pattern:{aggregation.observation.aggregation_key}",
                run_id=run.run_id,
                alert_id=case.alert_id,
            ),
            evidence_refs=[f"run:{run.run_id}"],
            validity=SocMemoryCandidateValidity(notes="Regression coverage only."),
            idempotency_key=f"corpus-workbench-regression:{run.run_id}",
        )
    )

    state = service.get_state()
    projected = next(item for item in state.alerts if item.alert_id == case.alert_id)

    assert projected.candidate_id == candidate.candidate_id
    assert projected.candidate_status == candidate.status.value

    audit = service.get_audit_bundle(case.alert_id, context=context)
    assert audit.run_id == run.run_id
    assert audit.safety.dev_only is True
    assert [item.file_name for item in audit.artifacts] == [
        "01-run-manifest.json",
        "02-source-input.json",
        "03-canonical-normalization.json",
        "04-entity-extraction.json",
        "05-fact-reconstruction.json",
        "06-bounded-analysis-input.json",
        "07-model-analysis-output.json",
        "08-output-validation.json",
        "09-decision-lineage.json",
        "10-memory-pattern-write.json",
    ]
    source_input = next(item for item in audit.artifacts if item.artifact_id == "source-input")
    normalization = next(item for item in audit.artifacts if item.artifact_id == "canonical-normalization")
    model_input = next(item for item in audit.artifacts if item.artifact_id == "bounded-analysis-input")
    memory = next(item for item in audit.artifacts if item.artifact_id == "memory-pattern-write")
    assert source_input.payload["input_payload"] == run.input_payload
    assert normalization.status == "available"
    assert normalization.payload["normalized_alert"]["alert_id"] == run.alert_id
    assert normalization.payload["evidence_input_policy"]["selected_input_path"]
    assert model_input.status == "partial"
    assert model_input.source == "read_model_projection"
    assert model_input.payload["projection_lineage"]["status"] == "reconstructed_with_current_builder"
    assert model_input.payload["projection_lineage"]["exact"] is False
    assert model_input.payload["model_visible_context"]["prompt_version"] != run.prompt_version
    assert model_input.payload["runtime_request_audit"]["alert_id"] == run.alert_id
    assert "projected_field_paths" not in json.dumps(
        model_input.payload["model_visible_context"],
        ensure_ascii=False,
    )
    assert memory.payload["pattern_observation"]["observation_id"] == aggregation.observation.observation_id
    assert memory.payload["memory_candidates"][0]["candidate_id"] == candidate.candidate_id


@pytest.mark.skipif(not _CORPUS.is_file(), reason="local PingAn corpus unavailable")
def test_corpus_workbench_execution_projects_active_provider_journal(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    service = SocCorpusWorkbenchService(
        repository=repository,
        analysis_service=SimpleNamespace(),
        pattern_service=SocMemoryPatternService(
            repository=repository,
            candidate_repository=repository,
            profile_registry=build_soc_memory_profile_registry(),
        ),
        source_path=_CORPUS,
        settings=SocLLMSettings(mode=SocAnalyzerMode.STUB),
        database_file="soc-corpus-workbench.sqlite",
    )
    case = next(iter(service._cases.values()))
    run = SocAnalysisService().analyze(service._payload_for_case(case))
    assert run.llm_analysis_request is not None
    request = run.llm_analysis_request.model_copy(
        update={"environment": "dev-corpus-eval"},
    )
    run.llm_analysis_request = request
    run.status = AnalysisRunStatus.RUNNING
    run.ended_at = None
    run.steps = [
        item
        for item in run.steps
        if item.step_name
        in {
            "normalize",
            "entity_extract",
            "fact_reconstruct",
            "select_skills",
            "build_analysis_input",
        }
    ]
    journal = AnalysisRequestJournal(
        action=AuditAction.ANALYSIS,
        request_id="corpus-live-provider-test",
        actor=ActorContext(
            actor_id="corpus-test",
            surface=EntrySurface.API,
            roles=["soc_admin"],
            auth_source=ActorAuthSource.SESSION,
        ),
        request_schema_version=request.schema_version,
        request_hash="a" * 64,
        source_type=request.source.source_type,
        source_system=request.source.source_system,
        detection_key=request.detection.detection_key,
        model_name="fixture-live-model",
        prompt_version="soc-analysis-test",
        provider_step_name="analyze_llm",
        provider_purpose=AnalysisProviderPurpose.PRIMARY_ANALYSIS,
        primary_evidence_present=request.primary_evidence is not None,
        supplementary_evidence_count=len(request.supplementary_evidence),
        selected_skills=[item.skill_name for item in request.skill_context.selected_skills],
    )
    run.request_journal = journal
    run.provider_request_journals = [journal]
    repository.save_run(run)

    execution = service.get_execution(case.alert_id)

    assert execution.status == "running"
    assert execution.current_phase == "reasoning"
    assert execution.provider_purpose == "primary_analysis"
    assert execution.provider_attempt_count == 1
    reasoning = next(item for item in execution.phases if item.phase == "reasoning")
    assert reasoning.status == "running"
    assert [(item.step_name, item.status) for item in reasoning.steps] == [("analyze_llm", "running")]


def test_corpus_workbench_process_endpoint_requires_admin() -> None:
    with pytest.raises(HTTPException) as exc_info:
        soc_corpus_workbench.process_corpus_workbench_alert(
            "1984426",
            request=_FakeRequest(system_role="user"),
            service=_FakeWorkbenchService(),
        )

    assert exc_info.value.status_code == 403


def test_corpus_workbench_process_endpoint_uses_authenticated_admin() -> None:
    service = _FakeWorkbenchService()

    result = soc_corpus_workbench.process_corpus_workbench_alert(
        "1984426",
        request=_FakeRequest(system_role="admin"),
        service=service,
    )

    assert service.calls == ["1984426"]
    assert result.actor.actor_id == "corpus-workbench-test"
    assert result.actor.roles == ["soc_admin"]


def test_corpus_workbench_execution_endpoint_reads_one_alert() -> None:
    service = _FakeWorkbenchService()

    result = soc_corpus_workbench.get_corpus_workbench_execution(
        "1984426",
        service=service,
    )

    assert service.calls == ["execution:1984426"]
    assert result.alert_id == "1984426"
    assert result.status == "running"


def test_corpus_workbench_audit_endpoint_requires_admin() -> None:
    with pytest.raises(HTTPException) as exc_info:
        soc_corpus_workbench.get_corpus_workbench_audit(
            "1984426",
            request=_FakeRequest(system_role="user"),
            service=_FakeWorkbenchService(),
        )

    assert exc_info.value.status_code == 403


def test_corpus_workbench_audit_endpoint_uses_authenticated_admin() -> None:
    service = _FakeWorkbenchService()

    result = soc_corpus_workbench.get_corpus_workbench_audit(
        "1984426",
        request=_FakeRequest(system_role="admin"),
        service=service,
    )

    assert service.calls == ["audit:1984426"]
    assert result.alert_id == "1984426"
    assert result.actor.roles == ["soc_admin"]
