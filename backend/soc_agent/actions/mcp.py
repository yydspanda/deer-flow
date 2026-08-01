"""SOC action adapters backed by MCP tools.

This module is the SOC boundary around MCP. Lead Agent code must keep using
SOC route/action names; MCP server/tool names stay inside adapter config.
"""

from __future__ import annotations

import concurrent.futures
import json
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from soc_agent.actions.adapters import SocActionAdapterRegistry, SocActionAdapterRegistryError
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
        server_name: str | None = None,
    ) -> Mapping[str, Any]: ...


class SocMcpToolProviderError(RuntimeError):
    """Raised by SOC MCP providers for external tool failures."""


class SocMcpToolNotFoundError(SocMcpToolProviderError, LookupError):
    """Raised when a configured MCP tool is not available."""


class DeerFlowCachedMcpToolProvider:
    """SOC provider backed by DeerFlow's cached MCP tool lifecycle."""

    def __init__(
        self,
        tools_loader: Callable[[], Iterable[Any]] | None = None,
        *,
        use_one_shot_invocation: bool = False,
    ) -> None:
        self._tools_loader = tools_loader or _load_deerflow_cached_mcp_tools
        self._use_one_shot_invocation = tools_loader is None and use_one_shot_invocation

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        tools = self._load_tools()
        return [
            SocMcpToolDescriptor(
                name=tool.name,
                server=_server_from_tool(tool),
                description=str(getattr(tool, "description", "") or ""),
                input_schema=_tool_input_schema(tool),
            )
            for tool in sorted(tools, key=lambda item: item.name)
        ]

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
        server_name: str | None = None,
    ) -> Mapping[str, Any]:
        tool = self._tool_by_name(tool_name)
        try:
            if self._use_one_shot_invocation:
                raw_result = _invoke_mcp_tool_once_with_timeout(
                    tool_name,
                    payload,
                    timeout_seconds=timeout_seconds,
                    server_name=server_name,
                )
            else:
                raw_result = _invoke_tool_with_timeout(
                    tool,
                    payload,
                    timeout_seconds=timeout_seconds,
                )
        except SocMcpToolProviderError:
            raise
        except Exception as exc:  # noqa: BLE001 - provider boundary wraps external tool failures
            raise SocMcpToolProviderError(f"MCP tool {tool_name!r} failed: {exc}") from exc
        return _normalize_tool_result(raw_result)

    def _tool_by_name(self, tool_name: str) -> Any:
        for tool in self._load_tools():
            if tool.name == tool_name:
                return tool
        raise SocMcpToolNotFoundError(f"MCP tool {tool_name!r} is not available in DeerFlow cache")

    def _load_tools(self) -> list[Any]:
        try:
            return list(self._tools_loader())
        except Exception as exc:  # noqa: BLE001 - provider boundary wraps cache/config failures
            raise SocMcpToolProviderError(f"Failed to load DeerFlow cached MCP tools: {exc}") from exc


class SocMcpToolBindingConfig(BaseModel):
    """MCP server/tool binding hidden behind a SOC action adapter config."""

    model_config = ConfigDict(extra="forbid")

    server: str | None = None
    tool: str = Field(min_length=1)
    timeout_seconds: int = Field(default=5, gt=0)
    input_mapping: dict[str, str] = Field(default_factory=dict)
    output_fields: list[str] = Field(default_factory=list)
    result_schema_version: str = "soc.mcp_tool_result.v1"


