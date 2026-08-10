"""Load operator-owned tenant policies from files without importing tenant code."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from pydantic import TypeAdapter

from soc_agent.contracts import TenantDispositionPolicy

_POLICY_LIST_ADAPTER = TypeAdapter(list[TenantDispositionPolicy])


def load_tenant_disposition_policies(path: str | Path) -> list[TenantDispositionPolicy]:
    source = Path(path).expanduser().resolve()
    if source.is_dir():
        policies: list[TenantDispositionPolicy] = []
        for child in sorted(source.glob("*.json")):
            policies.extend(_load_policy_file(child))
        if not policies:
            raise ValueError(f"tenant policy directory contains no JSON policies: {source}")
        return policies
    return _load_policy_file(source)


class StaticTenantPolicyResolver:
    """Resolve exactly one active policy for a tenant/environment pair."""

    def __init__(self, policies: Iterable[TenantDispositionPolicy]) -> None:
        self._policies = tuple(policies)
        if not self._policies:
            raise ValueError("tenant policy resolver requires at least one policy")

    def resolve(
        self,
        *,
        tenant_id: str | None,
        environment: str,
        evaluated_at: datetime | None = None,
    ) -> TenantDispositionPolicy | None:
        if not tenant_id:
            return None
        if evaluated_at is not None and (evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None):
            raise ValueError("tenant policy resolution time must be timezone-aware")
        matches = [
            policy
            for policy in self._policies
            if policy.tenant_id == tenant_id
            and environment.casefold() in {item.casefold() for item in policy.applicable_environments}
            and (evaluated_at is not None or (policy.effective_from is None and policy.effective_until is None))
            and (policy.effective_from is None or evaluated_at >= policy.effective_from)
            and (policy.effective_until is None or evaluated_at < policy.effective_until)
        ]
        if len(matches) > 1:
            identities = ", ".join(f"{item.policy_id}@{item.policy_version}" for item in matches)
            raise ValueError(f"multiple active tenant policies match {tenant_id}/{environment}: {identities}")
        return matches[0] if matches else None


def _load_policy_file(path: Path) -> list[TenantDispositionPolicy]:
    if not path.is_file():
        raise FileNotFoundError(f"tenant policy file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return _POLICY_LIST_ADAPTER.validate_python(payload)
    if isinstance(payload, dict) and "policies" in payload:
        return _POLICY_LIST_ADAPTER.validate_python(payload["policies"])
    return [TenantDispositionPolicy.model_validate(payload)]


__all__ = ["StaticTenantPolicyResolver", "load_tenant_disposition_policies"]
