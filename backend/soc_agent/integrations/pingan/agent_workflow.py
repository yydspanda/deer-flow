"""Minimal HTTP client for PingAn Agent Platform workflow execution.

This module intentionally reimplements only the reviewed wire contract used by
the PingAn asset locator. It does not import the legacy Agent Platform package,
Redis token manager, root config, or logging stack.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_SUCCESS_CODES = {0, 200, "0", "200"}
_PENDING_STATUSES = {None, "processing", "submitted"}
_FAILED_STATUSES = {"timeout", "error", "failed"}


class PingAnAgentWorkflowError(RuntimeError):
    """Base error for the PingAn Agent Platform workflow boundary."""


class PingAnAgentWorkflowConfigurationError(PingAnAgentWorkflowError, ValueError):
    """Raised when the workflow transport configuration is unsafe or incomplete."""


class PingAnAgentWorkflowResponseError(PingAnAgentWorkflowError):
    """Raised when Agent Platform returns data outside the reviewed contract."""


class PingAnAgentWorkflowTimeoutError(PingAnAgentWorkflowError, TimeoutError):
    """Raised when an asynchronous workflow does not finish in time."""


class PingAnAgentWorkflowHttpConfig(BaseModel):
    """Environment-owned Agent Platform HTTP configuration."""

    model_config = ConfigDict(extra="forbid")

    environment: Literal["dev", "stg", "prd"]
    base_url: str = Field(min_length=1)
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    app_id: str = Field(min_length=1)
    app_secret: SecretStr
    allow_prd: bool = False
    auth_path: str = "/appid/auth/login"
    request_timeout_seconds: float = Field(default=15.0, gt=0)
    workflow_timeout_seconds: float = Field(default=600.0, gt=0)
    poll_interval_seconds: float = Field(default=2.0, gt=0)
    token_ttl_seconds: float = Field(default=3600.0, gt=0)
    max_request_bytes: int = Field(default=1_000_000, gt=0)
    max_response_bytes: int = Field(default=2_000_000, gt=0)

    @field_validator("base_url", "app_id", "auth_path", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def _normalize_hosts(cls, value: Any) -> tuple[str, ...]:
        if isinstance(value, str):
            values: Sequence[Any] = value.split(",")
        elif isinstance(value, Sequence):
            values = value
        else:
            raise ValueError("allowed_hosts must be a sequence or comma-separated string")
        hosts = tuple(dict.fromkeys(str(item).strip().lower() for item in values if str(item).strip()))
        if not hosts:
            raise ValueError("allowed_hosts must contain at least one hostname")
        return hosts

    @model_validator(mode="after")
    def _validate_network_boundary(self) -> PingAnAgentWorkflowHttpConfig:
        parsed = urlparse(self.base_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Agent Platform base URL must use HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Agent Platform base URL cannot contain credentials, query, or fragment")
        if parsed.hostname.lower() not in set(self.allowed_hosts):
            raise ValueError("Agent Platform base URL host must match the configured allowlist")
        if not self.auth_path.startswith("/") or urlparse(self.auth_path).scheme:
            raise ValueError("Agent Platform auth endpoint must be an absolute path")
        if self.environment == "prd" and not self.allow_prd:
            raise ValueError("Agent Platform PRD requires explicit production confirmation")
        return self


class HttpPingAnAgentWorkflowPort:
    """Execute the legacy create/poll workflow protocol without legacy imports."""

    mocked = False

    def __init__(
        self,
        config: PingAnAgentWorkflowHttpConfig,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._client = client
        self._sleep = sleep
        self._clock = clock
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @property
    def environment(self) -> str:
        return self._config.environment

    def run(self, *, app_id: str, workflow_id: int, query_data: Mapping[str, Any]) -> Any:
        if app_id != self._config.app_id:
            raise PingAnAgentWorkflowConfigurationError("workflow app ID does not match the configured credential")
        if workflow_id <= 0:
            raise PingAnAgentWorkflowConfigurationError("workflow ID must be positive")
        serialized = json.dumps(dict(query_data), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(serialized) > self._config.max_request_bytes:
            raise PingAnAgentWorkflowConfigurationError("workflow request exceeded the configured size limit")

        created = self._authorized_json(
            "POST",
            f"/api/v1/workflows/{workflow_id}/runs",
            params={
                "is_async": True,
                "streaming": False,
                "workflow_version": "default",
            },
            content=serialized,
        )
        run_id = _workflow_run_id(created)
        deadline = self._clock() + self._config.workflow_timeout_seconds
        while self._clock() < deadline:
            result = self._authorized_json(
                "GET",
                f"/api/v1/workflows/{workflow_id}/runs/{run_id}/result",
                params={"events": False, "streaming": False},
            )
            data = result.get("data")
            if not isinstance(data, Mapping):
                raise PingAnAgentWorkflowResponseError("workflow result omitted its data object")
            status_raw = data.get("status")
            status = str(status_raw).strip().lower() if status_raw is not None else None
            if status == "completed":
                outputs = data.get("outputs")
                if outputs in (None, []):
                    return None
                if not isinstance(outputs, list) or not isinstance(outputs[0], Mapping):
                    raise PingAnAgentWorkflowResponseError("completed workflow returned invalid outputs")
                return outputs[0].get("content")
            if status in _FAILED_STATUSES:
                raise PingAnAgentWorkflowResponseError(f"workflow finished with status {status}")
            if status not in _PENDING_STATUSES:
                raise PingAnAgentWorkflowResponseError("workflow returned an unsupported status")
            remaining = deadline - self._clock()
            if remaining <= 0:
                break
            self._sleep(min(self._config.poll_interval_seconds, remaining))
        raise PingAnAgentWorkflowTimeoutError("workflow polling exceeded the configured timeout")

    def _authorized_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any],
        content: bytes | None = None,
    ) -> Mapping[str, Any]:
        for attempt in range(2):
            token = self._access_token()
            response = self._request(
                method,
                path,
                headers={
                    "auth-token": token,
                    **({"content-type": "application/json"} if content is not None else {}),
                },
                params=params,
                content=content,
            )
            if response.status_code not in {401, 403}:
                return self._decode_response(response, operation="workflow request")
            self._invalidate_token(token)
            if attempt == 1:
                response.raise_for_status()
        raise AssertionError("unreachable")

    def _access_token(self) -> str:
        now = self._clock()
        refresh_skew = min(300.0, self._config.token_ttl_seconds * 0.1)
        if self._token and now < self._token_expires_at - refresh_skew:
            return self._token
        with self._token_lock:
            now = self._clock()
            if self._token and now < self._token_expires_at - refresh_skew:
                return self._token
            response = self._request(
                "POST",
                self._config.auth_path,
                headers={"content-type": "application/json"},
                json_body={
                    "appId": self._config.app_id,
                    "appSecret": self._config.app_secret.get_secret_value(),
                },
            )
            payload = self._decode_response(response, operation="workflow authentication")
            if payload.get("code") not in {0, "0"}:
                raise PingAnAgentWorkflowResponseError("Agent Platform rejected workflow authentication")
            token = payload.get("data")
            if not isinstance(token, str) or not token.strip():
                raise PingAnAgentWorkflowResponseError("workflow authentication omitted its access token")
            self._token = token.strip()
            self._token_expires_at = self._clock() + self._config.token_ttl_seconds
            return self._token

    def _invalidate_token(self, token: str) -> None:
        with self._token_lock:
            if self._token == token:
                self._token = None
                self._token_expires_at = 0.0

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str],
        params: Mapping[str, Any] | None = None,
        content: bytes | None = None,
        json_body: Mapping[str, Any] | None = None,
    ) -> httpx.Response:
        url = self._url(path)
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            return client.request(
                method,
                url,
                headers=dict(headers),
                params=dict(params or {}),
                content=content,
                json=dict(json_body) if json_body is not None else None,
                timeout=self._config.request_timeout_seconds,
            )
        finally:
            if owns_client:
                client.close()

    def _url(self, path: str) -> str:
        if not path.startswith("/") or urlparse(path).scheme:
            raise PingAnAgentWorkflowConfigurationError("workflow endpoint must be an absolute path")
        url = urljoin(self._config.base_url.rstrip("/") + "/", path.lstrip("/"))
        parsed = urlparse(url)
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in set(self._config.allowed_hosts):
            raise PingAnAgentWorkflowConfigurationError("resolved workflow URL left the configured host allowlist")
        return url

    def _decode_response(self, response: httpx.Response, *, operation: str) -> Mapping[str, Any]:
        response.raise_for_status()
        if len(response.content) > self._config.max_response_bytes:
            raise PingAnAgentWorkflowResponseError(f"{operation} response exceeded the configured size limit")
        try:
            payload = response.json()
        except ValueError as exc:
            raise PingAnAgentWorkflowResponseError(f"{operation} returned invalid JSON") from exc
        if not isinstance(payload, Mapping):
            raise PingAnAgentWorkflowResponseError(f"{operation} returned a non-object JSON response")
        code = payload.get("code")
        if code is not None and code not in _SUCCESS_CODES:
            raise PingAnAgentWorkflowResponseError(f"{operation} returned a non-success business code")
        return payload


def _workflow_run_id(payload: Mapping[str, Any]) -> str:
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise PingAnAgentWorkflowResponseError("workflow creation omitted its data object")
    run_id = data.get("workflow_run_id")
    if not isinstance(run_id, (str, int)) or not str(run_id).strip():
        raise PingAnAgentWorkflowResponseError("workflow creation omitted its run ID")
    return str(run_id).strip()


__all__ = [
    "HttpPingAnAgentWorkflowPort",
    "PingAnAgentWorkflowConfigurationError",
    "PingAnAgentWorkflowError",
    "PingAnAgentWorkflowHttpConfig",
    "PingAnAgentWorkflowResponseError",
    "PingAnAgentWorkflowTimeoutError",
]
