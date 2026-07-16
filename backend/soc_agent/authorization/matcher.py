"""Pure deterministic matcher for authorized-activity facts."""

from __future__ import annotations

import html
import re
from collections import defaultdict
from datetime import datetime
from ipaddress import ip_address, ip_network
from pathlib import PurePath
from typing import Any
from zoneinfo import ZoneInfo

from soc_agent.contracts import (
    AuthorizationDimension,
    AuthorizationDimensionEvaluation,
    AuthorizationDimensionStatus,
    AuthorizationFactEvaluation,
    AuthorizationFactRef,
    AuthorizationMatchResult,
    AuthorizationMatchStatus,
    AuthorizationQuery,
    AuthorizationSelectorMatch,
    AuthorizationSourceFreshness,
    AuthorizedActivityBehaviorKind,
    AuthorizedActivityRecurringWindow,
    AuthorizedActivitySubjectKind,
    AuthorizedActivityTargetKind,
    GovernedContextFact,
    GovernedContextFactStatus,
)

_SPACE_RE = re.compile(r"\s+")


class AuthorizedActivityMatcher:
    """Evaluate governed fact history at the alert event time."""

    policy_version = "soc.authorization_match.v1"

    def match(
        self,
        query: AuthorizationQuery,
        facts: list[GovernedContextFact],
    ) -> AuthorizationMatchResult:
        unavailable_dimensions = _unavailable_query_dimensions(query)
        if unavailable_dimensions:
            return _result(
                query,
                status=AuthorizationMatchStatus.UNAVAILABLE,
                missing_dimensions=unavailable_dimensions,
                warnings=[*query.warnings, "authorization_query_missing_required_context"],
            )

        assert query.event_time is not None
        relevant = [fact for fact in facts if fact.tenant_id == query.tenant_id and fact.environment == query.environment]
        if not relevant:
            return _result(
                query,
                status=AuthorizationMatchStatus.NOT_FOUND,
                warnings=query.warnings,
            )

        blocking_conflict = any(item.blocks_authorization for item in query.conflicts)
        effective = _effective_versions(relevant, query.event_time)
        evaluations = [
            self._evaluate_fact(
                query,
                fact,
                lifecycle_available=lifecycle_available,
                blocking_query_conflict=blocking_conflict,
            )
            for fact, lifecycle_available in effective
        ]
        evaluations.sort(key=_evaluation_sort_key, reverse=True)
        exact = [item for item in evaluations if item.status is AuthorizationMatchStatus.EXACT]

        if exact:
            matched_refs = [item.fact_ref for item in exact]
            return _result(
                query,
                status=AuthorizationMatchStatus.EXACT,
                evaluations=evaluations,
                matched_fact_refs=matched_refs,
                candidate_fact_refs=[item.fact_ref for item in evaluations],
                matched_dimensions=_ordered_union(item.matched_dimensions for item in exact),
                source_freshness=[item.source_freshness for item in exact],
                evidence_refs=_unique(ref for item in exact for ref in item.evidence_refs),
                warnings=query.warnings,
            )

        best = evaluations[0]
        return _result(
            query,
            status=best.status,
            evaluations=evaluations,
            candidate_fact_refs=[item.fact_ref for item in evaluations],
            matched_dimensions=best.matched_dimensions,
            missing_dimensions=best.missing_dimensions,
            out_of_scope_dimensions=best.out_of_scope_dimensions,
            source_freshness=[item.source_freshness for item in evaluations],
            evidence_refs=best.evidence_refs,
            warnings=query.warnings,
        )

    def _evaluate_fact(
        self,
        query: AuthorizationQuery,
        fact: GovernedContextFact,
        *,
        lifecycle_available: bool,
        blocking_query_conflict: bool,
    ) -> AuthorizationFactEvaluation:
        assert query.event_time is not None
        event_time = query.event_time
        results = [
            _simple_dimension(
                AuthorizationDimension.TENANT,
                matched=fact.tenant_id == query.tenant_id,
                matched_reason="fact tenant matches query tenant",
                failure_reason="fact tenant is outside query tenant",
            ),
            _simple_dimension(
                AuthorizationDimension.ENVIRONMENT,
                matched=fact.environment == query.environment,
                matched_reason="fact environment matches query environment",
                failure_reason="fact environment is outside query environment",
            ),
            _simple_dimension(
                AuthorizationDimension.EVENT_TIME,
                matched=fact.valid_from <= event_time < fact.valid_until,
                matched_reason="alert event time is inside fact validity",
                failure_reason="alert event time is outside fact validity",
            ),
            _lifecycle_dimension(fact, lifecycle_available=lifecycle_available),
        ]
        freshness = _source_freshness(fact, event_time)
        results.append(_source_dimension(freshness))
        results.append(_recurrence_dimension(fact.payload.recurring_windows, event_time))
        results.extend(
            [
                _selector_dimension(
                    AuthorizationDimension.SUBJECT,
                    fact.payload.subject_scope,
                    query.subjects,
                ),
                _selector_dimension(
                    AuthorizationDimension.TARGET,
                    fact.payload.target_scope,
                    query.targets,
                ),
                _selector_dimension(
                    AuthorizationDimension.BEHAVIOR,
                    fact.payload.behavior_scope,
                    query.behaviors,
                ),
            ]
        )

        matched_dimensions = [item.dimension for item in results if item.status is AuthorizationDimensionStatus.MATCHED]
        missing_dimensions = [item.dimension for item in results if item.status in {AuthorizationDimensionStatus.MISSING, AuthorizationDimensionStatus.UNAVAILABLE}]
        out_of_scope_dimensions = [item.dimension for item in results if item.status is AuthorizationDimensionStatus.OUT_OF_SCOPE]
        temporal_failure = any(
            item.dimension
            in {
                AuthorizationDimension.EVENT_TIME,
                AuthorizationDimension.LIFECYCLE,
                AuthorizationDimension.SOURCE_FRESHNESS,
            }
            and item.status is AuthorizationDimensionStatus.OUT_OF_SCOPE
            for item in results
        )
        unavailable = any(item.status is AuthorizationDimensionStatus.UNAVAILABLE for item in results)
        scope_missing = any(item.dimension in {AuthorizationDimension.SUBJECT, AuthorizationDimension.TARGET, AuthorizationDimension.BEHAVIOR} and item.status is AuthorizationDimensionStatus.MISSING for item in results)
        scope_conflict = any(
            item.dimension
            in {
                AuthorizationDimension.SUBJECT,
                AuthorizationDimension.TARGET,
                AuthorizationDimension.BEHAVIOR,
                AuthorizationDimension.RECURRING_WINDOW,
            }
            and item.status is AuthorizationDimensionStatus.OUT_OF_SCOPE
            for item in results
        )

        if blocking_query_conflict:
            status = AuthorizationMatchStatus.CONFLICT
            reason = "canonical fact reconstruction contains an automation-blocking conflict"
        elif unavailable:
            status = AuthorizationMatchStatus.UNAVAILABLE
            reason = "fact lifecycle or source was not available at alert event time"
        elif temporal_failure:
            status = AuthorizationMatchStatus.EXPIRED
            reason = "fact was inactive, stale, or outside business validity at alert event time"
        elif scope_conflict:
            status = AuthorizationMatchStatus.CONFLICT
            reason = "one or more observed authorization dimensions are outside fact scope"
        elif scope_missing:
            status = AuthorizationMatchStatus.PARTIAL
            reason = "one or more required authorization dimensions are missing from canonical evidence"
        else:
            status = AuthorizationMatchStatus.EXACT
            reason = "all required authorization dimensions match at alert event time"

        selector_matches = [selector for item in results for selector in item.matched_selectors]
        required_group_count = sum(len(item.required_selector_groups) for item in results)
        evidence_refs = _unique(
            [
                *fact.evidence_refs,
                *(item.evidence_path for item in selector_matches),
            ]
        )
        return AuthorizationFactEvaluation(
            fact_ref=_fact_ref(fact),
            status=status,
            source_freshness=freshness,
            dimension_results=results,
            matched_dimensions=matched_dimensions,
            missing_dimensions=missing_dimensions,
            out_of_scope_dimensions=out_of_scope_dimensions,
            evidence_refs=evidence_refs,
            matched_selector_count=len(selector_matches),
            required_selector_group_count=required_group_count,
            reason=reason,
        )


