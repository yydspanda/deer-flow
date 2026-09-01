"""Hermetic acceptance for the PingAn legacy execution plane."""

from __future__ import annotations

import copy
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.pingan_compat import create_pingan_compat_app
from soc_agent.contracts import CallbackOutboxStatus, ProcessingJobStatus
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.db import (
    SqlAlchemyAlertRepository,
    SqlAlchemyProcessingJobRepository,
    to_sync_database_url,
    upgrade_soc_schema,
)
from soc_agent.integrations.pingan.legacy_compat.callback import (
    PingAnLegacyCallbackDispatcher,
    StaticPingAnZeusAlertCallbackPort,
)
from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PINGAN_LEGACY_QUEUE_NAME,
)
from soc_agent.integrations.pingan.legacy_compat.result_mapper import (
    PingAnLegacyResultMapper,
)
from soc_agent.integrations.pingan.legacy_compat.service import (
    PingAnLegacyTaskService,
)
from soc_agent.integrations.pingan.legacy_compat.worker import PingAnLegacyJobWorker
from soc_agent.integrations.pingan.legacy_compat.zeus_lifecycle import (
    PingAnAlertLifecycleService,
    StaticPingAnZeusAlertLifecyclePort,
)

_FAKE_FIXTURE_VERSION = "soc.pingan_legacy_fake_fixture.v1"
_FAKE_ALERT_ID = "FAKE-LEGACY-ACCEPTANCE"
_FAKE_RAW_MESSAGE = 'synthetic SyslogClient[1]: 2026-01-01 00:00:00|!fixture|!alert|!{"attack_type":"synthetic connectivity probe","sip":"192.0.2.10","dip":"198.51.100.20","proto":"tcp","severity":1}'


