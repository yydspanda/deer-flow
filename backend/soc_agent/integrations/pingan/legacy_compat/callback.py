"""Signed ZEUS callback transport and durable outbox dispatcher."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from soc_agent.contracts import SocCallbackOutboxRecord
from soc_agent.integrations.pingan.zeus_signing import isec_sign
from soc_agent.protocols import ProcessingJobRepository

PINGAN_ALERT_CALLBACK_DESTINATION = "pingan.zeus.alert_callback"


class PingAnAlertCallbackPort(Protocol):
    def send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


class PingAnAlertCallbackConfigurationError(ValueError):
    pass


class PingAnAlertCallbackResponseError(RuntimeError):
    pass


class HttpPingAnZeusAlertCallbackPort:
    def __init__(
        self,
        *,
        base_url: str,
        app_id: str,
        app_key: str,
        allowed_hosts: Sequence[str],
        signer: Callable[..., Mapping[str, Any]] = isec_sign,
        endpoint_path: str = "/public/alertModelCallback",
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 1_000_000,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/") + "/"
        parsed = urlparse(normalized_url)
        hosts = {value.strip().lower() for value in allowed_hosts if value.strip()}
        if parsed.scheme != "https" or not parsed.hostname:
            raise PingAnAlertCallbackConfigurationError("ZEUS callback base URL must use HTTPS")
        if not hosts or parsed.hostname.lower() not in hosts:
            raise PingAnAlertCallbackConfigurationError("ZEUS callback host must match the configured allowlist")
        if not app_id.strip() or not app_key:
            raise PingAnAlertCallbackConfigurationError("ZEUS callback app ID and key are required")
        if not endpoint_path.startswith("/") or urlparse(endpoint_path).scheme:
            raise PingAnAlertCallbackConfigurationError("ZEUS callback endpoint must be an absolute path")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise PingAnAlertCallbackConfigurationError("ZEUS callback limits must be positive")
        self._base_url = normalized_url
        self._app_id = app_id.strip()
        self._app_key = app_key
        self._hosts = hosts
        self._signer = signer
        self._endpoint_path = endpoint_path.lstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client = client

    def send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request_body = dict(payload)
        headers = dict(
            self._signer(
                data=request_body,
                app_id=self._app_id,
                app_key=self._app_key,
            )
        )
        url = urljoin(self._base_url, self._endpoint_path)
        if (urlparse(url).hostname or "").lower() not in self._hosts:
            raise PingAnAlertCallbackConfigurationError("resolved ZEUS callback URL left the configured host allowlist")
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            response = client.post(
                url,
                json=request_body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise PingAnAlertCallbackResponseError("ZEUS callback response exceeded the configured size limit")
            try:
                body = response.json()
            except ValueError:
                body = None
        finally:
            if owns_client:
                client.close()
        if body is not None and not isinstance(body, Mapping):
            raise PingAnAlertCallbackResponseError("ZEUS callback returned a non-object JSON response")
        provider_code = body.get("code") if isinstance(body, Mapping) else None
        if provider_code is not None and str(provider_code) != "200":
            raise PingAnAlertCallbackResponseError("ZEUS callback returned a non-success business code")
        return {
            "http_status": response.status_code,
            "provider_code": (str(provider_code) if provider_code is not None else None),
            "response_sha256": hashlib.sha256(response.content).hexdigest(),
            "mocked": False,
        }


class StaticPingAnZeusAlertCallbackPort:
    def __init__(self, responses: Sequence[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def send(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append(dict(payload))
        if not self._responses:
            value: Any = {"code": 200}
        else:
            value = self._responses.pop(0)
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, Mapping):
            raise PingAnAlertCallbackResponseError("fake callback response must be an object")
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return {
            "http_status": 200,
            "provider_code": (str(value.get("code")) if value.get("code") is not None else None),
            "response_sha256": hashlib.sha256(encoded).hexdigest(),
            "mocked": True,
        }


class PingAnLegacyCallbackDispatcher:
    """Deliver one callback at a time without touching the analysis job."""

    def __init__(
        self,
        *,
        repository: ProcessingJobRepository,
        port: PingAnAlertCallbackPort,
        dispatcher_id: str,
        lease_seconds: int = 60,
        max_attempts: int = 8,
        retry_backoff_seconds: int = 30,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if lease_seconds < 1 or max_attempts < 1 or retry_backoff_seconds < 0:
            raise ValueError("callback dispatcher lease/retry settings are invalid")
        self._repository = repository
        self._port = port
        self._dispatcher_id = dispatcher_id
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._now = now or (lambda: datetime.now(UTC))

    def run_once(self) -> SocCallbackOutboxRecord | None:
        claimed = self._repository.claim_next_callback(
            destination=PINGAN_ALERT_CALLBACK_DESTINATION,
            dispatcher_id=self._dispatcher_id,
            lease_seconds=self._lease_seconds,
            now=self._now(),
        )
        if claimed is None:
            return None
        try:
            metadata = dict(self._port.send(claimed.payload))
        except Exception as exc:
            failed_at = self._now()
            retry_at = failed_at + timedelta(seconds=self._retry_backoff_seconds * max(1, min(claimed.attempt_count, 10)))
            return self._repository.mark_callback_retry(
                claimed.outbox_id,
                dispatcher_id=self._dispatcher_id,
                error_code=type(exc).__name__,
                error_message="ZEUS callback delivery failed",
                available_at=retry_at,
                now=failed_at,
                dead_letter=claimed.attempt_count >= self._max_attempts,
            )
        delivered_at = self._now()
        return self._repository.mark_callback_delivered(
            claimed.outbox_id,
            dispatcher_id=self._dispatcher_id,
            response_metadata=metadata,
            now=delivered_at,
        )


__all__ = [
    "HttpPingAnZeusAlertCallbackPort",
    "PINGAN_ALERT_CALLBACK_DESTINATION",
    "PingAnAlertCallbackConfigurationError",
    "PingAnAlertCallbackPort",
    "PingAnAlertCallbackResponseError",
    "PingAnLegacyCallbackDispatcher",
    "StaticPingAnZeusAlertCallbackPort",
]
