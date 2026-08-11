"""Deterministic evaluator for versioned tenant disposition policies."""

from __future__ import annotations

from datetime import UTC, datetime
from fnmatch import fnmatchcase
from ipaddress import ip_address, ip_network

from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    ActorType,
    AnalysisRun,
    AuthorizationMatchResult,
    EntrySurface,
    SocDetectionTruthSnapshot,
    SocOperationalDisposition,
    TenantDispositionPolicy,
    TenantDispositionRule,
    TenantNetworkScope,
    TenantPolicyAdvisorResult,
    TenantPolicyConditionEvaluation,
    TenantPolicyDecision,
    TenantPolicyDecisionSource,
    TenantPolicyEvaluationStatus,
    TenantPolicyMode,
    TenantPolicyResponsePosture,
    TenantPolicyReviewEffect,
    TenantPolicyRuleEvaluation,
    TenantPolicyTimeSource,
)
from soc_agent.utils.hashing import stable_hash


class TenantPolicyNotApplicableError(ValueError):
    """Raised when a policy cannot govern the supplied run and environment."""


def evaluate_tenant_policy(
    policy: TenantDispositionPolicy,
    run: AnalysisRun,
    *,
    environment: str,
    authorization_result: AuthorizationMatchResult | None = None,
    triggered_by: ActorContext | None = None,
    policy_time: datetime | None = None,
    policy_time_source: TenantPolicyTimeSource | None = None,
    evaluated_at: datetime | None = None,
) -> TenantPolicyDecision:
    """Evaluate one persisted Runtime result without mutating it."""

    request = run.llm_analysis_request
    if request is None:
        raise TenantPolicyNotApplicableError("analysis run has no canonical LLM request projection")
    if not request.tenant_id:
        raise TenantPolicyNotApplicableError("analysis run has no tenant_id")
    if request.tenant_id != policy.tenant_id:
        raise TenantPolicyNotApplicableError("analysis run tenant does not match policy tenant")
    if environment.casefold() not in {item.casefold() for item in policy.applicable_environments}:
        raise TenantPolicyNotApplicableError("analysis environment is outside policy scope")

    now = _aware_utc(evaluated_at or datetime.now(UTC))
    effective_time = _aware_utc(policy_time) if policy_time is not None else now
    resolved_time_source = policy_time_source or (TenantPolicyTimeSource.ALERT_EVENT_TIME if policy_time is not None else TenantPolicyTimeSource.EVALUATION_TIME_FALLBACK)
    if policy_time is None and resolved_time_source is not TenantPolicyTimeSource.EVALUATION_TIME_FALLBACK:
        raise ValueError("tenant policy time source requires policy_time")
    if policy_time is not None and resolved_time_source is TenantPolicyTimeSource.EVALUATION_TIME_FALLBACK:
        raise ValueError("evaluation-time fallback cannot carry alert policy_time")
    if policy_time is None and (policy.effective_from is not None or policy.effective_until is not None):
        raise TenantPolicyNotApplicableError("bounded tenant policy requires alert event time")
    if policy.effective_from and effective_time < _aware_utc(policy.effective_from):
        raise TenantPolicyNotApplicableError("tenant policy is not effective yet")
    if policy.effective_until and effective_time >= _aware_utc(policy.effective_until):
        raise TenantPolicyNotApplicableError("tenant policy has expired")

    detection_truth = _detection_truth_snapshot(run)
    networks = tuple(ip_network(value, strict=False) for value in policy.internal_networks)
    evaluations = [
        _evaluate_rule(
            rule,
            run=run,
            detection_truth=detection_truth,
            internal_networks=networks,
            authorization_result=authorization_result,
        )
        for rule in policy.rules
        if rule.enabled
    ]
    matched = sorted(
        (item for item in evaluations if item.matched),
        key=lambda item: (item.priority, item.rule_id),
    )
    policy_hash = stable_hash(policy.model_dump(mode="json"))
    decision_key = stable_hash(
        {
            "run_id": run.run_id,
            "policy_id": policy.policy_id,
            "policy_version": policy.policy_version,
            "policy_hash": policy_hash,
        }
    )
    evaluator = ActorContext(
        actor_id="tenant-policy-evaluator",
        actor_type=ActorType.SYSTEM,
        surface=(triggered_by.surface if triggered_by else EntrySurface.DAEMON),
        roles=["soc_policy_evaluator"],
        auth_source=ActorAuthSource.SYSTEM,
    )
    trigger = triggered_by or evaluator

    if matched:
        selected = next(rule for rule in policy.rules if rule.rule_id == matched[0].rule_id)
        recommendation = selected.recommendation
        return TenantPolicyDecision(
            decision_key=decision_key,
            idempotency_key=f"tenant-policy:{decision_key}",
            run_id=run.run_id,
            alert_id=run.alert_id,
            tenant_id=request.tenant_id,
            environment=environment,
            policy_id=policy.policy_id,
            policy_version=policy.policy_version,
            policy_hash=policy_hash,
            policy_owner=policy.owner,
            policy_source_ref=policy.source_ref,
            policy_change_reason=policy.change_reason,
            policy_reviewed_by=policy.reviewed_by,
            policy_reviewed_at=policy.reviewed_at,
            policy_time=effective_time,
            policy_time_source=resolved_time_source,
            evaluation_status=TenantPolicyEvaluationStatus.MATCHED,
            decision_source=TenantPolicyDecisionSource.DETERMINISTIC_RULE,
            selected_rule_id=selected.rule_id,
            rule_evaluations=evaluations,
            detection_truth=detection_truth,
            runtime_suggested_action=(run.decision.suggested_action if run.decision else None),
            authorization_status=(authorization_result.status if authorization_result else None),
            authorization_query_id=(authorization_result.query_id if authorization_result else None),
            policy_mode=policy.policy_mode,
            response_posture=recommendation.response_posture,
            recommended_disposition=recommendation.recommended_disposition,
            review_effect=recommendation.review_effect,
            suggested_action=recommendation.suggested_action,
            summary=recommendation.summary,
            rationale=recommendation.rationale,
            manual_checks=recommendation.manual_checks,
            evaluated_by=evaluator,
            triggered_by=trigger,
            created_at=now,
            **_application_fields(
                policy_mode=policy.policy_mode,
                review_effect=recommendation.review_effect,
                recommended_disposition=recommendation.recommended_disposition,
            ),
        )

    return TenantPolicyDecision(
        decision_key=decision_key,
        idempotency_key=f"tenant-policy:{decision_key}",
        run_id=run.run_id,
        alert_id=run.alert_id,
        tenant_id=request.tenant_id,
        environment=environment,
        policy_id=policy.policy_id,
        policy_version=policy.policy_version,
        policy_hash=policy_hash,
        policy_owner=policy.owner,
        policy_source_ref=policy.source_ref,
        policy_change_reason=policy.change_reason,
        policy_reviewed_by=policy.reviewed_by,
        policy_reviewed_at=policy.reviewed_at,
        policy_time=effective_time,
        policy_time_source=resolved_time_source,
        evaluation_status=TenantPolicyEvaluationStatus.NO_MATCH,
        decision_source=TenantPolicyDecisionSource.NO_MATCH,
        rule_evaluations=evaluations,
        detection_truth=detection_truth,
        runtime_suggested_action=(run.decision.suggested_action if run.decision else None),
        authorization_status=(authorization_result.status if authorization_result else None),
        authorization_query_id=(authorization_result.query_id if authorization_result else None),
        policy_mode=policy.policy_mode,
        response_posture=TenantPolicyResponsePosture.STANDARD_TRIAGE,
        review_effect=TenantPolicyReviewEffect.PRESERVE,
        summary="No tenant disposition rule matched; retain the Runtime decision and normal review path.",
        rationale=["Tenant policy evaluation completed without an applicable rule."],
        manual_checks=[],
        evaluated_by=evaluator,
        triggered_by=trigger,
        created_at=now,
        shadow_only=policy.policy_mode is TenantPolicyMode.SHADOW,
    )


