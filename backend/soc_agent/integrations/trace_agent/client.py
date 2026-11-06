"""One-shot async Trace requests, with strict identity and response validation."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pydantic import ValidationError
from trace_agent.contracts import TraceCreateResponse, TraceHealth, TraceTaskResponse, extract_alert_id, normalize_evidence_gaps, validate_task_id

from .config import TraceClientSettings


class TraceClientError(RuntimeError):
    """Safe failure metadata; contains no raw provider body, headers or URL."""

    def __init__(self, code: str, *, operation: str, http_status: int | None = None) -> None:
        self.code = code
        self.operation = operation
        self.http_status = http_status
        super().__init__(f"Trace operation={operation} code={code}" + (f" status={http_status}" if http_status is not None else ""))


@dataclass(frozen=True)
class TraceClientReply:
    data: TraceCreateResponse | TraceTaskResponse
    revision: int
    alert_identity_verified: bool


class TraceClient:
    """No poller, scheduler, redirect following, automatic retry, or callback."""

    def __init__(self, settings: TraceClientSettings | None = None, *, client: httpx.AsyncClient | None = None, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if client is not None and transport is not None:
            raise ValueError("Inject either a dedicated AsyncClient or a transport")
        self.settings = settings or TraceClientSettings()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(transport=transport, follow_redirects=False, trust_env=False)

    async def __aenter__(self) -> TraceClient:
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create(self, *, task_id: str, expected_alert_id: str, alert: Mapping[str, Any], request_id: str, evidence_gaps: str | None = None) -> TraceClientReply:
        self._ensure_enabled("create")
        task_id = self._task_id(task_id, "create")
        expected_alert_id = self._alert_id(expected_alert_id, "create")
        headers = self._headers("create", request_id=request_id)
        try:
            if extract_alert_id(alert) != expected_alert_id:
                raise ValueError("source identity mismatch")
            questions = normalize_evidence_gaps(evidence_gaps)
            file_bytes = json.dumps(dict(alert), ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (ValueError, TypeError, UnicodeError):
            raise TraceClientError("invalid_input", operation="create") from None
        form = {"task_id": task_id}
        if questions:
            form["evidence_gaps"] = "\n".join(questions)
        body, revision = await self._request("create", "POST", "/traces", headers=headers, accepted=(200, 202), data=form, files={"file": ("alert.json", file_bytes, "application/json")})
        return self._reply(body, revision, operation="create", task_id=task_id, expected_alert_id=expected_alert_id, require_snapshot=False)

    async def get(self, task_id: str, *, expected_alert_id: str) -> TraceClientReply:
        self._ensure_enabled("get")
        task_id = self._task_id(task_id, "get")
        expected_alert_id = self._alert_id(expected_alert_id, "get")
        body, revision = await self._request("get", "GET", f"/traces/{quote(task_id, safe='')}", headers=self._headers("get"))
        return self._reply(body, revision, operation="get", task_id=task_id, expected_alert_id=expected_alert_id, require_snapshot=True)

    async def resume(self, task_id: str, *, expected_alert_id: str, request_id: str, expected_revision: int) -> TraceClientReply:
        return await self._operate("resume", task_id, expected_alert_id=expected_alert_id, request_id=request_id, expected_revision=expected_revision)

    async def cancel(self, task_id: str, *, expected_alert_id: str, request_id: str, expected_revision: int) -> TraceClientReply:
        return await self._operate("cancel", task_id, expected_alert_id=expected_alert_id, request_id=request_id, expected_revision=expected_revision)

    async def health(self, *, full: bool = False) -> TraceHealth:
        self._ensure_enabled("health")
        body, _ = await self._request("health", "GET", "/health", headers=self._headers("health"), params={"full": str(full).lower()}, needs_revision=False)
        try:
            return TraceHealth.model_validate(body)
        except ValidationError:
            raise TraceClientError("invalid_health", operation="health") from None

    async def _operate(self, operation: str, task_id: str, *, expected_alert_id: str, request_id: str, expected_revision: int) -> TraceClientReply:
        self._ensure_enabled(operation)
        task_id = self._task_id(task_id, operation)
        expected_alert_id = self._alert_id(expected_alert_id, operation)
        if type(expected_revision) is not int or not 0 < expected_revision < 2**63:
            raise TraceClientError("invalid_expected_revision", operation=operation)
        headers = self._headers(operation, request_id=request_id)
        headers["X-Expected-Trace-Revision"] = str(expected_revision)
        body, revision = await self._request(operation, "POST", f"/traces/{quote(task_id, safe='')}/{operation}", headers=headers, accepted=(200, 202))
        return self._reply(body, revision, operation=operation, task_id=task_id, expected_alert_id=expected_alert_id, require_snapshot=False)

    def _ensure_enabled(self, operation: str) -> None:
        if not self.settings.enabled:
            raise TraceClientError("disabled", operation=operation)

    @staticmethod
    def _task_id(value: str, operation: str) -> str:
        try:
            return validate_task_id(value)
        except (TypeError, ValueError):
            raise TraceClientError("invalid_task_id", operation=operation) from None

    @staticmethod
    def _alert_id(value: str, operation: str) -> str:
        if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
            raise TraceClientError("invalid_alert_id", operation=operation)
        return value

    def _headers(self, operation: str, *, request_id: str | None = None) -> dict[str, str]:
        headers = {"X-API-Key": self.settings.api_key or "", "Accept": "application/json"}
        if operation in {"create", "resume", "cancel"} and request_id is None:
            raise TraceClientError("invalid_request_id", operation=operation)
        if request_id is not None:
            if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256 or not request_id.isascii() or any(ord(char) < 32 or ord(char) == 127 for char in request_id):
                raise TraceClientError("invalid_request_id", operation=operation)
            headers["X-Request-Id"] = request_id
        return headers

    async def _request(self, operation: str, method: str, path: str, *, headers: Mapping[str, str], accepted: tuple[int, ...] = (200,), needs_revision: bool = True, **kwargs: Any) -> tuple[dict[str, Any], int]:
        url = (self.settings.base_url or "").rstrip("/") + path
        try:
            async with self._client.stream(method, url, headers=headers, follow_redirects=False, timeout=self.settings.timeout_seconds, auth=None, **kwargs) as response:
                if response.status_code not in accepted:
                    raise TraceClientError("http_error", operation=operation, http_status=response.status_code)
                revision = 0
                if needs_revision:
                    raw = response.headers.get("X-Trace-Revision", "")
                    if len(raw) > 20 or not re.fullmatch(r"[0-9]+", raw) or not 0 < int(raw) < 2**63:
                        raise TraceClientError("invalid_revision", operation=operation)
                    revision = int(raw)
                chunks: list[bytes] = []
                length = 0
                async for chunk in response.aiter_bytes():
                    length += len(chunk)
                    if length > self.settings.max_response_bytes:
                        raise TraceClientError("response_too_large", operation=operation)
                    chunks.append(chunk)
                try:
                    body = json.loads(b"".join(chunks).decode("utf-8"), object_pairs_hook=_unique_pairs, parse_constant=_reject_constant)
                    if not isinstance(body, dict):
                        raise ValueError("object expected")
                except (UnicodeError, ValueError):
                    raise TraceClientError("invalid_json", operation=operation) from None
                return body, revision
        except httpx.TimeoutException:
            raise TraceClientError("timeout", operation=operation) from None
        except httpx.HTTPError:
            raise TraceClientError("transport_error", operation=operation) from None

    @staticmethod
    def _reply(body: dict[str, Any], revision: int, *, operation: str, task_id: str, expected_alert_id: str, require_snapshot: bool) -> TraceClientReply:
        is_snapshot = require_snapshot or "result" in body
        try:
            parsed = TraceTaskResponse.model_validate(body) if is_snapshot else TraceCreateResponse.model_validate(body)
        except ValidationError:
            raise TraceClientError("invalid_response", operation=operation) from None
        if parsed.taskId != task_id:
            raise TraceClientError("task_identity_mismatch", operation=operation)
        if is_snapshot and parsed.result.alert_id != expected_alert_id:
            raise TraceClientError("alert_identity_mismatch", operation=operation)
        return TraceClientReply(data=parsed, revision=revision, alert_identity_verified=is_snapshot)


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _reject_constant(_value: str) -> None:
    raise ValueError("nonfinite JSON value")
