"""In-memory persistence for governed automation tests and local demos."""

from __future__ import annotations

from soc_agent.contracts import (
    SocActionAuthorizationRecord,
    SocActionExecutionRecord,
    SocDecisionTransitionRecord,
    SocDispositionTransitionRecord,
)


class InMemorySocAutomationRepository:
    def __init__(self) -> None:
        self._decisions: dict[str, SocDecisionTransitionRecord] = {}
        self._dispositions: dict[str, SocDispositionTransitionRecord] = {}
        self._authorizations: dict[str, SocActionAuthorizationRecord] = {}
        self._executions: dict[str, SocActionExecutionRecord] = {}

    def save_decision_transition(self, record: SocDecisionTransitionRecord) -> None:
        _append(self._decisions, record.transition_key, record)

    def find_decision_transition_by_key(
        self,
        transition_key: str,
    ) -> SocDecisionTransitionRecord | None:
        return self._decisions.get(transition_key)

    def save_disposition_transition(
        self,
        record: SocDispositionTransitionRecord,
    ) -> None:
        _append(self._dispositions, record.transition_key, record)

    def find_disposition_transition_by_key(
        self,
        transition_key: str,
    ) -> SocDispositionTransitionRecord | None:
        return self._dispositions.get(transition_key)

    def save_action_authorization(self, record: SocActionAuthorizationRecord) -> None:
        _append(self._authorizations, record.authorization_key, record)

    def find_action_authorization_by_key(
        self,
        authorization_key: str,
    ) -> SocActionAuthorizationRecord | None:
        return self._authorizations.get(authorization_key)

    def save_action_execution(self, record: SocActionExecutionRecord) -> None:
        _append(self._executions, record.execution_key, record)

    def find_action_execution_by_key(
        self,
        execution_key: str,
    ) -> SocActionExecutionRecord | None:
        return self._executions.get(execution_key)

    def list_decision_transitions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocDecisionTransitionRecord]:
        items = list(self._decisions.values())
        if run_id is not None:
            items = [item for item in items if item.run_id == run_id]
        if alert_id is not None:
            items = [item for item in items if item.alert_id == alert_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    def list_action_authorizations(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocActionAuthorizationRecord]:
        items = list(self._authorizations.values())
        if run_id is not None:
            items = [item for item in items if item.run_id == run_id]
        if alert_id is not None:
            items = [item for item in items if item.alert_id == alert_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    def list_disposition_transitions(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        limit: int = 100,
    ) -> list[SocDispositionTransitionRecord]:
        items = list(self._dispositions.values())
        if run_id is not None:
            items = [item for item in items if item.run_id == run_id]
        if alert_id is not None:
            items = [item for item in items if item.alert_id == alert_id]
        return sorted(items, key=lambda item: item.created_at, reverse=True)[:limit]

    def list_action_executions(
        self,
        *,
        run_id: str | None = None,
        authorization_id: str | None = None,
        limit: int = 100,
    ) -> list[SocActionExecutionRecord]:
        items = list(self._executions.values())
        if run_id is not None:
            items = [item for item in items if item.run_id == run_id]
        if authorization_id is not None:
            items = [item for item in items if item.authorization_id == authorization_id]
        return sorted(items, key=lambda item: item.started_at, reverse=True)[:limit]


def _append(store: dict[str, object], key: str, value: object) -> None:
    if key in store:
        raise ValueError(f"append-only automation key {key} already exists")
    store[key] = value


__all__ = ["InMemorySocAutomationRepository"]
