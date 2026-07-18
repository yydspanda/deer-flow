"""Lifecycle service for typed governed operational context facts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from soc_agent.contracts import (
    ActorContext,
    GovernedContextFact,
    GovernedContextFactCreateCommand,
    GovernedContextFactQuery,
    GovernedContextFactRevisionCommand,
    GovernedContextFactStatus,
    GovernedContextFactTransitionCommand,
    GovernedContextSourceType,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
)
from soc_agent.governed_context import GovernedContextFactVersionConflictError
from soc_agent.protocols import GovernedContextFactRepository, SocEventSink

from .access_control import require_actor_roles
from .service import NoopEventSink, SocServiceError, SocServiceNotFoundError, SocServiceNotImplementedError

_PROPOSE_ROLES = frozenset({"soc_analyst", "soc_engineer", "soc_admin", "soc_context_source"})
_GOVERN_ROLES = frozenset({"soc_context_approver", "soc_admin"})
_EXPIRE_ROLES = _GOVERN_ROLES | {"soc_context_service"}
_ACTIVATABLE_SOURCES = frozenset(
    {
        GovernedContextSourceType.AUTHORITATIVE_SYSTEM,
        GovernedContextSourceType.ADAPTER_SYNC,
        GovernedContextSourceType.TICKET,
        GovernedContextSourceType.ANALYST_CONFIRMATION,
    }
)


class SocGovernedContextService:
    """Own fact proposal, append-only versioning, and lifecycle transitions."""

    def __init__(
        self,
        *,
        repository: GovernedContextFactRepository | None = None,
        event_sink: SocEventSink | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._event_sink = event_sink or NoopEventSink()
        self._now_provider = now_provider or (lambda: datetime.now(UTC))

    def propose(
        self,
        command: GovernedContextFactCreateCommand,
        *,
        context: ServiceRequestContext,
    ) -> GovernedContextFact:
        self._require_repository()
        require_actor_roles(context, _PROPOSE_ROLES, operation="proposing governed context facts")
        now = self._now()
        evidence_refs = _normalized_refs(command.evidence_refs)
        fact = GovernedContextFact(
            fact_type=command.fact_type,
            tenant_id=command.tenant_id,
            environment=command.environment,
            valid_from=command.valid_from,
            valid_until=command.valid_until,
            source=command.source,
            owner_id=command.owner_id or context.actor.actor_id,
            created_by=context.actor,
            changed_by=context.actor,
            status_reason=command.reason.strip(),
            evidence_refs=evidence_refs,
            payload=command.payload,
            content_hash=_content_hash(command, evidence_refs=evidence_refs),
            created_at=now,
            updated_at=now,
            state_changed_at=now,
        )
        self._append(fact, expected_latest_version=None)
        self._emit(fact, context=context, event_type=SocEventType.GOVERNED_CONTEXT_FACT_PROPOSED)
        return fact

    def revise(
        self,
        command: GovernedContextFactRevisionCommand,
        *,
        context: ServiceRequestContext,
    ) -> GovernedContextFact:
        require_actor_roles(context, _PROPOSE_ROLES, operation="revising governed context facts")
        current = self._current(command.fact_id, expected_version=command.expected_latest_version)
        if current.status in {GovernedContextFactStatus.REVOKED, GovernedContextFactStatus.EXPIRED}:
            raise SocServiceError(f"cannot revise terminal governed fact in status {current.status.value}")
        if command.tenant_id != current.tenant_id or command.environment != current.environment:
            raise SocServiceError("governed fact revision cannot change tenant_id or environment")
        now = self._now()
        evidence_refs = _normalized_refs(command.evidence_refs)
        revised = _next_version(
            current,
            status=GovernedContextFactStatus.PROPOSED,
            actor=context.actor,
            reason=command.reason,
            now=now,
            reviewed_by=None,
            source=command.source,
            owner_id=command.owner_id or current.owner_id,
            valid_from=command.valid_from,
            valid_until=command.valid_until,
            evidence_refs=evidence_refs,
            payload=command.payload,
            content_hash=_revision_content_hash(current, command, evidence_refs=evidence_refs),
        )
        self._append(revised, expected_latest_version=current.version)
        self._emit(revised, context=context, event_type=SocEventType.GOVERNED_CONTEXT_FACT_REVISED)
        return revised

    def activate(
        self,
        command: GovernedContextFactTransitionCommand,
        *,
        context: ServiceRequestContext,
    ) -> GovernedContextFact:
        require_actor_roles(context, _GOVERN_ROLES, operation="activating governed context facts")
        current = self._current(command.fact_id, expected_version=command.expected_latest_version)
        if current.status not in {GovernedContextFactStatus.PROPOSED, GovernedContextFactStatus.SUSPENDED}:
            raise SocServiceError(f"cannot activate governed fact in status {current.status.value}")
        now = self._now()
        if current.valid_until <= now:
            raise SocServiceError("cannot activate a governed fact whose validity has ended")
        if not current.evidence_refs:
            raise SocServiceError("active governed facts require at least one evidence reference")
        if current.source.source_type not in _ACTIVATABLE_SOURCES:
            raise SocServiceError("source type is not eligible for governed fact activation")
        if current.source.fresh_until is not None and current.source.fresh_until <= now:
            raise SocServiceError("cannot activate a governed fact with stale source evidence")
        return self._transition(
            current,
            status=GovernedContextFactStatus.ACTIVE,
            command=command,
            context=context,
            event_type=SocEventType.GOVERNED_CONTEXT_FACT_ACTIVATED,
            reviewed_by=context.actor,
        )

    def suspend(
        self,
        command: GovernedContextFactTransitionCommand,
        *,
        context: ServiceRequestContext,
    ) -> GovernedContextFact:
        require_actor_roles(context, _GOVERN_ROLES, operation="suspending governed context facts")
        current = self._current(command.fact_id, expected_version=command.expected_latest_version)
        if current.status is not GovernedContextFactStatus.ACTIVE:
            raise SocServiceError(f"cannot suspend governed fact in status {current.status.value}")
        return self._transition(
            current,
            status=GovernedContextFactStatus.SUSPENDED,
            command=command,
            context=context,
            event_type=SocEventType.GOVERNED_CONTEXT_FACT_SUSPENDED,
        )

    def revoke(
        self,
        command: GovernedContextFactTransitionCommand,
        *,
        context: ServiceRequestContext,
    ) -> GovernedContextFact:
        require_actor_roles(context, _GOVERN_ROLES, operation="revoking governed context facts")
        current = self._current(command.fact_id, expected_version=command.expected_latest_version)
        if current.status not in {
            GovernedContextFactStatus.PROPOSED,
            GovernedContextFactStatus.ACTIVE,
            GovernedContextFactStatus.SUSPENDED,
        }:
            raise SocServiceError(f"cannot revoke governed fact in status {current.status.value}")
        return self._transition(
            current,
            status=GovernedContextFactStatus.REVOKED,
            command=command,
            context=context,
            event_type=SocEventType.GOVERNED_CONTEXT_FACT_REVOKED,
        )

    def expire(
        self,
        command: GovernedContextFactTransitionCommand,
        *,
        context: ServiceRequestContext,
    ) -> GovernedContextFact:
        require_actor_roles(context, _EXPIRE_ROLES, operation="expiring governed context facts")
        current = self._current(command.fact_id, expected_version=command.expected_latest_version)
        if current.status not in {
            GovernedContextFactStatus.PROPOSED,
            GovernedContextFactStatus.ACTIVE,
            GovernedContextFactStatus.SUSPENDED,
        }:
            raise SocServiceError(f"cannot expire governed fact in status {current.status.value}")
        if current.valid_until > self._now():
            raise SocServiceError("governed fact is not due to expire; revoke it to end validity early")
        return self._transition(
            current,
            status=GovernedContextFactStatus.EXPIRED,
            command=command,
            context=context,
            event_type=SocEventType.GOVERNED_CONTEXT_FACT_EXPIRED,
        )

    def get(self, fact_id: str, *, version: int | None = None) -> GovernedContextFact:
        fact = self._require_repository().get_governed_context_fact(fact_id, version=version)
        if fact is None:
            suffix = f" version {version}" if version is not None else ""
            raise SocServiceNotFoundError(f"governed context fact {fact_id}{suffix} not found")
        return fact

    def list(self, query: GovernedContextFactQuery) -> list[GovernedContextFact]:
        return self._require_repository().list_governed_context_facts(query)

    def list_versions(self, fact_id: str, *, limit: int = 100) -> list[GovernedContextFact]:
        items = self._require_repository().list_governed_context_fact_versions(fact_id, limit=limit)
        if not items:
            raise SocServiceNotFoundError(f"governed context fact {fact_id} not found")
        return items

    def _transition(
        self,
        current: GovernedContextFact,
        *,
        status: GovernedContextFactStatus,
        command: GovernedContextFactTransitionCommand,
        context: ServiceRequestContext,
        event_type: SocEventType,
        reviewed_by: ActorContext | None = None,
    ) -> GovernedContextFact:
        next_fact = _next_version(
            current,
            status=status,
            actor=context.actor,
            reason=command.reason,
            now=self._now(),
            reviewed_by=reviewed_by or current.reviewed_by,
        )
        self._append(next_fact, expected_latest_version=current.version)
        self._emit(next_fact, context=context, event_type=event_type)
        return next_fact

    def _current(self, fact_id: str, *, expected_version: int) -> GovernedContextFact:
        current = self.get(fact_id)
        if current.version != expected_version:
            raise SocServiceError(f"governed fact {fact_id} expected latest version {expected_version}, found {current.version}")
        return current

    def _append(self, fact: GovernedContextFact, *, expected_latest_version: int | None) -> None:
        try:
            self._require_repository().append_governed_context_fact(
                fact,
                expected_latest_version=expected_latest_version,
            )
        except GovernedContextFactVersionConflictError as exc:
            raise SocServiceError(str(exc)) from exc

    def _emit(
        self,
        fact: GovernedContextFact,
        *,
        context: ServiceRequestContext,
        event_type: SocEventType,
    ) -> None:
        self._event_sink.emit(
            SocEvent(
                event_type=event_type,
                request_id=context.request_id,
                actor=context.actor,
                payload={
                    "fact_id": fact.fact_id,
                    "fact_version_id": fact.fact_version_id,
                    "version": fact.version,
                    "fact_type": fact.fact_type.value,
                    "status": fact.status.value,
                    "tenant_id": fact.tenant_id,
                    "environment": fact.environment,
                    "content_hash": fact.content_hash,
                },
            )
        )

    def _require_repository(self) -> GovernedContextFactRepository:
        if self._repository is None:
            raise SocServiceNotImplementedError("governed context fact operation requires a repository")
        return self._repository

    def _now(self) -> datetime:
        value = self._now_provider()
        if value.tzinfo is None or value.utcoffset() is None:
            raise SocServiceError("governed context now_provider must return a timezone-aware datetime")
        return value


def _next_version(
    current: GovernedContextFact,
    *,
    status: GovernedContextFactStatus,
    actor: ActorContext,
    reason: str,
    now: datetime,
    reviewed_by: ActorContext | None,
    **updates: object,
) -> GovernedContextFact:
    values = current.model_dump(mode="python")
    values.update(updates)
    values.update(
        {
            "fact_version_id": f"GCFV-{uuid4().hex[:20].upper()}",
            "version": current.version + 1,
            "status": status,
            "changed_by": actor,
            "reviewed_by": reviewed_by,
            "status_reason": reason.strip(),
            "supersedes_version_id": current.fact_version_id,
            "is_latest": True,
            "updated_at": now,
            "state_changed_at": now,
        }
    )
    return GovernedContextFact.model_validate(values)


def _content_hash(
    command: GovernedContextFactCreateCommand,
    *,
    evidence_refs: list[str],
) -> str:
    payload = {
        "fact_type": command.fact_type.value,
        "tenant_id": command.tenant_id,
        "environment": command.environment,
        "valid_from": command.valid_from.isoformat(),
        "valid_until": command.valid_until.isoformat(),
        "source": command.source.model_dump(mode="json"),
        "evidence_refs": evidence_refs,
        "payload": command.payload.model_dump(mode="json"),
    }
    return _stable_sha256(payload)


def _revision_content_hash(
    current: GovernedContextFact,
    command: GovernedContextFactRevisionCommand,
    *,
    evidence_refs: list[str],
) -> str:
    payload = {
        "fact_type": current.fact_type.value,
        "tenant_id": command.tenant_id,
        "environment": command.environment,
        "valid_from": command.valid_from.isoformat(),
        "valid_until": command.valid_until.isoformat(),
        "source": command.source.model_dump(mode="json"),
        "evidence_refs": evidence_refs,
        "payload": command.payload.model_dump(mode="json"),
    }
    return _stable_sha256(payload)


def _stable_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalized_refs(values: list[str]) -> list[str]:
    return sorted({value.strip() for value in values if value.strip()})


__all__ = ["SocGovernedContextService"]
