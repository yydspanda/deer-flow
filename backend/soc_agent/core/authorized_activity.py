"""Public read-only service for deterministic authorized-activity matching."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from soc_agent.authorization import AuthorizationQueryBuilder, AuthorizedActivityMatcher
from soc_agent.contracts import (
    AlertInput,
    AuthorizationDimension,
    AuthorizationMatchResult,
    AuthorizationMatchStatus,
    AuthorizationQuery,
    ExtractedEntities,
    FactReconstructionResult,
    GovernedContextFactQuery,
    GovernedContextFactType,
)
from soc_agent.core.runtime import inspect_alert_normalization
from soc_agent.pipeline.fact_reconstructor import reconstruct_facts
from soc_agent.protocols import GovernedContextFactRepository


class SocAuthorizedActivityService:
    """Build canonical queries and match governed facts without side effects."""

    def __init__(
        self,
        *,
        repository: GovernedContextFactRepository | None = None,
        query_builder: AuthorizationQueryBuilder | None = None,
        matcher: AuthorizedActivityMatcher | None = None,
        candidate_limit: int = 500,
    ) -> None:
        if not 1 <= candidate_limit <= 500:
            raise ValueError("authorization candidate_limit must be in range 1..500")
        self._repository = repository
        self._query_builder = query_builder or AuthorizationQueryBuilder()
        self._matcher = matcher or AuthorizedActivityMatcher()
        self._candidate_limit = candidate_limit

    def build_query(
        self,
        alert: AlertInput,
        *,
        entities: ExtractedEntities | None = None,
        fact_reconstruction: FactReconstructionResult | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        event_timezone: str | None = None,
    ) -> AuthorizationQuery:
        return self._query_builder.build(
            alert,
            entities=entities,
            fact_reconstruction=fact_reconstruction,
            tenant_id=tenant_id,
            environment=environment,
            event_timezone=event_timezone,
        )

    def match_alert(
        self,
        alert: AlertInput,
        *,
        entities: ExtractedEntities | None = None,
        fact_reconstruction: FactReconstructionResult | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        event_timezone: str | None = None,
    ) -> AuthorizationMatchResult:
        reconstruction = fact_reconstruction or reconstruct_facts(alert)
        query = self.build_query(
            alert,
            entities=entities,
            fact_reconstruction=reconstruction,
            tenant_id=tenant_id,
            environment=environment,
            event_timezone=event_timezone,
        )
        return self.match(query)

    def match_payload(
        self,
        payload: Mapping[str, Any],
        *,
        tenant_id: str | None = None,
        environment: str | None = None,
        event_timezone: str | None = None,
    ) -> AuthorizationMatchResult:
        inspection = inspect_alert_normalization(payload)
        reconstruction = reconstruct_facts(inspection.alert)
        return self.match_alert(
            inspection.alert,
            entities=inspection.entities,
            fact_reconstruction=reconstruction,
            tenant_id=tenant_id,
            environment=environment,
            event_timezone=event_timezone,
        )

    def match(self, query: AuthorizationQuery) -> AuthorizationMatchResult:
        preflight = self._matcher.match(query, [])
        if preflight.status is AuthorizationMatchStatus.UNAVAILABLE:
            return preflight
        if self._repository is None:
            return _unavailable_result(query, "governed_context_repository_unavailable")

        try:
            facts = self._repository.list_governed_context_facts(
                GovernedContextFactQuery(
                    fact_type=GovernedContextFactType.AUTHORIZED_ACTIVITY,
                    tenant_id=query.tenant_id,
                    environment=query.environment,
                    latest_only=False,
                    limit=self._candidate_limit,
                )
            )
        except Exception as exc:  # noqa: BLE001 - source availability is an explicit match state
            return _unavailable_result(
                query,
                f"governed_context_repository_error:{type(exc).__name__}",
            )
        if len(facts) >= self._candidate_limit:
            return _unavailable_result(
                query,
                f"authorization_candidate_limit_reached:{self._candidate_limit}",
            )
        return self._matcher.match(query, facts)


def _unavailable_result(query: AuthorizationQuery, warning: str) -> AuthorizationMatchResult:
    return AuthorizationMatchResult(
        query_id=query.query_id,
        alert_id=query.alert_id,
        status=AuthorizationMatchStatus.UNAVAILABLE,
        event_time=query.event_time,
        missing_dimensions=[AuthorizationDimension.SOURCE_FRESHNESS],
        warnings=list(dict.fromkeys([*query.warnings, warning])),
    )


__all__ = ["SocAuthorizedActivityService"]
