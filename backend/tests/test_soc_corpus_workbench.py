from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.gateway.routers import soc_corpus_workbench
from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.core import SocMemoryPatternService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.demo.corpus_workbench import SocCorpusWorkbenchService
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
    assert state.readiness.fingerprint_coverage_count == 189
    # 17 groups have exact behavior recurrence; four additional detection-only
    # groups remain context-only and cannot carry a decision directive.
    assert state.readiness.recurrent_group_count == 21
    assert state.readiness.recurrent_alert_count >= 118
    assert state.readiness.candidate_window_group_count == 2
    assert state.readiness.candidate_window_alert_count == 12
    galaxy = next(item for item in state.groups if item.rule_name == "GalaxyLab_T1003-SAM-Dumping")
    assert galaxy.alert_count == 14
    assert galaxy.decision_eligible is True
    assert galaxy.max_window_alert_count == 6
    assert all(item.workflow_state == "ready" for item in state.alerts)
    serialized = state.model_dump_json()
    assert "zeusRawLogs" not in serialized
    assert "raw_message" not in serialized


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