def _effective_versions(
    facts: list[GovernedContextFact],
    event_time: datetime,
) -> list[tuple[GovernedContextFact, bool]]:
    grouped: dict[str, list[GovernedContextFact]] = defaultdict(list)
    for fact in facts:
        grouped[fact.fact_id].append(fact)

    effective: list[tuple[GovernedContextFact, bool]] = []
    for versions in grouped.values():
        available = [item for item in versions if item.state_changed_at <= event_time]
        if available:
            selected = max(available, key=lambda item: (item.state_changed_at, item.version))
            effective.append((selected, True))
        else:
            selected = min(versions, key=lambda item: (item.state_changed_at, item.version))
            effective.append((selected, False))
    return effective


def _selector_dimension(
    dimension: AuthorizationDimension,
    selectors: list[Any],
    candidates: list[Any],
) -> AuthorizationDimensionEvaluation:
    groups: dict[tuple[str, str | None], list[Any]] = defaultdict(list)
    for selector in selectors:
        groups[(selector.kind.value, selector.namespace)].append(selector)

    matched: list[AuthorizationSelectorMatch] = []
    missing_groups: list[str] = []
    out_of_scope_groups: list[str] = []
    required_groups = [_group_label(key) for key in sorted(groups, key=lambda item: (item[0], item[1] or ""))]

    for key, group in groups.items():
        compatible = [candidate for candidate in candidates if _compatible(group[0], candidate)]
        label = _group_label(key)
        if not compatible:
            missing_groups.append(label)
            continue
        group_matches = [
            AuthorizationSelectorMatch(
                dimension=dimension,
                fact_kind=selector.kind.value,
                fact_value=selector.value,
                query_kind=candidate.kind.value,
                query_value=candidate.value,
                evidence_path=candidate.evidence_path,
            )
            for selector in group
            for candidate in compatible
            if _selector_matches(selector, candidate)
        ]
        if group_matches:
            matched.extend(group_matches)
        else:
            out_of_scope_groups.append(label)

    if out_of_scope_groups:
        status = AuthorizationDimensionStatus.OUT_OF_SCOPE
        reason = "compatible canonical values exist but are outside one or more required selector groups"
    elif missing_groups:
        status = AuthorizationDimensionStatus.MISSING
        reason = "canonical evidence has no values for one or more required selector groups"
    else:
        status = AuthorizationDimensionStatus.MATCHED
        reason = "all required selector groups matched; kinds are ANDed and values within a kind are ORed"
    return AuthorizationDimensionEvaluation(
        dimension=dimension,
        status=status,
        matched_selectors=matched,
        required_selector_groups=required_groups,
        missing_selector_groups=sorted(missing_groups),
        out_of_scope_selector_groups=sorted(out_of_scope_groups),
        reason=reason,
    )


