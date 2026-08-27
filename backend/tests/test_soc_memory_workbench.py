from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.gateway.routers import soc_memory_workbench
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import AnalysisRun, AnalysisRunStatus
from soc_agent.core import SocMemoryPatternService, SocServiceConflictError
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.demo.memory_workbench import SocMemoryWorkbenchService
from soc_agent.llm import SocAnalyzerMode, SocLLMSettings

_CORPUS = Path(__file__).resolve().parents[2] / "validation" / "compact_zeus" / "data" / "corpus" / "full_alert_validation_corpus.pkl"


class _FakeRequest:
    def __init__(self, *, system_role: str = "admin") -> None:
        self.app = SimpleNamespace(state=SimpleNamespace())
        self.state = SimpleNamespace(
            auth_source="session",
            user=SimpleNamespace(
                id="memory-workbench-test",
                system_role=system_role,
            ),
        )
        self.headers = {
            "x-soc-surface": "web",
            "x-request-id": "memory-workbench-request",
            "x-trace-id": "memory-workbench-trace",
        }


class _FakeWorkbenchService:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def process_alert(self, alert_id: str, *, context):
        self.calls.append(alert_id)
        return SimpleNamespace(alert_id=alert_id, actor=context.actor)


def _repository(tmp_path) -> SqlAlchemyAlertRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'soc-workbench.sqlite'}")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


@pytest.mark.skipif(not _CORPUS.is_file(), reason="local PingAn corpus unavailable")
def test_workbench_loads_the_fixed_real_cohort_without_mutating_it(tmp_path) -> None:
    repository = _repository(tmp_path)
    service = SocMemoryWorkbenchService(
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
        database_file="soc-workbench.sqlite",
    )

    state = service.get_state()

    assert len(state.alerts) == 14
    assert [item.alert_id for item in state.alerts[:6]] == [
        "1984426",
        "1984281",
        "1984525",
        "1984510",
        "1984659",
        "1984919",
    ]
    assert state.progress.next_alert_id == "1984426"
    assert state.progress.next_action == "process_construction"
    assert state.alerts[0].workflow_state == "ready"
    assert all(item.workflow_state == "locked" for item in state.alerts[1:])
    assert state.cohort.detection_key == ("leagsoft-edr:rule_code:rpaadm_002010")
    assert state.cohort.behavior_components == [
        "attack_family:source_category:可疑操作行为",
        "command_module:updatedeploy.dll",
        "command_switch:classid",
        "command_switch:deploymenthandlerfullpath",
        "command_switch:runhandlercomserver",
        "parent_service:wuauserv",
        "process:services.exe",
        "process:svchost.exe",
        "process_image:wuaucltcore.exe",
        "process_path:windows/uus/amd64/wuaucltcore.exe",
        "target_class:windows_protected_registry_hive",
    ]


def test_workbench_process_endpoint_requires_admin() -> None:
    request = _FakeRequest(system_role="user")

    with pytest.raises(HTTPException) as exc_info:
        soc_memory_workbench.process_memory_workbench_alert(
            "1984426",
            request=request,
            service=_FakeWorkbenchService(),
        )

    assert exc_info.value.status_code == 403


def test_workbench_process_endpoint_uses_authenticated_admin_context() -> None:
    request = _FakeRequest(system_role="admin")
    service = _FakeWorkbenchService()

    result = soc_memory_workbench.process_memory_workbench_alert(
        "1984426",
        request=request,
        service=service,
    )

    assert service.calls == ["1984426"]
    assert result.actor.actor_id == "memory-workbench-test"
    assert result.actor.roles == ["soc_admin"]


def test_workbench_process_endpoint_maps_service_conflict_to_http_409() -> None:
    class _ConflictingWorkbenchService:
        def process_alert(self, alert_id: str, *, context):
            raise SocServiceConflictError(f"conflicting observation for {alert_id}")

    with pytest.raises(HTTPException) as exc_info:
        soc_memory_workbench.process_memory_workbench_alert(
            "1984426",
            request=_FakeRequest(system_role="admin"),
            service=_ConflictingWorkbenchService(),
        )

    assert exc_info.value.status_code == 409


@pytest.mark.skipif(not _CORPUS.is_file(), reason="local PingAn corpus unavailable")
def test_workbench_does_not_surface_runs_from_an_old_profile_generation(
    tmp_path,
) -> None:
    repository = _repository(tmp_path)
    service = SocMemoryWorkbenchService(
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
        database_file="soc-workbench.sqlite",
    )
    case = service._cases["1984426"]
    repository.save_run(
        AnalysisRun(
            run_id="RUN-OLD-PROFILE",
            alert_id=case.spec.alert_id,
            status=AnalysisRunStatus.SUCCESS,
            input_hash=case.payload_hash,
        )
    )

    state = service.get_state()

    first = state.alerts[0]
    assert state.progress.processed_count == 0
    assert state.progress.next_alert_id == "1984426"
    assert first.workflow_state == "ready"
    assert first.run_id is None


def test_workbench_dependency_is_not_exposed_without_explicit_flag(
    monkeypatch,
) -> None:
    monkeypatch.delenv("SOC_DEV_MEMORY_WORKBENCH_ENABLED", raising=False)
    request = _FakeRequest()

    with pytest.raises(HTTPException) as exc_info:
        soc_memory_workbench.get_soc_memory_workbench_service(request)

    assert exc_info.value.status_code == 404
