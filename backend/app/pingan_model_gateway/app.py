"""OpenAI-compatible HTTP surface for the bounded PingAn model gateway."""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse

from soc_agent.integrations.pingan.model_gateway import (
    PingAnModelGateway,
    PingAnModelGatewayError,
)


def create_pingan_model_gateway_app(
    *,
    gateway: PingAnModelGateway,
    service_api_keys: Sequence[str],
    max_request_bytes: int = 2_000_000,
) -> FastAPI:
    """Build the loopback gateway without reading process-global config."""

    keys = tuple(dict.fromkeys(key.strip() for key in service_api_keys if key.strip()))
    if not keys:
        raise ValueError("at least one model gateway service API key is required")
    if max_request_bytes < 1:
        raise ValueError("max_request_bytes must be >= 1")

    app = FastAPI(
        title="SOC PingAn Model Gateway",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/health")
    async def health() -> dict:
        return {
            "status": "ok",
            "models": gateway.model_inventory(),
            "capacity": gateway.capacity_snapshot(),
            "secrets_included": False,
        }

    @app.get("/v1/models")
    @app.get("/models")
    async def models(
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ) -> dict:
        _verify_service_key(authorization, x_api_key=x_api_key, expected=keys)
        return {
            "object": "list",
            "data": [
                {
                    "id": alias,
                    "object": "model",
                    "created": 0,
                    "owned_by": "soc-internal",
                }
                for alias in gateway.model_aliases
            ],
        }

    @app.post("/v1/chat/completions")
    @app.post("/chat/completions")
    async def chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
        x_api_key: str | None = Header(default=None, alias="x-api-key"),
    ) -> JSONResponse:
        _verify_service_key(authorization, x_api_key=x_api_key, expected=keys)
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError:
                return _error_response(
                    status_code=400,
                    code="invalid_content_length",
                    message="Content-Length must be an integer",
                )
            if declared_size < 0:
                return _error_response(
                    status_code=400,
                    code="invalid_content_length",
                    message="Content-Length must not be negative",
                )
            if declared_size > max_request_bytes:
                return _error_response(
                    status_code=413,
                    code="request_too_large",
                    message="request body exceeds configured limit",
                )
        try:
            raw_body = await _read_bounded_body(
                request,
                max_request_bytes=max_request_bytes,
            )
        except _RequestBodyTooLarge:
            return _error_response(
                status_code=413,
                code="request_too_large",
                message="request body exceeds configured limit",
            )
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error_response(
                status_code=400,
                code="invalid_json",
                message="request body must be valid JSON",
            )
        if not isinstance(body, dict):
            return _error_response(
                status_code=400,
                code="invalid_request",
                message="request body must be an object",
            )
        try:
            result = await gateway.complete(body)
        except PingAnModelGatewayError as exc:
            return _error_response(
                status_code=exc.http_status,
                code=exc.code,
                message=str(exc),
            )
        metadata = result.metadata
        headers = {
            "X-SOC-Model-Alias": str(metadata["public_model_alias"]),
            "X-SOC-Upstream-Model": str(metadata["upstream_model"]),
            "X-SOC-Usage-Measurement": str(metadata["usage_measurement_status"]),
            "X-SOC-Admission-Wait-Ms": str(metadata["admission_wait_duration_ms"]),
            "X-SOC-Provider-Duration-Ms": str(metadata["provider_duration_ms"]),
        }
        return JSONResponse(
            status_code=result.status_code,
            content=result.body,
            headers=headers,
        )

    return app


class _RequestBodyTooLarge(ValueError):
    pass


async def _read_bounded_body(
    request: Request,
    *,
    max_request_bytes: int,
) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > max_request_bytes:
            raise _RequestBodyTooLarge
        body.extend(chunk)
    return bytes(body)


def _verify_service_key(
    authorization: str | None,
    *,
    x_api_key: str | None,
    expected: Sequence[str],
) -> None:
    candidate = x_api_key
    if not candidate and authorization:
        scheme, separator, token = authorization.partition(" ")
        if separator and scheme.lower() == "bearer":
            candidate = token.strip()
    if candidate and any(secrets.compare_digest(candidate, configured) for configured in expected):
        return
    from fastapi import HTTPException

    raise HTTPException(status_code=401, detail="invalid API key")


def _error_response(*, status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "message": message,
                "type": "model_gateway_error",
                "param": None,
                "code": code,
            }
        },
    )


__all__ = ["create_pingan_model_gateway_app"]
