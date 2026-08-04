"""Deterministic planning for allowlisted read-only investigation actions."""

from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network
from typing import Any

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    EntityKind,
    RoleResolutionStatus,
    SocEnrichmentPlan,
    SocEnrichmentPlannedAction,
    SocEnrichmentPlanStatus,
    SocEnrichmentPolicy,
    SocEnrichmentSkippedCandidate,
    SocEnrichmentSkipReason,
)
from soc_agent.utils.hashing import stable_hash

ASSET_LOOKUP_ROUTE = "asset.lookup"
ASSET_LOCATE_ROUTE = "asset.locate"
THREAT_INTEL_ROUTE = "threat_intel.ip_reputation.lookup"
SECURITY_TAG_ROUTE = "security_tag.lookup"

_SUPPORTED_ENTITY_KINDS = {
    EntityKind.IP: "ip",
    EntityKind.DOMAIN: "domain",
    EntityKind.HOST: "host",
    EntityKind.USER: "user",
}
_ASSET_TYPES = {
    "ip": "IP",
    "domain": "DOMAIN",
    "host": "HOST",
    "user": "USER",
}
_ANALYZABLE_STATUSES = {
    AnalysisRunStatus.SUCCESS,
    AnalysisRunStatus.NEEDS_REVIEW,
    AnalysisRunStatus.REPLAYED,
}
_MAX_ENTITY_KEY_LENGTH = 2048


@dataclass
class _Candidate:
    value: str
    normalized_value: str
    kind: str
    roles: set[str]
    evidence_refs: set[str]
    conflicted_roles: set[str]


