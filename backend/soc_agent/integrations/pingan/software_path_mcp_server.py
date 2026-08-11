"""Read-only stdio MCP wrapper for the PingAn software-path catalog."""

from __future__ import annotations

import json
import sys
from typing import Any

from soc_agent.integrations.pingan.software_path_catalog import (
    PingAnSoftwarePathCatalog,
)

_SERVER_NAME = "soc-pingan-software-path"
_SERVER_VERSION = "0.1.0"
_DEFAULT_PROTOCOL_VERSION = "2025-11-25"
_TOOL_NAME = "software_path_lookup"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["path"],
    "properties": {
        "path": {"type": "string", "description": "Exact process or file path extracted from alert evidence."},
        "md5": {"type": "string", "pattern": "^[0-9A-Fa-f]{32}$"},
    },
    "additionalProperties": False,
}

_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": [
        "schema_version",
        "query_path",
        "normalized_path",
        "matched",
        "match_type",
        "exact_safe_path_candidate",
        "control_zone",
        "location_attention",
        "catalog_id",
        "source_sha256",
        "warnings",
        "provider_mode",
        "mocked",
        "candidate_only",
        "allowlist",
        "evidence_boundary",
        "decision_impact",
        "automation_eligible",
        "raw_rows_included",
    ],
    "properties": {
        "schema_version": {"type": "string"},
        "query_path": {"type": "string"},
        "normalized_path": {"type": "string"},
        "query_md5": {"type": "string"},
        "matched": {"type": "boolean"},
        "match_type": {"type": "string"},
        "control_zone": {"type": "string"},
        "location_attention": {"type": "string"},
        "historical_context": {"type": "object"},
        "path_family_context": {"type": "object"},
        "exact_safe_path_candidate": {"type": "boolean"},
        "catalog_id": {"type": "string"},
        "catalog_schema_version": {"type": "string"},
        "source_sha256": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "provider_mode": {"type": "string", "const": "local_catalog"},
        "mocked": {"type": "boolean", "const": False},
        "candidate_only": {"type": "boolean", "const": True},
        "allowlist": {"type": "boolean", "const": False},
        "evidence_boundary": {"type": "string", "const": "investigation_only"},
        "decision_impact": {"type": "string", "const": "none"},
        "automation_eligible": {"type": "boolean", "const": False},
        "raw_rows_included": {"type": "boolean", "const": False},
    },
    "additionalProperties": False,
}


def main() -> None:
    try:
        catalog = PingAnSoftwarePathCatalog.from_env()
    except Exception as exc:  # noqa: BLE001 - startup error is returned through MCP
        catalog = None
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
            response = _handle_message(message, catalog=catalog, startup_error=startup_error)
        except Exception as exc:  # noqa: BLE001 - stdio server must remain alive
            response = _error_response(_request_id(message), -32603, _safe_error(exc))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()


def _handle_message(
    message: dict[str, Any],
    *,
    catalog: PingAnSoftwarePathCatalog | None,
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
                "instructions": "Historical PingAn EDR exact/path-family context only. This MCP remains investigation-only; optional tenant policy authority is separate.",
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
                        "description": "Look up exact or inferred-family historical EDR path/hash context without changing the SOC decision.",
                        "inputSchema": _INPUT_SCHEMA,
                        "outputSchema": _OUTPUT_SCHEMA,
                    }
                ]
            },
        )
    if method == "tools/call":
        if startup_error or catalog is None:
            return _success_response(request_id, _tool_error(f"PingAn software path catalog unavailable: {startup_error}"))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        if params.get("name") != _TOOL_NAME:
            return _success_response(request_id, _tool_error(f"unknown PingAn software path tool: {params.get('name')}"))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        path = arguments.get("path")
        md5 = arguments.get("md5")
        try:
            if not isinstance(path, str) or not path.strip():
                raise ValueError("path is required")
            if md5 is not None and not isinstance(md5, str):
                raise ValueError("md5 must be a string")
            result = catalog.lookup(path, md5=md5).model_dump(mode="json", exclude_none=True)
        except ValueError as exc:
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


def _request_id(message: dict[str, Any] | None) -> str | int | None:
    return message.get("id") if isinstance(message, dict) else None


def _safe_error(exc: Exception) -> str:
    return str(exc)[:500] or exc.__class__.__name__


def _tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def _success_response(request_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


if __name__ == "__main__":
    main()