def apply_tenant_policy_advisor_result(
    decision: TenantPolicyDecision,
    result: TenantPolicyAdvisorResult,
) -> TenantPolicyDecision:
    """Resolve an advisor result into the same append-only decision contract."""

    advice = result.advice
    provenance = result.provenance
    policy_hash = stable_hash(
        {
            "deterministic_policy_hash": decision.policy_hash,
            "advisor_id": provenance.advisor_id,
            "model_name": provenance.model_name,
            "prompt_version": provenance.prompt_version,
            "prompt_hash": provenance.prompt_hash,
            "skill_name": provenance.skill_name,
            "skill_version": provenance.skill_version,
            "skill_hash": provenance.skill_hash,
        }
    )
    decision_key = stable_hash(
        {
            "run_id": decision.run_id,
            "policy_id": decision.policy_id,
            "policy_version": decision.policy_version,
            "policy_hash": policy_hash,
        }
    )
    rule_id = "llm-policy-skill-advice"
    rule_evaluations = [
        *decision.rule_evaluations,
        TenantPolicyRuleEvaluation(
            rule_id=rule_id,
            rule_name=(f"{provenance.skill_name}@{provenance.skill_version} bounded advice"),
            priority=9_000,
            matched=(advice.evaluation_status is TenantPolicyEvaluationStatus.MATCHED),
            conditions=[
                TenantPolicyConditionEvaluation(
                    condition="policy_skill_grounding",
                    matched=(advice.evaluation_status is TenantPolicyEvaluationStatus.MATCHED),
                    evidence_paths=list(advice.evidence_refs),
                    detail=("Policy Skill advice references resolved against the bounded current-alert catalog and Runtime reasoning."),
                )
            ],
        ),
    ]
    updates: dict[str, object] = {
        "decision_key": decision_key,
        "idempotency_key": f"tenant-policy:{decision_key}",
        "policy_hash": policy_hash,
        "decision_source": TenantPolicyDecisionSource.LLM_POLICY_SKILL,
        "evaluation_status": advice.evaluation_status,
        "selected_rule_id": (rule_id if advice.evaluation_status is TenantPolicyEvaluationStatus.MATCHED else None),
        "rule_evaluations": rule_evaluations,
        "response_posture": advice.response_posture,
        "recommended_disposition": advice.recommended_disposition,
        "review_effect": advice.review_effect,
        "suggested_action": advice.suggested_action,
        "summary": advice.summary,
        "rationale": advice.rationale,
        "manual_checks": advice.manual_checks,
        "advisor_advice": advice,
        "advisor_provenance": provenance,
    }
    if advice.evaluation_status is TenantPolicyEvaluationStatus.MATCHED:
        updates.update(
            _application_fields(
                policy_mode=decision.policy_mode,
                review_effect=advice.review_effect,
                recommended_disposition=advice.recommended_disposition,
            )
        )
    payload = decision.model_dump(mode="python")
    payload.update(updates)
    return TenantPolicyDecision.model_validate(payload)