class SocEnrichmentPlanner:
    """Build an immutable plan without invoking a provider or mutating a run."""

    def __init__(self, policy: SocEnrichmentPolicy | dict[str, Any]) -> None:
        self.policy = SocEnrichmentPolicy.model_validate(policy)
        self._internal_networks = tuple(ip_network(value, strict=False) for value in self.policy.internal_networks)

    def plan(self, run: AnalysisRun, *, thread_id: str) -> SocEnrichmentPlan:
        tenant_id = run.llm_analysis_request.tenant_id if run.llm_analysis_request is not None else None
        candidates, candidate_skips = _entity_candidates(run)
        input_projection = _input_projection(
            run,
            policy=self.policy,
            tenant_id=tenant_id,
            candidates=candidates,
            candidate_skips=candidate_skips,
        )
        input_hash = stable_hash(input_projection)
        plan_id = f"EPLAN-{input_hash[:12].upper()}"

        blocked = self._blocked_reason(run, tenant_id=tenant_id)
        if blocked is not None:
            return SocEnrichmentPlan(
                plan_id=plan_id,
                policy_version=self.policy.policy_version,
                run_id=run.run_id,
                alert_id=run.alert_id,
                tenant_id=tenant_id,
                thread_id=thread_id,
                input_hash=input_hash,
                status=SocEnrichmentPlanStatus.BLOCKED,
                skipped=[blocked],
            )

        actions: list[SocEnrichmentPlannedAction] = []
        skipped = list(candidate_skips)
        for route in self.policy.enabled_routes:
            route_actions, route_skipped = self._plan_route(
                route,
                candidates=candidates,
                plan_id=plan_id,
                remaining_total=self.policy.max_actions_total - len(actions),
            )
            actions.extend(route_actions)
            skipped.extend(route_skipped)

        return SocEnrichmentPlan(
            plan_id=plan_id,
            policy_version=self.policy.policy_version,
            run_id=run.run_id,
            alert_id=run.alert_id,
            tenant_id=tenant_id,
            thread_id=thread_id,
            input_hash=input_hash,
            status=(SocEnrichmentPlanStatus.PLANNED if actions else SocEnrichmentPlanStatus.NO_ACTIONS),
            actions=actions,
            skipped=skipped,
        )

    def _blocked_reason(
        self,
        run: AnalysisRun,
        *,
        tenant_id: str | None,
    ) -> SocEnrichmentSkippedCandidate | None:
        if run.status not in _ANALYZABLE_STATUSES:
            return SocEnrichmentSkippedCandidate(
                reason_code=SocEnrichmentSkipReason.RUN_NOT_ANALYZABLE,
                rationale=f"analysis run status {run.status.value} cannot produce automatic enrichment",
                evidence_refs=[f"run:{run.run_id}"],
            )
        if self.policy.tenant_id is not None and tenant_id != self.policy.tenant_id:
            return SocEnrichmentSkippedCandidate(
                reason_code=SocEnrichmentSkipReason.TENANT_MISMATCH,
                rationale="analysis tenant does not match the explicitly configured enrichment policy tenant",
                evidence_refs=[f"run:{run.run_id}"],
            )
        return None

    def _plan_route(
        self,
        route: str,
        *,
        candidates: list[_Candidate],
        plan_id: str,
        remaining_total: int,
    ) -> tuple[list[SocEnrichmentPlannedAction], list[SocEnrichmentSkippedCandidate]]:
        if route in {ASSET_LOOKUP_ROUTE, ASSET_LOCATE_ROUTE}:
            eligible = _eligible_candidates(
                candidates,
                kinds=set(self.policy.asset_entity_kinds),
                roles=self.policy.asset_roles,
            )
            return self._bounded_actions(
                route,
                eligible,
                plan_id=plan_id,
                remaining_total=remaining_total,
                reason_code="asset_context_required",
                rationale="Locate ownership and business context for a provenance-backed alert entity.",
            )
        if route == THREAT_INTEL_ROUTE:
            eligible = _eligible_candidates(
                candidates,
                kinds={"ip"},
                roles=self.policy.threat_intel_roles,
            )
            return self._threat_intel_actions(
                eligible,
                plan_id=plan_id,
                remaining_total=remaining_total,
            )
        if route == SECURITY_TAG_ROUTE:
            eligible = _eligible_candidates(
                candidates,
                kinds=set(self.policy.security_tag_entity_kinds),
                roles=self.policy.security_tag_roles,
            )
            return self._bounded_actions(
                route,
                eligible,
                plan_id=plan_id,
                remaining_total=remaining_total,
                reason_code="security_tag_context_required",
                rationale="Check exact authorization, maintenance, exercise, or security-label context without changing detection truth.",
            )
        raise ValueError(f"unsupported enrichment route: {route}")

    def _threat_intel_actions(
        self,
        candidates: list[_Candidate],
        *,
        plan_id: str,
        remaining_total: int,
    ) -> tuple[list[SocEnrichmentPlannedAction], list[SocEnrichmentSkippedCandidate]]:
        skipped: list[SocEnrichmentSkippedCandidate] = []
        if self.policy.threat_intel_requires_internal_networks and not self._internal_networks:
            for candidate in candidates:
                skipped.append(
                    _skipped_candidate(
                        THREAT_INTEL_ROUTE,
                        candidate,
                        SocEnrichmentSkipReason.NETWORK_SCOPE_UNCONFIGURED,
                        "tenant internal-network scope is required before sending an IP to external reputation lookup",
                    )
                )
            if not candidates:
                skipped.append(_no_entity_skip(THREAT_INTEL_ROUTE))
            return [], skipped

        external: list[_Candidate] = []
        for candidate in candidates:
            address = ip_address(candidate.normalized_value)
            if not address.is_global or any(address in network for network in self._internal_networks):
                skipped.append(
                    _skipped_candidate(
                        THREAT_INTEL_ROUTE,
                        candidate,
                        SocEnrichmentSkipReason.INTERNAL_OR_NON_GLOBAL_IP,
                        "IP is tenant-internal or non-global and is not eligible for external reputation lookup",
                    )
                )
                continue
            external.append(candidate)
        actions, bounded_skips = self._bounded_actions(
            THREAT_INTEL_ROUTE,
            external,
            plan_id=plan_id,
            remaining_total=remaining_total,
            reason_code="ip_reputation_required",
            rationale="Check current reputation for a provenance-backed external IP candidate.",
        )
        return actions, [*skipped, *bounded_skips]

    def _bounded_actions(
        self,
        route: str,
        candidates: list[_Candidate],
        *,
        plan_id: str,
        remaining_total: int,
        reason_code: str,
        rationale: str,
    ) -> tuple[list[SocEnrichmentPlannedAction], list[SocEnrichmentSkippedCandidate]]:
        if not candidates:
            return [], [_no_entity_skip(route)]
        actions: list[SocEnrichmentPlannedAction] = []
        skipped: list[SocEnrichmentSkippedCandidate] = []
        route_budget = min(self.policy.max_actions_per_route, max(remaining_total, 0))
        for candidate in candidates:
            if len(actions) >= route_budget:
                skipped.append(
                    _skipped_candidate(
                        route,
                        candidate,
                        SocEnrichmentSkipReason.ACTION_BUDGET_EXHAUSTED,
                        "versioned per-route or total enrichment action budget was exhausted",
                    )
                )
                continue
            actions.append(
                _planned_action(
                    route,
                    candidate,
                    plan_id=plan_id,
                    reason_code=reason_code,
                    rationale=rationale,
                )
            )
        return actions, skipped


