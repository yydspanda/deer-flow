"""Live, secret-safe acceptance for the old ZEUS task compatibility plane."""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from soc_agent.contracts import CallbackAttemptOutcome, CallbackOutboxStatus
from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PingAnLegacyTaskRequest,
    PingAnLegacyTaskResponse,
)
from soc_agent.protocols import ProcessingJobRepository

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_TERMINAL_STATUSES = frozenset({"SUCCESS", "FAILURE"})

PingAnLegacyLiveAcceptanceOutcome = Literal[
    "passed",
    "invalid_configuration",
    "authentication_failed",
    "provider_unavailable",
    "invalid_response",
    "timeout",
    "task_failed",
    "runtime_not_executed",
    "lifecycle_not_real",
    "callback_not_delivered",
    "callback_not_real",
]


class PingAnLegacyLiveAcceptanceReport(BaseModel):
    """Business-payload-free proof for one internal compatibility run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_legacy_live_acceptance.v2"] = "soc.pingan_legacy_live_acceptance.v2"
    outcome: PingAnLegacyLiveAcceptanceOutcome
    passed: bool
    simulated: Literal[False] = False
    proves_real_internal_connectivity: bool = False
    endpoint_scope: Literal["loopback"] = "loopback"
    submit_path: str = "/workflow/task"
    status_path: str = "/task/task_status"
    request_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    alert_id_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    task_id: str | None = None
    fresh_submission_confirmed: bool = False
    resumed_existing_confirmed: bool = False
    idempotent_replay_confirmed: bool = False
    submission_status: str | None = None
    replay_status: str | None = None
    terminal_status: str | None = None
    status_poll_count: int = Field(default=0, ge=0)
    result_present: bool = False
    result_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    run_id_present: bool = False
    model_name_present: bool = False
    model_name: str | None = None
    lifecycle_state: str | None = None
    lifecycle_mocked: bool | None = None
    callback_status: CallbackOutboxStatus | None = None
    callback_attempt_count: int = Field(default=0, ge=0)
    callback_mocked: bool | None = None
    duration_ms: int = Field(ge=0)
    http_status: int | None = None
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def run_pingan_legacy_live_acceptance(
    task_request: Mapping[str, Any],
    environ: Mapping[str, str] | None = None,
    *,
    repository: ProcessingJobRepository,
    client: httpx.Client | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    resume_existing: bool = False,
) -> PingAnLegacyLiveAcceptanceReport:
    """Submit or resume one task, prove replay idempotency, and verify real evidence."""

    started = time.monotonic()
    state: dict[str, Any] = {}
    values = dict(os.environ if environ is None else environ)
    try:
        request = PingAnLegacyTaskRequest.model_validate(task_request)
        if request.alert_data is None:
            raise ValueError("live acceptance requires alert_data")
        if _contains_placeholder(request.model_dump(mode="json")):
            raise ValueError("live acceptance request contains placeholders")
        base_url = _loopback_base_url(
            values.get(
                "SOC_PINGAN_COMPAT_SMOKE_BASE_URL",
                "http://127.0.0.1:8090",
            )
        )
        app_key = _app_key(values, app_code=request.app_code)
        _require_internal_mode(values, "SOC_PINGAN_LEGACY_LIFECYCLE_MODE")
        _require_internal_mode(values, "SOC_PINGAN_LEGACY_CALLBACK_MODE")
        timeout_seconds = _positive_float(
            values.get("SOC_PINGAN_COMPAT_SMOKE_TIMEOUT_SECONDS", "900"),
            name="SOC_PINGAN_COMPAT_SMOKE_TIMEOUT_SECONDS",
        )
        poll_interval = _positive_float(
            values.get(
                "SOC_PINGAN_COMPAT_SMOKE_POLL_INTERVAL_SECONDS",
                "1",
            ),
            name="SOC_PINGAN_COMPAT_SMOKE_POLL_INTERVAL_SECONDS",
        )
        max_response_bytes = _positive_int(
            values.get(
                "SOC_PINGAN_COMPAT_SMOKE_MAX_RESPONSE_BYTES",
                "5000000",
            ),
            name="SOC_PINGAN_COMPAT_SMOKE_MAX_RESPONSE_BYTES",
        )
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        return _report(
            outcome="invalid_configuration",
            started=started,
            state=state,
            error_type=exc.__class__.__name__,
            error_message="PingAn compatibility live-acceptance configuration is invalid.",
        )

    request_payload = request.model_dump(mode="json")
    state.update(
        request_sha256=_stable_json_sha256(request_payload),
        alert_id_sha256=_sha256_text(request.alert_id),
    )
    deadline = started + timeout_seconds
    owns_client = client is None
    transport = client or httpx.Client(timeout=timeout_seconds)
    try:
        first, first_http_status = _submit(
            transport,
            base_url=base_url,
            app_key=app_key,
            request=request_payload,
            max_response_bytes=max_response_bytes,
            timeout_seconds=timeout_seconds,
        )
        state.update(
            task_id=first.id,
            submission_status=first.status,
            fresh_submission_confirmed=first.status == "PENDING" and not resume_existing,
            resumed_existing_confirmed=resume_existing and first.status != "PENDING",
            http_status=first_http_status,
        )
        if not state["fresh_submission_confirmed"] and not state["resumed_existing_confirmed"]:
            if resume_existing:
                error_message = "Compatibility API still reports PENDING, so this run cannot prove that it resumed a previously submitted task; retry the same private request later."
            else:
                error_message = "Compatibility API returned an existing task; use a fresh session/request or explicitly resume this exact request."
            raise _AcceptanceError(
                outcome="invalid_response",
                error_message=error_message,
            )

        replay, replay_http_status = _submit(
            transport,
            base_url=base_url,
            app_key=app_key,
            request=request_payload,
            max_response_bytes=max_response_bytes,
            timeout_seconds=timeout_seconds,
        )
        state.update(
            replay_status=replay.status,
            idempotent_replay_confirmed=replay.id == first.id,
            http_status=replay_http_status,
        )
        if not state["idempotent_replay_confirmed"]:
            raise _AcceptanceError(
                outcome="invalid_response",
                error_message="Compatibility API created a second task for an identical replay.",
            )

        terminal = replay if replay.status in _TERMINAL_STATUSES else None
        while terminal is None:
            _sleep_before_poll(
                deadline=deadline,
                poll_interval=poll_interval,
                sleeper=sleeper,
            )
            terminal, status_http = _status(
                transport,
                base_url=base_url,
                app_key=app_key,
                task_id=first.id,
                max_response_bytes=max_response_bytes,
                timeout_seconds=timeout_seconds,
            )
            state["status_poll_count"] = state.get("status_poll_count", 0) + 1
            state["http_status"] = status_http
            if terminal.status not in _TERMINAL_STATUSES:
                terminal = None

        state["terminal_status"] = terminal.status
        if terminal.status == "FAILURE":
            raise _AcceptanceError(
                outcome="task_failed",
                error_message="Compatibility task reached the legacy FAILURE state.",
            )
        _inspect_runtime_result(terminal.result, state=state)
        _inspect_lifecycle(repository, first.id, state=state)
        _wait_for_real_callback(
            repository,
            first.id,
            state=state,
            deadline=deadline,
            poll_interval=poll_interval,
            sleeper=sleeper,
        )
    except _AcceptanceError as exc:
        return _report(
            outcome=exc.outcome,
            started=started,
            state=state,
            http_status=exc.http_status,
            error_type=exc.__class__.__name__,
            error_message=exc.error_message,
        )
    except httpx.TimeoutException as exc:
        return _report(
            outcome="timeout",
            started=started,
            state=state,
            error_type=exc.__class__.__name__,
            error_message="PingAn compatibility live acceptance timed out.",
        )
    except httpx.HTTPError as exc:
        return _report(
            outcome="provider_unavailable",
            started=started,
            state=state,
            error_type=exc.__class__.__name__,
            error_message="PingAn compatibility API could not be reached.",
        )
    except (TypeError, ValueError, ValidationError, json.JSONDecodeError) as exc:
        return _report(
            outcome="invalid_response",
            started=started,
            state=state,
            error_type=exc.__class__.__name__,
            error_message="PingAn compatibility live acceptance observed invalid evidence.",
        )
    finally:
        if owns_client:
            transport.close()

    return _report(
        outcome="passed",
        started=started,
        state=state,
        proves_real_internal_connectivity=True,
    )


def _submit(
    client: httpx.Client,
    *,
    base_url: str,
    app_key: str,
    request: dict[str, Any],
    max_response_bytes: int,
    timeout_seconds: float,
) -> tuple[PingAnLegacyTaskResponse, int]:
    response = client.post(
        f"{base_url}/workflow/task",
        headers={"authorization": f"Bearer {app_key}"},
        json=request,
        timeout=timeout_seconds,
    )
    return _parse_http_task_response(
        response,
        max_response_bytes=max_response_bytes,
    )


def _status(
    client: httpx.Client,
    *,
    base_url: str,
    app_key: str,
    task_id: str,
    max_response_bytes: int,
    timeout_seconds: float,
) -> tuple[PingAnLegacyTaskResponse, int]:
    response = client.get(
        f"{base_url}/task/task_status",
        headers={"app-key": app_key},
        params={"task_id": task_id},
        timeout=timeout_seconds,
    )
    parsed, http_status = _parse_http_task_response(
        response,
        max_response_bytes=max_response_bytes,
    )
    if parsed.id != task_id:
        raise _AcceptanceError(
            outcome="invalid_response",
            error_message="Compatibility status endpoint returned a different task ID.",
            http_status=http_status,
        )
    return parsed, http_status


def _parse_http_task_response(
    response: httpx.Response,
    *,
    max_response_bytes: int,
) -> tuple[PingAnLegacyTaskResponse, int]:
    if response.status_code in {401, 403}:
        raise _AcceptanceError(
            outcome="authentication_failed",
            error_message="Compatibility API rejected the configured application key.",
            http_status=response.status_code,
        )
    if not 200 <= response.status_code < 300:
        raise _AcceptanceError(
            outcome="provider_unavailable",
            error_message="Compatibility API returned a non-success HTTP status.",
            http_status=response.status_code,
        )
    if len(response.content) > max_response_bytes:
        raise _AcceptanceError(
            outcome="invalid_response",
            error_message="Compatibility API response exceeded the configured size limit.",
            http_status=response.status_code,
        )
    return PingAnLegacyTaskResponse.model_validate(response.json()), response.status_code


def _inspect_runtime_result(result: Any, *, state: dict[str, Any]) -> None:
    if not isinstance(result, Mapping):
        raise _AcceptanceError(
            outcome="runtime_not_executed",
            error_message="Compatibility task completed without a structured SOC result.",
        )
    state["result_present"] = True
    state["result_sha256"] = _stable_json_sha256(dict(result))
    lineage = result.get("soc_lineage")
    if not isinstance(lineage, Mapping):
        raise _AcceptanceError(
            outcome="runtime_not_executed",
            error_message="Compatibility result does not contain SOC decision lineage.",
        )
    run_id = lineage.get("run_id")
    state["run_id_present"] = isinstance(run_id, str) and bool(run_id.strip())
    model_name = _optional_text(result.get("model_name"))
    state["model_name"] = model_name
    state["model_name_present"] = model_name is not None
    state["lifecycle_state"] = _optional_text(lineage.get("external_lifecycle_state"))
    if not state["run_id_present"] or not state["model_name_present"] or lineage.get("skipped_before_analysis") is True:
        raise _AcceptanceError(
            outcome="runtime_not_executed",
            error_message="Compatibility task was skipped before a completed SOC Runtime analysis.",
        )


def _inspect_lifecycle(
    repository: ProcessingJobRepository,
    job_id: str,
    *,
    state: dict[str, Any],
) -> None:
    events = repository.list_events(job_id)
    lifecycle = next(
        (details for event in events if event.event_type == "analysis_started" and isinstance((details := event.details.get("lifecycle")), Mapping)),
        None,
    )
    if lifecycle is None:
        raise _AcceptanceError(
            outcome="lifecycle_not_real",
            error_message="No persisted ZEUS lifecycle evidence was found for the analysis.",
        )
    state["lifecycle_mocked"] = lifecycle.get("mocked")
    state["lifecycle_state"] = _optional_text(lifecycle.get("state")) or state.get("lifecycle_state")
    if state["lifecycle_mocked"] is not False or state["lifecycle_state"] != "pending":
        raise _AcceptanceError(
            outcome="lifecycle_not_real",
            error_message="ZEUS lifecycle evidence was not a real pending-alert precheck.",
        )


def _wait_for_real_callback(
    repository: ProcessingJobRepository,
    job_id: str,
    *,
    state: dict[str, Any],
    deadline: float,
    poll_interval: float,
    sleeper: Callable[[float], None],
) -> None:
    while True:
        callbacks = repository.list_callbacks(job_id)
        if len(callbacks) > 1:
            raise _AcceptanceError(
                outcome="invalid_response",
                error_message="Compatibility task produced multiple callback outbox records.",
            )
        if callbacks:
            callback = callbacks[0]
            state["callback_status"] = callback.status
            state["callback_attempt_count"] = callback.attempt_count
            metadata = callback.response_metadata or {}
            state["callback_mocked"] = metadata.get("mocked")
            if callback.status is CallbackOutboxStatus.DELIVERED:
                attempts = repository.list_callback_attempts(callback.outbox_id)
                state["callback_attempt_count"] = len(attempts)
                delivered_attempt = attempts[-1] if attempts else None
                attempt_metadata = (delivered_attempt.response_metadata if delivered_attempt is not None else None) or {}
                if state["callback_mocked"] is not False or delivered_attempt is None or delivered_attempt.outcome is not CallbackAttemptOutcome.DELIVERED or attempt_metadata.get("mocked") is not False:
                    raise _AcceptanceError(
                        outcome="callback_not_real",
                        error_message="Callback was delivered by a fake provider, not real ZEUS.",
                    )
                return
            if callback.status is CallbackOutboxStatus.DEAD_LETTER:
                raise _AcceptanceError(
                    outcome="callback_not_delivered",
                    error_message="ZEUS callback exhausted its delivery budget.",
                )
        _sleep_before_poll(
            deadline=deadline,
            poll_interval=poll_interval,
            sleeper=sleeper,
        )


def _sleep_before_poll(
    *,
    deadline: float,
    poll_interval: float,
    sleeper: Callable[[float], None],
) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise _AcceptanceError(
            outcome="timeout",
            error_message="Compatibility task or callback did not finish before the acceptance deadline.",
        )
    sleeper(min(poll_interval, remaining))


def _loopback_base_url(raw: str) -> str:
    normalized = raw.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or (parsed.hostname or "").lower() not in _LOOPBACK_HOSTS:
        raise ValueError("compatibility smoke base URL must be loopback HTTP(S)")
    if parsed.path or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("compatibility smoke base URL must not include a path or query")
    if parsed.username or parsed.password:
        raise ValueError("compatibility smoke base URL cannot include credentials")
    return normalized


def _app_key(values: Mapping[str, str], *, app_code: str) -> str:
    raw = values.get("SOC_PINGAN_COMPAT_APP_KEYS_JSON", "")
    parsed = json.loads(raw)
    if not isinstance(parsed, Mapping):
        raise ValueError("SOC_PINGAN_COMPAT_APP_KEYS_JSON must be an object")
    normalized_keys = {str(label).strip(): key.strip() for label, key in parsed.items() if str(label).strip() and isinstance(key, str) and key.strip()}
    if not normalized_keys or len(normalized_keys) != len(parsed):
        raise ValueError("SOC_PINGAN_COMPAT_APP_KEYS_JSON requires non-empty string keys and values")
    exact_key = normalized_keys.get(app_code)
    if exact_key is not None:
        return exact_key
    unique_keys = tuple(dict.fromkeys(normalized_keys.values()))
    if len(unique_keys) == 1:
        return unique_keys[0]
    raise ValueError("live acceptance cannot choose one compatibility key from multiple allowed values")


def _require_internal_mode(values: Mapping[str, str], name: str) -> None:
    if values.get(name, "").strip().lower() != "internal":
        raise ValueError(f"{name} must be internal for live acceptance")


def _positive_float(raw: str, *, name: str) -> float:
    value = float(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _positive_int(raw: str, *, name: str) -> int:
    value = int(raw)
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _stable_json_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _contains_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not (normalized.startswith("<") and normalized.endswith(">")):
            return False
        return any(
            marker in normalized
            for marker in (
                "approved",
                "internal",
                "placeholder",
                "replace",
                "unique",
            )
        )
    if isinstance(value, Mapping):
        return any(_contains_placeholder(key) or _contains_placeholder(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_placeholder(item) for item in value)
    return False


def _report(
    *,
    outcome: PingAnLegacyLiveAcceptanceOutcome,
    started: float,
    state: Mapping[str, Any],
    proves_real_internal_connectivity: bool = False,
    http_status: int | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> PingAnLegacyLiveAcceptanceReport:
    values = dict(state)
    if http_status is not None:
        values["http_status"] = http_status
    return PingAnLegacyLiveAcceptanceReport(
        outcome=outcome,
        passed=outcome == "passed",
        proves_real_internal_connectivity=proves_real_internal_connectivity,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
        error_type=error_type,
        error_message=error_message,
        **values,
    )


class _AcceptanceError(RuntimeError):
    def __init__(
        self,
        *,
        outcome: PingAnLegacyLiveAcceptanceOutcome,
        error_message: str,
        http_status: int | None = None,
    ) -> None:
        super().__init__(error_message)
        self.outcome = outcome
        self.error_message = error_message
        self.http_status = http_status


__all__ = [
    "PingAnLegacyLiveAcceptanceReport",
    "run_pingan_legacy_live_acceptance",
]