def _evaluate_rule(
    rule: TenantDispositionRule,
    *,
    run: AnalysisRun,
    detection_truth: SocDetectionTruthSnapshot,
    internal_networks: tuple,
    authorization_result: AuthorizationMatchResult | None,
) -> TenantPolicyRuleEvaluation:
    request = run.llm_analysis_request
    assert request is not None
    conditions: list[TenantPolicyConditionEvaluation] = []
    match = rule.match

    if match.source_types:
        actual = request.source.source_type
        conditions.append(
            _condition(
                "source_type",
                actual in match.source_types,
                ["llm_analysis_request.source.source_type"],
                f"actual={actual.value}; allowed={','.join(item.value for item in match.source_types)}",
            )
        )
    if match.detection_verdicts:
        actual = detection_truth.verdict
        conditions.append(
            _condition(
                "detection_verdict",
                actual in match.detection_verdicts,
                [f"{detection_truth.source}.verdict"],
                f"actual={actual.value}; allowed={','.join(item.value for item in match.detection_verdicts)}",
            )
        )
    if match.detection_categories:
        categories = _normalized_values(
            request.classification.category,
            request.detection.rule_category,
        )
        expected = {item.strip().casefold() for item in match.detection_categories}
        conditions.append(
            _condition(
                "detection_category",
                bool(categories & expected),
                [
                    "llm_analysis_request.classification.category",
                    "llm_analysis_request.detection.rule_category",
                ],
                f"actual={','.join(sorted(categories)) or '<missing>'}; allowed={','.join(sorted(expected))}",
            )
        )
    if match.rule_codes:
        actual = request.detection.rule_code
        expected = {item.strip().casefold() for item in match.rule_codes}
        conditions.append(
            _condition(
                "rule_code",
                bool(actual and actual.strip().casefold() in expected),
                ["llm_analysis_request.detection.rule_code"],
                f"actual={actual or '<missing>'}; allowed={','.join(sorted(expected))}",
            )
        )
    if match.detection_keys:
        actual = request.detection.detection_key
        expected = {item.strip().casefold() for item in match.detection_keys}
        conditions.append(
            _condition(
                "detection_key",
                bool(actual and actual.strip().casefold() in expected),
                ["llm_analysis_request.detection.detection_key"],
                f"actual={actual or '<missing>'}; allowed={','.join(sorted(expected))}",
            )
        )
    if match.scenario_keys:
        scenario_keys = {item.scenario_key.strip().casefold() for item in (run.analysis.scenario_assessments if run.analysis else []) if item.scenario_key}
        expected = {item.strip().casefold() for item in match.scenario_keys}
        conditions.append(
            _condition(
                "scenario_key",
                bool(scenario_keys & expected),
                ["analysis.scenario_assessments[].scenario_key"],
                f"actual={','.join(sorted(scenario_keys)) or '<missing>'}; allowed={','.join(sorted(expected))}",
            )
        )
    if match.classification_labels:
        labels = {key.strip().casefold(): (key, value) for key, value in request.classification.labels.items() if key.strip()}
        for configured_key, configured_values in sorted(match.classification_labels.items()):
            normalized_key = configured_key.strip().casefold()
            source_key, actual = labels.get(
                normalized_key,
                (configured_key, None),
            )
            expected = {value.strip().casefold() for value in configured_values}
            conditions.append(
                _condition(
                    f"classification_label:{configured_key}",
                    bool(actual is not None and actual.strip().casefold() in expected),
                    [f"llm_analysis_request.classification.labels.{source_key}"],
                    (f"actual={actual or '<missing>'}; allowed={','.join(sorted(expected))}"),
                )
            )
    if match.classification_labels_excluded:
        labels = {key.strip().casefold(): (key, value) for key, value in request.classification.labels.items() if key.strip()}
        for configured_key, configured_values in sorted(match.classification_labels_excluded.items()):
            normalized_key = configured_key.strip().casefold()
            source_key, actual = labels.get(
                normalized_key,
                (configured_key, None),
            )
            excluded = {value.strip().casefold() for value in configured_values}
            conditions.append(
                _condition(
                    f"classification_label_excluded:{configured_key}",
                    bool(actual is None or actual.strip().casefold() not in excluded),
                    [f"llm_analysis_request.classification.labels.{source_key}"],
                    (f"actual={actual or '<missing>'}; excluded={','.join(sorted(excluded))}"),
                )
            )
    if match.source_ip_scope is not None:
        value = request.canonical_entities.network.source_ip
        conditions.append(
            _network_condition(
                "source_ip_scope",
                value,
                match.source_ip_scope,
                internal_networks,
                "llm_analysis_request.canonical_entities.network.source_ip",
            )
        )
    if match.destination_ip_scope is not None:
        value = request.canonical_entities.network.destination_ip
        conditions.append(
            _network_condition(
                "destination_ip_scope",
                value,
                match.destination_ip_scope,
                internal_networks,
                "llm_analysis_request.canonical_entities.network.destination_ip",
            )
        )
    if match.http_host_globs:
        hosts = _http_hosts(run)
        patterns = [item.strip().casefold() for item in match.http_host_globs]
        host_matched = any(fnmatchcase(host, pattern) for host in hosts for pattern in patterns)
        conditions.append(
            _condition(
                "http_host_glob",
                host_matched,
                [
                    "llm_analysis_request.canonical_entities.http.host",
                    "llm_analysis_request.canonical_entities.http.observations[].host",
                ],
                f"actual={','.join(sorted(hosts)) or '<missing>'}; patterns={','.join(patterns)}",
            )
        )
    if match.http_status_codes:
        statuses = _http_status_codes(run)
        expected = set(match.http_status_codes)
        conditions.append(
            _condition(
                "http_status_code",
                bool(statuses & expected),
                [
                    "llm_analysis_request.canonical_entities.http.status_code",
                    "llm_analysis_request.canonical_entities.http.observations[].status_code",
                ],
                f"actual={','.join(str(item) for item in sorted(statuses)) or '<missing>'}; allowed={','.join(str(item) for item in sorted(expected))}",
            )
        )
    if match.http_status_excluded_codes:
        statuses = _http_status_codes(run)
        excluded = set(match.http_status_excluded_codes)
        conditions.append(
            _condition(
                "http_status_excluded_code",
                bool(statuses) and not bool(statuses & excluded),
                [
                    "llm_analysis_request.canonical_entities.http.status_code",
                    "llm_analysis_request.canonical_entities.http.observations[].status_code",
                ],
                (f"actual={','.join(str(item) for item in sorted(statuses)) or '<missing>'}; excluded={','.join(str(item) for item in sorted(excluded))}"),
            )
        )
    if match.authorization_statuses:
        actual = authorization_result.status if authorization_result else None
        conditions.append(
            _condition(
                "authorization_status",
                actual in match.authorization_statuses if actual else False,
                ["authorization_match_result.status"],
                f"actual={actual.value if actual else '<unavailable>'}; allowed={','.join(item.value for item in match.authorization_statuses)}",
            )
        )

    return TenantPolicyRuleEvaluation(
        rule_id=rule.rule_id,
        rule_name=rule.name,
        priority=rule.priority,
        matched=all(item.matched for item in conditions),
        conditions=conditions,
    )