def _entity_candidates(run: AnalysisRun) -> tuple[list[_Candidate], list[SocEnrichmentSkippedCandidate]]:
    by_key: dict[tuple[str, str], _Candidate] = {}
    skipped: list[SocEnrichmentSkippedCandidate] = []
    entities = run.entities
    if entities is None:
        return [], skipped
    for mention in entities.mentions:
        kind = _SUPPORTED_ENTITY_KINDS.get(mention.kind)
        if kind is None:
            continue
        try:
            normalized = _normalize_entity(mention.value, kind=kind)
        except ValueError:
            skipped.append(
                SocEnrichmentSkippedCandidate(
                    entity_key=mention.value[:_MAX_ENTITY_KEY_LENGTH],
                    entity_kind=kind,
                    entity_role=mention.role,
                    reason_code=SocEnrichmentSkipReason.INVALID_ENTITY,
                    rationale="typed entity value could not be normalized for safe provider routing",
                    evidence_refs=[mention.evidence_path] if mention.evidence_path else [],
                )
            )
            continue
        key = (kind, normalized)
        candidate = by_key.get(key)
        if candidate is None:
            candidate = _Candidate(
                value=mention.value.strip(),
                normalized_value=normalized,
                kind=kind,
                roles=set(),
                evidence_refs=set(),
                conflicted_roles=set(),
            )
            by_key[key] = candidate
        else:
            candidate.value = min(candidate.value, mention.value.strip(), key=lambda value: (value.casefold(), value))
        if mention.role:
            candidate.roles.add(mention.role)
        if mention.evidence_path:
            candidate.evidence_refs.add(mention.evidence_path)

    reconstruction = run.fact_reconstruction
    if reconstruction is not None:
        for resolution in reconstruction.role_resolutions:
            if not resolution.selected_value:
                continue
            matching = _matching_candidates(by_key.values(), resolution.selected_value)
            for candidate in matching:
                if resolution.status in {RoleResolutionStatus.CONFLICTED, RoleResolutionStatus.UNRESOLVED}:
                    candidate.conflicted_roles.add(resolution.role)
                else:
                    candidate.roles.add(resolution.role)
                candidate.evidence_refs.update(f"role_claim:{claim_id}" for claim_id in resolution.supporting_claim_ids)
    return sorted(by_key.values(), key=_candidate_sort_key), sorted(
        skipped,
        key=lambda item: (
            item.entity_kind or "",
            item.entity_key or "",
            item.entity_role or "",
        ),
    )


def _matching_candidates(candidates: Any, value: str) -> list[_Candidate]:
    matches: list[_Candidate] = []
    for candidate in candidates:
        try:
            normalized = _normalize_entity(value, kind=candidate.kind)
        except ValueError:
            continue
        if normalized == candidate.normalized_value:
            matches.append(candidate)
    return matches


def _eligible_candidates(
    candidates: list[_Candidate],
    *,
    kinds: set[str],
    roles: list[str],
) -> list[_Candidate]:
    role_set = set(roles)
    eligible = [candidate for candidate in candidates if candidate.kind in kinds and candidate.roles.intersection(role_set)]
    role_priority = {role: index for index, role in enumerate(roles)}
    return sorted(
        eligible,
        key=lambda candidate: (
            min((role_priority[role] for role in candidate.roles if role in role_priority), default=len(role_priority)),
            _candidate_sort_key(candidate),
        ),
    )


