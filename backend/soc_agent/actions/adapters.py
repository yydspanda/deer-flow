"""Registry contract for approved SOC response action adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from soc_agent.contracts import (
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentActionCommand,
    SocAgentActionResult,
    SocAgentRiskLevel,
    SocAssetLookupRecord,
)
from soc_agent.protocols import SocActionAdapter


class SocActionAdapterRegistryError(ValueError):
    """Raised when an action adapter registry contract is violated."""


class SocActionAdapterNotFoundError(LookupError):
    """Raised when no adapter is registered for a route/action pair."""


class SocActionAdapterRegistry:
    """Allowlisted registry for approved SOC action adapters.

    The registry is intentionally side-effect free. It only resolves registered
    adapters by exact route/action and delegates dry-run or execution to them.
    Approval token validation and consumption stay in SocAgentApprovalService.
    """

    def __init__(self, adapters: Iterable[SocActionAdapter] | None = None) -> None:
        self._adapters: dict[tuple[str, str], SocActionAdapter] = {}
        for adapter in adapters or ():
            self.register(adapter)

    def register(self, adapter: SocActionAdapter) -> None:
        descriptor = adapter.descriptor
        key = _adapter_key(descriptor.route, descriptor.action)
        if key in self._adapters:
            existing = self._adapters[key].descriptor.adapter_id
            raise SocActionAdapterRegistryError(f"action adapter for route={descriptor.route!r} action={descriptor.action!r} is already registered by {existing!r}")
        self._adapters[key] = adapter

    def get(self, *, route: str, action: str) -> SocActionAdapter:
        key = _adapter_key(route, action)
        adapter = self._adapters.get(key)
        if adapter is None:
            raise SocActionAdapterNotFoundError(f"no action adapter registered for route={route!r} action={action!r}")
        return adapter

    def list_descriptors(self) -> list[SocAgentActionAdapterDescriptor]:
        return [
            adapter.descriptor
            for _, adapter in sorted(
                self._adapters.items(),
                key=lambda item: (item[0][0], item[0][1], item[1].descriptor.adapter_id),
            )
        ]

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if not command.dry_run:
            raise SocActionAdapterRegistryError("adapter dry_run requires command.dry_run=true")
        adapter = self.get(route=command.route, action=command.action)
        if not adapter.descriptor.dry_run_supported:
            raise SocActionAdapterRegistryError(f"action adapter {adapter.descriptor.adapter_id!r} does not support dry-run")
        return adapter.dry_run(command, context=context)

    def execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if command.dry_run:
            raise SocActionAdapterRegistryError("adapter execute requires command.dry_run=false")
        adapter = self.get(route=command.route, action=command.action)
        return adapter.execute(command, context=context)

    def preflight_execute(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if command.dry_run:
            raise SocActionAdapterRegistryError("adapter execute preflight requires command.dry_run=false")
        adapter = self.get(route=command.route, action=command.action)
        descriptor = adapter.descriptor
        _validate_command_matches_descriptor(command, descriptor)
        if not descriptor.execute_supported:
            raise SocActionAdapterRegistryError(f"action adapter {descriptor.adapter_id!r} does not support execute")
        if descriptor.idempotency_required and not context.idempotency_key:
            raise SocActionAdapterRegistryError(f"action adapter {descriptor.adapter_id!r} requires an idempotency_key")
        _validate_required_fields(
            "payload",
            command.payload,
            descriptor.required_payload_fields,
        )
        context_refs = command.payload.get("context_refs")
        _validate_required_fields(
            "context_refs",
            context_refs if isinstance(context_refs, Mapping) else {},
            descriptor.required_context_refs,
        )
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="Action adapter execute preflight validated; no external side effect executed.",
            payload={
                "adapter_id": descriptor.adapter_id,
                "adapter_kind": descriptor.adapter_kind,
                "adapter_execute_supported": descriptor.execute_supported,
                "adapter_external_side_effect": descriptor.external_side_effect,
                "dry_run": False,
                "preflight_only": True,
                "external_side_effect": "not_executed",
                "required_payload_fields": descriptor.required_payload_fields,
                "required_context_refs": descriptor.required_context_refs,
                "executed_by": context.actor.model_dump(mode="json"),
                "idempotency_key": context.idempotency_key,
            },
        )


class DryRunOnlySocActionAdapter:
    """Contract adapter for actions that are planned but not yet executable."""

    def __init__(self, descriptor: SocAgentActionAdapterDescriptor) -> None:
        if descriptor.execute_supported:
            raise SocActionAdapterRegistryError("DryRunOnlySocActionAdapter requires execute_supported=false")
        if not descriptor.dry_run_supported:
            raise SocActionAdapterRegistryError("DryRunOnlySocActionAdapter requires dry_run_supported=true")
        self.descriptor = descriptor

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        _validate_command_matches_descriptor(command, self.descriptor)
        _validate_required_fields(
            "payload",
            command.payload,
            self.descriptor.required_payload_fields,
        )
        context_refs = command.payload.get("context_refs")
        _validate_required_fields(
            "context_refs",
            context_refs if isinstance(context_refs, Mapping) else {},
            self.descriptor.required_context_refs,
        )
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="Action adapter dry-run validated; no external side effect executed.",
            payload={
                "adapter_id": self.descriptor.adapter_id,
                "adapter_kind": self.descriptor.adapter_kind,
                "dry_run": True,
                "external_side_effect": "not_executed",
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
        _validate_command_matches_descriptor(command, self.descriptor)
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="failed",
            message=f"Action adapter {self.descriptor.adapter_id} is dry-run only; no external side effect executed.",
            payload={
                "adapter_id": self.descriptor.adapter_id,
                "adapter_kind": self.descriptor.adapter_kind,
                "dry_run": command.dry_run,
                "external_side_effect": "not_executed",
                "executed_by": context.actor.model_dump(mode="json"),
                "idempotency_key": context.idempotency_key,
            },
        )


ASSET_LOOKUP_ACTION = "asset.lookup"


def asset_lookup_adapter_descriptor(
    *,
    adapter_id: str = "asset-lookup-in-memory",
) -> SocAgentActionAdapterDescriptor:
    """Descriptor for the first concrete read-only SOC action adapter."""

    return SocAgentActionAdapterDescriptor(
        adapter_id=adapter_id,
        route=ASSET_LOOKUP_ACTION,
        action=ASSET_LOOKUP_ACTION,
        risk_level=SocAgentRiskLevel.READ_ONLY,
        adapter_kind="service",
        external_side_effect="read",
        dry_run_supported=True,
        execute_supported=True,
        idempotency_required=False,
        required_payload_fields=["asset_key"],
        description="Read-only asset inventory lookup adapter.",
    )


class InMemoryAssetLookupActionAdapter:
    """Read-only asset lookup adapter backed by an in-memory inventory."""

    def __init__(
        self,
        records: Iterable[SocAssetLookupRecord | Mapping[str, Any]] | None = None,
        *,
        descriptor: SocAgentActionAdapterDescriptor | None = None,
    ) -> None:
        self.descriptor = descriptor or asset_lookup_adapter_descriptor()
        if self.descriptor.action != ASSET_LOOKUP_ACTION or self.descriptor.route != ASSET_LOOKUP_ACTION:
            raise SocActionAdapterRegistryError("InMemoryAssetLookupActionAdapter requires route/action asset.lookup")
        if self.descriptor.risk_level is not SocAgentRiskLevel.READ_ONLY:
            raise SocActionAdapterRegistryError("InMemoryAssetLookupActionAdapter must be read-only")
        if self.descriptor.external_side_effect != "read":
            raise SocActionAdapterRegistryError("InMemoryAssetLookupActionAdapter must declare external_side_effect=read")
        self._records = _index_asset_records(records or ())

    def dry_run(
        self,
        command: SocAgentActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if not command.dry_run:
            raise SocActionAdapterRegistryError("asset.lookup dry-run requires command.dry_run=true")
        _validate_command_matches_descriptor(command, self.descriptor)
        _validate_required_fields("payload", command.payload, self.descriptor.required_payload_fields)
        asset_key = _asset_key_from_payload(command.payload)
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message="Asset lookup dry-run validated; no asset inventory read executed.",
            payload={
                "adapter_id": self.descriptor.adapter_id,
                "adapter_kind": self.descriptor.adapter_kind,
                "dry_run": True,
                "asset_key": asset_key,
                "asset_found": None,
                "external_side_effect": "not_executed",
                "read_only": True,
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
            raise SocActionAdapterRegistryError("asset.lookup execute requires command.dry_run=false")
        _validate_command_matches_descriptor(command, self.descriptor)
        _validate_required_fields("payload", command.payload, self.descriptor.required_payload_fields)
        asset_key = _asset_key_from_payload(command.payload)
        record = self._records.get(_normalize_asset_key(asset_key))
        if record is None:
            return SocAgentActionResult(
                route=command.route,
                action=command.action,
                status="success",
                message=f"Asset lookup completed; no asset record matched {asset_key}.",
                payload=_asset_lookup_payload(
                    descriptor=self.descriptor,
                    asset_key=asset_key,
                    record=None,
                    context=context,
                ),
            )
        return SocAgentActionResult(
            route=command.route,
            action=command.action,
            status="success",
            message=f"Asset lookup completed for {asset_key}.",
            payload=_asset_lookup_payload(
                descriptor=self.descriptor,
                asset_key=asset_key,
                record=record,
                context=context,
            ),
        )


def _adapter_key(route: str, action: str) -> tuple[str, str]:
    return (route.strip(), action.strip())


def _validate_command_matches_descriptor(
    command: SocAgentActionCommand,
    descriptor: SocAgentActionAdapterDescriptor,
) -> None:
    if command.route != descriptor.route:
        raise SocActionAdapterRegistryError("command route does not match action adapter descriptor")
    if command.action != descriptor.action:
        raise SocActionAdapterRegistryError("command action does not match action adapter descriptor")


def _validate_required_fields(name: str, payload: Mapping[str, Any], required_fields: list[str]) -> None:
    missing = [field for field in required_fields if not _has_value(payload.get(field))]
    if missing:
        raise SocActionAdapterRegistryError(f"missing required {name} fields: {', '.join(missing)}")


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _index_asset_records(records: Iterable[SocAssetLookupRecord | Mapping[str, Any]]) -> dict[str, SocAssetLookupRecord]:
    index: dict[str, SocAssetLookupRecord] = {}
    for item in records:
        record = item if isinstance(item, SocAssetLookupRecord) else SocAssetLookupRecord.model_validate(item)
        for key in (record.asset_key, record.asset_id, record.hostname, record.primary_ip):
            if key:
                index[_normalize_asset_key(key)] = record
    return index


def _asset_key_from_payload(payload: Mapping[str, Any]) -> str:
    value = payload.get("asset_key")
    if not isinstance(value, str) or not value.strip():
        raise SocActionAdapterRegistryError("asset.lookup requires non-empty payload asset_key")
    return value.strip()


def _normalize_asset_key(value: str) -> str:
    return value.strip().casefold()


def _asset_lookup_payload(
    *,
    descriptor: SocAgentActionAdapterDescriptor,
    asset_key: str,
    record: SocAssetLookupRecord | None,
    context: ServiceRequestContext,
) -> dict[str, Any]:
    payload = {
        "adapter_id": descriptor.adapter_id,
        "adapter_kind": descriptor.adapter_kind,
        "dry_run": False,
        "asset_key": asset_key,
        "asset_found": record is not None,
        "asset_record": record.model_dump(mode="json") if record is not None else None,
        "external_side_effect": "read",
        "read_only": True,
        "executed_by": context.actor.model_dump(mode="json"),
        "idempotency_key": context.idempotency_key,
    }
    return payload


__all__ = [
    "ASSET_LOOKUP_ACTION",
    "DryRunOnlySocActionAdapter",
    "InMemoryAssetLookupActionAdapter",
    "SocActionAdapterNotFoundError",
    "SocActionAdapterRegistry",
    "SocActionAdapterRegistryError",
    "asset_lookup_adapter_descriptor",
]
