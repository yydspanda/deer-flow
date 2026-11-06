"""Authenticated, engine-injected HTTP shell; importing it starts no service."""

from __future__ import annotations

import json
import secrets
from collections.abc import Awaitable
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, Header, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .contracts import TraceCreateRequest, TraceCreateResponse, TraceHealth, TraceOperation, TraceTaskResponse, extract_alert_id, normalize_evidence_gaps, validate_task_id
from .engine import TraceEngine, TraceReply, TraceServiceError, UnavailableEngine

MAX_ALERT_BYTES = 10 * 1024 * 1024


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise ValueError("Non-finite JSON values are not allowed")


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"errorCode": code, "errorMessage": message}, status_code=status_code)


def _validated_reply(reply: Any, model: type[TraceCreateResponse], task_id: str, *, status_code: int) -> JSONResponse:
    if not isinstance(reply, TraceReply):
        raise ValueError("Engine must return a TraceReply snapshot")
    # Revalidate even constructed/copied models; engine output crosses a trust boundary.
    body = model.model_validate(reply.body.model_dump(mode="json"))
    if body.taskId != task_id or isinstance(reply.revision, bool) or not isinstance(reply.revision, int) or reply.revision < 1:
        raise ValueError("Engine returned a conflicting identity or revision")
    data = body.model_dump(mode="json")
    for name in ("errorCode", "errorMessage"):
        if data.get(name) is None:
            data.pop(name, None)
    return JSONResponse(data, status_code=status_code, headers={"X-Trace-Revision": str(reply.revision)})


async def _invoke(awaitable: Awaitable[Any], model: type[TraceCreateResponse], task_id: str, *, status_code: int = 200, create: bool = False) -> JSONResponse:
    try:
        reply = await awaitable
        code = (202 if reply.created else 200) if create and isinstance(reply, TraceReply) else status_code
        return _validated_reply(reply, model, task_id, status_code=code)
    except TraceServiceError as exc:
        return _error(exc.status_code, exc.code, exc.public_message)
    except Exception:
        return _error(502, "TRACE_ENGINE_PROTOCOL_ERROR", "Trace engine did not return a valid result.")


def create_app(*, engine: TraceEngine | None = None, api_key: str | None = None) -> FastAPI:
    """Create a shell, using only the explicitly supplied engine and API key.

    No global application instance or import-time factory is created. Health is
    intentionally public; every other request passes authentication before body
    parsing, route validation and engine-owned operation deduplication.
    """
    application = FastAPI(title="Trace investigation service", version="0.1.0")
    selected_engine = engine if engine is not None else UnavailableEngine()
    key = api_key if isinstance(api_key, str) and api_key.strip() else None

    @application.middleware("http")
    async def authenticate(request: Request, call_next):
        if request.method == "GET" and request.url.path == "/health":
            return await call_next(request)
        if key is None:
            return _error(503, "TRACE_API_KEY_UNCONFIGURED", "Trace API authentication is not configured.")
        supplied = request.headers.get("X-API-Key", "")
        if not secrets.compare_digest(supplied.encode("utf-8"), key.encode("utf-8")):
            return _error(401, "TRACE_UNAUTHORIZED", "A valid X-API-Key is required.")
        return await call_next(request)

    @application.exception_handler(RequestValidationError)
    async def invalid_parameters(request: Request, exc: RequestValidationError):
        return _error(422, "TRACE_INVALID_REQUEST", "Required Trace request fields are missing or invalid.")

    @application.get("/health", response_model=TraceHealth)
    async def health():
        if key is None:
            return TraceHealth(status="degraded", detail="api_key_not_configured")
        try:
            value = await selected_engine.health()
            return TraceHealth.model_validate(value.model_dump(mode="json"))
        except Exception:
            return TraceHealth(status="degraded", detail="engine_health_unavailable")

    @application.post("/traces", response_model=TraceCreateResponse, status_code=202)
    async def create_trace(
        file: Annotated[UploadFile, File()],
        task_id: Annotated[str, Form()],
        request_id: Annotated[str, Header(alias="X-Request-Id")],
        evidence_gaps: Annotated[str | None, Form()] = None,
    ):
        try:
            validate_task_id(task_id)
            raw = await file.read(MAX_ALERT_BYTES + 1)
            if len(raw) > MAX_ALERT_BYTES:
                return _error(413, "TRACE_ALERT_TOO_LARGE", "The alert file exceeds the 10 MiB limit.")
            alert = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant)
            request = TraceCreateRequest(task_id=task_id, alert=alert, alert_id=extract_alert_id(alert), evidence_gaps=normalize_evidence_gaps(evidence_gaps), operation_id=request_id)
        except (ValueError, UnicodeError, ValidationError):
            return _error(422, "TRACE_INVALID_REQUEST", "Alert identity, JSON or investigation questions are invalid.")
        finally:
            await file.close()
        return await _invoke(selected_engine.create(request), TraceCreateResponse, task_id, create=True)

    @application.get("/traces/{task_id}", response_model=TraceTaskResponse)
    async def get_trace(task_id: str):
        try:
            validate_task_id(task_id)
        except ValueError:
            return _error(422, "TRACE_INVALID_REQUEST", "Invalid task id.")
        return await _invoke(selected_engine.get(task_id), TraceTaskResponse, task_id)

    async def operate(task_id: str, request_id: str, revision: int, *, cancel: bool):
        try:
            validate_task_id(task_id)
            operation = TraceOperation(operation_id=request_id, expected_version=revision)
        except ValueError:
            return _error(422, "TRACE_INVALID_REQUEST", "Invalid task operation or expected revision.")
        method = selected_engine.cancel if cancel else selected_engine.resume
        return await _invoke(method(task_id, operation), TraceCreateResponse, task_id, status_code=202)

    @application.post("/traces/{task_id}/resume", response_model=TraceCreateResponse, status_code=202)
    async def resume_trace(task_id: str, request_id: Annotated[str, Header(alias="X-Request-Id")], expected_revision: Annotated[int, Header(alias="X-Expected-Trace-Revision")]):
        return await operate(task_id, request_id, expected_revision, cancel=False)

    @application.post("/traces/{task_id}/cancel", response_model=TraceCreateResponse, status_code=202)
    async def cancel_trace(task_id: str, request_id: Annotated[str, Header(alias="X-Request-Id")], expected_revision: Annotated[int, Header(alias="X-Expected-Trace-Revision")]):
        return await operate(task_id, request_id, expected_revision, cancel=True)

    return application