def _compatible(selector, candidate) -> bool:
    if selector.namespace is not None and selector.namespace != candidate.namespace:
        return False
    if selector.kind.value == candidate.kind.value:
        return True
    return selector.kind.value == "cidr" and candidate.kind.value in {"ip", "cidr"}


def _selector_matches(selector, candidate) -> bool:
    kind = selector.kind
    if kind in {AuthorizedActivitySubjectKind.CIDR, AuthorizedActivityTargetKind.CIDR}:
        network = ip_network(selector.value, strict=False)
        if candidate.kind.value == "ip":
            return ip_address(candidate.value) in network
        return ip_network(candidate.value, strict=False).subnet_of(network)
    if kind in {AuthorizedActivitySubjectKind.IP, AuthorizedActivityTargetKind.IP}:
        return ip_address(selector.value) == ip_address(candidate.value)
    if kind is AuthorizedActivityTargetKind.DOMAIN:
        return _domain(selector.value) == _domain(candidate.value)
    if kind is AuthorizedActivityBehaviorKind.PROCESS:
        return _process_name(selector.value) == _process_name(candidate.value)
    if isinstance(kind, AuthorizedActivityBehaviorKind):
        return _normalized_text(selector.value) == _normalized_text(candidate.value)
    return selector.value.strip() == candidate.value.strip()


