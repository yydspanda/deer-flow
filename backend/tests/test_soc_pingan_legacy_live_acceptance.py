from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from soc_agent.contracts import (
    CallbackAttemptOutcome,
    CallbackOutboxStatus,
    ProcessingJobStatus,
    SocCallbackAttemptRecord,
    SocCallbackOutboxRecord,
    SocProcessingJobEvent,
)
from soc_agent.integrations.pingan.legacy_compat.live_acceptance import (
    run_pingan_legacy_live_acceptance,
)


def test_live_acceptance_proves_submit_replay_runtime_lifecycle_and_callback() -> None:
    calls: list[tuple[str, str]] = []
    status_polls = iter(
        [
            {"id": "JOB-1", "status": "STARTED", "result": None},
            {
                "id": "JOB-1",
                "status": "SUCCESS",
                "result": {
                    "alert_action": "转交",
                    "model_name": "deepseek-v4-flash-0731",
                    "soc_lineage": {
                        "run_id": "RUN-1",
                        "external_lifecycle_state": "pending",
                    },
                },
            },
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.url == "http://127.0.0.1:8090/workflow/task"
            assert request.headers["authorization"] == "Bearer compat-secret"
            assert json.loads(request.content) == _task_request()
            return httpx.Response(
                200,
                json={"id": "JOB-1", "status": "PENDING", "result": None},
            )
        assert request.url == "http://127.0.0.1:8090/task/task_status?task_id=JOB-1"
        assert request.headers["app-key"] == "compat-secret"
        return httpx.Response(200, json=next(status_polls))

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_legacy_live_acceptance(
            _task_request(),
            _valid_env(),
            repository=_EvidenceRepository(),
            client=client,
            sleeper=lambda _seconds: None,
        )

    assert report.passed is True
    assert report.outcome == "passed"
    assert report.simulated is False
    assert report.proves_real_internal_connectivity is True
    assert report.idempotent_replay_confirmed is True
    assert report.terminal_status == "SUCCESS"
    assert report.run_id_present is True
    assert report.lifecycle_mocked is False
    assert report.callback_status is CallbackOutboxStatus.DELIVERED
    assert report.callback_mocked is False
    assert report.callback_attempt_count == 1
    assert calls == [
        ("POST", "/workflow/task"),
        ("POST", "/workflow/task"),
        ("GET", "/task/task_status"),
        ("GET", "/task/task_status"),
    ]
    serialized = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert "compat-secret" not in serialized
    assert "sensitive-message" not in serialized
    assert "转交" not in serialized


def test_live_acceptance_refuses_fake_provider_modes_before_network() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    env = _valid_env()
    env["SOC_PINGAN_LEGACY_CALLBACK_MODE"] = "fake"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_legacy_live_acceptance(
            _task_request(),
            env,
            repository=_EvidenceRepository(),
            client=client,
        )

    assert report.passed is False
    assert report.outcome == "invalid_configuration"
    assert called is False


def test_live_acceptance_refuses_placeholder_request_before_network() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    request = _task_request()
    request["session_id"] = "<unique-session-id>"
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_legacy_live_acceptance(
            request,
            _valid_env(),
            repository=_EvidenceRepository(),
            client=client,
        )

    assert report.outcome == "invalid_configuration"
    assert called is False


def test_live_acceptance_fails_when_callback_was_delivered_by_fake_port() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                200,
                json={"id": "JOB-1", "status": "PENDING", "result": None},
            )
        return httpx.Response(
            200,
            json={
                "id": "JOB-1",
                "status": "SUCCESS",
                "result": {
                    "model_name": "deepseek-v4-flash-0731",
                    "soc_lineage": {
                        "run_id": "RUN-1",
                        "external_lifecycle_state": "pending",
                    },
                },
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        report = run_pingan_legacy_live_acceptance(
            _task_request(),
            _valid_env(),
            repository=_EvidenceRepository(callback_mocked=True),
            client=client,
            sleeper=lambda _seconds: None,
        )

    assert report.passed is False
    assert report.outcome == "callback_not_real"
    assert report.proves_real_internal_connectivity is False


class _EvidenceRepository:
    def __init__(self, *, callback_mocked: bool = False) -> None:
        self._callback_mocked = callback_mocked

    def list_events(self, job_id: str) -> list[SocProcessingJobEvent]:
        return [
            SocProcessingJobEvent(
                event_id="EVT-1",
                job_id=job_id,
                event_type="analysis_started",
                sequence=1,
                from_status=ProcessingJobStatus.PRECHECKING,
                to_status=ProcessingJobStatus.ANALYZING,
                worker_id="worker-1",
                attempt=1,
                occurred_at=datetime(2026, 9, 1, tzinfo=UTC),
                details={"lifecycle": {"state": "pending", "mocked": False}},
            )
        ]

    def list_callbacks(self, job_id: str) -> list[SocCallbackOutboxRecord]:
        now = datetime(2026, 9, 1, tzinfo=UTC)
        return [
            SocCallbackOutboxRecord(
                outbox_id="OUT-1",
                job_id=job_id,
                destination="pingan.zeus.alert_callback",
                idempotency_key="callback-1",
                status=CallbackOutboxStatus.DELIVERED,
                payload={"secret": "not-read-by-acceptance"},
                attempt_count=1,
                available_at=now,
                response_metadata={
                    "http_status": 200,
                    "mocked": self._callback_mocked,
                },
                created_at=now,
                updated_at=now,
                delivered_at=now,
            )
        ]

    def list_callback_attempts(self, outbox_id: str) -> list[SocCallbackAttemptRecord]:
        now = datetime(2026, 9, 1, tzinfo=UTC)
        return [
            SocCallbackAttemptRecord(
                attempt_id="ATT-1",
                outbox_id=outbox_id,
                job_id="JOB-1",
                destination="pingan.zeus.alert_callback",
                attempt_number=1,
                dispatcher_id="dispatcher-1",
                outcome=CallbackAttemptOutcome.DELIVERED,
                started_at=now,
                completed_at=now,
                response_metadata={
                    "http_status": 200,
                    "mocked": self._callback_mocked,
                },
            )
        ]


def _valid_env() -> dict[str, str]:
    return {
        "SOC_PINGAN_COMPAT_SMOKE_BASE_URL": "http://127.0.0.1:8090",
        "SOC_PINGAN_COMPAT_APP_KEYS_JSON": '{"SEC-MODEL":"compat-secret"}',
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "internal",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "internal",
        "SOC_PINGAN_COMPAT_SMOKE_TIMEOUT_SECONDS": "10",
        "SOC_PINGAN_COMPAT_SMOKE_POLL_INTERVAL_SECONDS": "0.01",
        "SOC_PINGAN_COMPAT_SMOKE_MAX_RESPONSE_BYTES": "1000000",
    }


def _task_request() -> dict:
    return {
        "app_code": "SEC-MODEL",
        "flow_id": "zeus-alert-analysis",
        "session_id": "acceptance-20260901-1",
        "alert_id": "1965449",
        "alert_data": {
            "alert": {
                "id": "1965449",
                "executeType": 0,
                "message": "sensitive-message",
            }
        },
    }
