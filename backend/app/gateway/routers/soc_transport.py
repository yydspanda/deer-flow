"""Shared HTTP transport contract for SOC Gateway routes."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response

from deerflow.trace_context import (
    TRACE_ID_HEADER,
    generate_trace_id,
    get_current_trace_id,
    normalize_trace_id,
)

SOC_API_VERSION = "1"
SOC_API_VERSION_HEADER = "X-SOC-API-Version"
SOC_REQUEST_ID_HEADER = "X-Request-Id"
SOC_IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"
_MAX_REQUEST_ID_LENGTH = 128
_MAX_IDEMPOTENCY_KEY_LENGTH = 512
_REQUEST_ID_ALLOWED_PUNCTUATION = frozenset("-._:")


class SocValidationIssue(BaseModel):
    """Sanitized request-validation issue safe for an API response."""

    model_config = ConfigDict(extra="forbid")

    location: list[str | int]
    message: str
    error_type: str


class SocProblemDetails(BaseModel):
    """RFC Problem Details response with stable SOC extensions."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.api.problem.v1"] = "soc.api.problem.v1"
    type: str
    title: str
    status: int
    detail: str
    instance: str
    code: str
    request_id: str
    trace_id: str
    retryable: bool = False
    errors: list[SocValidationIssue] = Field(default_factory=list)


_RESPONSE_HEADERS_OPENAPI = {
    SOC_API_VERSION_HEADER: {
        "description": "Stable SOC HTTP transport version.",
        "schema": {"type": "string", "const": SOC_API_VERSION},
    },
    SOC_REQUEST_ID_HEADER: {
        "description": "Caller-provided or server-generated request correlation identifier.",
        "schema": {"type": "string"},
    },
    TRACE_ID_HEADER: {
        "description": "Gateway trace correlation identifier.",
        "schema": {"type": "string"},
    },
}


def _problem_response_spec(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "model": SocProblemDetails,
        "headers": _RESPONSE_HEADERS_OPENAPI,
        "content": {"application/problem+json": {}},
    }


SOC_COMMON_RESPONSES: dict[int, dict[str, Any]] = {
    200: {
        "description": "Successful typed SOC response.",
        "headers": _RESPONSE_HEADERS_OPENAPI,
    },
    400: _problem_response_spec("Invalid SOC request."),
    403: _problem_response_spec("SOC operation is not authorized."),
    404: _problem_response_spec("SOC resource was not found."),
    409: _problem_response_spec("SOC state or idempotency conflict."),
    422: _problem_response_spec("SOC request schema validation failed."),
    503: _problem_response_spec("Required SOC dependency is unavailable."),
}


async def _declare_soc_request_headers(
    x_request_id: Annotated[
        str | None,
        Header(
            alias=SOC_REQUEST_ID_HEADER,
            max_length=_MAX_REQUEST_ID_LENGTH,
            description="Optional caller request ID; echoed in the response.",
        ),
    ] = None,
    x_trace_id: Annotated[
        str | None,
        Header(
            alias=TRACE_ID_HEADER,
            max_length=512,
            description="Optional distributed trace ID; normalized by the Gateway.",
        ),
    ] = None,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias=SOC_IDEMPOTENCY_KEY_HEADER,
            max_length=_MAX_IDEMPOTENCY_KEY_LENGTH,
            description="Required only by operations that declare idempotent mutation semantics.",
        ),
    ] = None,
) -> None:
    del x_request_id, x_trace_id, idempotency_key


