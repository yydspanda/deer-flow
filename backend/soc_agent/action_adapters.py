"""Registry contract for approved SOC response action adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from soc_agent.contracts import (
    ServiceRequestContext,
    SocAgentActionAdapterDescriptor,
    SocAgentActionResult,
    SocAgentApprovedActionCommand,
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
        command: SocAgentApprovedActionCommand,
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
        command: SocAgentApprovedActionCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocAgentActionResult:
        if command.dry_run:
            raise SocActionAdapterRegistryError("adapter execute requires command.dry_run=false")
        adapter = self.get(route=command.route, action=command.action)
        return adapter.execute(command, context=context)


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
        command: SocAgentApprovedActionCommand,
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
        command: SocAgentApprovedActionCommand,
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


def _adapter_key(route: str, action: str) -> tuple[str, str]:
    return (route.strip(), action.strip())


def _validate_command_matches_descriptor(
    command: SocAgentApprovedActionCommand,
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


__all__ = [
    "DryRunOnlySocActionAdapter",
    "SocActionAdapterNotFoundError",
    "SocActionAdapterRegistry",
    "SocActionAdapterRegistryError",
]
