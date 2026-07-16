"""Repository helpers for append-only governed context fact versions."""

from __future__ import annotations

from collections.abc import Iterable

from soc_agent.contracts import GovernedContextFact, GovernedContextFactQuery


class GovernedContextFactVersionConflictError(RuntimeError):
    """Raised when a writer's expected latest version is stale."""


class InMemoryGovernedContextFactRepository:
    """Deterministic in-memory fact repository for services and tests."""

    def __init__(self, facts: Iterable[GovernedContextFact] | None = None) -> None:
        self._versions: dict[str, list[GovernedContextFact]] = {}
        for fact in facts or ():
            expected = fact.version - 1 if fact.version > 1 else None
            self.append_governed_context_fact(fact, expected_latest_version=expected)

    def append_governed_context_fact(
        self,
        fact: GovernedContextFact,
        *,
        expected_latest_version: int | None,
    ) -> None:
        versions = self._versions.setdefault(fact.fact_id, [])
        latest = versions[-1] if versions else None
        validate_governed_context_fact_append(
            fact,
            latest=latest,
            expected_latest_version=expected_latest_version,
        )
        if latest is not None:
            versions[-1] = latest.model_copy(update={"is_latest": False})
        versions.append(fact.model_copy(deep=True))

    def get_governed_context_fact(
        self,
        fact_id: str,
        *,
        version: int | None = None,
    ) -> GovernedContextFact | None:
        versions = self._versions.get(fact_id, [])
        if version is None:
            return versions[-1].model_copy(deep=True) if versions else None
        return next(
            (item.model_copy(deep=True) for item in versions if item.version == version),
            None,
        )

    def list_governed_context_facts(
        self,
        query: GovernedContextFactQuery,
    ) -> list[GovernedContextFact]:
        if query.latest_only:
            items = [versions[-1] for versions in self._versions.values() if versions]
        else:
            items = [item for versions in self._versions.values() for item in versions]
        items = _filter_facts(items, query)
        return [item.model_copy(deep=True) for item in items[: query.limit]]

    def list_governed_context_fact_versions(
        self,
        fact_id: str,
        *,
        limit: int = 100,
    ) -> list[GovernedContextFact]:
        versions = sorted(
            self._versions.get(fact_id, []),
            key=lambda item: item.version,
            reverse=True,
        )
        return [item.model_copy(deep=True) for item in versions[:limit]]


def validate_governed_context_fact_append(
    fact: GovernedContextFact,
    *,
    latest: GovernedContextFact | None,
    expected_latest_version: int | None,
) -> None:
    actual_version = latest.version if latest is not None else None
    if actual_version != expected_latest_version:
        raise GovernedContextFactVersionConflictError(f"governed fact {fact.fact_id} expected latest version {expected_latest_version!r}, found {actual_version!r}")
    expected_new_version = 1 if latest is None else latest.version + 1
    if fact.version != expected_new_version:
        raise GovernedContextFactVersionConflictError(f"governed fact {fact.fact_id} version must be {expected_new_version}, got {fact.version}")
    if not fact.is_latest:
        raise GovernedContextFactVersionConflictError("new governed fact version must set is_latest=true")
    if latest is not None and fact.supersedes_version_id != latest.fact_version_id:
        raise GovernedContextFactVersionConflictError("new governed fact version must reference the latest superseded version")


def _filter_facts(
    facts: list[GovernedContextFact],
    query: GovernedContextFactQuery,
) -> list[GovernedContextFact]:
    items = facts
    filters = {
        "fact_id": query.fact_id,
        "fact_type": query.fact_type,
        "status": query.status,
        "tenant_id": query.tenant_id,
        "environment": query.environment,
    }
    for name, value in filters.items():
        if value is not None:
            items = [item for item in items if getattr(item, name) == value]
    if query.valid_at is not None:
        items = [item for item in items if item.valid_from <= query.valid_at < item.valid_until]
    return sorted(items, key=lambda item: (item.updated_at, item.version), reverse=True)


__all__ = [
    "GovernedContextFactVersionConflictError",
    "InMemoryGovernedContextFactRepository",
    "validate_governed_context_fact_append",
]
