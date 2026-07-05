"""SOC action adapters backed by MCP tools.

This module is the SOC boundary around MCP. Lead Agent code must keep using
SOC route/action names; MCP server/tool names stay inside adapter config.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from soc_agent.action_adapters import SocActionAdapterRegistryError
from soc_agent.contracts import (
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentRiskLevel,
)


@dataclass(frozen=True)
class SocMcpToolDescriptor:
    """Stable SOC-facing descriptor for an MCP tool candidate."""

    name: str
    server: str | None = None
    description: str = ""
    input_schema: Mapping[str, Any] = field(default_factory=dict)


class SocMcpToolProviderPort(Protocol):
    """Narrow provider port for MCP-backed SOC action adapters."""

    def list_tools(self) -> list[SocMcpToolDescriptor]: ...

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
    ) -> Mapping[str, Any]: ...


class SocMcpToolProviderError(RuntimeError):
    """Raised by SOC MCP providers for external tool failures."""


class SocMcpToolNotFoundError(SocMcpToolProviderError, LookupError):
    """Raised when a configured MCP tool is not available."""


def mcp_read_only_adapter_descriptor(
    *,
    adapter_id: str,
    route: str,
    action: str | None = None,
    required_payload_fields: list[str] | None = None,
    required_context_refs: list[str] | None = None,
    description: str = "",
    metadata: dict[str, Any] | None = None,
) -> SocAgentActionAdapterDescriptor:
    """Build a descriptor for a read-only MCP-backed SOC action adapter."""

    return SocAgentActionAdapterDescriptor(
        adapter_id=adapter_id,
        route=route,
        action=action or route,
        risk_level=SocAgentRiskLevel.READ_ONLY,
        adapter_kind="mcp",
        external_side_effect="read",
        dry_run_supported=True,
        execute_supported=True,
        idempotency_required=False,
        required_payload_fields=required_payload_fields or [],
        required_context_refs=required_context_refs or [],
        description=description,
        metadata=metadata or {},
    )


class SocMcpToolActionAdapter:
    """Read-only SOC action adapter that invokes an allowlisted MCP tool."""

    def __init__(
        self,
        *,
        descriptor: SocAgentActionAdapterDescriptor,
        provider: SocMcpToolProviderPort,
        tool_name: str,
        timeout_seconds: int = 5,
        input_mapping: Mapping[str, str] | None = None,
        output_fields: Iterable[str] = (),
        result_schema_version: str = "soc.mcp_tool_result.v1",
    ) -> None:
        if descriptor.adapter_kind != "mcp":
            raise SocActionAdapterRegistryError("SocMcpToolActionAdapter requires adapter_kind=mcp")
        if descriptor.risk_level != SocAgentRiskLevel.READ_ONLY:
            raise SocActionAdapterRegistryError("SocMcpToolActionAdapter currently supports read-only actions only")
        if descriptor.external_side_effect != "read":
            raise SocActionAdapterRegistryError("SocMcpToolActionAdapter requires external_side_effect=read")
        if not descriptor.execute_supported:
            raise SocActionAdapterRegistryError("SocMcpToolActionAdapter requires execute_supported=true for read-only invocation")
        if timeout_seconds <= 0:
            raise SocActionAdapterRegistryError("SocMcpToolActionAdapter timeout_seconds must be positive")
        self.descriptor = descriptor
        self._provider = provider
        self._tool_name = tool_name
        self._timeout_seconds = timeout_seconds
        self._input_mapping = dict(input_mapping or {})
        self._output_fields = tuple(output_fields)
        self._result_schema_version = result_schema_version

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if not command.dry_run:
            raise SocActionAdapterRegistryError("MCP adapter dry-run requires command.dry_run=true")
        _validate_command(command, self.descriptor)
        _validate_required_fields("payload", command.payload, self.descriptor.required_payload_fields)
        _validate_context_refs(command.payload, self.descriptor.required_context_refs)
        _validate_tool_available(self._provider, self._tool_name)
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message=f"MCP tool dry-run validated for {self._tool_name}; no external read executed.",
            payload={
                "adapter_id": self.descriptor.adapter_id,
                "adapter_kind": self.descriptor.adapter_kind,
                "tool_name": self._tool_name,
                "dry_run": True,
                "external_side_effect": "not_executed",
                "read_only": True,
                "timeout_seconds": self._timeout_seconds,
                "required_payload_fields": self.descriptor.required_payload_fields,
                "required_context_refs": self.descriptor.required_context_refs,
                "executed_by": context.actor.model_dump(mode="json"),
                "idempotency_key": context.idempotency_key,
            },
        )

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if command.dry_run:
            raise SocActionAdapterRegistryError("MCP adapter execute requires command.dry_run=false")
        _validate_command(command, self.descriptor)
        _validate_required_fields("payload", command.payload, self.descriptor.required_payload_fields)
        _validate_context_refs(command.payload, self.descriptor.required_context_refs)
        tool_payload = _tool_payload(command.payload, self._input_mapping)
        try:
            raw_result = self._provider.invoke(
                self._tool_name,
                tool_payload,
                timeout_seconds=self._timeout_seconds,
            )
            if not isinstance(raw_result, Mapping):
                raise SocMcpToolProviderError("MCP tool returned a non-object result")
            result = _select_output_fields(raw_result, self._output_fields)
        except Exception as exc:  # noqa: BLE001 - external provider boundary maps failures to action result
            return SocAgentActionResult(
                route=command.route,
                action=command.action,
                status="failed",
                message=f"MCP tool {self._tool_name} execution failed: {exc}",
                payload={
                    "adapter_id": self.descriptor.adapter_id,
                    "adapter_kind": self.descriptor.adapter_kind,
                    "tool_name": self._tool_name,
                    "dry_run": False,
                    "external_side_effect": "not_executed",
                    "read_only": True,
                    "timeout_seconds": self._timeout_seconds,
                    "error_type": exc.__class__.__name__,
                    "error": str(exc),
                    "executed_by": context.actor.model_dump(mode="json"),
                    "idempotency_key": context.idempotency_key,
                },
            )
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message=f"MCP tool {self._tool_name} completed for SOC action {command.action}.",
            payload={
                "adapter_id": self.descriptor.adapter_id,
                "adapter_kind": self.descriptor.adapter_kind,
                "tool_name": self._tool_name,
                "dry_run": False,
                "external_side_effect": "read",
                "read_only": True,
                "timeout_seconds": self._timeout_seconds,
                "result_schema_version": self._result_schema_version,
                "mcp_result": result,
                "executed_by": context.actor.model_dump(mode="json"),
                "idempotency_key": context.idempotency_key,
            },
        )


def _validate_command(command: SocAgentActionCommand, descriptor: SocAgentActionAdapterDescriptor) -> None:
    if command.route != descriptor.route:
        raise SocActionAdapterRegistryError("command route does not match MCP action adapter descriptor")
    if command.action != descriptor.action:
        raise SocActionAdapterRegistryError("command action does not match MCP action adapter descriptor")


def _validate_required_fields(name: str, payload: Mapping[str, Any], required_fields: list[str]) -> None:
    missing = [field for field in required_fields if not _has_value(payload.get(field))]
    if missing:
        raise SocActionAdapterRegistryError(f"missing required {name} fields: {', '.join(missing)}")


def _validate_context_refs(payload: Mapping[str, Any], required_context_refs: list[str]) -> None:
    context_refs = payload.get("context_refs")
    refs = context_refs if isinstance(context_refs, Mapping) else {}
    _validate_required_fields("context_refs", refs, required_context_refs)


def _validate_tool_available(provider: SocMcpToolProviderPort, tool_name: str) -> None:
    if not any(tool.name == tool_name for tool in provider.list_tools()):
        raise SocActionAdapterRegistryError(f"MCP tool {tool_name!r} is not available")


def _tool_payload(command_payload: Mapping[str, Any], input_mapping: Mapping[str, str]) -> dict[str, Any]:
    if input_mapping:
        return {tool_field: command_payload[source_field] for source_field, tool_field in input_mapping.items() if _has_value(command_payload.get(source_field))}
    return {key: value for key, value in command_payload.items() if key != "context_refs"}


def _select_output_fields(result: Mapping[str, Any], output_fields: tuple[str, ...]) -> dict[str, Any]:
    if not output_fields:
        return {}
    return {field: result[field] for field in output_fields if field in result}


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


__all__ = [
    "SocMcpToolActionAdapter",
    "SocMcpToolDescriptor",
    "SocMcpToolNotFoundError",
    "SocMcpToolProviderError",
    "SocMcpToolProviderPort",
    "mcp_read_only_adapter_descriptor",
]