class SocMcpActionAdapterConfig(BaseModel):
    """Explicit allowlist config for one read-only MCP-backed SOC action adapter."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.mcp_action_adapter_config.v1"
    enabled: bool = True
    owner: str | None = None
    environment: str | None = None
    adapter_id: str = Field(min_length=1)
    route: str = Field(min_length=1)
    action: str | None = None
    risk_level: SocAgentRiskLevel = SocAgentRiskLevel.READ_ONLY
    adapter_kind: Literal["mcp"] = "mcp"
    external_side_effect: Literal["read"] = "read"
    dry_run_supported: Literal[True] = True
    execute_supported: Literal[True] = True
    idempotency_required: Literal[False] = False
    required_payload_fields: list[str] = Field(default_factory=list)
    required_context_refs: list[str] = Field(default_factory=list)
    description: str = ""
    payload_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    mcp: SocMcpToolBindingConfig

    @model_validator(mode="after")
    def _validate_read_only_config(self) -> Self:
        if self.risk_level != SocAgentRiskLevel.READ_ONLY:
            raise ValueError("SocMcpActionAdapterConfig currently supports risk_level=read_only only")
        return self


class SocMcpActionSmokeReport(BaseModel):
    """Structured report for dev/staging read-only MCP action smoke runs."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.mcp_action_smoke_report.v1"
    config_path: str
    route: str
    action: str
    dry_run: bool
    status: Literal["success", "failed"]
    result_status: Literal["success", "denied", "failed"] | None = None
    duration_ms: float = Field(ge=0)
    action_payload_bytes: int = Field(ge=0)
    action_result_bytes: int = Field(ge=0)
    mcp_result_bytes: int | None = Field(default=None, ge=0)
    adapter_id: str | None = None
    adapter_kind: str | None = None
    mcp_server: str | None = None
    tool_name: str | None = None
    timeout_seconds: int | None = None
    output_fields: list[str] = Field(default_factory=list)
    output_filter_applied: bool = False
    mcp_result_keys: list[str] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    action_result: dict[str, Any] = Field(default_factory=dict)


class SocMcpToolInventoryItem(BaseModel):
    """Safe MCP tool inventory item for smoke readiness checks."""

    model_config = ConfigDict(extra="forbid")

    name: str
    server: str | None = None
    description: str = ""
    input_schema: dict[str, Any] | None = None


