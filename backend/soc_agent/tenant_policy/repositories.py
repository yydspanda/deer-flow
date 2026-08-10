"""In-memory persistence for immutable tenant policy decisions."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import TenantPolicyDecision


class TenantPolicyDecisionConflictError(ValueError):
    """Raised when append-only tenant policy decision identity is reused."""


class InMemoryTenantPolicyDecisionRepository:
    def __init__(self, decisions: Iterable[TenantPolicyDecision] | None = None) -> None:
        self._decisions: dict[str, TenantPolicyDecision] = {}
        self._decision_key_index: dict[str, str] = {}
        self._idempotency_index: dict[str, str] = {}
        for decision in decisions or ():
            self.save_tenant_policy_decision(decision)

    def save_tenant_policy_decision(self, decision: TenantPolicyDecision) -> None:
        if decision.decision_id in self._decisions:
            raise TenantPolicyDecisionConflictError(f"tenant policy decision {decision.decision_id} already exists")
        if decision.decision_key in self._decision_key_index:
            raise TenantPolicyDecisionConflictError("tenant policy decision key already exists")
        if decision.idempotency_key in self._idempotency_index:
            raise TenantPolicyDecisionConflictError("tenant policy decision idempotency key already exists")
        self._decisions[decision.decision_id] = decision
        self._decision_key_index[decision.decision_key] = decision.decision_id
        self._idempotency_index[decision.idempotency_key] = decision.decision_id

    def get_tenant_policy_decision(self, decision_id: str) -> TenantPolicyDecision | None:
        return self._decisions.get(decision_id)

    def find_tenant_policy_decision_by_key(self, decision_key: str) -> TenantPolicyDecision | None:
        decision_id = self._decision_key_index.get(decision_key)
        return self._decisions.get(decision_id) if decision_id else None

    def find_tenant_policy_decision_by_idempotency_key(self, idempotency_key: str) -> TenantPolicyDecision | None:
        decision_id = self._idempotency_index.get(idempotency_key)
        return self._decisions.get(decision_id) if decision_id else None

    def list_tenant_policy_decisions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        tenant_id: str | None = None,
        policy_id: str | None = None,
        limit: int = 100,
    ) -> list[TenantPolicyDecision]:
        filters = {
            "run_id": run_id,
            "alert_id": alert_id,
            "tenant_id": tenant_id,
            "policy_id": policy_id,
        }
        active = {name: value for name, value in filters.items() if value is not None}
        decisions = [decision for decision in self._decisions.values() if all(getattr(decision, name) == value for name, value in active.items())]
        return sorted(decisions, key=lambda item: item.created_at, reverse=True)[:limit]


__all__ = [
    "InMemoryTenantPolicyDecisionRepository",
    "TenantPolicyDecisionConflictError",
]
