from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from soc_agent.contracts import (
    ActorContext,
    AlertSourceType,
    AlertSummary,
    AnalysisResult,
    AnalysisRun,
    AnalysisRunStatus,
    Decision,
    EntrySurface,
    EvidenceItem,
    InvestigationContext,
    ReviewQueueItem,
    ServiceRequestContext,
    SocAgentChatRequest,
    Verdict,
)
from soc_agent.lead_agent_chat import SocLeadAgentChatService, SocLeadAgentProfileNotInstalledError, SocLeadAgentReviewContextError
from soc_agent.skills import SOC_LEAD_AGENT_NAME


class FakeDeerFlowClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def stream(
        self,
        message: str,
        *,
        thread_id: str | None = None,
        **kwargs,
    ) -> Iterator[SimpleNamespace]:
        del kwargs
        self.calls.append((message, thread_id))
        yield SimpleNamespace(type="values", data={"title": "SOC"})
        yield SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "m1", "content": "ready"})
        yield SimpleNamespace(type="end", data={"usage": {"total_tokens": 1}})


class FakeReviewContextProvider:
    def __init__(self, context: InvestigationContext) -> None:
        self.context = context
        self.calls: list[str] = []

    def get_investigation_context(self, queue_id: str) -> InvestigationContext:
        self.calls.append(queue_id)
        return self.context


def _reset_deerflow_home(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr("deerflow.config.paths._paths", None)


def test_soc_lead_agent_chat_service_streams_through_deerflow_client() -> None:
    client = FakeDeerFlowClient()
    service = SocLeadAgentChatService(client_factory=lambda: client, require_profile=False)
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst", surface=EntrySurface.TUI))

    events = list(service.stream(SocAgentChatRequest(message="hello", thread_id="SOC-THREAD-1"), context=context))

    assert events[0].type == "custom"
    assert events[0].data == {
        "kind": "soc.lead_agent_entry",
        "agent_name": SOC_LEAD_AGENT_NAME,
        "thread_id": "SOC-THREAD-1",
        "actor_surface": "tui",
    }
    assert client.calls == [("hello", "SOC-THREAD-1")]
    assert events[1].type == "values"
    assert events[1].data["thread_id"] == "SOC-THREAD-1"
    assert events[2].data["content"] == "ready"
    assert events[3].type == "end"
    assert events[3].data["thread_id"] == "SOC-THREAD-1"


def test_soc_lead_agent_chat_service_bridges_review_context_to_deerflow_client() -> None:
    client = FakeDeerFlowClient()
    review_provider = FakeReviewContextProvider(_investigation_context())
    service = SocLeadAgentChatService(
        client_factory=lambda: client,
        require_profile=False,
        review_service=review_provider,
    )
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst", surface=EntrySurface.TUI))

    events = list(
        service.stream(
            SocAgentChatRequest(message="/open REV-1", queue_id="REV-1", thread_id="SOC-THREAD-1"),
            context=context,
        )
    )

    assert events[0].data["kind"] == "soc.lead_agent_entry"
    assert events[1].data["kind"] == "soc.lead_agent_review_context"
    assert events[1].data["queue_id"] == "REV-1"
    assert events[1].data["run_id"] == "RUN-1"
    assert events[1].data["alert_id"] == "ALT-1"
    assert events[1].data["context_hash"]
    assert events[1].data["artifact"]["schema_version"] == "soc.lead_agent_review_context_artifact.v1"
    assert events[1].data["artifact"]["actor"]["actor_id"] == "analyst"
    assert events[1].data["artifact"]["skill_context"]["selected_skills"]
    assert review_provider.calls == ["REV-1"]

    sent_message, sent_thread_id = client.calls[0]
    assert sent_thread_id == "SOC-THREAD-1"
    assert "<soc_review_context_artifact>" in sent_message
    assert '"queue_id": "REV-1"' in sent_message
    assert "Operator message:\nOpen and investigate this SOC review context." in sent_message
    assert events[2].type == "values"
    assert events[-1].type == "end"


def test_soc_lead_agent_chat_service_requires_review_provider_for_queue_context() -> None:
    service = SocLeadAgentChatService(client_factory=lambda: FakeDeerFlowClient(), require_profile=False)

    with pytest.raises(SocLeadAgentReviewContextError, match="requires SocReviewService"):
        list(service.stream(SocAgentChatRequest(message="/open REV-1", queue_id="REV-1")))


def test_soc_lead_agent_chat_service_requires_installed_profile(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)

    service = SocLeadAgentChatService(client_factory=lambda: FakeDeerFlowClient(), require_profile=True)

    with pytest.raises(SocLeadAgentProfileNotInstalledError, match="install-profile"):
        list(service.stream("hello"))


def test_soc_lead_agent_chat_service_accepts_installed_profile(tmp_path: Path, monkeypatch) -> None:
    _reset_deerflow_home(tmp_path, monkeypatch)
    agent_dir = get_paths().user_agent_dir(get_effective_user_id(), SOC_LEAD_AGENT_NAME)
    agent_dir.mkdir(parents=True)
    (agent_dir / "config.yaml").write_text(f"name: {SOC_LEAD_AGENT_NAME}\n", encoding="utf-8")

    client = FakeDeerFlowClient()
    service = SocLeadAgentChatService(client_factory=lambda: client, require_profile=True)

    events = list(service.stream("hello"))

    assert events[0].data["agent_name"] == SOC_LEAD_AGENT_NAME
    assert client.calls[0][0] == "hello"


def _investigation_context() -> InvestigationContext:
    analysis = AnalysisResult(
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        summary="Suspicious endpoint activity needs analyst review.",
        evidence=[EvidenceItem(source="edr", description="Process connected to suspicious IP", value="1.2.3.4")],
        reason="EDR evidence is suspicious but incomplete.",
        recommended_action="Review endpoint process tree before containment.",
    )
    decision = Decision(
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        suggested_action="Review endpoint process tree before containment.",
        needs_review=True,
        reason="Low confidence endpoint alert.",
    )
    run = AnalysisRun(
        run_id="RUN-1",
        alert_id="ALT-1",
        status=AnalysisRunStatus.NEEDS_REVIEW,
        input_hash="input-hash",
        analysis=analysis,
        decision=decision,
    )
    summary = AlertSummary(
        run_id="RUN-1",
        alert_id="ALT-1",
        source_type=AlertSourceType.EDR,
        source_system="edr",
        rule_code="EDR-001",
        rule_name="Suspicious process network",
        severity="high",
        entity_keys=["ip:1.2.3.4", "host:endpoint-1"],
        status=AnalysisRunStatus.NEEDS_REVIEW,
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        needs_review=True,
        summary="Suspicious endpoint activity needs analyst review.",
        recommended_action="Review endpoint process tree before containment.",
        input_hash="input-hash",
    )
    queue_item = ReviewQueueItem(
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        reason="low_confidence",
        source_type=AlertSourceType.EDR,
        source_system="edr",
        rule_code="EDR-001",
        rule_name="Suspicious process network",
        severity="high",
        entity_keys=["ip:1.2.3.4", "host:endpoint-1"],
        verdict=Verdict.SUSPICIOUS,
        confidence=0.62,
        summary="Suspicious endpoint activity needs analyst review.",
    )
    return InvestigationContext(queue_item=queue_item, run=run, summary=summary)