def _source_freshness(
    fact: GovernedContextFact,
    event_time: datetime,
) -> AuthorizationSourceFreshness:
    if fact.source.observed_at > event_time:
        return AuthorizationSourceFreshness.FUTURE
    if fact.source.fresh_until is None:
        return AuthorizationSourceFreshness.NOT_REQUIRED
    if event_time < fact.source.fresh_until:
        return AuthorizationSourceFreshness.FRESH
    return AuthorizationSourceFreshness.STALE


def _source_dimension(freshness: AuthorizationSourceFreshness) -> AuthorizationDimensionEvaluation:
    if freshness in {AuthorizationSourceFreshness.FRESH, AuthorizationSourceFreshness.NOT_REQUIRED}:
        return AuthorizationDimensionEvaluation(
            dimension=AuthorizationDimension.SOURCE_FRESHNESS,
            status=AuthorizationDimensionStatus.MATCHED,
            reason=f"source freshness is {freshness.value}",
        )
    if freshness is AuthorizationSourceFreshness.FUTURE:
        return AuthorizationDimensionEvaluation(
            dimension=AuthorizationDimension.SOURCE_FRESHNESS,
            status=AuthorizationDimensionStatus.UNAVAILABLE,
            reason="source observation occurred after the alert event time",
        )
    return AuthorizationDimensionEvaluation(
        dimension=AuthorizationDimension.SOURCE_FRESHNESS,
        status=AuthorizationDimensionStatus.OUT_OF_SCOPE,
        reason=f"source freshness is {freshness.value}",
    )


def _lifecycle_dimension(
    fact: GovernedContextFact,
    *,
    lifecycle_available: bool,
) -> AuthorizationDimensionEvaluation:
    if not lifecycle_available:
        return AuthorizationDimensionEvaluation(
            dimension=AuthorizationDimension.LIFECYCLE,
            status=AuthorizationDimensionStatus.UNAVAILABLE,
            reason="the first governed lifecycle version was created after alert event time",
        )
    if fact.status is GovernedContextFactStatus.ACTIVE:
        return AuthorizationDimensionEvaluation(
            dimension=AuthorizationDimension.LIFECYCLE,
            status=AuthorizationDimensionStatus.MATCHED,
            reason="fact lifecycle status was active at alert event time",
        )
    return AuthorizationDimensionEvaluation(
        dimension=AuthorizationDimension.LIFECYCLE,
        status=AuthorizationDimensionStatus.OUT_OF_SCOPE,
        reason=f"fact lifecycle status was {fact.status.value} at alert event time",
    )


def _recurrence_dimension(
    windows: list[AuthorizedActivityRecurringWindow],
    event_time: datetime,
) -> AuthorizationDimensionEvaluation:
    if not windows:
        return AuthorizationDimensionEvaluation(
            dimension=AuthorizationDimension.RECURRING_WINDOW,
            status=AuthorizationDimensionStatus.MATCHED,
            reason="fact has no recurring-window restriction",
        )
    if any(_inside_recurring_window(window, event_time) for window in windows):
        return AuthorizationDimensionEvaluation(
            dimension=AuthorizationDimension.RECURRING_WINDOW,
            status=AuthorizationDimensionStatus.MATCHED,
            reason="alert event time is inside an allowed recurring window",
        )
    return AuthorizationDimensionEvaluation(
        dimension=AuthorizationDimension.RECURRING_WINDOW,
        status=AuthorizationDimensionStatus.OUT_OF_SCOPE,
        reason="alert event time is outside all allowed recurring windows",
    )


def _inside_recurring_window(
    window: AuthorizedActivityRecurringWindow,
    event_time: datetime,
) -> bool:
    local = event_time.astimezone(ZoneInfo(window.timezone))
    local_time = local.timetz().replace(tzinfo=None)
    if window.start_time < window.end_time:
        return local.weekday() in window.days_of_week and window.start_time <= local_time < window.end_time
    if local_time >= window.start_time:
        return local.weekday() in window.days_of_week
    previous_day = (local.weekday() - 1) % 7
    return local_time < window.end_time and previous_day in window.days_of_week


