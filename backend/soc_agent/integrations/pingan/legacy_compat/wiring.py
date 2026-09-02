"""Operator-owned settings and provider wiring for the legacy ZEUS bridge."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

import httpx
from sqlalchemy.engine import make_url

from soc_agent.db import resolve_database_url, to_sync_database_url
from soc_agent.integrations.pingan.legacy_compat.callback import (
    HttpPingAnZeusAlertCallbackPort,
    PingAnAlertCallbackPort,
    StaticPingAnZeusAlertCallbackPort,
)
from soc_agent.integrations.pingan.legacy_compat.zeus_lifecycle import (
    HttpPingAnZeusAlertLifecyclePort,
    PingAnAlertLifecyclePort,
    PingAnAlertLifecycleService,
    StaticPingAnZeusAlertLifecyclePort,
)
from soc_agent.integrations.pingan.zeus_target import load_pingan_zeus_target


class PingAnLegacyProviderMode(StrEnum):
    """Whether a provider uses deterministic fake data or the configured ZEUS target."""

    FAKE = "fake"
    INTERNAL = "internal"


@dataclass(frozen=True)
class PingAnLegacyApiSettings:
    """Settings for the old `/workflow/task` HTTP compatibility surface."""

    database_url: str
    app_keys: dict[str, str] = field(repr=False)
    bind_host: str = "127.0.0.1"
    port: int = 8090
    max_request_bytes: int = 5_000_000
    queue_ttl_seconds: int = 1_800
    auto_migrate: bool = True

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PingAnLegacyApiSettings:
        values = os.environ if environ is None else environ
        raw_keys = _required(values, "SOC_PINGAN_COMPAT_APP_KEYS_JSON")
        try:
            parsed_keys = json.loads(raw_keys)
        except json.JSONDecodeError as exc:
            raise ValueError("SOC_PINGAN_COMPAT_APP_KEYS_JSON must be valid JSON") from exc
        if not isinstance(parsed_keys, Mapping):
            raise ValueError("SOC_PINGAN_COMPAT_APP_KEYS_JSON must be an object")
        app_keys = {str(app).strip(): str(key) for app, key in parsed_keys.items() if str(app).strip() and isinstance(key, str) and key}
        if not app_keys or len(app_keys) != len(parsed_keys):
            raise ValueError("SOC_PINGAN_COMPAT_APP_KEYS_JSON requires non-empty string keys and values")
        return cls(
            database_url=_database_url(values, explicit_environ=environ is not None),
            app_keys=app_keys,
            bind_host=values.get("SOC_PINGAN_COMPAT_HOST", "127.0.0.1").strip(),
            port=_parse_int(
                values.get("SOC_PINGAN_COMPAT_PORT", "8090"),
                name="SOC_PINGAN_COMPAT_PORT",
                minimum=1,
                maximum=65_535,
            ),
            max_request_bytes=_parse_int(
                values.get("SOC_PINGAN_COMPAT_MAX_REQUEST_BYTES", "5000000"),
                name="SOC_PINGAN_COMPAT_MAX_REQUEST_BYTES",
                minimum=1,
            ),
            queue_ttl_seconds=_parse_int(
                values.get("SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS", "1800"),
                name="SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS",
                minimum=1,
            ),
            auto_migrate=_parse_bool(
                values.get("SOC_PINGAN_COMPAT_AUTO_MIGRATE", "true"),
                name="SOC_PINGAN_COMPAT_AUTO_MIGRATE",
            ),
        )


@dataclass(frozen=True)
class PingAnLegacyWorkerSettings:
    """Settings for durable processing workers and callback dispatch."""

    database_url: str
    lifecycle_mode: PingAnLegacyProviderMode = PingAnLegacyProviderMode.FAKE
    callback_mode: PingAnLegacyProviderMode = PingAnLegacyProviderMode.FAKE
    worker_concurrency: int = 1
    poll_interval_seconds: float = 1.0
    worker_lease_seconds: int = 900
    worker_max_attempts: int = 3
    worker_retry_backoff_seconds: int = 30
    callback_lease_seconds: int = 60
    callback_max_attempts: int = 8
    callback_retry_backoff_seconds: int = 30
    auto_migrate: bool = False

    @classmethod
    def from_env(
        cls,
        environ: Mapping[str, str] | None = None,
    ) -> PingAnLegacyWorkerSettings:
        values = os.environ if environ is None else environ
        database_url = _database_url(values, explicit_environ=environ is not None)
        worker_concurrency = _parse_int(
            values.get("SOC_PINGAN_LEGACY_WORKER_CONCURRENCY", "1"),
            name="SOC_PINGAN_LEGACY_WORKER_CONCURRENCY",
            minimum=1,
        )
        if make_url(to_sync_database_url(database_url)).get_backend_name() == "sqlite" and worker_concurrency != 1:
            raise ValueError("SQLite Host DEV supports exactly one worker")
        return cls(
            database_url=database_url,
            lifecycle_mode=_provider_mode(
                values.get("SOC_PINGAN_LEGACY_LIFECYCLE_MODE", "fake"),
                name="SOC_PINGAN_LEGACY_LIFECYCLE_MODE",
            ),
            callback_mode=_provider_mode(
                values.get("SOC_PINGAN_LEGACY_CALLBACK_MODE", "fake"),
                name="SOC_PINGAN_LEGACY_CALLBACK_MODE",
            ),
            worker_concurrency=worker_concurrency,
            poll_interval_seconds=_parse_float(
                values.get("SOC_PINGAN_LEGACY_POLL_INTERVAL_SECONDS", "1"),
                name="SOC_PINGAN_LEGACY_POLL_INTERVAL_SECONDS",
                minimum=0.01,
            ),
            worker_lease_seconds=_parse_int(
                values.get("SOC_PINGAN_LEGACY_WORKER_LEASE_SECONDS", "900"),
                name="SOC_PINGAN_LEGACY_WORKER_LEASE_SECONDS",
                minimum=1,
            ),
            worker_max_attempts=_parse_int(
                values.get("SOC_PINGAN_LEGACY_WORKER_MAX_ATTEMPTS", "3"),
                name="SOC_PINGAN_LEGACY_WORKER_MAX_ATTEMPTS",
                minimum=1,
            ),
            worker_retry_backoff_seconds=_parse_int(
                values.get(
                    "SOC_PINGAN_LEGACY_WORKER_RETRY_BACKOFF_SECONDS",
                    "30",
                ),
                name="SOC_PINGAN_LEGACY_WORKER_RETRY_BACKOFF_SECONDS",
                minimum=0,
            ),
            callback_lease_seconds=_parse_int(
                values.get("SOC_PINGAN_LEGACY_CALLBACK_LEASE_SECONDS", "60"),
                name="SOC_PINGAN_LEGACY_CALLBACK_LEASE_SECONDS",
                minimum=1,
            ),
            callback_max_attempts=_parse_int(
                values.get("SOC_PINGAN_LEGACY_CALLBACK_MAX_ATTEMPTS", "8"),
                name="SOC_PINGAN_LEGACY_CALLBACK_MAX_ATTEMPTS",
                minimum=1,
            ),
            callback_retry_backoff_seconds=_parse_int(
                values.get(
                    "SOC_PINGAN_LEGACY_CALLBACK_RETRY_BACKOFF_SECONDS",
                    "30",
                ),
                name="SOC_PINGAN_LEGACY_CALLBACK_RETRY_BACKOFF_SECONDS",
                minimum=0,
            ),
            auto_migrate=_parse_bool(
                values.get("SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE", "false"),
                name="SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE",
            ),
        )


def build_pingan_lifecycle_service(
    settings: PingAnLegacyWorkerSettings,
    *,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> PingAnAlertLifecycleService:
    return PingAnAlertLifecycleService(
        port=build_pingan_lifecycle_port(
            settings,
            environ=environ,
            client=client,
        )
    )


def build_pingan_lifecycle_port(
    settings: PingAnLegacyWorkerSettings,
    *,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> PingAnAlertLifecyclePort:
    """Build the exact lifecycle transport used by the compatibility worker."""

    values = os.environ if environ is None else environ
    if settings.lifecycle_mode is PingAnLegacyProviderMode.FAKE:
        raw = values.get(
            "SOC_PINGAN_LEGACY_FAKE_LIFECYCLE_RESPONSES_JSON",
            "{}",
        )
        try:
            responses = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("SOC_PINGAN_LEGACY_FAKE_LIFECYCLE_RESPONSES_JSON must be valid JSON") from exc
        if not isinstance(responses, Mapping):
            raise ValueError("SOC_PINGAN_LEGACY_FAKE_LIFECYCLE_RESPONSES_JSON must be an object")
        return StaticPingAnZeusAlertLifecyclePort(responses)
    zeus = load_pingan_zeus_target(values)
    return HttpPingAnZeusAlertLifecyclePort(
        base_url=zeus.base_url,
        app_id=zeus.app_id,
        app_key=zeus.app_key,
        allowed_hosts=zeus.allowed_hosts,
        endpoint_path=values.get(
            "SOC_PINGAN_ZEUS_ALERT_BRIEF_PATH",
            "/public/getAlertBrief",
        ),
        timeout_seconds=_parse_float(
            values.get("SOC_PINGAN_ZEUS_TIMEOUT_SECONDS", "10"),
            name="SOC_PINGAN_ZEUS_TIMEOUT_SECONDS",
            minimum=0.001,
        ),
        max_response_bytes=_parse_int(
            values.get("SOC_PINGAN_ZEUS_MAX_RESPONSE_BYTES", "2000000"),
            name="SOC_PINGAN_ZEUS_MAX_RESPONSE_BYTES",
            minimum=1,
        ),
        client=client,
    )


def build_pingan_callback_port(
    settings: PingAnLegacyWorkerSettings,
    *,
    environ: Mapping[str, str] | None = None,
    client: httpx.Client | None = None,
) -> PingAnAlertCallbackPort:
    values = os.environ if environ is None else environ
    if settings.callback_mode is PingAnLegacyProviderMode.FAKE:
        return StaticPingAnZeusAlertCallbackPort([{"code": 200}])
    zeus = load_pingan_zeus_target(values)
    return HttpPingAnZeusAlertCallbackPort(
        base_url=zeus.base_url,
        app_id=zeus.app_id,
        app_key=zeus.app_key,
        allowed_hosts=zeus.allowed_hosts,
        endpoint_path=values.get(
            "SOC_PINGAN_ZEUS_ALERT_CALLBACK_PATH",
            "/public/alertModelCallback",
        ),
        timeout_seconds=_parse_float(
            values.get("SOC_PINGAN_ZEUS_CALLBACK_TIMEOUT_SECONDS", "10"),
            name="SOC_PINGAN_ZEUS_CALLBACK_TIMEOUT_SECONDS",
            minimum=0.001,
        ),
        max_response_bytes=_parse_int(
            values.get(
                "SOC_PINGAN_ZEUS_CALLBACK_MAX_RESPONSE_BYTES",
                "1000000",
            ),
            name="SOC_PINGAN_ZEUS_CALLBACK_MAX_RESPONSE_BYTES",
            minimum=1,
        ),
        client=client,
    )


def _database_url(values: Mapping[str, str], *, explicit_environ: bool) -> str:
    configured = values.get("SOC_DATABASE_URL", "").strip()
    if configured:
        return configured
    if explicit_environ:
        raise ValueError("SOC_DATABASE_URL is required")
    return resolve_database_url()


def _provider_mode(value: str, *, name: str) -> PingAnLegacyProviderMode:
    try:
        return PingAnLegacyProviderMode(value.strip().lower())
    except ValueError as exc:
        raise ValueError(name + " must be 'fake' or 'internal'") from exc


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise ValueError(name + " is required")
    return value


def _parse_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(name + " must be a boolean value")


def _parse_int(
    value: str,
    *,
    name: str,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value.strip())
    except ValueError as exc:
        raise ValueError(name + " must be an integer") from exc
    if parsed < minimum or (maximum is not None and parsed > maximum):
        raise ValueError(name + " is outside the supported range")
    return parsed


def _parse_float(value: str, *, name: str, minimum: float) -> float:
    try:
        parsed = float(value.strip())
    except ValueError as exc:
        raise ValueError(name + " must be a number") from exc
    if not isfinite(parsed) or parsed < minimum:
        raise ValueError(name + " is outside the supported range")
    return parsed


__all__ = [
    "PingAnLegacyApiSettings",
    "PingAnLegacyProviderMode",
    "PingAnLegacyWorkerSettings",
    "build_pingan_callback_port",
    "build_pingan_lifecycle_port",
    "build_pingan_lifecycle_service",
]
