from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.gateway.routers import soc_approvals
from app.gateway.routers.soc_dependencies import soc_service_context_from_request
from soc_agent.contracts import (
    ActorContext,
    EntrySurface,
    SocAgentApprovalGrant,
    SocAgentApprovalRequest,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
)
from soc_agent.core import SocAgentApprovalService


class InMemoryApprovalGrantRepository:
    def __init__(self) -> None:
        self.grants: dict[str, SocAgentApprovalGrant] = {}
        self.requests: dict[str, SocAgentApprovalRequest] = {}

    def save_approval_grant(self, grant: SocAgentApprovalGrant) -> None:
        self.grants[grant.approval_grant_id] = grant

    def get_approval_grant(self, approval_grant_id: str) -> SocAgentApprovalGrant | None:
        return self.grants.get(approval_grant_id)

    def get_approval_grant_by_token(self, execution_token_id: str) -> SocAgentApprovalGrant | None:
        for grant in self.grants.values():
            if grant.execution_token_id == execution_token_id:
                return grant
        return None

    def save_approval_request(self, approval_request: SocAgentApprovalRequest) -> None:
        self.requests[approval_request.approval_request_id] = approval_request

    def get_approval_request(self, approval_request_id: str) -> SocAgentApprovalRequest | None:
        return self.requests.get(approval_request_id)

    def list_approval_requests(
        self,
        *,
        status: str | None = "pending",
        limit: int = 50,
    ) -> list[SocAgentApprovalRequest]:
        requests = list(self.requests.values())
        if status is not None:
            requests = [request for request in requests if request.status == status]
        return requests[:limit]


class FakeRequest:
    def __init__(
        self,
        headers: dict[str, str] | None = None,
        user_id: str | None = None,
        system_role: str = "user",
    ) -> None:
        self.headers = headers or {}
        if user_id is not None:
            self.state = SimpleNamespace(user=SimpleNamespace(id=user_id, system_role=system_role))


def _approval_request() -> SocAgentApprovalRequest:
    return SocAgentApprovalRequest(
        approval_request_id="APR-API-001",
        permission_decision_id="PERM-API-001",
        route="response.block_ip",
        action="response.block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        reason="requires approval",
        requested_by=ActorContext(actor_id="analyst-1", surface=EntrySurface.WEB, roles=["analyst"]),
    )


def test_soc_approvals_api_creates_grant() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository)

    grant = soc_approvals.create_approval_grant(
        soc_approvals.ApprovalGrantRequest(
            approval_request=_approval_request(),
            reason="approved containment scope",
            expires_in_seconds=300,
        ),
        FakeRequest(
            {
                "x-soc-surface": "web",
                "idempotency-key": "idem-approve-1",
            },
            user_id="approver-1",
            system_role="admin",
        ),
        service=service,
    )

    assert grant.approval_grant_id.startswith("APG-")
    assert grant.approved_by.actor_id == "approver-1"
    assert grant.approved_by.surface == EntrySurface.WEB
    assert grant.idempotency_key == "idem-approve-1"
    assert repository.get_approval_grant_by_token(grant.execution_token_id) == grant


def test_soc_approvals_api_creates_and_lists_approval_requests() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(request_repository=repository)
    approval_request = _approval_request()

    created = soc_approvals.create_approval_request(approval_request, service=service)
    listed = soc_approvals.list_approval_requests(service=service, status="pending", limit=50)
    fetched = soc_approvals.get_approval_request("APR-API-001", service=service)

    assert created == approval_request
    assert listed.items == [approval_request]
    assert fetched == approval_request
    assert repository.get_approval_request("APR-API-001") == approval_request


def test_soc_approvals_api_maps_missing_request_to_404() -> None:
    service = SocAgentApprovalService(request_repository=InMemoryApprovalGrantRepository())

    with pytest.raises(HTTPException) as exc_info:
        soc_approvals.get_approval_request("APR-MISSING", service=service)

    assert exc_info.value.status_code == 404


def test_soc_approvals_api_dry_runs_and_executes_approved_action() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository)
    grant = service.approve(
        _approval_request(),
        context=soc_service_context_from_request(
            FakeRequest({"x-soc-surface": "web"}, user_id="approver-1", system_role="admin"),
            include_soc_roles=True,
        ),
        reason="approved containment scope",
    )

    dry_run = soc_approvals.dry_run_approved_action(
        SocAgentApprovedActionCommand(
            execution_token_id=grant.execution_token_id,
            route="response.block_ip",
            action="response.block_ip",
        ),
        FakeRequest({"x-soc-surface": "web"}, user_id="analyst-2"),
        service=service,
    )
    executed = soc_approvals.execute_approved_action(
        SocAgentApprovedActionCommand(
            execution_token_id=grant.execution_token_id,
            route="response.block_ip",
            action="response.block_ip",
            dry_run=False,
            payload={"ip": "203.0.113.8"},
        ),
        FakeRequest({"x-soc-surface": "web", "idempotency-key": "idem-execute-1"}, user_id="analyst-2"),
        service=service,
    )

    assert dry_run.payload["dry_run"] is True
    assert executed.payload["dry_run"] is False
    assert executed.payload["external_side_effect"] == "not_executed"
    assert repository.get_approval_grant(grant.approval_grant_id).status == "consumed"


def test_soc_approvals_api_maps_missing_token_to_404() -> None:
    service = SocAgentApprovalService(grant_repository=InMemoryApprovalGrantRepository())

    with pytest.raises(HTTPException) as exc_info:
        soc_approvals.dry_run_approved_action(
            SocAgentApprovedActionCommand(
                execution_token_id="SAT-MISSING",
                route="response.block_ip",
                action="response.block_ip",
            ),
            FakeRequest(),
            service=service,
        )

    assert exc_info.value.status_code == 404


def test_soc_approvals_router_exposes_mvp_paths() -> None:
    paths = {route.path for route in soc_approvals.router.routes}

    assert "/api/soc/approvals/requests" in paths
    assert "/api/soc/approvals/requests/{approval_request_id}" in paths
    assert "/api/soc/approvals/grants" in paths
    assert "/api/soc/approvals/actions/dry-run" in paths
    assert "/api/soc/approvals/actions/execute" in paths
