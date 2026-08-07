"""SOC request and trace correlation helpers without router imports."""

from __future__ import annotations

from uuid import uuid4

from fastapi import Request

from deerflow.trace_context import (
    TRACE_ID_HEADER,
    generate_trace_id,
    get_current_trace_id,
    normalize_trace_id,
)

SOC_REQUEST_ID_HEADER = "X-Request-Id"
SOC_REQUEST_ID_MAX_LENGTH = 128
_REQUEST_ID_ALLOWED_PUNCTUATION = frozenset("-._:")


def normalize_soc_request_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    request_id = value.strip()
    if not request_id or len(request_id) > SOC_REQUEST_ID_MAX_LENGTH:
        return None
    if not all(character.isascii() and (character.isalnum() or character in _REQUEST_ID_ALLOWED_PUNCTUATION) for character in request_id):
        return None
    return request_id


def generate_soc_request_id() -> str:
    return f"req_{uuid4().hex}"


def resolve_soc_trace_id(request: Request) -> str:
    return get_current_trace_id() or normalize_trace_id(request.headers.get(TRACE_ID_HEADER)) or generate_trace_id()


def soc_request_id_from_request(request: Request) -> str:
    request_id = normalize_soc_request_id(getattr(request.state, "soc_request_id", None))
    if request_id is None:
        request_id = normalize_soc_request_id(request.headers.get(SOC_REQUEST_ID_HEADER)) or generate_soc_request_id()
        request.state.soc_request_id = request_id
    return request_id


def soc_trace_id_from_request(request: Request) -> str:
    trace_id = normalize_trace_id(getattr(request.state, "soc_trace_id", None))
    if trace_id is None:
        trace_id = resolve_soc_trace_id(request)
        request.state.soc_trace_id = trace_id
    return trace_id


__all__ = [
    "SOC_REQUEST_ID_HEADER",
    "SOC_REQUEST_ID_MAX_LENGTH",
    "generate_soc_request_id",
    "normalize_soc_request_id",
    "resolve_soc_trace_id",
    "soc_request_id_from_request",
    "soc_trace_id_from_request",
]
