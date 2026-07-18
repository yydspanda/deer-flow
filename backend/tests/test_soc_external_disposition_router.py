from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.gateway.routers import soc_external_dispositions
from soc_agent.contracts import (
    SocExternalDispositionEvent,
    SocExternalDispositionIngressCommand,
)
from soc_agent.core import SocExternalDispositionService
from soc_agent.external_disposition import InMemoryExternalDispositionRepository


def _request(*, user_id: str = "admin-1", system_role: str = "admin") -> SimpleNamespace:
    return SimpleNamespace(
        headers={"x-soc-surface": "web", "x-trace-id": "trace-external-1"},
        state=SimpleNamespace(
            user=SimpleNamespace(id=user_id, system_role=system_role),
            auth_source="session",
        ),
        app=SimpleNamespace(state=SimpleNamespace()),
    )


def _command(
    *,
    source_event_id: str = "ZEUS-EVT-1",
    external_status: str = "pending_manual_review",
) -> SocExternalDispositionIngressCommand:
    return SocExternalDispositionIngressCommand(
        event=SocExternalDispositionEvent(
            external_system="zeus",
            external_case_id="ZEUS-CASE-1",
            source_event_id=source_event_id,
            source_version="1",
            external_status=external_status,
            external_reason="operator synchronized the current ticket state",
            updated_at=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
            raw_payload_hash="a" * 64,
        )
    )


def test_external_disposition_ingress_applies_and_replays_one_event() -> None:
    repository = InMemoryExternalDispositionRepository()
    service = SocExternalDispositionService(repository=repository)
    command = _command()

    first = soc_external_dispositions.apply_external_disposition(
        command,
        _request(),
        service,
    )
    duplicate = soc_external_dispositions.apply_external_disposition(
        command,
        _request(),
        service,
    )

    assert first.idempotent is False
    assert first.record.event.source_event_id == "ZEUS-EVT-1"
    assert first.record.apply_status.value == "unmatched"
    assert duplicate.idempotent is True
    assert duplicate.record.disposition_id == first.record.disposition_id
    assert len(repository.list_external_dispositions()) == 1


def test_external_disposition_ingress_rejects_changed_idempotent_retry() -> None:
    service = SocExternalDispositionService(
        repository=InMemoryExternalDispositionRepository(),
    )
    soc_external_dispositions.apply_external_disposition(
        _command(),
        _request(),
        service,
    )

    with pytest.raises(HTTPException) as exc_info:
        soc_external_dispositions.apply_external_disposition(
            _command(external_status="closed_false_positive"),
            _request(),
            service,
        )

    assert exc_info.value.status_code == 409
    assert "different content" in exc_info.value.detail


def test_external_disposition_ingress_requires_admin_or_adapter_role() -> None:
    service = SocExternalDispositionService(
        repository=InMemoryExternalDispositionRepository(),
    )

    with pytest.raises(HTTPException) as exc_info:
        soc_external_dispositions.apply_external_disposition(
            _command(),
            _request(user_id="analyst-1", system_role="user"),
            service,
        )

    assert exc_info.value.status_code == 403
    assert "external_disposition_adapter, soc_admin" in exc_info.value.detail


def test_external_disposition_ingress_reports_unavailable_service() -> None:
    with pytest.raises(HTTPException) as exc_info:
        soc_external_dispositions.apply_external_disposition(
            _command(),
            _request(),
            SocExternalDispositionService(),
        )

    assert exc_info.value.status_code == 503
    assert "requires a SocExternalDispositionRepository" in exc_info.value.detail


def test_external_disposition_ingress_contract_requires_source_event_id() -> None:
    with pytest.raises(ValidationError, match="source_event_id"):
        SocExternalDispositionIngressCommand(
            event=SocExternalDispositionEvent(
                external_system="zeus",
                external_case_id="ZEUS-CASE-1",
                external_status="pending_manual_review",
                updated_at=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
                raw_payload_hash="a" * 64,
            )
        )


def test_external_disposition_router_exposes_application_ingress() -> None:
    paths = {route.path for route in soc_external_dispositions.router.routes}

    assert "/api/soc/external-dispositions" in paths
