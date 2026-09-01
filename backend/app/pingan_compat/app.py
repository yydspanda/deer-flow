"""Small FastAPI surface that preserves the old ZEUS task protocol."""

from __future__ import annotations

import secrets
from collections.abc import Mapping

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from soc_agent.db import ProcessingJobConflictError
from soc_agent.integrations.pingan.legacy_compat import (
    PingAnLegacyTaskNotFoundError,
    PingAnLegacyTaskRequest,
    PingAnLegacyTaskResponse,
    PingAnLegacyTaskService,
)


def create_pingan_compat_app(
    *,
    service: PingAnLegacyTaskService,
    app_keys: Mapping[str, str],
    max_request_bytes: int = 5_000_000,
) -> FastAPI:
    if max_request_bytes < 1:
        raise ValueError("max_request_bytes must be >= 1")
    normalized_keys = {app.strip(): key for app, key in app_keys.items() if app.strip() and key}
    if not normalized_keys:
        raise ValueError("at least one PingAn compatibility app key is required")
    allowed_keys = tuple(dict.fromkeys(normalized_keys.values()))

    app = FastAPI(title="SOC PingAn Compatibility API", docs_url=None, redoc_url=None)

    app.add_middleware(
        _BoundedWorkflowTaskBodyMiddleware,
        max_request_bytes=max_request_bytes,
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/workflow/task", response_model=PingAnLegacyTaskResponse)
    def submit_task(
        body: PingAnLegacyTaskRequest,
        authorization: str | None = Header(default=None),
        x_idempotency_key: str | None = Header(
            default=None,
            alias="X-Idempotency-Key",
        ),
    ) -> PingAnLegacyTaskResponse:
        _verify_bearer(
            authorization,
            allowed_keys=allowed_keys,
        )
        try:
            return service.submit(
                body,
                idempotency_key=x_idempotency_key,
            )
        except ProcessingJobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/task/task_status", response_model=PingAnLegacyTaskResponse)
    def task_status(
        task_id: str,
        app_key: str | None = Header(default=None, alias="app-key"),
    ) -> PingAnLegacyTaskResponse:
        _verify_allowed_key(app_key, allowed_keys=allowed_keys)
        try:
            return service.get_status(task_id)
        except PingAnLegacyTaskNotFoundError as exc:
            raise HTTPException(status_code=404, detail="task not found") from exc

    return app


class _BoundedWorkflowTaskBodyMiddleware:
    """Buffer only the legacy ingress body so chunked uploads cannot bypass limits."""

    def __init__(self, app: ASGIApp, *, max_request_bytes: int) -> None:
        self._app = app
        self._max_request_bytes = max_request_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") != "POST" or scope.get("path") != "/workflow/task":
            await self._app(scope, receive, send)
            return

        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", ())}
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                await _send_body_limit_error(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid Content-Length",
                )
                return
            if declared_size < 0:
                await _send_body_limit_error(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="invalid Content-Length",
                )
                return
            if declared_size > self._max_request_bytes:
                await _send_body_limit_error(
                    scope,
                    receive,
                    send,
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="request body exceeds configured limit",
                )
                return

        buffered: list[Message] = []
        observed_size = 0
        while True:
            message = await receive()
            buffered.append(message)
            if message["type"] == "http.request":
                observed_size += len(message.get("body", b""))
                if observed_size > self._max_request_bytes:
                    await _send_body_limit_error(
                        scope,
                        receive,
                        send,
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="request body exceeds configured limit",
                    )
                    return
                if not message.get("more_body", False):
                    break
            elif message["type"] == "http.disconnect":
                break

        index = 0

        async def replay_receive() -> Message:
            nonlocal index
            if index < len(buffered):
                message = buffered[index]
                index += 1
                return message
            return await receive()

        await self._app(scope, replay_receive, send)


async def _send_body_limit_error(
    scope: Scope,
    receive: Receive,
    send: Send,
    *,
    status_code: int,
    detail: str,
) -> None:
    response = JSONResponse(status_code=status_code, content={"detail": detail})
    await response(scope, receive, send)


def _verify_bearer(value: str | None, *, allowed_keys: tuple[str, ...]) -> None:
    if value is None or not value.startswith("Bearer "):
        raise HTTPException(status_code=403, detail="invalid API key")
    _verify_allowed_key(
        value.removeprefix("Bearer ").strip(),
        allowed_keys=allowed_keys,
    )


def _verify_allowed_key(value: str | None, *, allowed_keys: tuple[str, ...]) -> None:
    matched = False
    if value is not None:
        for allowed_key in allowed_keys:
            matched = secrets.compare_digest(value, allowed_key) or matched
    if not matched:
        raise HTTPException(status_code=403, detail="invalid API key")


__all__ = ["create_pingan_compat_app"]
