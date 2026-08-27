"""Build vendor-neutral authorization queries from canonical SOC contracts."""

from __future__ import annotations

import html
import re
from datetime import datetime
from ipaddress import ip_address
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from soc_agent.contracts import (
    AlertInput,
    AuthorizationQuery,
    AuthorizationQueryBehavior,
    AuthorizationQueryConflict,
    AuthorizationQuerySubject,
    AuthorizationQueryTarget,
    AuthorizedActivityBehaviorKind,
    AuthorizedActivitySubjectKind,
    AuthorizedActivityTargetKind,
    EntityKind,
    ExtractedEntities,
    FactReconstructionResult,
    RoleResolutionStatus,
)

_SPACE_RE = re.compile(r"\s+")


class AuthorizationQueryBuilder:
    """Project canonical alert facts into a bounded authorization query."""

    def build(
        self,
        alert: AlertInput,
        *,
        entities: ExtractedEntities | None = None,
        fact_reconstruction: FactReconstructionResult | None = None,
        tenant_id: str | None = None,
        environment: str | None = None,
        event_timezone: str | None = None,
    ) -> AuthorizationQuery:
        conflicts: list[AuthorizationQueryConflict] = []
        warnings: list[str] = []
        selected_tenant = tenant_id or alert.tenant_id
        extension_environment = _extension_string(alert, "environment")
        selected_environment = environment or extension_environment

        if tenant_id and alert.tenant_id and tenant_id != alert.tenant_id:
            conflicts.append(
                AuthorizationQueryConflict(
                    conflict_type="tenant_context_mismatch",
                    reason="caller tenant_id differs from canonical alert tenant_id",
                    evidence_paths=["tenant_id", "request_context.tenant_id"],
                )
            )
        if environment and extension_environment and environment != extension_environment:
            conflicts.append(
                AuthorizationQueryConflict(
                    conflict_type="environment_context_mismatch",
                    reason="caller environment differs from canonical alert environment",
                    evidence_paths=["extensions.environment", "request_context.environment"],
                )
            )

        event_time, unresolved_event_time = _event_time(
            alert.event.event_time,
            event_timezone=event_timezone,
            warnings=warnings,
        )
        _append_canonical_time_policy_warning(alert, warnings)
        subjects = _subject_candidates(alert, entities, fact_reconstruction, warnings=warnings)
        targets = _target_candidates(alert, fact_reconstruction, warnings=warnings)
        behaviors = _behavior_candidates(alert, entities, fact_reconstruction)

        if fact_reconstruction is not None:
            conflicts.extend(_fact_conflicts(fact_reconstruction))

        if not selected_tenant:
            warnings.append("authorization_query_missing_tenant_id")
        if not selected_environment:
            warnings.append("authorization_query_missing_environment")
        if not subjects:
            warnings.append("authorization_query_missing_subject_candidates")
        if not targets:
            warnings.append("authorization_query_missing_target_candidates")
        if not behaviors:
            warnings.append("authorization_query_missing_behavior_candidates")

        return AuthorizationQuery(
            alert_id=alert.alert_id,
            tenant_id=selected_tenant,
            environment=selected_environment,
            event_time=event_time,
            unresolved_event_time=unresolved_event_time,
            subjects=_deduplicate(subjects),
            targets=_deduplicate(targets),
            behaviors=_deduplicate(behaviors),
            conflicts=_deduplicate_conflicts(conflicts),
            warnings=_unique(warnings),
        )