def run_pingan_legacy_fake_acceptance(
    *,
    database_url: str,
    report_path: Path | None = None,
    app_code: str = "common",
    app_key: str = "fake-acceptance-key",
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    """Exercise the old API and durable execution semantics without private IO."""

    started = time.monotonic()
    now = observed_at or datetime.now(UTC)
    payload = _build_synthetic_alert_payload()

    upgrade_soc_schema(database_url)
    engine = create_engine(to_sync_database_url(database_url), pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    jobs = SqlAlchemyProcessingJobRepository(session_factory)
    alerts = SqlAlchemyAlertRepository(session_factory)
    task_service = PingAnLegacyTaskService(repository=jobs)
    app = create_pingan_compat_app(
        service=task_service,
        app_keys={app_code: app_key},
    )

    crash_payload = copy.deepcopy(payload)
    crash_payload["alert"]["alertId"] = "FAKE-LEASE-RECOVERY"
    normal_alert_id = str(payload["alert"].get("alertId") or payload["alert"].get("id") or "FAKE-NORMAL")
    requests = (
        _legacy_request(
            app_code=app_code,
            alert_id="FAKE-LEASE-RECOVERY",
            alert_data=crash_payload,
        ),
        _legacy_request(
            app_code=app_code,
            alert_id=normal_alert_id,
            alert_data=payload,
        ),
    )

    try:
        with TestClient(app) as client:
            submitted: list[dict[str, Any]] = []
            for index, request_body in enumerate(requests, start=1):
                response = client.post(
                    "/workflow/task",
                    headers={
                        "Authorization": f"Bearer {app_key}",
                        "X-Idempotency-Key": f"fake-acceptance-{index}",
                    },
                    json=request_body,
                )
                response.raise_for_status()
                submitted.append(response.json())
            replay = client.post(
                "/workflow/task",
                headers={
                    "Authorization": f"Bearer {app_key}",
                    "X-Idempotency-Key": "fake-acceptance-2",
                },
                json=requests[1],
            )
            replay.raise_for_status()

            claim_at = max(now, datetime.now(UTC)) + timedelta(milliseconds=1)
            dead_claim = jobs.claim_next(
                queue_name=PINGAN_LEGACY_QUEUE_NAME,
                worker_id="fake-dead-worker",
                lease_seconds=1,
                now=claim_at,
            )
            if dead_claim is None:
                raise RuntimeError("acceptance could not claim crash-recovery job")

            runtime_now = claim_at + timedelta(seconds=2)
            analysis_service = SocAnalysisService(
                runtime=DeterministicAnalysisRuntime(),
                repository=alerts,
                summary_repository=alerts,
                audit_repository=alerts,
                review_queue_repository=alerts,
                analysis_persistence=alerts,
            )
            lifecycle = PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({}))
            worker = PingAnLegacyJobWorker(
                repository=jobs,
                lifecycle_service=lifecycle,
                analysis_service=analysis_service,
                result_mapper=PingAnLegacyResultMapper(),
                worker_id="fake-acceptance-worker",
                lease_seconds=30,
                max_attempts=2,
                now=lambda: runtime_now,
            )
            callback_port = StaticPingAnZeusAlertCallbackPort([{"code": 200}])
            callback = PingAnLegacyCallbackDispatcher(
                repository=jobs,
                port=callback_port,
                dispatcher_id="fake-acceptance-callback",
                lease_seconds=30,
                max_attempts=2,
                now=lambda: runtime_now,
            )

            worker.run_once()
            worker.run_once()
            delivered_callbacks = [callback.run_once(), callback.run_once()]
            statuses = [
                client.get(
                    "/task/task_status",
                    params={"task_id": item["id"]},
                    headers={"app-key": app_key},
                ).json()
                for item in submitted
            ]

        jobs_by_id = [jobs.get(item["id"]) for item in submitted]
        if any(job is None for job in jobs_by_id):
            raise RuntimeError("acceptance lost a submitted processing job")
        job_records = [job for job in jobs_by_id if job is not None]
        callbacks = [item for item in delivered_callbacks if item is not None]
        callback_attempts = [attempt for callback_record in callbacks for attempt in jobs.list_callback_attempts(callback_record.outbox_id)]
        event_types = {job.job_id: [event.event_type for event in jobs.list_events(job.job_id)] for job in job_records}
        recovered_job_id = dead_claim.job_id
        invariants = {
            "legacy_http_contract_passed": all(status["status"] == "SUCCESS" for status in statuses),
            "idempotent_replay_reused_job": replay.json()["id"] == submitted[1]["id"],
            "expired_lease_recovered": "lease_expired_requeued" in event_types[recovered_job_id],
            "all_jobs_terminal": all(job.status is ProcessingJobStatus.COMPLETED for job in job_records),
            "callbacks_independent_and_delivered": len(callbacks) == 2 and all(item.status is CallbackOutboxStatus.DELIVERED for item in callbacks),
            "callback_attempts_audited": len(callback_attempts) == 2,
            "raw_payload_excluded_from_report": True,
        }
        report = {
            "schema_version": "soc.pingan_legacy_fake_acceptance.v2",
            "generated_at": datetime.now(UTC).isoformat(),
            "mode": "fake",
            "simulated": True,
            "proves_real_internal_connectivity": False,
            "fixture": {
                "kind": "synthetic_protocol_alert",
                "version": _FAKE_FIXTURE_VERSION,
                "alert_id": normal_alert_id,
            },
            "jobs": [
                {
                    "job_id": job.job_id,
                    "alert_id": job.alert_id,
                    "status": job.status.value,
                    "attempt_count": job.attempt_count,
                    "run_id": job.run_id,
                    "model_name": job.model_name,
                    "event_types": event_types[job.job_id],
                    "result_keys": sorted((job.result_payload or {}).keys()),
                }
                for job in job_records
            ],
            "callbacks": [
                {
                    "outbox_id": item.outbox_id,
                    "job_id": item.job_id,
                    "status": item.status.value,
                    "attempt_count": item.attempt_count,
                }
                for item in callbacks
            ],
            "callback_attempts": [
                {
                    "outbox_id": attempt.outbox_id,
                    "attempt_number": attempt.attempt_number,
                    "outcome": attempt.outcome.value,
                    "dispatcher_id": attempt.dispatcher_id,
                }
                for attempt in callback_attempts
            ],
            "invariants": invariants,
            "passed": all(invariants.values()),
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "secrets_included": False,
        }
        if report_path is not None:
            report_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            report_path.write_text(
                json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            report_path.chmod(0o600)
        return report
    finally:
        engine.dispose()


def _build_synthetic_alert_payload() -> dict[str, Any]:
    """Return a value-free alert that exercises the PingAn adapter and Runtime."""

    return {
        "tenant_id": "pingan",
        "alert": {
            "alertId": _FAKE_ALERT_ID,
            "alertCode": "FAKE-LEGACY-001",
            "alertName": "Synthetic legacy compatibility check",
            "riskLevel": "low",
            "createAt": "2026-01-01T00:00:00+08:00",
            "executeType": "0",
            "hitLog": [
                {
                    "topic": "soc_fake_acceptance",
                    "topicName": "Synthetic protocol fixture",
                    "ruleCode": "FAKE_LEGACY_PROTOCOL_001",
                    "ruleName": "Synthetic legacy protocol fixture",
                    "zeusRawLogs": [{"message": _FAKE_RAW_MESSAGE}],
                }
            ],
        },
        "relatedAlertList": [],
    }


def _legacy_request(
    *,
    app_code: str,
    alert_id: str,
    alert_data: dict[str, Any],
) -> dict[str, Any]:
    return {
        "app_code": app_code,
        "flow_id": "alert_agent",
        "session_id": f"acceptance-{alert_id}",
        "alert_id": alert_id,
        "alert_data": alert_data,
    }


__all__ = ["run_pingan_legacy_fake_acceptance"]
