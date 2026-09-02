"""Secret-safe direct acceptance for one ZEUS lifecycle lookup."""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PingAnAlertLifecycleState,
)
from soc_agent.integrations.pingan.legacy_compat.wiring import (
    PingAnLegacyProviderMode,
    PingAnLegacyWorkerSettings,
    build_pingan_lifecycle_service,
)
from soc_agent.integrations.pingan.legacy_compat.zeus_lifecycle import (
    PingAnAlertLifecycleService,
)

PingAnZeusLifecycleSmokeOutcome = Literal[
    "pending",
    "alert_not_pending",
    "provider_business_error",
    "provider_unavailable",
    "invalid_response",
    "fake_provider",
    "invalid_configuration",
]


class PingAnZeusLifecycleSmokeReport(BaseModel):
    """Bounded proof emitted before any model-backed compatibility run."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_zeus_lifecycle_smoke.v1"] = "soc.pingan_zeus_lifecycle_smoke.v1"
    outcome: PingAnZeusLifecycleSmokeOutcome
    passed: bool
    ready_for_live_acceptance: bool
    alert_id_sha256: str = Field(min_length=64, max_length=64)
    lifecycle_state: str | None = None
    provider_code: str | None = None
    provider_status: str | None = None
    reason: str | None = None
    mocked: bool | None = None
    response_sha256: str | None = Field(default=None, min_length=64, max_length=64)
    duration_ms: int = Field(ge=0)
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def run_pingan_zeus_lifecycle_smoke(
    alert_id: str,
    environ: Mapping[str, str] | None = None,
    *,
    service: PingAnAlertLifecycleService | None = None,
) -> PingAnZeusLifecycleSmokeReport:
    """Query one approved alert without submitting a Job or invoking an LLM."""

    started = time.monotonic()
    normalized_alert_id = alert_id.strip()
    alert_id_sha256 = hashlib.sha256(normalized_alert_id.encode("utf-8")).hexdigest()
    if not normalized_alert_id:
        return _report(
            outcome="invalid_configuration",
            alert_id_sha256=alert_id_sha256,
            started=started,
            error_type="ValueError",
            error_message="ZEUS lifecycle smoke requires an alert ID.",
        )
    try:
        lifecycle_service = service or _build_live_service(environ)
        check = lifecycle_service.check(normalized_alert_id)
    except (TypeError, ValueError) as exc:
        return _report(
            outcome="invalid_configuration",
            alert_id_sha256=alert_id_sha256,
            started=started,
            error_type=type(exc).__name__,
            error_message="ZEUS lifecycle smoke configuration is invalid.",
        )

    values = {
        "lifecycle_state": check.state.value,
        "provider_code": check.provider_code,
        "provider_status": check.provider_status,
        "reason": check.reason,
        "mocked": check.mocked,
        "response_sha256": check.response_sha256,
    }
    if check.mocked:
        outcome: PingAnZeusLifecycleSmokeOutcome = "fake_provider"
    elif check.state is PingAnAlertLifecycleState.PENDING and check.provider_code == "200" and check.provider_status == "1":
        outcome = "pending"
    elif check.state is PingAnAlertLifecycleState.HANDLED:
        outcome = "alert_not_pending"
    elif check.reason == "provider_business_error":
        outcome = "provider_business_error"
    elif (check.reason or "").startswith("provider_unavailable:"):
        outcome = "provider_unavailable"
    else:
        outcome = "invalid_response"
    ready = outcome == "pending"
    return _report(
        outcome=outcome,
        alert_id_sha256=alert_id_sha256,
        started=started,
        passed=ready,
        ready_for_live_acceptance=ready,
        **values,
    )


def _build_live_service(
    environ: Mapping[str, str] | None,
) -> PingAnAlertLifecycleService:
    values = dict(os.environ if environ is None else environ)
    settings = PingAnLegacyWorkerSettings.from_env(values)
    if settings.lifecycle_mode is not PingAnLegacyProviderMode.INTERNAL:
        raise ValueError("SOC_PINGAN_LEGACY_LIFECYCLE_MODE must equal internal")
    return build_pingan_lifecycle_service(settings, environ=values)


def _report(
    *,
    outcome: PingAnZeusLifecycleSmokeOutcome,
    alert_id_sha256: str,
    started: float,
    passed: bool = False,
    ready_for_live_acceptance: bool = False,
    **values: object,
) -> PingAnZeusLifecycleSmokeReport:
    return PingAnZeusLifecycleSmokeReport(
        outcome=outcome,
        passed=passed,
        ready_for_live_acceptance=ready_for_live_acceptance,
        alert_id_sha256=alert_id_sha256,
        duration_ms=max(0, round((time.monotonic() - started) * 1000)),
        **values,
    )


__all__ = [
    "PingAnZeusLifecycleSmokeReport",
    "run_pingan_zeus_lifecycle_smoke",
]
