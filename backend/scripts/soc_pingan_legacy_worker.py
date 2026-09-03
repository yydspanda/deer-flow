"""Run durable PingAn compatibility workers and callback dispatcher."""

from __future__ import annotations

import os
import signal
import socket
import sys
import tempfile
import threading
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.application import build_soc_analysis_service  # noqa: E402
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    SqlAlchemyProcessingJobRepository,
    to_sync_database_url,
    upgrade_soc_schema,
)
from soc_agent.integrations.pingan.legacy_compat import (  # noqa: E402
    PingAnLegacyCallbackDispatcher,
    PingAnLegacyJobWorker,
    PingAnLegacyResultMapper,
)
from soc_agent.integrations.pingan.legacy_compat.execution import (  # noqa: E402
    PingAnLegacyExecutionSupervisor,
)
from soc_agent.integrations.pingan.legacy_compat.wiring import (  # noqa: E402
    PingAnLegacyWorkerSettings,
    build_pingan_callback_port,
    build_pingan_lifecycle_service,
)


def main() -> None:
    settings = PingAnLegacyWorkerSettings.from_env()
    if settings.auto_migrate:
        upgrade_soc_schema(settings.database_url)
    engine = create_engine(
        to_sync_database_url(settings.database_url),
        pool_pre_ping=True,
    )
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    processing_repository = SqlAlchemyProcessingJobRepository(session_factory)
    alert_repository = SqlAlchemyAlertRepository(session_factory)
    execute_actions = _env_bool("SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS", False)
    analysis_service = build_soc_analysis_service(
        repository=alert_repository,
        execute_authorized_actions=execute_actions,
    )
    unknown_lifecycle_analysis_service = (
        build_soc_analysis_service(
            repository=alert_repository,
            execute_authorized_actions=False,
        )
        if execute_actions
        else analysis_service
    )
    http_client = httpx.Client(
        timeout=None,
        follow_redirects=False,
        trust_env=False,
    )
    try:
        lifecycle = build_pingan_lifecycle_service(
            settings,
            client=http_client,
        )
        callback_port = build_pingan_callback_port(
            settings,
            client=http_client,
        )
        process_identity = f"{socket.gethostname()}-{os.getpid()}"
        workers = [
            PingAnLegacyJobWorker(
                repository=processing_repository,
                lifecycle_service=lifecycle,
                analysis_service=analysis_service,
                unknown_lifecycle_analysis_service=(unknown_lifecycle_analysis_service),
                result_mapper=PingAnLegacyResultMapper(),
                worker_id=f"{process_identity}-worker-{index + 1}",
                lineage_repository=alert_repository,
                lease_seconds=settings.worker_lease_seconds,
                max_attempts=settings.worker_max_attempts,
                retry_backoff_seconds=settings.worker_retry_backoff_seconds,
            )
            for index in range(settings.worker_concurrency)
        ]
        callback = PingAnLegacyCallbackDispatcher(
            repository=processing_repository,
            port=callback_port,
            dispatcher_id=f"{process_identity}-callback",
            lease_seconds=settings.callback_lease_seconds,
            max_attempts=settings.callback_max_attempts,
            retry_backoff_seconds=settings.callback_retry_backoff_seconds,
        )
        supervisor = PingAnLegacyExecutionSupervisor(
            workers=workers,
            callback_dispatcher=callback,
            poll_interval_seconds=settings.poll_interval_seconds,
        )
        stop_event = threading.Event()

        def request_stop(_signum: int, _frame: object) -> None:
            stop_event.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        readiness_path = _publish_readiness()
        try:
            supervisor.run_forever(stop_event=stop_event)
        finally:
            if readiness_path is not None:
                readiness_path.unlink(missing_ok=True)
    finally:
        http_client.close()
        engine.dispose()


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value")


def _publish_readiness() -> Path | None:
    raw_path = os.environ.get("SOC_PINGAN_LEGACY_WORKER_READY_FILE", "").strip()
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        raise ValueError("SOC_PINGAN_LEGACY_WORKER_READY_FILE must be absolute")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
        text=True,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(str(os.getpid()))
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return path


if __name__ == "__main__":
    main()
