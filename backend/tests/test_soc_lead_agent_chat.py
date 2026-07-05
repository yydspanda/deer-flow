from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from soc_agent.actions.adapters import InMemoryAssetLookupActionAdapter, SocActionAdapterRegistry
from soc_agent.actions.mcp import SocMcpToolDescriptor, build_mcp_action_adapter_registry
from soc_agent.actions.proposals import SocLeadAgentActionProposalBoundary
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
    InvestigationEvidence,
    ReviewQueueItem,
    ServiceRequestContext,
    SocAgentApprovalRequest,
    SocAgentChatRequest,
    SocAssetLookupRecord,
    Verdict,
)
from soc_agent.core import SocAgentActionDispatcher, SocAgentApprovalService, SocAgentCapabilityRouter
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


class FakeApprovalRequestRepository:
    def __init__(self) -> None:
        self.requests: dict[str, SocAgentApprovalRequest] = {}

    def save_approval_request(self, approval_request: SocAgentApprovalRequest) -> None:
        self.requests[approval_request.approval_request_id] = approval_request

    def get_approval_request(self, approval_request_id: str) -> SocAgentApprovalRequest | None:
        return self.requests.get(approval_request_id)

    def list_approval_requests(self, *, status: str | None = "pending", limit: int = 50) -> list[SocAgentApprovalRequest]:
        requests = list(self.requests.values())
        if status is not None:
            requests = [request for request in requests if request.status == status]
        return requests[:limit]


class ProposalFakeDeerFlowClient(FakeDeerFlowClient):
    def __init__(self, content: str) -> None:
        super().__init__()
        self.content = content

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
        yield SimpleNamespace(type="messages-tuple", data={"type": "ai", "id": "m1", "content": self.content})
        yield SimpleNamespace(type="end", data={"usage": {"total_tokens": 1}})


class FakeMcpToolProvider:
    def __init__(self) -> None:
        self.invocations: list[dict[str, object]] = []

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        return [SocMcpToolDescriptor(name="soc_dev_asset_locate", server="soc_dev")]

    def invoke(self, tool_name, payload, *, timeout_seconds, server_name=None):
        self.invocations.append(
            {
                "tool_name": tool_name,
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
                "server_name": server_name,
            }
        )
        return {
            "found": True,
            "company_code": "PA011",
            "biz_group": "平安科技/支付研发",
            "mocked": True,
        }


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
    assert events[1].data["artifact"]["action_evidence"][0]["action"] == "asset.locate"
    assert events[1].data["artifact"]["action_evidence"][0]["result_payload"]["mcp_result"]["company_code"] == "PA011"
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


def test_soc_lead_agent_chat_service_routes_action_proposal_to_approval_inbox() -> None:
    content = (
        "I recommend analyst-approved containment.\n"
        '<soc_action_proposal>{"route":"response.block_ip","action":"response.block_ip",'
        '"reason":"Block the confirmed malicious source IP after analyst approval.",'
        '"payload":{"ip":"1.2.3.4"},"confidence":0.82}</soc_action_proposal>'
    )
    client = ProposalFakeDeerFlowClient(content)
    request_repository = FakeApprovalRequestRepository()
    service = SocLeadAgentChatService(
        client_factory=lambda: client,
        require_profile=False,
        action_proposal_boundary=SocLeadAgentActionProposalBoundary(
            approval_service=SocAgentApprovalService(request_repository=request_repository),
        ),
    )
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst", surface=EntrySurface.TUI))

    events = list(service.stream(SocAgentChatRequest(message="contain this", thread_id="SOC-THREAD-1"), context=context))

    assert events[2].type == "messages-tuple"
    assert events[2].data["content"] == "I recommend analyst-approved containment."
    proposal_event = events[3]
    permission_event = events[4]
    approval_event = events[5]
    assert proposal_event.data["kind"] == "soc.action_proposal"
    assert proposal_event.data["action"] == "response.block_ip"
    assert proposal_event.data["confidence"] == 0.82
    assert permission_event.data["kind"] == "soc.permission_decision"
    assert permission_event.data["action"] == "response.block_ip"
    assert permission_event.data["requires_human_approval"] is True
    assert approval_event.data["kind"] == "soc.approval_request"
    assert approval_event.data["source_proposal_id"] == proposal_event.data["proposal_id"]
    assert approval_event.data["action_payload"] == {"ip": "1.2.3.4"}
    assert approval_event.data["context_refs"]["thread_id"] == "SOC-THREAD-1"

    saved = request_repository.get_approval_request(approval_event.data["approval_request_id"])
    assert saved is not None
    assert saved.source_proposal_id == proposal_event.data["proposal_id"]
    assert saved.action_payload == {"ip": "1.2.3.4"}