def _condition(
    name: str,
    matched: bool,
    evidence_paths: list[str],
    detail: str,
) -> TenantPolicyConditionEvaluation:
    return TenantPolicyConditionEvaluation(
        condition=name,
        matched=matched,
        evidence_paths=evidence_paths,
        detail=detail,
    )


def _network_condition(
    name: str,
    value: str | None,
    expected: TenantNetworkScope,
    internal_networks: tuple,
    evidence_path: str,
) -> TenantPolicyConditionEvaluation:
    actual = "missing"
    matched = False
    if value:
        try:
            address = ip_address(value)
        except ValueError:
            actual = "invalid"
        else:
            is_internal = any(address.version == network.version and address in network for network in internal_networks)
            actual = "internal" if is_internal else "external"
            matched = expected is TenantNetworkScope.PRESENT or (expected is TenantNetworkScope.INTERNAL and is_internal) or (expected is TenantNetworkScope.EXTERNAL and not is_internal)
    return _condition(name, matched, [evidence_path], f"value={value or '<missing>'}; actual={actual}; expected={expected.value}")


def _http_hosts(run: AnalysisRun) -> set[str]:
    request = run.llm_analysis_request
    assert request is not None
    http = request.canonical_entities.http
    values = [http.host, *(item.host for item in http.observations)]
    return {value.strip().casefold() for value in values if value and value.strip()}