class SocAPIRoute(APIRoute):
    """Apply one compatible transport contract to every SOC endpoint."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        openapi_extra = dict(kwargs.pop("openapi_extra", None) or {})
        openapi_extra.setdefault("x-soc-api-version", SOC_API_VERSION)
        super().__init__(*args, openapi_extra=openapi_extra, **kwargs)

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_route_handler = super().get_route_handler()

        async def soc_route_handler(request: Request) -> Response:
            incoming_request_id = request.headers.get(SOC_REQUEST_ID_HEADER)
            request_id = normalize_soc_request_id(incoming_request_id) or _generate_request_id()
            trace_id = _resolve_trace_id(request)
            request.state.soc_request_id = request_id
            request.state.soc_trace_id = trace_id

            if incoming_request_id is not None and normalize_soc_request_id(incoming_request_id) is None:
                return _problem_response(
                    request,
                    status_code=400,
                    code="soc.invalid_request_id",
                    detail=(f"{SOC_REQUEST_ID_HEADER} must be 1-{_MAX_REQUEST_ID_LENGTH} characters using letters, digits, '-', '.', '_', or ':'"),
                )

            try:
                response = await original_route_handler(request)
            except RequestValidationError as exc:
                return _validation_problem_response(request, exc)
            except HTTPException as exc:
                return _http_problem_response(request, exc)

            return _apply_transport_headers(response, request_id=request_id, trace_id=trace_id)

        return soc_route_handler


def create_soc_router(*, prefix: str, tags: list[str]) -> APIRouter:
    """Create a router that exposes the stable SOC transport contract."""

    return APIRouter(
        prefix=prefix,
        tags=tags,
        route_class=SocAPIRoute,
        dependencies=[Depends(_declare_soc_request_headers)],
        responses=SOC_COMMON_RESPONSES,
    )


def normalize_soc_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    request_id = value.strip()
    if not request_id or len(request_id) > _MAX_REQUEST_ID_LENGTH:
        return None
    if not all(character.isascii() and (character.isalnum() or character in _REQUEST_ID_ALLOWED_PUNCTUATION) for character in request_id):
        return None
    return request_id


def soc_request_id_from_request(request: Request) -> str:
    request_id = normalize_soc_request_id(getattr(request.state, "soc_request_id", None))
    if request_id is None:
        request_id = normalize_soc_request_id(request.headers.get(SOC_REQUEST_ID_HEADER)) or _generate_request_id()
        request.state.soc_request_id = request_id
    return request_id


def soc_trace_id_from_request(request: Request) -> str:
    trace_id = normalize_trace_id(getattr(request.state, "soc_trace_id", None))
    if trace_id is None:
        trace_id = _resolve_trace_id(request)
        request.state.soc_trace_id = trace_id
    return trace_id


def _generate_request_id() -> str:
    return f"req_{uuid4().hex}"


def _resolve_trace_id(request: Request) -> str:
    return get_current_trace_id() or normalize_trace_id(request.headers.get(TRACE_ID_HEADER)) or generate_trace_id()


def _validation_problem_response(request: Request, exc: RequestValidationError) -> JSONResponse:
    issues = [
        SocValidationIssue(
            location=list(error.get("loc") or []),
            message=str(error.get("msg") or "Request validation failed"),
            error_type=str(error.get("type") or "validation_error"),
        )
        for error in exc.errors()
    ]
    return _problem_response(
        request,
        status_code=422,
        code="soc.validation_error",
        detail="SOC request schema validation failed",
        errors=issues,
    )


def _http_problem_response(request: Request, exc: HTTPException) -> JSONResponse:
    status_code = exc.status_code
    title = _status_title(status_code)
    detail = exc.detail if isinstance(exc.detail, str) else title
    return _problem_response(
        request,
        status_code=status_code,
        code=_problem_code(status_code),
        detail=detail,
        headers=exc.headers,
    )


def _problem_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    detail: str,
    errors: list[SocValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = soc_request_id_from_request(request)
    trace_id = soc_trace_id_from_request(request)
    problem = SocProblemDetails(
        type=f"urn:deerflow:soc:problem:{code}",
        title=_status_title(status_code),
        status=status_code,
        detail=detail,
        instance=request.url.path,
        code=code,
        request_id=request_id,
        trace_id=trace_id,
        retryable=status_code >= 500,
        errors=errors or [],
    )
    response = JSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=headers,
        media_type="application/problem+json",
    )
    return _apply_transport_headers(response, request_id=request_id, trace_id=trace_id)


def _apply_transport_headers(response: Response, *, request_id: str, trace_id: str) -> Response:
    response.headers[SOC_API_VERSION_HEADER] = SOC_API_VERSION
    response.headers[SOC_REQUEST_ID_HEADER] = request_id
    response.headers[TRACE_ID_HEADER] = trace_id
    return response


def _problem_code(status_code: int) -> str:
    return {
        400: "soc.bad_request",
        401: "soc.unauthorized",
        403: "soc.forbidden",
        404: "soc.not_found",
        409: "soc.conflict",
        422: "soc.validation_error",
        503: "soc.unavailable",
    }.get(status_code, "soc.http_error")


def _status_title(status_code: int) -> str:
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP Error"


__all__ = [
    "SOC_API_VERSION",
    "SOC_API_VERSION_HEADER",
    "SOC_IDEMPOTENCY_KEY_HEADER",
    "SOC_REQUEST_ID_HEADER",
    "SocAPIRoute",
    "SocProblemDetails",
    "SocValidationIssue",
    "create_soc_router",
    "normalize_soc_request_id",
    "soc_request_id_from_request",
    "soc_trace_id_from_request",
]