def test_soc_lead_agent_chat_service_dispatches_read_only_action_proposal() -> None:
    content = (
        "I will check the asset owner before suggesting a suppression target.\n"
        '<soc_action_proposal>{"route":"asset.lookup","action":"asset.lookup",'
        '"reason":"Look up asset ownership before deciding suppression target.",'
        '"payload":{"asset_key":"10.10.1.5"},"confidence":0.74}</soc_action_proposal>'
    )
    client = ProposalFakeDeerFlowClient(content)
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_lookup_record()])])
    service = SocLeadAgentChatService(
        client_factory=lambda: client,
        require_profile=False,
        action_proposal_boundary=SocLeadAgentActionProposalBoundary(
            read_only_capability_router=SocAgentCapabilityRouter(allowed_routes={"asset.lookup"}),
            read_only_action_dispatcher=SocAgentActionDispatcher(action_adapter_registry=registry),
        ),
    )
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst", surface=EntrySurface.TUI))

    events = list(service.stream(SocAgentChatRequest(message="who owns this asset", thread_id="SOC-THREAD-1"), context=context))

    assert events[2].data["content"] == "I will check the asset owner before suggesting a suppression target."
    proposal_event = events[3]
    route_event = events[4]
    permission_event = events[5]
    action_event = events[6]
    assert proposal_event.data["kind"] == "soc.action_proposal"
    assert proposal_event.data["action"] == "asset.lookup"
    assert route_event.data["kind"] == "soc.route_decision"
    assert route_event.data["route"] == "asset.lookup"
    assert route_event.data["allowed"] is True
    assert permission_event.data["kind"] == "soc.permission_decision"
    assert permission_event.data["action"] == "asset.lookup"
    assert permission_event.data["risk_level"] == "read_only"
    assert permission_event.data["allowed"] is True
    assert action_event.data["kind"] == "soc.action_result"
    assert action_event.data["action"] == "asset.lookup"
    assert action_event.data["status"] == "success"
    assert action_event.data["payload"]["asset_found"] is True
    assert action_event.data["payload"]["asset_record"]["asset_id"] == "asset-001"
    assert action_event.data["payload"]["external_side_effect"] == "read"


def test_soc_lead_agent_chat_service_dispatches_mcp_asset_locate_proposal() -> None:
    content = (
        "I will locate the impacted asset owner before assigning disposal.\n"
        '<soc_action_proposal>{"route":"asset.locate","action":"asset.locate",'
        '"reason":"Locate the impacted asset owner before assigning disposal target.",'
        '"payload":{"asset_key":"10.10.1.5","asset_type":"IP","role":"target"},'
        '"confidence":0.74}</soc_action_proposal>'
    )
    client = ProposalFakeDeerFlowClient(content)
    provider = FakeMcpToolProvider()
    registry = build_mcp_action_adapter_registry(
        [
            {
                "adapter_id": "asset-locate-soc-dev-mcp",
                "route": "asset.locate",
                "action": "asset.locate",
                "required_payload_fields": ["asset_key"],
                "required_context_refs": ["thread_id"],
                "mcp": {
                    "server": "soc_dev",
                    "tool": "soc_dev_asset_locate",
                    "timeout_seconds": 5,
                    "input_mapping": {
                        "asset_key": "query",
                        "asset_type": "asset_type",
                        "role": "role",
                    },
                    "output_fields": ["found", "company_code", "biz_group", "mocked"],
                    "result_schema_version": "soc.dev_asset_location_result.v1",
                },
            }
        ],
        provider,
    )
    service = SocLeadAgentChatService(
        client_factory=lambda: client,
        require_profile=False,
        action_proposal_boundary=SocLeadAgentActionProposalBoundary(
            read_only_capability_router=SocAgentCapabilityRouter(allowed_routes={"asset.locate"}),
            read_only_action_dispatcher=SocAgentActionDispatcher(action_adapter_registry=registry),
        ),
    )
    context = ServiceRequestContext(actor=ActorContext(actor_id="analyst", surface=EntrySurface.TUI))

    events = list(service.stream(SocAgentChatRequest(message="locate owner", thread_id="SOC-THREAD-1"), context=context))

    action_event = events[6]
    assert action_event.data["kind"] == "soc.action_result"
    assert action_event.data["action"] == "asset.locate"
    assert action_event.data["status"] == "success"
    assert action_event.data["payload"]["mcp_server"] == "soc_dev"
    assert action_event.data["payload"]["tool_name"] == "soc_dev_asset_locate"
    assert action_event.data["payload"]["mcp_result"] == {
        "found": True,
        "company_code": "PA011",
        "biz_group": "平安科技/支付研发",
        "mocked": True,
    }
    assert provider.invocations == [
        {
            "tool_name": "soc_dev_asset_locate",
            "payload": {"query": "10.10.1.5", "asset_type": "IP", "role": "target"},
            "timeout_seconds": 5,
            "server_name": "soc_dev",
        }
    ]


