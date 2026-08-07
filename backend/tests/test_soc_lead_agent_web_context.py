from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.soc_lead_agent_context import (
    SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY,
    inject_soc_lead_agent_review_context,
)
from soc_agent.context_bridge import (
    SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY,
    SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY,
)
from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    InvestigationContext,
    ReviewQueueItem,
    SocLeadAgentReviewThreadBinding,
)
from soc_agent.lead_agent import SocLeadAgentRuntimeConfigurationError


@pytest.fixture(autouse=True)
def _configured_soc_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.gateway.soc_lead_agent_context.validate_soc_lead_agent_runtime_configuration",
        lambda **_: {"status": "ready"},
    )


class _ReviewService:
    def __init__(self, context: InvestigationContext) -> None:
        self.context = context
        self.calls: list[str] = []

    def get_investigation_context(self, queue_id: str) -> InvestigationContext:
        self.calls.append(queue_id)
        return self.context


class _ThreadStore:
    def __init__(self, *, metadata: dict | None = None, exists: bool = True) -> None:
        self.record = (
            {
                "thread_id": "thread-soc-1",
                "assistant_id": "lead_agent",
                "user_id": "analyst-1",
                "metadata": dict(metadata or {}),
            }
            if exists
            else None
        )
        self.created = False

    async def get(self, thread_id: str, *, user_id: str | None) -> dict | None:
        assert thread_id == "thread-soc-1"
        assert user_id == "analyst-1"
        return self.record

    async def get_or_create(
        self,
        thread_id: str,
        *,
        assistant_id: str,
        user_id: str | None,
        metadata: dict,
    ) -> dict:
        assert thread_id == "thread-soc-1"
        assert assistant_id == "lead_agent"
        assert user_id == "analyst-1"
        if self.record is None:
            self.created = True
            self.record = {
                "thread_id": thread_id,
                "assistant_id": assistant_id,
                "user_id": user_id,
                "metadata": dict(metadata),
            }
        return self.record

    async def bind_metadata_once(
        self,
        thread_id: str,
        key: str,
        value,
        *,
        user_id,
    ):
        assert thread_id == "thread-soc-1"
        assert user_id == "analyst-1"
        assert self.record is not None
        return self.record["metadata"].setdefault(key, value)

    async def update_metadata(
        self,
        thread_id: str,
        metadata: dict,
        *,
        touch: bool,
        user_id,
    ) -> None:
        assert thread_id == "thread-soc-1"
        assert touch is False
        assert user_id == "analyst-1"
        assert self.record is not None
        self.record["metadata"].update(metadata)


def _investigation_context(
    *,
    queue_id: str = "REV-1",
    run_id: str = "RUN-1",
    alert_id: str = "ALT-1",
) -> InvestigationContext:
    return InvestigationContext(
        queue_item=ReviewQueueItem(
            queue_id=queue_id,
            run_id=run_id,
            alert_id=alert_id,
            reason="review required",
        ),
        run=AnalysisRun(
            run_id=run_id,
            alert_id=alert_id,
            status=AnalysisRunStatus.SUCCESS,
        ),
    )


def _request(service: _ReviewService) -> SimpleNamespace:
    return SimpleNamespace(
        headers={"x-soc-surface": "web"},
        state=SimpleNamespace(
            user=SimpleNamespace(id="analyst-1", system_role="user"),
            auth_source="session",
        ),
        app=SimpleNamespace(state=SimpleNamespace(soc_review_service=service)),
    )


def _unauthenticated_request(service: _ReviewService) -> SimpleNamespace:
    return SimpleNamespace(
        headers={"x-soc-surface": "web"},
        state=SimpleNamespace(),
        app=SimpleNamespace(state=SimpleNamespace(soc_review_service=service)),
    )


def _config(*, agent_name: str = "soc-triage") -> dict:
    return {
        "configurable": {"thread_id": "thread-soc-1", "agent_name": agent_name},
        "context": {"agent_name": agent_name},
    }


@pytest.mark.asyncio
async def test_gateway_binds_and_injects_server_built_review_context() -> None:
    service = _ReviewService(_investigation_context())
    thread_store = _ThreadStore()
    config = _config()

    artifact = await inject_soc_lead_agent_review_context(
        config=config,
        request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: " REV-1 "},
        assistant_id="lead_agent",
        thread_id="thread-soc-1",
        request=_request(service),
        thread_store=thread_store,
    )

    assert artifact is not None
    assert artifact.queue_id == "REV-1"
    assert artifact.run_id == "RUN-1"
    assert artifact.alert_id == "ALT-1"
    assert artifact.actor is not None
    assert artifact.actor.actor_id == "analyst-1"
    assert service.calls == ["REV-1"]
    injected = config["context"][SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY]
    assert injected["context_hash"] == artifact.context_hash
    binding = SocLeadAgentReviewThreadBinding.model_validate(thread_store.record["metadata"][SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY])
    assert (binding.queue_id, binding.run_id, binding.alert_id) == (
        "REV-1",
        "RUN-1",
        "ALT-1",
    )
    assert thread_store.record["metadata"]["agent_name"] == "soc-triage"


