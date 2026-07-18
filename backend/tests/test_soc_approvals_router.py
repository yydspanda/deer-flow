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
    SocAgentApprovalRequestStatus,
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

    def get_approval_grant_by_request_id(self, approval_request_id: str) -> SocAgentApprovalGrant | None:
        return next(
            (grant for grant in self.grants.values() if grant.approval_request_id == approval_request_id),
            None,
        )

    def create_approval_request(self, approval_request: SocAgentApprovalRequest) -> bool:
        if approval_request.approval_request_id in self.requests:
            return False
        self.requests[approval_request.approval_request_id] = approval_request
        return True

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

    def resolve_approval_request(
        self,
        approval_request: SocAgentApprovalRequest,
        *,
        expected_status: SocAgentApprovalRequestStatus,
        grant: SocAgentApprovalGrant | None = None,
    ) -> bool:
        current = self.requests.get(approval_request.approval_request_id)
        if current is None or current.status is not expected_status:
            return False
        if grant is not None and self.get_approval_grant_by_request_id(approval_request.approval_request_id) is not None:
            return False
        self.requests[approval_request.approval_request_id] = approval_request
        if grant is not None:
            self.grants[grant.approval_grant_id] = grant
        return True


class FakeRequest:
    def __init__(
        self,
        headers: dict[str, str] | None = None,
        user_id: str | None = None,
        system_role: str = "user",
        auth_source: str | None = "session",
    ) -> None:
        self.headers = headers or {}
        self.state = SimpleNamespace()
        if auth_source is not None:
            self.state.auth_source = auth_source
        if user_id is not None:
            self.state.user = SimpleNamespace(id=user_id, system_role=system_role)


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
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    service.submit_request(
        _approval_request(),
        context=soc_service_context_from_request(
            FakeRequest({"x-soc-surface": "web"}, user_id="analyst-1"),
            include_soc_roles=True,
        ),
    )

    grant = soc_approvals.create_approval_grant(
        soc_approvals.ApprovalGrantRequest(
            approval_request_id="APR-API-001",
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

    created = soc_approvals.create_approval_request(
        approval_request,
        FakeRequest({"x-soc-surface": "web"}, user_id="analyst-1"),
        service=service,
    )
    listed = soc_approvals.list_approval_requests(service=service, status="pending", limit=50)
    fetched = soc_approvals.get_approval_request("APR-API-001", service=service)

    assert created.requested_by.actor_id == "analyst-1"
    assert created.requested_by.auth_source == "session"
    assert created.submitted_by == created.requested_by
    assert listed.items == [created]
    assert fetched == created
    assert repository.get_approval_request("APR-API-001") == created


def test_soc_approvals_api_rejects_forged_or_untrusted_request_actor() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(request_repository=repository)

    with pytest.raises(HTTPException) as forged:
        soc_approvals.create_approval_request(
            _approval_request(),
            FakeRequest(user_id="different-user"),
            service=service,
        )
    with pytest.raises(HTTPException) as untrusted:
        soc_approvals.create_approval_request(
            _approval_request(),
            FakeRequest(
                {"x-soc-actor-id": "analyst-1"},
                auth_source=None,
            ),
            service=service,
        )

    assert forged.value.status_code == 403
    assert untrusted.value.status_code == 403
    assert repository.requests == {}


def test_soc_approvals_api_requires_admin_to_resolve_request() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    service.submit_request(
        _approval_request(),
        context=soc_service_context_from_request(
            FakeRequest(user_id="analyst-1"),
            include_soc_roles=True,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        soc_approvals.create_approval_grant(
            soc_approvals.ApprovalGrantRequest(
                approval_request_id="APR-API-001",
                reason="analyst attempted approval",
            ),
            FakeRequest(
                {"idempotency-key": "approve:analyst"},
                user_id="analyst-2",
            ),
            service=service,
        )

    assert exc_info.value.status_code == 403
    assert repository.grants == {}


def test_soc_approvals_api_maps_stale_repeated_resolution_to_409() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    service.submit_request(
        _approval_request(),
        context=soc_service_context_from_request(
            FakeRequest(user_id="analyst-1"),
            include_soc_roles=True,
        ),
    )
    first = soc_approvals.create_approval_grant(
        soc_approvals.ApprovalGrantRequest(
            approval_request_id="APR-API-001",
            reason="approved scope",
        ),
        FakeRequest(
            {"idempotency-key": "approve:first"},
            user_id="admin-1",
            system_role="admin",
        ),
        service=service,
    )

    with pytest.raises(HTTPException) as exc_info:
        soc_approvals.create_approval_grant(
            soc_approvals.ApprovalGrantRequest(
                approval_request_id="APR-API-001",
                reason="different repeated approval",
            ),
            FakeRequest(
                {"idempotency-key": "approve:second"},
                user_id="admin-1",
                system_role="admin",
            ),
            service=service,
        )

    assert exc_info.value.status_code == 409
    assert list(repository.grants.values()) == [first]


@pytest.mark.parametrize(
    ("resolution", "expected_status"),
    [
        ("reject", SocAgentApprovalRequestStatus.REJECTED),
        ("expire", SocAgentApprovalRequestStatus.EXPIRED),
    ],
)
def test_soc_approvals_api_resolves_request_by_id(
    resolution: str,
    expected_status: SocAgentApprovalRequestStatus,
) -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    approval_request = _approval_request().model_copy(update={"approval_request_id": f"APR-API-{resolution.upper()}"})
    service.submit_request(
        approval_request,
        context=soc_service_context_from_request(
            FakeRequest(user_id="analyst-1"),
            include_soc_roles=True,
        ),
    )
    endpoint = getattr(soc_approvals, f"{resolution}_approval_request")

    resolved = endpoint(
        approval_request.approval_request_id,
        soc_approvals.ApprovalResolutionRequest(reason=f"admin chose to {resolution}"),
        FakeRequest(
            {"idempotency-key": f"{resolution}:api"},
            user_id="admin-1",
            system_role="admin",
        ),
        service=service,
    )

    assert resolved.status is expected_status
    assert resolved.resolved_by is not None
    assert resolved.resolved_by.actor_id == "admin-1"


def test_soc_approvals_api_maps_missing_request_to_404() -> None:
    service = SocAgentApprovalService(request_repository=InMemoryApprovalGrantRepository())

    with pytest.raises(HTTPException) as exc_info:
        soc_approvals.get_approval_request("APR-MISSING", service=service)

    assert exc_info.value.status_code == 404


def test_soc_approvals_api_dry_runs_and_executes_approved_action() -> None:
    repository = InMemoryApprovalGrantRepository()
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    service.submit_request(
        _approval_request(),
        context=soc_service_context_from_request(
            FakeRequest({"x-soc-surface": "web"}, user_id="analyst-1"),
            include_soc_roles=True,
        ),
    )
    grant = service.approve(
        "APR-API-001",
        context=soc_service_context_from_request(
            FakeRequest(
                {"x-soc-surface": "web", "idempotency-key": "idem-approve-1"},
                user_id="approver-1",
                system_role="admin",
            ),
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
            FakeRequest(user_id="analyst-2"),
            service=service,
        )

    assert exc_info.value.status_code == 404


def test_soc_approvals_router_exposes_mvp_paths() -> None:
    paths = {route.path for route in soc_approvals.router.routes}

    assert "/api/soc/approvals/requests" in paths
    assert "/api/soc/approvals/requests/{approval_request_id}" in paths
    assert "/api/soc/approvals/requests/{approval_request_id}/reject" in paths
    assert "/api/soc/approvals/requests/{approval_request_id}/expire" in paths
    assert "/api/soc/approvals/grants" in paths
    assert "/api/soc/approvals/actions/dry-run" in paths
    assert "/api/soc/approvals/actions/execute" in paths