def _planned_action(
    route: str,
    candidate: _Candidate,
    *,
    plan_id: str,
    reason_code: str,
    rationale: str,
) -> SocEnrichmentPlannedAction:
    role = _primary_role(candidate)
    payload: dict[str, Any]
    if route == ASSET_LOOKUP_ROUTE:
        payload = {"asset_key": candidate.value}
    elif route == ASSET_LOCATE_ROUTE:
        payload = {
            "asset_key": candidate.value,
            "asset_type": _ASSET_TYPES[candidate.kind],
            "role": role,
        }
        if candidate.kind == "user" and role == "um_account":
            payload["um"] = candidate.value
    elif route == THREAT_INTEL_ROUTE:
        payload = {"ip": candidate.normalized_value}
    elif route == SECURITY_TAG_ROUTE:
        payload = {
            "entity_key": candidate.normalized_value,
            "entity_type": candidate.kind,
        }
    else:  # pragma: no cover - policy validation and caller dispatch guard this branch
        raise ValueError(f"unsupported enrichment route: {route}")

    deduplication_key = stable_hash(
        {
            "route": route,
            "entity_kind": candidate.kind,
            "entity_key": candidate.normalized_value,
        }
    )
    action_id = f"EPA-{stable_hash({'plan_id': plan_id, 'key': deduplication_key})[:12].upper()}"
    conflict_note = f" Role semantics remain conflicted for {', '.join(sorted(candidate.conflicted_roles))}; the lookup must not choose a response target." if candidate.conflicted_roles else ""
    return SocEnrichmentPlannedAction(
        action_id=action_id,
        route=route,
        action=route,
        reason_code=reason_code,
        rationale=f"{rationale}{conflict_note}",
        payload=payload,
        entity_key=candidate.normalized_value,
        entity_kind=candidate.kind,
        entity_role=role,
        evidence_refs=sorted(candidate.evidence_refs),
        deduplication_key=deduplication_key,
    )


def _skipped_candidate(
    route: str,
    candidate: _Candidate,
    reason_code: SocEnrichmentSkipReason,
    rationale: str,
) -> SocEnrichmentSkippedCandidate:
    return SocEnrichmentSkippedCandidate(
        route=route,
        entity_key=candidate.normalized_value,
        entity_kind=candidate.kind,
        entity_role=_primary_role(candidate),
        reason_code=reason_code,
        rationale=rationale,
        evidence_refs=sorted(candidate.evidence_refs),
    )


def _no_entity_skip(route: str) -> SocEnrichmentSkippedCandidate:
    return SocEnrichmentSkippedCandidate(
        route=route,
        reason_code=SocEnrichmentSkipReason.NO_ELIGIBLE_ENTITY,
        rationale="no provenance-backed entity matched the route's configured kinds and roles",
    )


def _normalize_entity(value: str, *, kind: str) -> str:
    stripped = value.strip()
    if not stripped:
        raise ValueError("entity value cannot be empty")
    if len(stripped) > _MAX_ENTITY_KEY_LENGTH:
        raise ValueError("entity value exceeds the enrichment routing boundary")
    if kind == "ip":
        return str(ip_address(stripped))
    if kind == "domain":
        return stripped.rstrip(".").lower()
    return stripped.casefold()


def _primary_role(candidate: _Candidate) -> str | None:
    return sorted(candidate.roles)[0] if candidate.roles else None


def _candidate_sort_key(candidate: _Candidate) -> tuple[str, str]:
    return candidate.kind, candidate.normalized_value


def _input_projection(
    run: AnalysisRun,
    *,
    policy: SocEnrichmentPolicy,
    tenant_id: str | None,
    candidates: list[_Candidate],
    candidate_skips: list[SocEnrichmentSkippedCandidate],
) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "alert_id": run.alert_id,
        "run_status": run.status.value,
        "tenant_id": tenant_id,
        "candidates": [
            {
                "value": item.value,
                "normalized_value": item.normalized_value,
                "kind": item.kind,
                "roles": sorted(item.roles),
                "evidence_refs": sorted(item.evidence_refs),
                "conflicted_roles": sorted(item.conflicted_roles),
            }
            for item in candidates
        ],
        "invalid_candidates": [item.model_dump(mode="json") for item in candidate_skips],
        "policy": policy.model_dump(mode="json"),
    }


__all__ = [
    "ASSET_LOCATE_ROUTE",
    "ASSET_LOOKUP_ROUTE",
    "SECURITY_TAG_ROUTE",
    "SocEnrichmentPlanner",
    "THREAT_INTEL_ROUTE",
]
