"""Local read-only MCP stdio server for SOC development smoke tests.

The server speaks the MCP JSON-RPC stdio protocol directly. It is deliberately
deterministic and has no external side effects, so it can validate DeerFlow's
real MCP client path before staging CMDB/EDR/F5 services are connected.
"""

from __future__ import annotations

import json
import sys
from typing import Any

_SERVER_NAME = "soc-dev"
_SERVER_VERSION = "0.1.0"
_DEFAULT_PROTOCOL_VERSION = "2025-11-25"

_ASSET_LOOKUP_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["query"],
    "properties": {
        "query": {
            "type": "string",
            "description": "IP address, hostname, or asset key to look up.",
        }
    },
    "additionalProperties": False,
}

_ASSET_LOOKUP_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "query", "asset_found", "asset_record", "source"],
    "properties": {
        "schema_version": {"type": "string"},
        "query": {"type": "string"},
        "asset_found": {"type": "boolean"},
        "asset_record": {
            "type": ["object", "null"],
            "additionalProperties": True,
        },
        "source": {"type": "string"},
    },
    "additionalProperties": True,
}

_ASSETS: dict[str, dict[str, Any]] = {
    "10.10.1.5": {
        "asset_id": "asset-001",
        "asset_key": "10.10.1.5",
        "asset_type": "server",
        "business_owner": "payments-sre",
        "environment": "dev",
        "criticality": "high",
        "network_zone": "intranet",
    },
    "203.0.113.10": {
        "asset_id": "internet-001",
        "asset_key": "203.0.113.10",
        "asset_type": "external-ip",
        "business_owner": "internet-edge",
        "environment": "staging",
        "criticality": "medium",
        "network_zone": "internet",
    },
}


def main() -> None:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = _handle_message(message)
        except Exception as exc:  # noqa: BLE001 - dev MCP server returns protocol errors
            response = _error_response(_request_id_from_message(locals().get("message")), -32603, str(exc))
        if response is not None:
            _write_response(response)


def _handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = _request_id_from_message(message)
    if method == "initialize":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        protocol_version = params.get("protocolVersion") if isinstance(params.get("protocolVersion"), str) else _DEFAULT_PROTOCOL_VERSION
        return _success_response(
            request_id,
            {
                "protocolVersion": protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": _SERVER_NAME,
                    "version": _SERVER_VERSION,
                },
                "instructions": "Read-only deterministic SOC development MCP server.",
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
                        "name": "asset_lookup",
                        "description": "Read-only lookup of deterministic SOC development asset ownership.",
                        "inputSchema": _ASSET_LOOKUP_INPUT_SCHEMA,
                        "outputSchema": _ASSET_LOOKUP_OUTPUT_SCHEMA,
                    }
                ]
            },
        )
    if method == "tools/call":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        return _success_response(request_id, _call_tool(params))
    if method == "ping":
        return _success_response(request_id, {})
    if request_id is None:
        return None
    return _error_response(request_id, -32601, f"method not found: {method}")


def _call_tool(params: dict[str, Any]) -> dict[str, Any]:
    tool_name = params.get("name")
    arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    if tool_name != "asset_lookup":
        return _tool_error(f"unknown SOC development tool: {tool_name}")
    query = arguments.get("query")
    if not isinstance(query, str):
        return _tool_error("query must be a string")
    result = _asset_lookup(query)
    return {
        "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        "structuredContent": result,
        "isError": False,
    }


def _asset_lookup(query: str) -> dict[str, Any]:
    normalized_query = query.strip()
    asset_record = _ASSETS.get(normalized_query)
    return {
        "schema_version": "soc.dev_asset_lookup_result.v1",
        "query": normalized_query,
        "asset_found": asset_record is not None,
        "asset_record": asset_record,
        "source": "soc_dev_mcp_server",
    }


def _tool_error(message: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": message}],
        "isError": True,
    }


def _success_response(request_id: str | int | None, result: dict[str, Any]) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


def _error_response(request_id: str | int | None, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": code,
            "message": message,
        },
    }


def _request_id_from_message(message: Any) -> str | int | None:
    return message.get("id") if isinstance(message, dict) and "id" in message else None


def _write_response(response: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