def _http_status_codes(run: AnalysisRun) -> set[int]:
    request = run.llm_analysis_request
    assert request is not None
    http = request.canonical_entities.http
    values = [http.status_code, *(item.status_code for item in http.observations)]
    return {value for value in values if value is not None and 100 <= value <= 599}


def _application_fields(
    *,
    policy_mode: TenantPolicyMode,
    review_effect: TenantPolicyReviewEffect,
    recommended_disposition: SocOperationalDisposition | None,
) -> dict[str, object]:
    enforced = policy_mode is TenantPolicyMode.ENFORCED
    return {
        "shadow_only": not enforced,
        "requires_human_review": (True if not enforced else review_effect is not TenantPolicyReviewEffect.CLEAR),
        "auto_apply_allowed": enforced,
        "review_queue_impact": review_effect.value if enforced else "none",
        "disposition_impact": ("eligible" if enforced and recommended_disposition is not None else "proposed" if recommended_disposition is not None else "none"),
    }


def _normalized_values(*values: str | None) -> set[str]:
    return {value.strip().casefold() for value in values if value and value.strip()}


def _detection_truth_snapshot(run: AnalysisRun) -> SocDetectionTruthSnapshot:
    if run.decision is not None:
        return SocDetectionTruthSnapshot(
            verdict=run.decision.verdict,
            confidence=run.decision.confidence,
            source="decision",
            decision_policy_version=run.decision.policy_version,
            latest_correction_id=run.corrections[-1].correction_id if run.corrections else None,
        )
    if run.analysis is not None:
        return SocDetectionTruthSnapshot(
            verdict=run.analysis.verdict,
            confidence=run.analysis.confidence,
            source="analysis",
            latest_correction_id=run.corrections[-1].correction_id if run.corrections else None,
        )
    raise TenantPolicyNotApplicableError("analysis run has no detection truth")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("tenant policy timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "TenantPolicyNotApplicableError",
    "apply_tenant_policy_advisor_result",
    "evaluate_tenant_policy",
]