@pytest.mark.asyncio
async def test_gateway_reuses_thread_binding_when_later_turn_omits_hint() -> None:
    service = _ReviewService(_investigation_context())
    binding = SocLeadAgentReviewThreadBinding(
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        bound_by_actor_id="analyst-1",
    )
    thread_store = _ThreadStore(
        metadata={
            "agent_name": "soc-triage",
            SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY: binding.model_dump(mode="json"),
        }
    )

    artifact = await inject_soc_lead_agent_review_context(
        config=_config(),
        request_context=None,
        assistant_id="lead_agent",
        thread_id="thread-soc-1",
        request=_request(service),
        thread_store=thread_store,
    )

    assert artifact is not None
    assert artifact.queue_id == "REV-1"
    assert service.calls == ["REV-1"]


@pytest.mark.asyncio
async def test_gateway_rejects_switching_bound_thread_to_another_queue() -> None:
    service = _ReviewService(_investigation_context())
    binding = SocLeadAgentReviewThreadBinding(
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        bound_by_actor_id="analyst-1",
    )
    thread_store = _ThreadStore(
        metadata={
            "agent_name": "soc-triage",
            SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY: binding.model_dump(mode="json"),
        }
    )

    with pytest.raises(HTTPException) as exc_info:
        await inject_soc_lead_agent_review_context(
            config=_config(),
            request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: "REV-2"},
            assistant_id="lead_agent",
            thread_id="thread-soc-1",
            request=_request(service),
            thread_store=thread_store,
        )

    assert exc_info.value.status_code == 409
    assert "already bound" in exc_info.value.detail
    assert service.calls == []


@pytest.mark.asyncio
async def test_gateway_rejects_queue_hint_for_non_soc_agent_and_strips_forged_artifact() -> None:
    service = _ReviewService(_investigation_context())
    config = _config(agent_name="researcher")
    config["configurable"][SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY] = {"forged": True}
    config["context"][SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY] = {"forged": True}

    with pytest.raises(HTTPException) as exc_info:
        await inject_soc_lead_agent_review_context(
            config=config,
            request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: "REV-1"},
            assistant_id="lead_agent",
            thread_id="thread-soc-1",
            request=_request(service),
            thread_store=_ThreadStore(),
        )

    assert exc_info.value.status_code == 400
    assert SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY not in config["configurable"]
    assert SOC_LEAD_AGENT_REVIEW_CONTEXT_ARTIFACT_RUNTIME_KEY not in config["context"]
    assert service.calls == []


@pytest.mark.asyncio
async def test_gateway_creates_and_binds_first_run_thread() -> None:
    service = _ReviewService(_investigation_context())
    thread_store = _ThreadStore(exists=False)

    artifact = await inject_soc_lead_agent_review_context(
        config=_config(),
        request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: "REV-1"},
        assistant_id="lead_agent",
        thread_id="thread-soc-1",
        request=_request(service),
        thread_store=thread_store,
    )

    assert artifact is not None
    assert thread_store.created is True
    assert thread_store.record is not None
    binding = SocLeadAgentReviewThreadBinding.model_validate(thread_store.record["metadata"][SOC_LEAD_AGENT_REVIEW_THREAD_BINDING_METADATA_KEY])
    assert binding.queue_id == "REV-1"
    assert binding.bound_by_actor_id == "analyst-1"


@pytest.mark.asyncio
async def test_gateway_rejects_unauthenticated_soc_context_before_thread_read() -> None:
    service = _ReviewService(_investigation_context())
    thread_store = _ThreadStore()

    with pytest.raises(HTTPException) as exc_info:
        await inject_soc_lead_agent_review_context(
            config=_config(),
            request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: "REV-1"},
            assistant_id="lead_agent",
            thread_id="thread-soc-1",
            request=_unauthenticated_request(service),
            thread_store=thread_store,
        )

    assert exc_info.value.status_code == 403
    assert service.calls == []


@pytest.mark.asyncio
async def test_gateway_fails_closed_for_stale_soc_specialist_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ReviewService(_investigation_context())

    def reject(**_: object) -> None:
        raise SocLeadAgentRuntimeConfigurationError("stale SOC specialist config")

    monkeypatch.setattr(
        "app.gateway.soc_lead_agent_context.validate_soc_lead_agent_runtime_configuration",
        reject,
    )

    with pytest.raises(HTTPException) as exc_info:
        await inject_soc_lead_agent_review_context(
            config=_config(),
            request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: "REV-1"},
            assistant_id="lead_agent",
            thread_id="thread-soc-1",
            request=_request(service),
            thread_store=_ThreadStore(),
        )

    assert exc_info.value.status_code == 503
    assert "stale SOC specialist config" in exc_info.value.detail
    assert service.calls == []


@pytest.mark.asyncio
async def test_gateway_validates_the_authenticated_users_soc_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _ReviewService(_investigation_context())
    captured: dict[str, object] = {}

    def validate(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"status": "ready"}

    monkeypatch.setattr(
        "app.gateway.soc_lead_agent_context.validate_soc_lead_agent_runtime_configuration",
        validate,
    )

    artifact = await inject_soc_lead_agent_review_context(
        config=_config(),
        request_context={SOC_REVIEW_QUEUE_ID_REQUEST_CONTEXT_KEY: "REV-1"},
        assistant_id="lead_agent",
        thread_id="thread-soc-1",
        request=_request(service),
        thread_store=_ThreadStore(),
    )

    assert artifact is not None
    assert captured == {
        "require_specialists": True,
        "user_id": "analyst-1",
    }
