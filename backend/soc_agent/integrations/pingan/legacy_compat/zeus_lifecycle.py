"""Read-only ZEUS alert-state check performed before expensive analysis."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Protocol
from urllib.parse import urljoin, urlparse

import httpx

from soc_agent.integrations.pingan.legacy_compat.contracts import (
    PingAnAlertLifecycleCheck,
    PingAnAlertLifecycleState,
)
from soc_agent.integrations.pingan.zeus_signing import (
    isec_sign,
    serialize_isec_json_body,
)

_STATUS_NAMES = {
    0: "已忽略",
    1: "待审阅",
    2: "退回中",
    3: "待确认",
    4: "处理中",
    5: "待复核",
    6: "待关闭",
    7: "子单处理中",
    8: "子单已关闭",
    9: "已关闭",
    10: "编辑",
}


class PingAnAlertLifecyclePort(Protocol):
    mocked: bool

    def query(self, *, alert_id: str) -> Mapping[str, Any]: ...


class PingAnAlertLifecycleConfigurationError(ValueError):
    pass


class PingAnAlertLifecycleResponseError(RuntimeError):
    pass


class HttpPingAnZeusAlertLifecyclePort:
    mocked = False

    def __init__(
        self,
        *,
        base_url: str,
        app_id: str,
        app_key: str,
        allowed_hosts: Sequence[str],
        signer: Callable[..., Mapping[str, Any]] = isec_sign,
        endpoint_path: str = "/public/getAlertBrief",
        timeout_seconds: float = 10.0,
        max_response_bytes: int = 2_000_000,
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/") + "/"
        parsed = urlparse(normalized_url)
        hosts = {value.strip().lower() for value in allowed_hosts if value.strip()}
        if parsed.scheme != "https" or not parsed.hostname:
            raise PingAnAlertLifecycleConfigurationError("ZEUS lifecycle base URL must use HTTPS")
        if not hosts or parsed.hostname.lower() not in hosts:
            raise PingAnAlertLifecycleConfigurationError("ZEUS lifecycle host must match the configured allowlist")
        if not app_id.strip() or not app_key:
            raise PingAnAlertLifecycleConfigurationError("ZEUS lifecycle app ID and key are required")
        if not endpoint_path.startswith("/") or urlparse(endpoint_path).scheme:
            raise PingAnAlertLifecycleConfigurationError("ZEUS lifecycle endpoint must be an absolute path")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise PingAnAlertLifecycleConfigurationError("ZEUS lifecycle limits must be positive")
        self._base_url = normalized_url
        self._app_id = app_id.strip()
        self._app_key = app_key
        self._hosts = hosts
        self._signer = signer
        self._endpoint_path = endpoint_path.lstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._client = client

    def query(self, *, alert_id: str) -> Mapping[str, Any]:
        try:
            wire_alert_id: int | str = int(alert_id)
        except ValueError:
            wire_alert_id = alert_id
        request_body = {"alertId": wire_alert_id}
        headers = dict(
            self._signer(
                data=request_body,
                app_id=self._app_id,
                app_key=self._app_key,
            )
        )
        headers["Content-Type"] = "application/json"
        wire_body = serialize_isec_json_body(request_body)
        url = urljoin(self._base_url, self._endpoint_path)
        if (urlparse(url).hostname or "").lower() not in self._hosts:
            raise PingAnAlertLifecycleConfigurationError("resolved ZEUS lifecycle URL left the configured allowlist")
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            response = client.post(
                url,
                content=wire_body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise PingAnAlertLifecycleResponseError("ZEUS lifecycle response exceeded the configured size limit")
            value = response.json()
        finally:
            if owns_client:
                client.close()
        if not isinstance(value, Mapping):
            raise PingAnAlertLifecycleResponseError("ZEUS lifecycle returned a non-object JSON response")
        return value


class StaticPingAnZeusAlertLifecyclePort:
    mocked = True

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self._responses = dict(responses)
        self.calls: list[str] = []

    def query(self, *, alert_id: str) -> Mapping[str, Any]:
        self.calls.append(alert_id)
        value = self._responses.get(alert_id, {"code": 200, "data": {"status": 1}})
        if isinstance(value, Exception):
            raise value
        if not isinstance(value, Mapping):
            raise PingAnAlertLifecycleResponseError("fake lifecycle response must be an object")
        return value


class PingAnAlertLifecycleService:
    def __init__(self, *, port: PingAnAlertLifecyclePort) -> None:
        self._port = port

    def check(self, alert_id: str) -> PingAnAlertLifecycleCheck:
        try:
            response = self._port.query(alert_id=alert_id)
        except Exception as exc:  # provider boundary deliberately removes details
            return PingAnAlertLifecycleCheck(
                alert_id=alert_id,
                state=PingAnAlertLifecycleState.UNKNOWN,
                reason=f"provider_unavailable:{type(exc).__name__}",
                mocked=self._port.mocked,
            )

        response_hash = hashlib.sha256(
            json.dumps(
                response,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()
        code = response.get("code")
        provider_code = _bounded_scalar_text(code)
        if provider_code is not None and provider_code != "200":
            return PingAnAlertLifecycleCheck(
                alert_id=alert_id,
                state=PingAnAlertLifecycleState.UNKNOWN,
                provider_code=provider_code,
                reason="provider_business_error",
                mocked=self._port.mocked,
                response_sha256=response_hash,
            )
        data = response.get("data")
        status_value = data.get("status") if isinstance(data, Mapping) else None
        try:
            status_code = int(status_value)
        except (TypeError, ValueError):
            return PingAnAlertLifecycleCheck(
                alert_id=alert_id,
                state=PingAnAlertLifecycleState.UNKNOWN,
                provider_code=provider_code,
                reason="malformed_or_missing_status",
                mocked=self._port.mocked,
                response_sha256=response_hash,
            )
        if status_code == 1:
            state = PingAnAlertLifecycleState.PENDING
            reason = "ZEUS alert remains pending review"
        else:
            state = PingAnAlertLifecycleState.HANDLED
            status_name = _STATUS_NAMES.get(
                status_code,
                f"未知状态({status_code})",
            )
            reason = f"ZEUS 告警已由运营处置（{status_name}）"
        return PingAnAlertLifecycleCheck(
            alert_id=alert_id,
            state=state,
            provider_code=provider_code,
            provider_status=str(status_code),
            reason=reason,
            mocked=self._port.mocked,
            response_sha256=response_hash,
        )


def _bounded_scalar_text(value: Any) -> str | None:
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return None
    normalized = str(value).strip()
    return normalized[:128] or None


__all__ = [
    "HttpPingAnZeusAlertLifecyclePort",
    "PingAnAlertLifecycleConfigurationError",
    "PingAnAlertLifecyclePort",
    "PingAnAlertLifecycleResponseError",
    "PingAnAlertLifecycleService",
    "StaticPingAnZeusAlertLifecyclePort",
]
