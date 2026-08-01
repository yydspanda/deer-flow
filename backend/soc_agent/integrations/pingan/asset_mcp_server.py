"""Read-only stdio MCP wrapper for the PingAn asset location provider."""

from __future__ import annotations

import json
import sys
from typing import Any

from pydantic import ValidationError

from soc_agent.integrations.pingan.asset_location import (
    PingAnAssetLocationQuery,
    PingAnAssetProviderError,
    build_pingan_asset_locator_from_env,
)

_SERVER_NAME = "soc-pingan-asset"
_SERVER_VERSION = "0.1.0"
_DEFAULT_PROTOCOL_VERSION = "2025-11-25"
_TOOL_NAME = "asset_locate"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {"type": "string", "description": "Already-extracted IP, host, domain, URL, or UM account."},
        "asset_type": {"type": "string", "enum": ["IP", "DOMAIN", "WEB", "HOST", "USER"]},
        "role": {"type": "string", "description": "Optional SOC role hint; never used as disposal authorization."},
        "um": {"type": "string", "description": "Optional UM fallback after asset lookup and asset-to-BU miss."},
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "query",
        "asset_type",
        "found",
        "resolved",
        "ambiguous",
        "candidates",
        "attempts",
        "mocked",
        "provider_mode",
        "evidence_boundary",
        "decision_impact",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "query": {"type": "string"},
        "asset_type": {"type": "string"},
        "role": {"type": "string"},
        "found": {"type": "boolean"},
        "resolved": {"type": "boolean"},
        "ambiguous": {"type": "boolean"},
        "company_code": {"type": "string"},
        "company_name": {"type": "string"},
        "biz_group": {"type": "string"},
        "source": {"type": "string"},
        "candidates": {"type": "array", "items": {"type": "object"}},
        "attempts": {"type": "array", "items": {"type": "object"}},
        "mocked": {"type": "boolean"},
        "provider_mode": {"type": "string", "enum": ["fake", "internal"]},
        "evidence_boundary": {"type": "string", "const": "investigation_only"},
        "decision_impact": {"type": "string", "const": "none"},
        "raw_response_included": {"type": "boolean", "const": False},
    },
    "additionalProperties": False,
}


def main() -> None:
    try:
        locator = build_pingan_asset_locator_from_env()
    except Exception as exc:  # noqa: BLE001 - startup error is returned through MCP requests
        locator = None
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
            response = _handle_message(
                message,
                locator=locator,
                startup_error=startup_error,
            )
        except Exception as exc:  # noqa: BLE001 - server must return protocol errors
            response = _error_response(_request_id(message), -32603, _safe_error(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def _handle_message(
    message: dict[str, Any],
    *,
    locator: Any,
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
                "instructions": "Read-only PingAn asset ownership provider. Results are investigation evidence only.",
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
                        "description": "Locate PingAn asset business ownership through ZEUS and configured fallback workflows.",
                        "inputSchema": _INPUT_SCHEMA,
                        "outputSchema": _OUTPUT_SCHEMA,
                    }
                ]
            },
        )
    if method == "tools/call":
        if startup_error or locator is None:
            return _success_response(request_id, _tool_error(f"PingAn asset provider unavailable: {startup_error}"))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("name") != _TOOL_NAME:
            return _success_response(request_id, _tool_error(f"unknown PingAn asset tool: {params.get('name')}"))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        try:
            query = PingAnAssetLocationQuery.model_validate(arguments)
            result = locator.locate(query).model_dump(mode="json")
        except (ValidationError, PingAnAssetProviderError) as exc:
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
