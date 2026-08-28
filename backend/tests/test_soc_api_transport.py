from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.routers import (
    soc_alerts,
    soc_approvals,
    soc_effectiveness,
    soc_external_dispositions,
    soc_memory,
    soc_normalization,
    soc_operations,
    soc_review,
)
from app.gateway.routers.soc_dependencies import soc_service_context_from_request
from app.gateway.routers.soc_transport import create_soc_router

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPOSITORY_ROOT / "contracts" / "soc_api" / "openapi-v1.snapshot.json"
HTTP_METHODS = frozenset({"delete", "get", "patch", "post", "put"})
ERROR_STATUSES = ("400", "403", "404", "409", "422", "503")


class _ValidationBody(BaseModel):
    token: str = Field(pattern="^allowed$")


def _transport_test_app() -> FastAPI:
    app = FastAPI()
    router = create_soc_router(prefix="/api/soc/test", tags=["soc-test"])

    @router.get("/context")
    async def get_context(request: Request) -> dict[str, str | None]:
        context = soc_service_context_from_request(request)
        return {"request_id": context.request_id, "trace_id": context.trace_id}

    @router.get("/missing")
    async def get_missing() -> None:
        raise HTTPException(status_code=404, detail="SOC test resource is missing")

    @router.post("/validate")
    async def validate_body(body: _ValidationBody) -> _ValidationBody:
        return body

    app.include_router(router)
    return app


async def _request(method: str, path: str, **kwargs) -> httpx.Response:
    transport = httpx.ASGITransport(app=_transport_test_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.request(method, path, **kwargs)


def _soc_openapi() -> dict:
    app = FastAPI()
    for module in (
        soc_alerts,
        soc_approvals,
        soc_effectiveness,
        soc_external_dispositions,
        soc_memory,
        soc_normalization,
        soc_operations,
        soc_review,
    ):
        app.include_router(module.router)
    return app.openapi()


def _contract_snapshot(openapi: dict) -> dict:
    paths: dict[str, list[str]] = {}
    operations: list[dict] = []
    for path, path_item in openapi["paths"].items():
        if not path.startswith("/api/soc/"):
            continue
        methods = sorted(method for method in path_item if method in HTTP_METHODS)
        paths[path] = methods
        operations.extend(path_item[method] for method in methods)

    expected_request_headers = {"Idempotency-Key", "X-Request-Id", "X-Trace-Id"}
    expected_response_headers = {"X-Request-Id", "X-SOC-API-Version", "X-Trace-Id"}
    for operation in operations:
        assert operation["x-soc-api-version"] == "1"
        request_headers = {parameter["name"] for parameter in operation.get("parameters", []) if parameter.get("in") == "header"}
        assert request_headers == expected_request_headers
        assert set(operation["responses"]["200"]["headers"]) == expected_response_headers
        for status in ERROR_STATUSES:
            response = operation["responses"][status]
            assert set(response["headers"]) == expected_response_headers
            assert "application/problem+json" in response["content"]
            assert response["content"]["application/json"]["schema"]["$ref"] == ("#/components/schemas/SocProblemDetails")

    return {
        "schema_version": "soc.openapi.snapshot.v1",
        "api_version": "1",
        "base_path": "/api/soc",
        "success_body": "direct_typed_json",
        "problem_schema": "#/components/schemas/SocProblemDetails",
        "request_headers": sorted(expected_request_headers),
        "response_headers": sorted(expected_response_headers),
        "error_statuses": list(ERROR_STATUSES),
        "paths": dict(sorted(paths.items())),
    }


@pytest.mark.asyncio
async def test_soc_transport_echoes_request_metadata_into_service_context_and_response() -> None:
    response = await _request(
        "GET",
        "/api/soc/test/context",
        headers={"X-Request-Id": "req-client-1", "X-Trace-Id": "trace-client-1"},
    )

    assert response.status_code == 200
    assert response.headers["X-SOC-API-Version"] == "1"
    assert response.headers["X-Request-Id"] == "req-client-1"
    assert response.headers["X-Trace-Id"] == "trace-client-1"
    assert response.json() == {
        "request_id": "req-client-1",
        "trace_id": "trace-client-1",
    }


@pytest.mark.asyncio
async def test_soc_transport_generates_request_and_trace_ids() -> None:
    response = await _request("GET", "/api/soc/test/context")

    assert response.status_code == 200
    assert response.headers["X-Request-Id"].startswith("req_")
    assert response.headers["X-Trace-Id"]
    assert response.json()["request_id"] == response.headers["X-Request-Id"]
    assert response.json()["trace_id"] == response.headers["X-Trace-Id"]


@pytest.mark.asyncio
async def test_soc_transport_returns_problem_details_for_http_errors() -> None:
    response = await _request(
        "GET",
        "/api/soc/test/missing",
        headers={"X-Request-Id": "req-missing-1"},
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["X-SOC-API-Version"] == "1"
    assert response.json() == {
        "schema_version": "soc.api.problem.v1",
        "type": "urn:deerflow:soc:problem:soc.not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "SOC test resource is missing",
        "instance": "/api/soc/test/missing",
        "code": "soc.not_found",
        "request_id": "req-missing-1",
        "trace_id": response.headers["X-Trace-Id"],
        "retryable": False,
        "errors": [],
    }


@pytest.mark.asyncio
async def test_soc_transport_sanitizes_validation_errors() -> None:
    secret_input = "secret-value-that-must-not-be-reflected"
    response = await _request(
        "POST",
        "/api/soc/test/validate",
        headers={"X-Request-Id": "req-validation-1"},
        json={"token": secret_input},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "soc.validation_error"
    assert response.json()["errors"] == [
        {
            "location": ["body", "token"],
            "message": "String should match pattern '^allowed$'",
            "error_type": "string_pattern_mismatch",
        }
    ]
    assert secret_input not in response.text


@pytest.mark.asyncio
async def test_soc_transport_rejects_header_unsafe_request_id() -> None:
    response = await _request(
        "GET",
        "/api/soc/test/context",
        headers={"X-Request-Id": "invalid request id"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "soc.invalid_request_id"
    assert response.headers["X-Request-Id"].startswith("req_")


def test_soc_openapi_contract_matches_reviewed_snapshot() -> None:
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    actual = _contract_snapshot(_soc_openapi())

    assert actual == expected
    assert not any(path.startswith("/api/soc/v1/") for path in actual["paths"])