def _simple_dimension(
    dimension: AuthorizationDimension,
    *,
    matched: bool,
    matched_reason: str,
    failure_reason: str,
) -> AuthorizationDimensionEvaluation:
    return AuthorizationDimensionEvaluation(
        dimension=dimension,
        status=(AuthorizationDimensionStatus.MATCHED if matched else AuthorizationDimensionStatus.OUT_OF_SCOPE),
        reason=matched_reason if matched else failure_reason,
    )


def _unavailable_query_dimensions(query: AuthorizationQuery) -> list[AuthorizationDimension]:
    dimensions: list[AuthorizationDimension] = []
    if not query.tenant_id:
        dimensions.append(AuthorizationDimension.TENANT)
    if not query.environment:
        dimensions.append(AuthorizationDimension.ENVIRONMENT)
    if query.event_time is None:
        dimensions.append(AuthorizationDimension.EVENT_TIME)
    return dimensions


def _fact_ref(fact: GovernedContextFact) -> AuthorizationFactRef:
    return AuthorizationFactRef(
        fact_id=fact.fact_id,
        fact_version_id=fact.fact_version_id,
        version=fact.version,
        status=fact.status,
        content_hash=fact.content_hash,
    )


def _evaluation_sort_key(evaluation: AuthorizationFactEvaluation) -> tuple[int, int, int, int]:
    status_rank = {
        AuthorizationMatchStatus.EXACT: 5,
        AuthorizationMatchStatus.PARTIAL: 4,
        AuthorizationMatchStatus.CONFLICT: 3,
        AuthorizationMatchStatus.EXPIRED: 2,
        AuthorizationMatchStatus.UNAVAILABLE: 1,
        AuthorizationMatchStatus.NOT_FOUND: 0,
    }
    return (
        evaluation.matched_selector_count,
        status_rank[evaluation.status],
        -len(evaluation.missing_dimensions),
        evaluation.fact_ref.version,
    )


def _result(
    query: AuthorizationQuery,
    *,
    status: AuthorizationMatchStatus,
    evaluations: list[AuthorizationFactEvaluation] | None = None,
    matched_fact_refs: list[AuthorizationFactRef] | None = None,
    candidate_fact_refs: list[AuthorizationFactRef] | None = None,
    matched_dimensions: list[AuthorizationDimension] | None = None,
    missing_dimensions: list[AuthorizationDimension] | None = None,
    out_of_scope_dimensions: list[AuthorizationDimension] | None = None,
    source_freshness: list[AuthorizationSourceFreshness] | None = None,
    evidence_refs: list[str] | None = None,
    warnings: list[str] | None = None,
) -> AuthorizationMatchResult:
    return AuthorizationMatchResult(
        query_id=query.query_id,
        alert_id=query.alert_id,
        status=status,
        event_time=query.event_time,
        matched_fact_refs=matched_fact_refs or [],
        candidate_fact_refs=candidate_fact_refs or [],
        matched_dimensions=matched_dimensions or [],
        missing_dimensions=missing_dimensions or [],
        out_of_scope_dimensions=out_of_scope_dimensions or [],
        source_freshness=source_freshness or [],
        evidence_refs=evidence_refs or [],
        fact_evaluations=evaluations or [],
        warnings=_unique(warnings or []),
    )


def _group_label(key: tuple[str, str | None]) -> str:
    kind, namespace = key
    return f"{kind}@{namespace}" if namespace else kind


def _ordered_union(values) -> list:
    return list(dict.fromkeys(item for group in values for item in group))


def _unique(values) -> list:
    return list(dict.fromkeys(values))


def _normalized_text(value: str) -> str:
    return _SPACE_RE.sub(" ", html.unescape(value)).strip().casefold()


def _domain(value: str) -> str:
    return value.strip().rstrip(".").casefold()


def _process_name(value: str) -> str:
    normalized = value.strip().replace("\\", "/")
    return PurePath(normalized).name.casefold()


__all__ = ["AuthorizedActivityMatcher"]
