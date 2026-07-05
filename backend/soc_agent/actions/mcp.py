"""SOC action adapters backed by MCP tools.

This module is the SOC boundary around MCP. Lead Agent code must keep using
SOC route/action names; MCP server/tool names stay inside adapter config.
"""

from __future__ import annotations

import concurrent.futures
import json
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
    ) -> None:
        self._tools_loader = tools_loader or _load_deerflow_cached_mcp_tools

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
    ) -> Mapping[str, Any]:
        tool = self._tool_by_name(tool_name)
        try:
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
    return None


def _normalize_tool_result(result: Any) -> Mapping[str, Any]:
    if isinstance(result, Mapping):
        return _json_safe(result)
    if isinstance(result, tuple) and len(result) == 2:
        content, artifact = result
        return {
            "content": _json_safe(content),
            "artifact": _json_safe(artifact),
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
    "SocMcpToolActionAdapter",
    "SocMcpToolBindingConfig",
    "SocMcpToolDescriptor",
    "SocMcpToolNotFoundError",
    "SocMcpToolProviderError",
    "SocMcpToolProviderPort",
    "build_mcp_action_adapter",
    "build_mcp_action_adapter_registry",
    "build_mcp_action_adapter_registry_from_file",
    "load_mcp_action_adapter_configs",
    "mcp_read_only_adapter_descriptor",
]
