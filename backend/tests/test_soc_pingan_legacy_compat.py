from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.pingan_compat.app import create_pingan_compat_app
from soc_agent.contracts import ProcessingJobStatus
from soc_agent.db import SqlAlchemyProcessingJobRepository, create_soc_tables
from soc_agent.integrations.pingan.legacy_compat import (
    PingAnLegacyTaskRequest,
    PingAnLegacyTaskService,
    extract_pingan_legacy_task_metadata,
    project_legacy_task_status,
)


def _repository() -> SqlAlchemyProcessingJobRepository:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    create_soc_tables(engine)
    return SqlAlchemyProcessingJobRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _request(*, changed: bool = False) -> PingAnLegacyTaskRequest:
    return PingAnLegacyTaskRequest(
        app_code="zeus",
        flow_id="alert_agent",
        session_id="session-1",
        alert_id="1965449",
        alert_data={
            "alert": {
                "id": "1965449",
                "executeType": 3,
                "profileCode": "RPAADM_002631",
                "ruleCode": "RULE-REVERSE-SHELL",
                "changed": changed,
            }
        },
    )


def test_pingan_adapter_extracts_priority_and_queryable_task_fields() -> None:
    metadata = extract_pingan_legacy_task_metadata(_request())

    assert metadata.execute_type == "3"
    assert metadata.profile_code == "RPAADM_002631"
    assert metadata.rule_code == "RULE-REVERSE-SHELL"
    assert metadata.detection_key == "rule_code:RULE-REVERSE-SHELL"
    assert metadata.priority == 3
    assert metadata.queue_name == "deepseek-v4-flash"
    assert metadata.model_name == "deepseek-v4-flash-0731"


@pytest.mark.parametrize(
    ("status", "legacy"),
    [
        (ProcessingJobStatus.QUEUED, "PENDING"),
        (ProcessingJobStatus.CLAIMED, "STARTED"),
        (ProcessingJobStatus.PRECHECKING, "STARTED"),
        (ProcessingJobStatus.ANALYZING, "STARTED"),
        (ProcessingJobStatus.PROJECTING, "STARTED"),
        (ProcessingJobStatus.COMPLETED, "SUCCESS"),
        (ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED, "SUCCESS"),
        (ProcessingJobStatus.EXPIRED_BEFORE_ANALYSIS, "SUCCESS"),
        (ProcessingJobStatus.FAILED, "FAILURE"),
    ],
)
def test_internal_status_is_projected_only_at_the_legacy_boundary(
    status: ProcessingJobStatus,
    legacy: str,
) -> None:
    assert project_legacy_task_status(status) == legacy


def test_task_service_returns_same_task_for_exact_replay() -> None:
    repository = _repository()
    service = PingAnLegacyTaskService(repository=repository)
    now = datetime(2026, 9, 1, 2, 0, tzinfo=UTC)

    first = service.submit(_request(), now=now)
    replay = service.submit(_request(), now=now)

    assert first == replay
    assert first.status == "PENDING"
    persisted = repository.get(first.id)
    assert persisted is not None
    assert persisted.metadata["app_code"] == "zeus"
    assert persisted.metadata["flow_id"] == "alert_agent"
    assert persisted.metadata["session_id"] == "session-1"
    assert persisted.detection_key == "rule_code:RULE-REVERSE-SHELL"
    assert persisted.expires_at == now.replace(minute=30)


def test_non_alert_legacy_task_does_not_receive_alert_queue_deadline() -> None:
    repository = _repository()
    service = PingAnLegacyTaskService(repository=repository)
    request = _request().model_copy(deep=True)
    request.alert_data["alert"]["executeType"] = -1

    response = service.submit(
        request,
        now=datetime(2026, 9, 1, 2, 0, tzinfo=UTC),
    )

    persisted = repository.get(response.id)
    assert persisted is not None
    assert persisted.expires_at is None


def test_legacy_http_api_preserves_wire_shape_and_authenticates_against_allowed_key_set() -> None:
    repository = _repository()
    app = create_pingan_compat_app(
        service=PingAnLegacyTaskService(repository=repository),
        app_keys={"common": "local-test-secret"},
    )
    client = TestClient(app)

    unauthorized = client.post(
        "/workflow/task",
        json=_request().model_dump(mode="json"),
        headers={"Authorization": "Bearer wrong"},
    )
    submitted = client.post(
        "/workflow/task",
        json=_request().model_dump(mode="json"),
        headers={
            "Authorization": "Bearer local-test-secret",
            "X-Idempotency-Key": "caller-key-1",
        },
    )

    assert unauthorized.status_code == 403
    assert submitted.status_code == 200
    body = submitted.json()
    assert set(body) == {"id", "status", "result"}
    assert body["status"] == "PENDING"
    assert body["result"] is None

    wrong_status_auth = client.get(
        "/task/task_status",
        params={"task_id": body["id"]},
        headers={"app-key": "wrong"},
    )
    status = client.get(
        "/task/task_status",
        params={"task_id": body["id"]},
        headers={"app-key": "local-test-secret"},
    )

    assert wrong_status_auth.status_code == 403
    assert status.status_code == 200
    assert status.json() == body


def test_legacy_status_authenticates_before_revealing_task_existence() -> None:
    app = create_pingan_compat_app(
        service=PingAnLegacyTaskService(repository=_repository()),
        app_keys={"common": "local-test-secret"},
    )
    client = TestClient(app)

    unauthorized = client.get(
        "/task/task_status",
        params={"task_id": "missing-job"},
        headers={"app-key": "wrong"},
    )
    authorized = client.get(
        "/task/task_status",
        params={"task_id": "missing-job"},
        headers={"app-key": "local-test-secret"},
    )

    assert unauthorized.status_code == 403
    assert authorized.status_code == 404


def test_tracked_legacy_request_example_keeps_old_zeus_wire_shape() -> None:
    sample_path = Path(__file__).resolve().parents[2] / "backend/samples/pingan_dev/legacy-task-request.example.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))

    assert sample["app_code"] == "zeus"
    assert sample["flow_id"] == "alert_agent"
    assert list(sample["alert_data"]) == ["<replace-entire-alert_data-object-with-approved-complete-payload>"]
    assert "message" not in sample["alert_data"]


def test_legacy_http_api_rejects_oversized_content_length_before_processing() -> None:
    app = create_pingan_compat_app(
        service=PingAnLegacyTaskService(repository=_repository()),
        app_keys={"zeus": "local-test-secret"},
        max_request_bytes=100,
    )
    response = TestClient(app).post(
        "/workflow/task",
        json=_request().model_dump(mode="json"),
        headers={"Authorization": "Bearer local-test-secret"},
    )

    assert response.status_code == 413


@pytest.mark.anyio
async def test_legacy_http_api_rejects_oversized_chunked_body() -> None:
    app = create_pingan_compat_app(
        service=PingAnLegacyTaskService(repository=_repository()),
        app_keys={"zeus": "local-test-secret"},
        max_request_bytes=100,
    )
    encoded = json.dumps(_request().model_dump(mode="json")).encode()

    async def chunks():
        yield encoded[:80]
        yield encoded[80:]

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        response = await client.post(
            "/workflow/task",
            content=chunks(),
            headers={
                "Authorization": "Bearer local-test-secret",
                "Content-Type": "application/json",
            },
        )

    assert response.status_code == 413
