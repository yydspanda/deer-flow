"""Persisted, replayable authorization enrichment orchestration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import uuid4

from soc_agent.authorization import AuthorizationEnrichmentConflictError
from soc_agent.contracts import (
    AuthorizationEnrichmentApplyResult,
    AuthorizationEnrichmentCommand,
    AuthorizationEnrichmentRecord,
    AuthorizationQuery,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
)
from soc_agent.core.authorized_activity import SocAuthorizedActivityService
from soc_agent.core.service import (
    NoopEventSink,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from soc_agent.protocols import (
    AlertRepository,
    AuthorizationEnrichmentRepository,
    ReviewQueueRepository,
    SocEventSink,
)
from soc_agent.utils.hashing import stable_hash


class AuthorizationEnrichmentIdempotencyConflictError(ValueError):
    """Raised when an idempotency key is reused for different enrichment input."""


class SocAuthorizationEnrichmentService:
    """Attach deterministic authorization matches to investigations without decisions."""

    def __init__(
        self,
        *,
        authorization_service: SocAuthorizedActivityService,
        repository: AuthorizationEnrichmentRepository | None = None,
        alert_repository: AlertRepository | None = None,
        review_queue_repository: ReviewQueueRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._authorization_service = authorization_service
        self._repository = repository
        self._alert_repository = alert_repository
        self._review_queue_repository = review_queue_repository
        self._event_sink = event_sink or NoopEventSink()

    def enrich_run(
        self,
        run_id: str,
        *,
        queue_id: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        event_timezone: str | None = None,
        idempotency_key: str,
        context: ServiceRequestContext | None = None,
    ) -> AuthorizationEnrichmentApplyResult:
        """Build a query from one persisted run and append its match result."""

        run = self._get_run(run_id)
        if run.input_payload is None:
            raise ValueError(f"run {run_id} does not retain an input payload")
        query = self._authorization_service.build_query_from_payload(
            run.input_payload,
            tenant_id=tenant_id,
            environment=environment,
            event_timezone=event_timezone,
        )
        return self.enrich(
            AuthorizationEnrichmentCommand(
                run_id=run_id,
                queue_id=queue_id,
                query=query,
                idempotency_key=idempotency_key,
            ),
            context=context,
        )

    def enrich_payload(
        self,
        payload: Mapping[str, Any],
        *,
        run_id: str,
        queue_id: str | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        event_timezone: str | None = None,
        idempotency_key: str,
        context: ServiceRequestContext | None = None,
    ) -> AuthorizationEnrichmentApplyResult:
        """Build and persist enrichment from a caller-supplied alert payload."""

        query = self._authorization_service.build_query_from_payload(
            payload,
            tenant_id=tenant_id,
            environment=environment,
            event_timezone=event_timezone,
        )
        return self.enrich(
            AuthorizationEnrichmentCommand(
                run_id=run_id,
                queue_id=queue_id,
                query=query,
                idempotency_key=idempotency_key,
            ),
            context=context,
        )

    def enrich(
        self,
        command: AuthorizationEnrichmentCommand,
        *,
        context: ServiceRequestContext | None = None,
    ) -> AuthorizationEnrichmentApplyResult:
        """Match and append one immutable enrichment record."""

        repository = self._require_repository()
        run = self._get_run(command.run_id)
        if run.alert_id != command.query.alert_id:
            raise ValueError(f"authorization query alert {command.query.alert_id} does not belong to run {run.run_id}")
        self._validate_queue(command.queue_id, run_id=run.run_id, alert_id=run.alert_id)
        self._validate_replay_source(command)

        query_hash = authorization_query_hash(command.query)
        existing = repository.find_authorization_enrichment_by_idempotency_key(command.idempotency_key)
        if existing is not None:
            _validate_idempotent_enrichment(existing, command, query_hash=query_hash)
            return AuthorizationEnrichmentApplyResult(record=existing, idempotent=True)

        request_context = context or ServiceRequestContext()
        match_result = self._authorization_service.match(command.query)
        record = AuthorizationEnrichmentRecord(
            run_id=run.run_id,
            alert_id=run.alert_id,
            queue_id=command.queue_id,
            query=command.query,
            query_hash=query_hash,
            match_result=match_result,
            matcher_policy_version=match_result.policy_version,
            idempotency_key=command.idempotency_key,
            replay_of_enrichment_id=command.replay_of_enrichment_id,
            created_by=request_context.actor,
        )
        try:
            repository.save_authorization_enrichment(record)
        except AuthorizationEnrichmentConflictError:
            concurrent = repository.find_authorization_enrichment_by_idempotency_key(command.idempotency_key)
            if concurrent is None:
                raise
            _validate_idempotent_enrichment(concurrent, command, query_hash=query_hash)
            return AuthorizationEnrichmentApplyResult(record=concurrent, idempotent=True)

        event_type = SocEventType.AUTHORIZATION_ENRICHMENT_REPLAYED if command.replay_of_enrichment_id is not None else SocEventType.AUTHORIZATION_ENRICHMENT_RECORDED
        self._event_sink.emit(
            SocEvent(
                event_type=event_type,
                request_id=request_context.request_id,
                run_id=record.run_id,
                alert_id=record.alert_id,
                actor=request_context.actor,
                payload={
                    "enrichment_id": record.enrichment_id,
                    "queue_id": record.queue_id,
                    "status": record.match_result.status.value,
                    "query_hash": record.query_hash,
                    "matcher_policy_version": record.matcher_policy_version,
                    "matched_fact_version_ids": [item.fact_version_id for item in record.match_result.matched_fact_refs],
                    "replay_of_enrichment_id": record.replay_of_enrichment_id,
                    "shadow_only": True,
                    "decision_impact": "none",
                },
            )
        )
        return AuthorizationEnrichmentApplyResult(
            record=record,
            idempotent=False,
            event_written=True,
        )

    def replay(
        self,
        enrichment_id: str,
        *,
        idempotency_key: str,
        context: ServiceRequestContext | None = None,
    ) -> AuthorizationEnrichmentApplyResult:
        """Re-run a stored query against current governed-fact state."""

        repository = self._require_repository()
        source = repository.get_authorization_enrichment(enrichment_id)
        if source is None:
            raise SocServiceNotFoundError(f"authorization enrichment {enrichment_id} not found")
        replay_query = source.query.model_copy(update={"query_id": f"AAQ-{uuid4().hex[:20].upper()}"})
        return self.enrich(
            AuthorizationEnrichmentCommand(
                run_id=source.run_id,
                queue_id=source.queue_id,
                query=replay_query,
                idempotency_key=idempotency_key,
                replay_of_enrichment_id=source.enrichment_id,
            ),
            context=context,
        )

    def get(self, enrichment_id: str) -> AuthorizationEnrichmentRecord:
        record = self._require_repository().get_authorization_enrichment(enrichment_id)
        if record is None:
            raise SocServiceNotFoundError(f"authorization enrichment {enrichment_id} not found")
        return record

    def list(
        self,
        *,
        run_id: str | None = None,
        alert_id: str | None = None,
        queue_id: str | None = None,
        limit: int = 50,
    ) -> list[AuthorizationEnrichmentRecord]:
        return self._require_repository().list_authorization_enrichments(
            run_id=run_id,
            alert_id=alert_id,
            queue_id=queue_id,
            limit=limit,
        )

    def _require_repository(self) -> AuthorizationEnrichmentRepository:
        if self._repository is None:
            raise SocServiceNotImplementedError("authorization enrichment requires an AuthorizationEnrichmentRepository")
        return self._repository

    def _get_run(self, run_id: str):
        if self._alert_repository is None:
            raise SocServiceNotImplementedError("authorization enrichment requires an AlertRepository")
        run = self._alert_repository.get_run(run_id)
        if run is None:
            raise SocServiceNotFoundError(f"run {run_id} not found")
        return run

    def _validate_queue(
        self,
        queue_id: str | None,
        *,
        run_id: str,
        alert_id: str,
    ) -> None:
        if queue_id is None:
            return
        if self._review_queue_repository is None:
            raise SocServiceNotImplementedError("queue-linked authorization enrichment requires a ReviewQueueRepository")
        item = self._review_queue_repository.get_review_item(queue_id)
        if item is None:
            raise SocServiceNotFoundError(f"review queue item {queue_id} not found")
        if item.run_id != run_id or item.alert_id != alert_id:
            raise ValueError(f"review queue item {queue_id} does not belong to run {run_id} / alert {alert_id}")

    def _validate_replay_source(self, command: AuthorizationEnrichmentCommand) -> None:
        if command.replay_of_enrichment_id is None:
            return
        source = self._require_repository().get_authorization_enrichment(command.replay_of_enrichment_id)
        if source is None:
            raise SocServiceNotFoundError(f"authorization replay source {command.replay_of_enrichment_id} not found")
        if source.run_id != command.run_id or source.alert_id != command.query.alert_id:
            raise ValueError("authorization replay must remain attached to its source run and alert")


def authorization_query_hash(query: AuthorizationQuery) -> str:
    """Hash semantic query content while excluding the per-attempt query id."""

    return stable_hash(query.model_dump(mode="json", exclude={"query_id"}))


def _validate_idempotent_enrichment(
    existing: AuthorizationEnrichmentRecord,
    command: AuthorizationEnrichmentCommand,
    *,
    query_hash: str,
) -> None:
    identity = (
        existing.run_id,
        existing.queue_id,
        existing.query_hash,
        existing.replay_of_enrichment_id,
    )
    requested = (
        command.run_id,
        command.queue_id,
        query_hash,
        command.replay_of_enrichment_id,
    )
    if identity != requested:
        raise AuthorizationEnrichmentIdempotencyConflictError("authorization enrichment idempotency key was reused for different input")


__all__ = [
    "AuthorizationEnrichmentIdempotencyConflictError",
    "SocAuthorizationEnrichmentService",
    "authorization_query_hash",
]