class SocMcpToolInventoryReport(BaseModel):
    """Safe report of currently available DeerFlow cached MCP tools."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.mcp_tool_inventory.v1"
    status: Literal["success", "failed"]
    tool_count: int = Field(ge=0)
    include_input_schema: bool = False
    tools: list[SocMcpToolInventoryItem] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None


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


def build_mcp_action_adapter(
    config: SocMcpActionAdapterConfig | Mapping[str, Any],
    provider: SocMcpToolProviderPort,
) -> SocMcpToolActionAdapter:
    """Build one read-only MCP-backed action adapter from explicit config."""

    adapter_config = _coerce_mcp_action_adapter_config(config)
    if not adapter_config.enabled:
        raise SocActionAdapterRegistryError("disabled MCP action adapter config cannot be built directly")
    descriptor = mcp_read_only_adapter_descriptor(
        adapter_id=adapter_config.adapter_id,
        route=adapter_config.route,
        action=adapter_config.action,
        required_payload_fields=adapter_config.required_payload_fields,
        required_context_refs=adapter_config.required_context_refs,
        description=adapter_config.description,
        metadata=_descriptor_metadata(adapter_config),
    )
    return SocMcpToolActionAdapter(
        descriptor=descriptor,
        provider=provider,
        tool_name=adapter_config.mcp.tool,
        mcp_server=adapter_config.mcp.server,
        timeout_seconds=adapter_config.mcp.timeout_seconds,
        input_mapping=adapter_config.mcp.input_mapping,
        output_fields=adapter_config.mcp.output_fields,
        result_schema_version=adapter_config.mcp.result_schema_version,
    )


def build_mcp_action_adapter_registry(
    configs: Iterable[SocMcpActionAdapterConfig | Mapping[str, Any]],
    provider: SocMcpToolProviderPort,
    *,
    base_adapters: Iterable[Any] = (),
) -> SocActionAdapterRegistry:
    """Build an action adapter registry from enabled MCP adapter configs."""

    registry = SocActionAdapterRegistry(base_adapters)
    for config in configs:
        adapter_config = _coerce_mcp_action_adapter_config(config)
        if not adapter_config.enabled:
            continue
        registry.register(build_mcp_action_adapter(adapter_config, provider))
    return registry


def load_mcp_action_adapter_configs(config_path: str | Path) -> list[SocMcpActionAdapterConfig]:
    """Load explicit MCP-backed SOC action adapter configs from JSON/YAML."""

    path = Path(config_path)
    document = _load_config_document(path)
    entries = _extract_adapter_config_entries(document, source=path)
    configs: list[SocMcpActionAdapterConfig] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping):
            raise SocActionAdapterRegistryError(f"MCP action adapter config entry adapters[{index}] must be an object")
        try:
            configs.append(SocMcpActionAdapterConfig.model_validate(entry))
        except ValidationError as exc:
            raise SocActionAdapterRegistryError(f"invalid MCP action adapter config at adapters[{index}]: {exc}") from exc
    return configs


def build_mcp_action_adapter_registry_from_file(
    config_path: str | Path,
    provider: SocMcpToolProviderPort,
    *,
    base_adapters: Iterable[Any] = (),
) -> SocActionAdapterRegistry:
    """Build an action adapter registry from a JSON/YAML allowlist config file."""

    return build_mcp_action_adapter_registry(
        load_mcp_action_adapter_configs(config_path),
        provider,
        base_adapters=base_adapters,
    )


def run_mcp_action_adapter_smoke(
    config_path: str | Path,
    provider: SocMcpToolProviderPort,
    *,
    command: SocAgentActionCommand,
    context: ServiceRequestContext,
    base_adapters: Iterable[Any] = (),
) -> SocMcpActionSmokeReport:
    """Run one read-only MCP action smoke and return metrics for dev/staging checks."""

    path = Path(config_path)
    started = time.perf_counter()
    action_payload_bytes = _json_byte_size(command.payload)
    matching_config: SocMcpActionAdapterConfig | None = None
    try:
        configs = load_mcp_action_adapter_configs(path)
        matching_config = _find_enabled_mcp_action_config(configs, route=command.route, action=command.action)
        registry = build_mcp_action_adapter_registry(configs, provider, base_adapters=base_adapters)
        result = registry.dry_run(command, context=context) if command.dry_run else registry.execute(command, context=context)
    except Exception as exc:  # noqa: BLE001 - smoke report must preserve failure as structured JSON
        return _mcp_smoke_report(
            config_path=path,
            command=command,
            started=started,
            action_payload_bytes=action_payload_bytes,
            matching_config=matching_config,
            result=None,
            error=exc,
        )

    return _mcp_smoke_report(
        config_path=path,
        command=command,
        started=started,
        action_payload_bytes=action_payload_bytes,
        matching_config=matching_config,
        result=result,
        error=None,
    )


def inspect_mcp_tool_inventory(
    provider: SocMcpToolProviderPort,
    *,
    include_input_schema: bool = False,
) -> SocMcpToolInventoryReport:
    """Inspect currently available MCP tools without exposing secrets or invoking tools."""

    try:
        descriptors = provider.list_tools()
    except Exception as exc:  # noqa: BLE001 - readiness report should be structured
        return SocMcpToolInventoryReport(
            status="failed",
            tool_count=0,
            include_input_schema=include_input_schema,
            error_type=exc.__class__.__name__,
            error_message=str(exc),
        )
    tools = [
        SocMcpToolInventoryItem(
            name=descriptor.name,
            server=descriptor.server,
            description=descriptor.description,
            input_schema=_json_safe(descriptor.input_schema) if include_input_schema else None,
        )
        for descriptor in sorted(descriptors, key=lambda item: (item.server or "", item.name))
    ]
    return SocMcpToolInventoryReport(
        status="success",
        tool_count=len(tools),
        include_input_schema=include_input_schema,
        tools=tools,
    )


class SocMcpToolActionAdapter:
    """Read-only SOC action adapter that invokes an allowlisted MCP tool."""

    def __init__(
        self,
        *,
        descriptor: SocAgentActionAdapterDescriptor,
        provider: SocMcpToolProviderPort,
        tool_name: str,
        mcp_server: str | None = None,
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
        self._mcp_server = mcp_server
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
                "mcp_server": self._mcp_server,
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
                server_name=self._mcp_server,
            )
            if not isinstance(raw_result, Mapping):
                raise SocMcpToolProviderError("MCP tool returned a non-object result")
            _raise_for_mcp_tool_error(raw_result)
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
                    "mcp_server": self._mcp_server,
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
                "mcp_server": self._mcp_server,
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


def _raise_for_mcp_tool_error(result: Mapping[str, Any]) -> None:
    if result.get("isError") is not True and result.get("is_error") is not True:
        return
    message = "MCP tool reported an error"
    content = result.get("content")
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, Mapping):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                message = text.strip()[:500]
                break
    raise SocMcpToolProviderError(message)


def _load_deerflow_cached_mcp_tools() -> Iterable[Any]:
    from deerflow.mcp.cache import get_cached_mcp_tools

    return get_cached_mcp_tools()


def _invoke_tool_with_timeout(
    tool: Any,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: int,
) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="soc-mcp-tool",
    )
    future = executor.submit(tool.invoke, dict(payload))
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise SocMcpToolProviderError(f"MCP tool {tool.name!r} timed out after {timeout_seconds}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _invoke_mcp_tool_once_with_timeout(
    tool_name: str,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: int,
    server_name: str | None = None,
) -> Any:
    executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=1,
        thread_name_prefix="soc-mcp-one-shot",
    )
    future = executor.submit(_run_mcp_tool_once, tool_name, dict(payload), server_name=server_name)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise SocMcpToolProviderError(f"MCP tool {tool_name!r} timed out after {timeout_seconds}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _run_mcp_tool_once(tool_name: str, payload: Mapping[str, Any], *, server_name: str | None = None) -> Any:
    import asyncio

    return asyncio.run(_invoke_mcp_tool_once(tool_name, payload, server_name=server_name))


async def _invoke_mcp_tool_once(tool_name: str, payload: Mapping[str, Any], *, server_name: str | None = None) -> Any:
    from langchain_mcp_adapters.sessions import create_session

    server_name, original_tool_name, connection = _resolve_mcp_tool_target(tool_name, server_name=server_name)
    try:
        async with create_session(connection) as session:
            await session.initialize()
            return await session.call_tool(original_tool_name, dict(payload))
    except Exception as exc:  # noqa: BLE001 - provider boundary wraps external MCP failures
        raise SocMcpToolProviderError(f"MCP tool {tool_name!r} one-shot call failed on server {server_name!r}: {exc}") from exc


def _resolve_mcp_tool_target(tool_name: str, *, server_name: str | None = None) -> tuple[str, str, dict[str, Any]]:
    from deerflow.config.extensions_config import ExtensionsConfig
    from deerflow.mcp.client import build_servers_config

    servers_config = build_servers_config(ExtensionsConfig.from_file())
    if server_name:
        connection = servers_config.get(server_name)
        if connection is None:
            raise SocMcpToolNotFoundError(f"MCP server {server_name!r} is not configured")
        prefix = f"{server_name}_"
        original_tool_name = tool_name[len(prefix) :] if tool_name.startswith(prefix) else tool_name
        return server_name, original_tool_name, dict(connection)
    for server_name in sorted(servers_config, key=len, reverse=True):
        prefix = f"{server_name}_"
        if tool_name.startswith(prefix):
            original_tool_name = tool_name[len(prefix) :]
            return server_name, original_tool_name, dict(servers_config[server_name])
    raise SocMcpToolNotFoundError(f"MCP tool {tool_name!r} does not include a configured server prefix")


def _tool_input_schema(tool: Any) -> Mapping[str, Any]:
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None and hasattr(args_schema, "model_json_schema"):
        return _json_safe(args_schema.model_json_schema())
    tool_call_schema = getattr(tool, "tool_call_schema", None)
    if tool_call_schema is not None and hasattr(tool_call_schema, "model_json_schema"):
        return _json_safe(tool_call_schema.model_json_schema())
    args = getattr(tool, "args", None)
    if isinstance(args, Mapping):
        return {"type": "object", "properties": _json_safe(args)}
    return {}


def _server_from_tool(tool: Any) -> str | None:
    metadata = getattr(tool, "metadata", None)
    if isinstance(metadata, Mapping):
        for key in ("server", "server_name", "mcp_server", "mcp_server_name"):
            value = metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    inferred = _server_name_from_prefixed_tool_name(str(getattr(tool, "name", "") or ""))
    if inferred:
        return inferred
    return None


def _server_name_from_prefixed_tool_name(tool_name: str) -> str | None:
    if not tool_name:
        return None
    try:
        from deerflow.config.extensions_config import ExtensionsConfig
        from deerflow.mcp.client import build_servers_config

        servers_config = build_servers_config(ExtensionsConfig.from_file())
    except Exception:  # noqa: BLE001 - inventory server names are best-effort metadata only
        return None
    for server_name in sorted(servers_config, key=len, reverse=True):
        if tool_name.startswith(f"{server_name}_"):
            return server_name
    return None


def _normalize_tool_result(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        return _json_safe(result)
    if isinstance(result, tuple) and len(result) == 2:
        content, artifact = result
        normalized_artifact = _json_safe(artifact)
        if isinstance(normalized_artifact, Mapping):
            structured_content = normalized_artifact.get("structured_content")
            if isinstance(structured_content, Mapping):
                return {
                    **_json_safe(structured_content),
                    "content": _json_safe(content),
                    "artifact": normalized_artifact,
                }
        return {
            "content": _json_safe(content),
            "artifact": normalized_artifact,
        }
    structured_content = getattr(result, "structuredContent", None)
    if isinstance(structured_content, Mapping):
        return {
            **_json_safe(structured_content),
            "content": _json_safe(getattr(result, "content", [])),
            "artifact": {"structured_content": _json_safe(structured_content)},
        }
    if hasattr(result, "model_dump"):
        dumped = result.model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return _json_safe(dumped)
        return {"content": _json_safe(dumped)}
    return {"content": _json_safe(result)}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_json_safe(item) for item in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _json_safe(asdict(value))
    return str(value)


def _coerce_mcp_action_adapter_config(config: SocMcpActionAdapterConfig | Mapping[str, Any]) -> SocMcpActionAdapterConfig:
    if isinstance(config, SocMcpActionAdapterConfig):
        return config
    return SocMcpActionAdapterConfig.model_validate(config)


def _load_config_document(path: Path) -> Any:
    try:
        source = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SocActionAdapterRegistryError(f"cannot read MCP action adapter config {path}: {exc}") from exc

    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(source)
        if suffix in {".yaml", ".yml"}:
            return yaml.safe_load(source)
    except json.JSONDecodeError as exc:
        raise SocActionAdapterRegistryError(f"invalid JSON MCP action adapter config {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SocActionAdapterRegistryError(f"invalid YAML MCP action adapter config {path}: {exc}") from exc

    raise SocActionAdapterRegistryError("MCP action adapter config path must end with .json, .yaml, or .yml")


def _extract_adapter_config_entries(document: Any, *, source: Path) -> list[Any]:
    if isinstance(document, list):
        return list(document)
    if isinstance(document, Mapping):
        entries = document.get("adapters")
        if not isinstance(entries, list):
            raise SocActionAdapterRegistryError(f"MCP action adapter config {source} must contain an adapters list")
        return list(entries)
    raise SocActionAdapterRegistryError(f"MCP action adapter config {source} must be a list or an object with adapters")


def _find_enabled_mcp_action_config(
    configs: Iterable[SocMcpActionAdapterConfig],
    *,
    route: str,
    action: str,
) -> SocMcpActionAdapterConfig | None:
    for config in configs:
        if config.enabled and config.route == route and (config.action or config.route) == action:
            return config
    return None


def _mcp_smoke_report(
    *,
    config_path: Path,
    command: SocAgentActionCommand,
    started: float,
    action_payload_bytes: int,
    matching_config: SocMcpActionAdapterConfig | None,
    result: SocAgentActionResult | None,
    error: Exception | None,
) -> SocMcpActionSmokeReport:
    duration_ms = round((time.perf_counter() - started) * 1000, 3)
    action_result = result.model_dump(mode="json", exclude_none=True) if result is not None else {}
    result_payload = result.payload if result is not None else {}
    mcp_result = result_payload.get("mcp_result") if isinstance(result_payload, Mapping) else None
    mcp_result_mapping = mcp_result if isinstance(mcp_result, Mapping) else None
    result_status = result.status if result is not None else None
    return SocMcpActionSmokeReport(
        config_path=str(config_path),
        route=command.route,
        action=command.action,
        dry_run=command.dry_run,
        status="success" if result_status == "success" and error is None else "failed",
        result_status=result_status,
        duration_ms=duration_ms,
        action_payload_bytes=action_payload_bytes,
        action_result_bytes=_json_byte_size(action_result),
        mcp_result_bytes=_json_byte_size(mcp_result_mapping) if mcp_result_mapping is not None else None,
        adapter_id=result_payload.get("adapter_id") if isinstance(result_payload.get("adapter_id"), str) else (matching_config.adapter_id if matching_config else None),
        adapter_kind=result_payload.get("adapter_kind") if isinstance(result_payload.get("adapter_kind"), str) else (matching_config.adapter_kind if matching_config else None),
        mcp_server=matching_config.mcp.server if matching_config else None,
        tool_name=result_payload.get("tool_name") if isinstance(result_payload.get("tool_name"), str) else (matching_config.mcp.tool if matching_config else None),
        timeout_seconds=result_payload.get("timeout_seconds") if isinstance(result_payload.get("timeout_seconds"), int) else (matching_config.mcp.timeout_seconds if matching_config else None),
        output_fields=list(matching_config.mcp.output_fields) if matching_config else [],
        output_filter_applied=bool(matching_config and matching_config.mcp.output_fields),
        mcp_result_keys=sorted(str(key) for key in mcp_result_mapping) if mcp_result_mapping is not None else [],
        error_type=(error.__class__.__name__ if error is not None else _result_error_type(result_payload)),
        error_message=(str(error) if error is not None else _result_error_message(result)),
        action_result=action_result,
    )


def _result_error_type(result_payload: Mapping[str, Any]) -> str | None:
    value = result_payload.get("error_type")
    return value if isinstance(value, str) and value else None


def _result_error_message(result: SocAgentActionResult | None) -> str | None:
    if result is None or result.status == "success":
        return None
    return result.message


def _json_byte_size(value: Any) -> int:
    return len(json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def _descriptor_metadata(config: SocMcpActionAdapterConfig) -> dict[str, Any]:
    metadata = dict(config.metadata)
    metadata["mcp"] = {
        "server": config.mcp.server,
        "tool": config.mcp.tool,
        "timeout_seconds": config.mcp.timeout_seconds,
        "result_schema_version": config.mcp.result_schema_version,
    }
    metadata["config"] = {
        "schema_version": config.schema_version,
        "enabled": config.enabled,
        "owner": config.owner,
        "environment": config.environment,
    }
    if config.payload_schema:
        metadata["payload_schema"] = config.payload_schema
    return metadata


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


__all__ = [
    "DeerFlowCachedMcpToolProvider",
    "SocMcpActionAdapterConfig",
    "SocMcpActionSmokeReport",
    "SocMcpToolActionAdapter",
    "SocMcpToolBindingConfig",
    "SocMcpToolDescriptor",
    "SocMcpToolInventoryItem",
    "SocMcpToolInventoryReport",
    "SocMcpToolNotFoundError",
    "SocMcpToolProviderError",
    "SocMcpToolProviderPort",
    "build_mcp_action_adapter",
    "build_mcp_action_adapter_registry",
    "build_mcp_action_adapter_registry_from_file",
    "inspect_mcp_tool_inventory",
    "load_mcp_action_adapter_configs",
    "mcp_read_only_adapter_descriptor",
    "run_mcp_action_adapter_smoke",
]
