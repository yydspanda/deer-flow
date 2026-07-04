from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from soc_agent.contracts import ActorContext, EntrySurface, ServiceRequestContext, SocAgentChatRequest
from soc_agent.lead_agent_chat import SocLeadAgentChatService, SocLeadAgentProfileNotInstalledError
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
