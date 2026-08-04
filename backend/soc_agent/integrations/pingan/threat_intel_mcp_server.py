"""Read-only stdio MCP wrapper for PingAn ZEUS threat intelligence."""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import ValidationError

from soc_agent.integrations.pingan.threat_intel import (
    PingAnThreatIntelProviderError,
    PingAnThreatIntelQuery,
    build_pingan_threat_intel_service_from_env,
)

_SERVER_NAME = "soc-pingan-threat-intel"
_SERVER_VERSION = "0.1.0"
_DEFAULT_PROTOCOL_VERSION = "2025-11-25"
_TOOL_NAME = "ip_reputation_lookup"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["ip"],
    "properties": {
        "ip": {
            "type": "string",
            "description": "A validated IPv4 or IPv6 address selected by the SOC investigation planner.",
        }
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "ip",
        "reputation_found",
        "report_summaries",
        "label_evidence",
        "freshness_status",
        "queried_at",
        "duration_ms",
        "response_sha256",
        "mapping_warnings",
        "mocked",
        "provider_mode",
        "evidence_boundary",
        "decision_impact",
        "automation_eligible",
        "raw_response_included",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "ip": {"type": "string"},
        "reputation_found": {"type": "boolean"},
        "reputation": {"type": ["object", "null"]},
        "report_summaries": {"type": "array", "items": {"type": "object"}},
        "label_evidence": {"type": "array", "items": {"type": "object"}},
        "freshness_status": {"type": "string", "enum": ["fresh", "stale", "unknown", "not_found"]},
        "queried_at": {"type": "string"},
        "duration_ms": {"type": "number", "minimum": 0},
        "response_sha256": {"type": "string"},
        "mapping_warnings": {"type": "array", "items": {"type": "string"}},
        "mocked": {"type": "boolean"},
        "provider_mode": {"type": "string", "enum": ["fake", "internal"]},
        "evidence_boundary": {"type": "string", "const": "investigation_only"},
        "decision_impact": {"type": "string", "const": "none"},
        "automation_eligible": {"type": "boolean", "const": False},
        "raw_response_included": {"type": "boolean", "const": False},
    },
    "additionalProperties": False,
}


def main() -> None:
    try:
        service = build_pingan_threat_intel_service_from_env()
    except Exception as exc:  # noqa: BLE001 - startup failure is returned through tool calls
        service = None
        startup_error = _safe_error(exc)
    else:
        startup_error = None

    for line in sys.stdin:
        if not line.strip():
            continue
        message: dict[str, Any] | None = None
        try:
            loaded = json.loads(line)
            if not isinstance(loaded, dict):
                raise ValueError("MCP message must be a JSON object")
            message = loaded
            response = _handle_message(message, service=service, startup_error=startup_error)
        except Exception as exc:  # noqa: BLE001 - server must keep protocol errors structured
            response = _error_response(_request_id(message), -32603, _safe_error(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def _handle_message(
    message: dict[str, Any],
    *,
    service: Any,
    startup_error: str | None,
) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = _request_id(message)
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        protocol_version = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else _DEFAULT_PROTOCOL_VERSION
        return _success_response(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": _SERVER_NAME, "version": _SERVER_VERSION},
                "instructions": "Read-only PingAn ZEUS threat intelligence. Results are investigation evidence only.",
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return _success_response(
            request_id,
            {
                "tools": [
                    {
                        "name": _TOOL_NAME,
                        "description": "Query bounded PingAn ZEUS IP reputation and source lineage without scoring or disposal side effects.",
                        "inputSchema": _INPUT_SCHEMA,
                        "outputSchema": _OUTPUT_SCHEMA,
                    }
                ]
            },
        )
    if method == "tools/call":
        if startup_error or service is None:
            return _success_response(request_id, _tool_error(f"PingAn threat-intel provider unavailable: {startup_error}"))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("name") != _TOOL_NAME:
            return _success_response(request_id, _tool_error(f"unknown PingAn threat-intel tool: {params.get('name')}"))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            query = PingAnThreatIntelQuery.model_validate(arguments)
            result = service.lookup(query).model_dump(mode="json", exclude_none=False)
        except (ValidationError, PingAnThreatIntelProviderError) as exc:
            return _success_response(request_id, _tool_error(_safe_error(exc)))
        return _success_response(
            request_id,
            {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "structuredContent": result,
                "isError": False,
            },
        )
    if method == "ping":
        return _success_response(request_id, {})
    if request_id is None:
        return None
    return _error_response(request_id, -32601, f"method not found: {method}")


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, PingAnThreatIntelProviderError):
        return f"{exc.__class__.__name__}: {str(exc)[:400]}"
    return str(exc)[:500] or exc.__class__.__name__


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _success_response(request_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _request_id(message: Any) -> str | int | None:
    return message.get("id") if isinstance(message, dict) and "id" in message else None


__all__ = ["main"]