def _event_time(
    value: datetime | None,
    *,
    event_timezone: str | None,
    warnings: list[str],
) -> tuple[datetime | None, str | None]:
    if value is None:
        warnings.append("authorization_event_time_missing")
        return None, None
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value, None
    if not event_timezone:
        warnings.append("authorization_event_time_timezone_missing")
        return None, value.isoformat()
    try:
        timezone = ZoneInfo(event_timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown authorization event timezone: {event_timezone}") from exc
    warnings.append(f"authorization_event_time_timezone_assumed:{event_timezone}")
    return value.replace(tzinfo=timezone), value.isoformat()


def _append_canonical_time_policy_warning(
    alert: AlertInput,
    warnings: list[str],
) -> None:
    policy = alert.extensions.get("event_time_policy")
    if not isinstance(policy, dict) or policy.get("event_time_timezone_assumed") is not True:
        return
    timezone_name = policy.get("naive_timezone")
    if isinstance(timezone_name, str) and timezone_name.strip():
        warnings.append(f"authorization_event_time_timezone_assumed:{timezone_name.strip()}")


def _subject_candidates(
    alert: AlertInput,
    entities: ExtractedEntities | None,
    reconstruction: FactReconstructionResult | None,
    *,
    warnings: list[str],
) -> list[AuthorizationQuerySubject]:
    candidates: list[AuthorizationQuerySubject] = []
    host = alert.entities.host
    user = alert.entities.user

    if host.asset_id:
        candidates.append(_subject(AuthorizedActivitySubjectKind.ASSET_ID, host.asset_id, "entities.host.asset_id", role="host"))
    if host.host_id:
        candidates.append(_subject(AuthorizedActivitySubjectKind.AGENT_ID, host.host_id, "entities.host.host_id", role="host"))
    for field_name, value in (
        ("user_id", user.user_id),
        ("um_account", user.um_account),
        ("username", user.username),
        ("src_user", user.src_user),
    ):
        if value:
            candidates.append(
                _subject(
                    AuthorizedActivitySubjectKind.ACCOUNT_ID,
                    value,
                    f"entities.user.{field_name}",
                    role="account",
                )
            )

    resolutions = _resolutions(reconstruction)
    attacker = resolutions.get("attacker")
    if _usable_resolution(attacker):
        subject_kind = _subject_resolution_kind(attacker.selected_value, alert)
        if subject_kind is not None:
            candidates.append(
                _subject(
                    subject_kind,
                    attacker.selected_value,
                    _resolution_evidence_path(attacker, reconstruction),
                    role="attacker",
                    confidence=attacker.semantic_confidence,
                )
            )
        else:
            warnings.append("authorization_attacker_resolution_type_unavailable")
    else:
        source = resolutions.get("source")
        if source is not None and source.status in {RoleResolutionStatus.OBSERVED, RoleResolutionStatus.CONFIRMED} and source.selected_value:
            subject_kind = _subject_resolution_kind(source.selected_value, alert)
            if subject_kind is not None:
                candidates.append(
                    _subject(
                        subject_kind,
                        source.selected_value,
                        _resolution_evidence_path(source, reconstruction),
                        role="observed_source",
                        confidence=source.semantic_confidence,
                    )
                )
                warnings.append("authorization_subject_fell_back_to_observed_source")
            else:
                warnings.append("authorization_source_resolution_type_unavailable")

    if entities is not None:
        for mention in entities.mentions:
            if mention.kind is EntityKind.USER:
                candidates.append(
                    _subject(
                        AuthorizedActivitySubjectKind.ACCOUNT_ID,
                        mention.value,
                        mention.evidence_path or "extracted_entities.mentions",
                        role=mention.role,
                        confidence=mention.confidence,
                    )
                )
            elif mention.kind is EntityKind.IP and mention.role in {"attacker", "source", "initiator"}:
                candidates.append(
                    _subject(
                        AuthorizedActivitySubjectKind.IP,
                        mention.value,
                        mention.evidence_path or "extracted_entities.mentions",
                        role=mention.role,
                        confidence=mention.confidence,
                    )
                )
    return candidates


def _target_candidates(
    alert: AlertInput,
    reconstruction: FactReconstructionResult | None,
    *,
    warnings: list[str],
) -> list[AuthorizationQueryTarget]:
    candidates: list[AuthorizationQueryTarget] = []
    host = alert.entities.host
    network = alert.entities.network
    http = alert.entities.http

    if host.asset_id:
        candidates.append(_target(AuthorizedActivityTargetKind.ASSET_ID, host.asset_id, "entities.host.asset_id", role="host"))
    for index, value in enumerate(host.ip_addresses):
        candidates.append(
            _target(
                AuthorizedActivityTargetKind.IP,
                value,
                f"entities.host.ip_addresses[{index}]",
                role="host",
            )
        )

    resolutions = _resolutions(reconstruction)
    resolved_target = False
    for role in ("impacted_asset", "victim"):
        resolution = resolutions.get(role)
        if _usable_resolution(resolution):
            target_kind = _target_resolution_kind(resolution.selected_value, alert)
            if target_kind is not None:
                candidates.append(
                    _target(
                        target_kind,
                        resolution.selected_value,
                        _resolution_evidence_path(resolution, reconstruction),
                        role=role,
                        confidence=resolution.semantic_confidence,
                    )
                )
                resolved_target = True
            else:
                warnings.append(f"authorization_{role}_resolution_type_unavailable")
    if not resolved_target:
        destination = resolutions.get("destination")
        if destination is not None and destination.status in {RoleResolutionStatus.OBSERVED, RoleResolutionStatus.CONFIRMED} and destination.selected_value:
            target_kind = _target_resolution_kind(destination.selected_value, alert)
            if target_kind is not None:
                candidates.append(
                    _target(
                        target_kind,
                        destination.selected_value,
                        _resolution_evidence_path(destination, reconstruction),
                        role="observed_destination",
                        confidence=destination.semantic_confidence,
                    )
                )
                warnings.append("authorization_target_fell_back_to_observed_destination")
            else:
                warnings.append("authorization_destination_resolution_type_unavailable")

    for path, value in (
        ("entities.network.domain", network.domain),
        ("entities.http.host", http.host),
    ):
        if value:
            candidates.append(_target(AuthorizedActivityTargetKind.DOMAIN, value, path, role="service_target"))
    return candidates


def _behavior_candidates(
    alert: AlertInput,
    entities: ExtractedEntities | None,
    reconstruction: FactReconstructionResult | None,
) -> list[AuthorizationQueryBehavior]:
    candidates: list[AuthorizationQueryBehavior] = []
    process = alert.entities.process

    if reconstruction is not None:
        for index, scenario in enumerate(reconstruction.scenario_hypotheses):
            candidates.append(
                _behavior(
                    AuthorizedActivityBehaviorKind.SCENARIO,
                    scenario.scenario_type,
                    scenario.evidence_paths[0] if scenario.evidence_paths else f"fact_reconstruction.scenario_hypotheses[{index}]",
                    confidence=scenario.confidence,
                )
            )

    for path, value in (
        ("entities.process.process_name", process.process_name),
        ("entities.process.parent_process_name", process.parent_process_name),
    ):
        if value:
            candidates.append(_behavior(AuthorizedActivityBehaviorKind.PROCESS, value, path))
    if process.parent_process_name and process.process_name:
        candidates.append(
            _behavior(
                AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                _process_signature([process.parent_process_name, process.process_name]),
                "entities.process.parent_process_name->entities.process.process_name",
            )
        )
    for path, value in (
        ("entities.process.command_line", process.command_line),
        ("entities.process.parent_command_line", process.parent_command_line),
    ):
        if value:
            candidates.append(_behavior(AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE, _normalized_text(value), path))

    for observation_index, observation in enumerate(process.observations):
        names = [node.process_name for node in observation.nodes]
        for node_index, node in enumerate(observation.nodes):
            candidates.append(
                _behavior(
                    AuthorizedActivityBehaviorKind.PROCESS,
                    node.process_name,
                    f"{observation.evidence_path}#process.nodes[{node_index}]",
                )
            )
        if len(names) >= 2:
            candidates.append(
                _behavior(
                    AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                    _process_signature(names),
                    observation.evidence_path,
                )
            )
            for index in range(len(names) - 1):
                candidates.append(
                    _behavior(
                        AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                        _process_signature(names[index : index + 2]),
                        observation.evidence_path,
                    )
                )

    if alert.entities.network.protocol:
        candidates.append(
            _behavior(
                AuthorizedActivityBehaviorKind.PROTOCOL,
                alert.entities.network.protocol,
                "entities.network.protocol",
            )
        )
    for index, technique in enumerate(alert.classification.technique):
        candidates.append(
            _behavior(
                AuthorizedActivityBehaviorKind.TECHNIQUE,
                technique,
                f"classification.technique[{index}]",
            )
        )
    for path, value in (
        ("detection.rule_code", alert.detection.rule_code),
        ("detection.rule_name", alert.detection.rule_name),
        ("detection.detection_key", alert.detection.detection_key),
        ("detection.rule_category", alert.detection.rule_category),
    ):
        if value:
            candidates.append(_behavior(AuthorizedActivityBehaviorKind.DETECTION_ALIAS, value, path))

    if entities is not None:
        for mention in entities.mentions:
            path = mention.evidence_path or "extracted_entities.mentions"
            if mention.kind is EntityKind.PROCESS:
                candidates.append(_behavior(AuthorizedActivityBehaviorKind.PROCESS, mention.value, path, confidence=mention.confidence))
            elif mention.kind is EntityKind.BEHAVIOR:
                candidates.append(
                    _behavior(
                        AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                        mention.value,
                        path,
                        confidence=mention.confidence,
                    )
                )
            elif mention.kind is EntityKind.MITRE:
                candidates.append(_behavior(AuthorizedActivityBehaviorKind.TECHNIQUE, mention.value, path, confidence=mention.confidence))
            elif mention.kind in {EntityKind.RULE, EntityKind.RULE_CODE, EntityKind.RULE_NAME}:
                candidates.append(
                    _behavior(
                        AuthorizedActivityBehaviorKind.DETECTION_ALIAS,
                        mention.value,
                        path,
                        confidence=mention.confidence,
                    )
                )
    return candidates


def _fact_conflicts(reconstruction: FactReconstructionResult) -> list[AuthorizationQueryConflict]:
    conflicts = [
        AuthorizationQueryConflict(
            conflict_type=report.conflict_type,
            reason=report.description,
            evidence_paths=report.involved_fields,
            blocks_authorization=report.blocks_automation,
        )
        for report in reconstruction.conflict_reports
        if report.blocks_automation
    ]
    for resolution in reconstruction.role_resolutions:
        if resolution.status is RoleResolutionStatus.CONFLICTED:
            conflicts.append(
                AuthorizationQueryConflict(
                    conflict_type=f"role_resolution_conflict:{resolution.role}",
                    reason=resolution.rationale,
                    evidence_paths=[claim.evidence_path for claim in reconstruction.role_claims if claim.claim_id in {*resolution.supporting_claim_ids, *resolution.contradicting_claim_ids}],
                )
            )
    return conflicts


def _resolutions(reconstruction: FactReconstructionResult | None) -> dict[str, object]:
    if reconstruction is None:
        return {}
    return {item.role: item for item in reconstruction.role_resolutions}


def _usable_resolution(resolution) -> bool:
    return bool(resolution is not None and resolution.selected_value and resolution.status not in {RoleResolutionStatus.CONFLICTED, RoleResolutionStatus.UNRESOLVED})


def _resolution_evidence_path(resolution, reconstruction: FactReconstructionResult | None) -> str:
    if reconstruction is not None:
        claims = {item.claim_id: item for item in reconstruction.role_claims}
        for claim_id in resolution.supporting_claim_ids:
            if claim_id in claims:
                return claims[claim_id].evidence_path
    return f"fact_reconstruction.role_resolutions.{resolution.role}"


def _subject(kind, value, evidence_path, *, role=None, confidence=1.0) -> AuthorizationQuerySubject:
    return AuthorizationQuerySubject(
        kind=kind,
        value=value,
        evidence_path=evidence_path,
        role=role,
        semantic_confidence=confidence,
    )


def _target(kind, value, evidence_path, *, role=None, confidence=1.0) -> AuthorizationQueryTarget:
    return AuthorizationQueryTarget(
        kind=kind,
        value=value,
        evidence_path=evidence_path,
        role=role,
        semantic_confidence=confidence,
    )


def _behavior(kind, value, evidence_path, *, confidence=1.0) -> AuthorizationQueryBehavior:
    return AuthorizationQueryBehavior(
        kind=kind,
        value=value,
        evidence_path=evidence_path,
        semantic_confidence=confidence,
    )


def _process_signature(names: list[str]) -> str:
    return "->".join(_normalized_text(name) for name in names)


def _normalized_text(value: str) -> str:
    return _SPACE_RE.sub(" ", html.unescape(value)).strip().casefold()


def _extension_string(alert: AlertInput, key: str) -> str | None:
    value = alert.extensions.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _subject_resolution_kind(
    value: str,
    alert: AlertInput,
) -> AuthorizedActivitySubjectKind | None:
    if _is_ip(value):
        return AuthorizedActivitySubjectKind.IP
    if value == alert.entities.host.asset_id:
        return AuthorizedActivitySubjectKind.ASSET_ID
    if value == alert.entities.host.host_id:
        return AuthorizedActivitySubjectKind.AGENT_ID
    return None


def _target_resolution_kind(
    value: str,
    alert: AlertInput,
) -> AuthorizedActivityTargetKind | None:
    if _is_ip(value):
        return AuthorizedActivityTargetKind.IP
    if value == alert.entities.host.asset_id:
        return AuthorizedActivityTargetKind.ASSET_ID
    return None


def _is_ip(value: str) -> bool:
    try:
        ip_address(value)
    except ValueError:
        return False
    return True


def _deduplicate(items: list):
    result = []
    seen: set[tuple[str, str, str | None]] = set()
    for item in items:
        key = (item.kind.value, _normalized_text(item.value), item.namespace)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _deduplicate_conflicts(items: list[AuthorizationQueryConflict]) -> list[AuthorizationQueryConflict]:
    result: list[AuthorizationQueryConflict] = []
    seen: set[tuple[str, str]] = set()
    for item in items:
        key = (item.conflict_type, item.reason)
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = ["AuthorizationQueryBuilder"]