def test_soc_lead_agent_read_only_action_proposal_requires_route_allowlist() -> None:
    content = 'I will check ownership.\n<soc_action_proposal>{"route":"asset.lookup","action":"asset.lookup","reason":"Look up asset ownership.","payload":{"asset_key":"10.10.1.5"},"confidence":0.74}</soc_action_proposal>'
    client = ProposalFakeDeerFlowClient(content)
    registry = SocActionAdapterRegistry([InMemoryAssetLookupActionAdapter(records=[_asset_lookup_record()])])
    service = SocLeadAgentChatService(
        client_factory=lambda: client,
        require_profile=False,
        action_proposal_boundary=SocLeadAgentActionProposalBoundary(
            read_only_capability_router=SocAgentCapabilityRouter(),
            read_only_action_dispatcher=SocAgentActionDispatcher(action_adapter_registry=registry),
        ),
    )

    events = list(service.stream(SocAgentChatRequest(message="who owns this asset", thread_id="SOC-THREAD-1")))

    assert events[3].data["kind"] == "soc.action_proposal"
    assert events[4].data["kind"] == "soc.route_decision"
    assert events[4].data["route"] == "asset.lookup"
    assert events[4].data["allowed"] is False
    assert events[5].data["kind"] == "soc.permission_decision"
    assert events[5].data["allowed"] is True
    assert [event.data.get("kind") for event in events if event.type == "custom"].count("soc.action_result") == 0


def test_soc_lead_agent_chat_service_rejects_bad_action_proposal_json() -> None:
    client = ProposalFakeDeerFlowClient("Bad proposal <soc_action_proposal>{bad json}</soc_action_proposal>")
    service = SocLeadAgentChatService(client_factory=lambda: client, require_profile=False)

    events = list(service.stream(SocAgentChatRequest(message="contain this", thread_id="SOC-THREAD-1")))

    assert events[2].data["content"] == "Bad proposal"
    assert events[3].data["kind"] == "soc.action_proposal_error"
    assert "not valid JSON" in events[3].data["error"]


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
    evidence = InvestigationEvidence(
        route="asset.locate",
        action="asset.locate",
        status="success",
        message="Asset location completed.",
        result_payload={"mcp_result": {"company_code": "PA011", "mocked": True}},
        queue_id="REV-1",
        run_id="RUN-1",
        alert_id="ALT-1",
        thread_id="SOC-THREAD-1",
        source_proposal_id="PROP-1",
    )
    return InvestigationContext(queue_item=queue_item, run=run, summary=summary, action_evidence=[evidence])


def _asset_lookup_record() -> SocAssetLookupRecord:
    return SocAssetLookupRecord(
        asset_key="srv-payments-01",
        asset_id="asset-001",
        hostname="srv-payments-01",
        primary_ip="10.10.1.5",
        owner="payments-sre",
        business_unit="payments",
        environment="prod",
        criticality="critical",
        source="unit-test",
    )
