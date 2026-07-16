"""Bounded context bridges for SOC interactive agent surfaces."""

from __future__ import annotations

import json
from typing import Any

from soc_agent.contracts import (
    AlertSummary,
    InvestigationContext,
    InvestigationEvidence,
    ServiceRequestContext,
    SimilarAlertMatch,
    SocLeadAgentReviewContextArtifact,
    SocSkillContext,
)
from soc_agent.skills import SocSkillResolver, build_soc_skill_context
from soc_agent.utils.hashing import stable_hash

_MAX_ENTITY_KEYS = 20
_MAX_EVIDENCE_ITEMS = 5
_MAX_SIMILAR_ALERTS = 5
_MAX_FACT_ITEMS = 10
_MAX_ACTION_EVIDENCE_ITEMS = 5
_MAX_AUTHORIZATION_ENRICHMENT_ITEMS = 5
_MAX_EXTERNAL_DISPOSITION_ITEMS = 5
_MAX_MEMORY_CANDIDATE_ITEMS = 5
_MAX_RELEVANT_MEMORY_ITEMS = 5

_LEAD_AGENT_CONTEXT_INSTRUCTIONS = [
    "Treat this artifact as bounded SOC review context supplied by SOC services.",
    "Do not read SOC repositories directly from the lead agent.",
    "Do not execute response actions from this context.",
    "High-risk actions must be proposed as bounded action requests and routed through SOC approval.",
    "Before proposing a duplicate read-only lookup, inspect action_evidence and reuse fresh matching results.",
    "Treat external_dispositions as operator feedback; pending memory candidates are not confirmed reusable knowledge.",
    "Treat authorization_enrichments as read-only shadow context; they never replace detection truth or authorize disposition by themselves.",
    "Treat memory_candidates as reviewable proposals only; do not use them as confirmed facts or active lessons.",
    "Treat relevant_memories as retrieval-policy-approved context only; they can inform analysis but never bypass evidence or approval.",
    "If evidence conflicts, explain the conflict and ask for review instead of forcing a conclusion.",
]


def skill_context_from_investigation_context(context: InvestigationContext) -> SocSkillContext | None:
    """Return compact skill context for one investigation context."""
    request = context.run.llm_analysis_request
    if request is not None:
        if request.skill_context.selected_skills:
            return request.skill_context
        return build_soc_skill_context(SocSkillResolver().resolve_for_analysis_request(request))
    if context.summary is not None:
        return build_soc_skill_context(SocSkillResolver().resolve_for_summary(context.summary))
    return None


def build_lead_agent_review_context_artifact(
    context: InvestigationContext,
    *,
    request_context: ServiceRequestContext | None = None,
) -> SocLeadAgentReviewContextArtifact:
    """Build a redacted, hashable review context artifact for DeerFlow lead_agent."""
    skill_context = skill_context_from_investigation_context(context)
    skill_payload = skill_context.model_dump(mode="json", exclude_none=True) if skill_context is not None else None
    review_payload = _review_payload(context)
    analysis_payload = _analysis_payload(context)
    fact_payload = _fact_context_payload(context)
    summary_payload = _summary_payload(context.summary)
    similar_payload = [_similar_alert_payload(match) for match in context.similar_alerts[:_MAX_SIMILAR_ALERTS]]
    action_evidence_payload = [_action_evidence_payload(item) for item in context.action_evidence[:_MAX_ACTION_EVIDENCE_ITEMS]]
    authorization_enrichment_payload = [_authorization_enrichment_payload(item) for item in context.authorization_enrichments[:_MAX_AUTHORIZATION_ENRICHMENT_ITEMS]]
    external_disposition_payload = [_external_disposition_payload(item) for item in context.external_dispositions[:_MAX_EXTERNAL_DISPOSITION_ITEMS]]
    memory_candidate_payload = [_memory_candidate_payload(item) for item in context.memory_candidates[:_MAX_MEMORY_CANDIDATE_ITEMS]]
    relevant_memory_payload = _relevant_memory_payload(context.relevant_memories)
    investigation_view_payload = _investigation_view_payload(context)
    hash_payload = {
        "queue_id": context.queue_item.queue_id,
        "run_id": context.run.run_id,
        "alert_id": context.run.alert_id,
        "review": review_payload,
        "analysis": analysis_payload,
        "fact_context": fact_payload,
        "summary": summary_payload,
        "similar_alerts": similar_payload,
        "action_evidence": action_evidence_payload,
        "authorization_enrichments": authorization_enrichment_payload,
        "external_dispositions": external_disposition_payload,
        "memory_candidates": memory_candidate_payload,
        "relevant_memories": relevant_memory_payload,
        "investigation_view": investigation_view_payload,
        "skill_context": skill_payload,
        "instructions": _LEAD_AGENT_CONTEXT_INSTRUCTIONS,
    }
    return SocLeadAgentReviewContextArtifact(
        queue_id=context.queue_item.queue_id,
        run_id=context.run.run_id,
        alert_id=context.run.alert_id,
        context_hash=stable_hash(hash_payload),
        skill_context_hash=stable_hash(skill_payload) if skill_payload is not None else None,
        actor=request_context.actor if request_context is not None else None,
        review=review_payload,
        analysis=analysis_payload,
        fact_context=fact_payload,
        summary=summary_payload,
        similar_alerts=similar_payload,
        action_evidence=action_evidence_payload,
        authorization_enrichments=authorization_enrichment_payload,
        external_dispositions=external_disposition_payload,
        memory_candidates=memory_candidate_payload,
        relevant_memories=relevant_memory_payload,
        investigation_view=investigation_view_payload,
        skill_context=skill_context,
        instructions=list(_LEAD_AGENT_CONTEXT_INSTRUCTIONS),
    )


def render_lead_agent_review_context_message(
    *,
    message: str,
    artifact: SocLeadAgentReviewContextArtifact,
) -> str:
    """Prefix the operator message with a bounded review context artifact."""
    artifact_payload = artifact.model_dump(mode="json", exclude_none=True)
    artifact_json = json.dumps(artifact_payload, ensure_ascii=True, sort_keys=True, indent=2)
    operator_message = message.strip() or "Continue the SOC investigation."
    return (
        "Use the following bounded SOC review context artifact. "
        "It is supplied by SOC services and is the only review context available for this turn.\n"
        "<soc_review_context_artifact>\n"
        f"{artifact_json}\n"
        "</soc_review_context_artifact>\n\n"
        "Operator message:\n"
        f"{operator_message}"
    )


def _review_payload(context: InvestigationContext) -> dict[str, Any]:
    item = context.queue_item
    return {
        "queue_id": item.queue_id,
        "status": item.status.value,
        "priority": item.priority.value,
        "reason": item.reason,
        "source_type": item.source_type.value,
        "source_system": item.source_system,
        "rule_code": item.rule_code,
        "rule_name": item.rule_name,
        "severity": item.severity,
        "category": item.category,
        "verdict": item.verdict.value if item.verdict is not None else None,
        "confidence": item.confidence,
        "entity_keys": item.entity_keys[:_MAX_ENTITY_KEYS],
        "summary": item.summary,
    }


def _analysis_payload(context: InvestigationContext) -> dict[str, Any]:
    run = context.run
    payload: dict[str, Any] = {
        "run_id": run.run_id,
        "alert_id": run.alert_id,
        "status": run.status.value,
        "pipeline_version": run.pipeline_version,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "input_hash": run.input_hash,
        "replay_of_run_id": run.replay_of_run_id,
    }
    if run.analysis is not None:
        payload["analysis"] = {
            "verdict": run.analysis.verdict.value,
            "confidence": run.analysis.confidence,
            "summary": run.analysis.summary,
            "reason": run.analysis.reason,
            "recommended_action": run.analysis.recommended_action,
            "evidence": [item.model_dump(mode="json", exclude_none=True) for item in run.analysis.evidence[:_MAX_EVIDENCE_ITEMS]],
        }
    if run.decision is not None:
        payload["decision"] = {
            "verdict": run.decision.verdict.value,
            "confidence": run.decision.confidence,
            "suggested_action": run.decision.suggested_action,
            "needs_review": run.decision.needs_review,
            "reason": run.decision.reason,
            "automation_allowed": run.decision.automation_allowed,
        }
    return payload


def _fact_context_payload(context: InvestigationContext) -> dict[str, Any]:
    run = context.run
    request = run.llm_analysis_request
    reconstruction = run.fact_reconstruction
    payload: dict[str, Any] = {}
    if request is not None:
        payload.update(
            {
                "primary_evidence_path": request.primary_evidence_path,
                "conflict_count": request.conflict_count,
                "conflict_types": request.conflict_types,
                "warnings": request.warnings[:_MAX_FACT_ITEMS],
            }
        )
    if reconstruction is not None:
        payload["selected_input_path"] = reconstruction.selected_input_path
        payload["selected_input_available"] = reconstruction.selected_input_available
        payload["conflict_reports"] = [item.model_dump(mode="json", exclude_none=True) for item in reconstruction.conflict_reports[:_MAX_FACT_ITEMS]]
        payload["scenario_hypotheses"] = [item.model_dump(mode="json", exclude_none=True) for item in reconstruction.scenario_hypotheses[:_MAX_FACT_ITEMS]]
        payload["role_resolutions"] = [item.model_dump(mode="json", exclude_none=True) for item in reconstruction.role_resolutions[:_MAX_FACT_ITEMS]]
        payload["warnings"] = payload.get("warnings") or reconstruction.warnings[:_MAX_FACT_ITEMS]
    return payload


def _summary_payload(summary: AlertSummary | None) -> dict[str, Any] | None:
    if summary is None:
        return None
    return {
        "run_id": summary.run_id,
        "alert_id": summary.alert_id,
        "source_type": summary.source_type.value,
        "source_system": summary.source_system,
        "detection_key": summary.detection_key,
        "rule_code": summary.rule_code,
        "rule_name": summary.rule_name,
        "severity": summary.severity,
        "category": summary.category,
        "entity_keys": summary.entity_keys[:_MAX_ENTITY_KEYS],
        "status": summary.status.value,
        "verdict": summary.verdict.value if summary.verdict is not None else None,
        "confidence": summary.confidence,
        "needs_review": summary.needs_review,
        "summary": summary.summary,
        "recommended_action": summary.recommended_action,
        "input_hash": summary.input_hash,
    }


def _similar_alert_payload(match: SimilarAlertMatch) -> dict[str, Any]:
    summary = match.summary
    return {
        "run_id": summary.run_id,
        "alert_id": summary.alert_id,
        "score": match.score,
        "matched_reasons": match.matched_reasons,
        "source_type": summary.source_type.value,
        "rule_code": summary.rule_code,
        "severity": summary.severity,
        "verdict": summary.verdict.value if summary.verdict is not None else None,
        "confidence": summary.confidence,
        "summary": summary.summary,
    }


def _action_evidence_payload(evidence: InvestigationEvidence) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "evidence_id": evidence.evidence_id,
        "source_type": evidence.source_type,
        "route": evidence.route,
        "action": evidence.action,
        "status": evidence.status,
        "message": evidence.message,
        "queue_id": evidence.queue_id,
        "run_id": evidence.run_id,
        "alert_id": evidence.alert_id,
        "thread_id": evidence.thread_id,
        "source_proposal_id": evidence.source_proposal_id,
        "context_hash": evidence.context_hash,
        "created_at": evidence.created_at.isoformat(),
        "result_payload": evidence.result_payload,
    }
    if evidence.actor is not None:
        payload["actor"] = evidence.actor.model_dump(mode="json", exclude_none=True)
    return {key: value for key, value in payload.items() if value is not None}


def _authorization_enrichment_payload(record: Any) -> dict[str, Any]:
    result = record.match_result
    return {
        "enrichment_id": record.enrichment_id,
        "run_id": record.run_id,
        "alert_id": record.alert_id,
        "queue_id": record.queue_id,
        "query_id": record.query.query_id,
        "query_hash": record.query_hash,
        "tenant_id": record.query.tenant_id,
        "environment": record.query.environment,
        "event_time": (record.query.event_time.isoformat() if record.query.event_time is not None else None),
        "status": result.status.value,
        "matcher_policy_version": record.matcher_policy_version,
        "matched_fact_refs": [item.model_dump(mode="json", exclude_none=True) for item in result.matched_fact_refs[:10]],
        "candidate_fact_refs": [item.model_dump(mode="json", exclude_none=True) for item in result.candidate_fact_refs[:10]],
        "matched_dimensions": [item.value for item in result.matched_dimensions],
        "missing_dimensions": [item.value for item in result.missing_dimensions],
        "out_of_scope_dimensions": [item.value for item in result.out_of_scope_dimensions],
        "evidence_refs": result.evidence_refs[:20],
        "warnings": result.warnings[:10],
        "replay_of_enrichment_id": record.replay_of_enrichment_id,
        "created_at": record.created_at.isoformat(),
        "shadow_only": True,
        "decision_impact": "none",
    }


def _external_disposition_payload(record: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "disposition_id": record.disposition_id,
        "external_system": record.event.external_system,
        "external_case_id": record.event.external_case_id,
        "external_status": record.event.external_status,
        "canonical_status": record.canonical_status.value,
        "apply_status": record.apply_status.value,
        "apply_reason": record.apply_reason,
        "external_reason": record.event.external_reason,
        "matched_by": record.matched_by,
        "audit_id": record.audit_id,
        "correction_id": record.correction_id,
        "memory_candidate_id": record.memory_candidate_id,
        "created_at": record.created_at.isoformat(),
        "external_updated_at": record.event.updated_at.isoformat(),
        "external_tags": record.event.external_tags,
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _memory_candidate_payload(candidate: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "candidate_id": candidate.candidate_id,
        "candidate_type": candidate.candidate_type.value,
        "target_artifact": candidate.target_artifact.value,
        "status": candidate.status.value,
        "summary": candidate.summary,
        "content": candidate.content,
        "tenant_scope": candidate.tenant_scope,
        "tenant_id": candidate.tenant_id,
        "source": candidate.source.model_dump(mode="json", exclude_none=True),
        "evidence_refs": candidate.evidence_refs,
        "confidence": candidate.confidence,
        "facets": candidate.facets,
        "decision_impact": candidate.decision_impact.value,
        "runtime_decision_allowed": candidate.runtime_decision_allowed,
        "review_owner": candidate.review_owner,
        "labels": candidate.labels,
        "created_at": candidate.created_at.isoformat(),
        "updated_at": candidate.updated_at.isoformat(),
    }
    return {key: value for key, value in payload.items() if value not in (None, [], {})}


def _relevant_memory_payload(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    matches = []
    for match in result.matches[:_MAX_RELEVANT_MEMORY_ITEMS]:
        record = match.record
        matches.append(
            {
                "memory_id": match.memory_id,
                "version": match.version,
                "memory_type": record.memory_type.value,
                "target_artifact": record.target_artifact.value,
                "score": match.score,
                "match_reasons": match.match_reasons,
                "matched_facets": match.matched_facets,
                "token_estimate": match.token_estimate,
                "content_hash": match.content_hash,
                "facets_hash": match.facets_hash,
                "summary": record.summary,
                "content": record.content,
                "evidence_refs": record.evidence_refs,
                "tenant_scope": record.tenant_scope,
                "tenant_id": record.tenant_id,
            }
        )
    return {
        "policy_version": result.policy_version,
        "returned_count": result.returned_count,
        "total_candidate_count": result.total_candidate_count,
        "total_token_estimate": result.total_token_estimate,
        "max_tokens": result.max_tokens,
        "skipped_retrieval_disabled": result.skipped_retrieval_disabled,
        "skipped_status": result.skipped_status,
        "skipped_expired": result.skipped_expired,
        "skipped_below_min_score": result.skipped_below_min_score,
        "matches": matches,
    }


def _investigation_view_payload(context: InvestigationContext) -> dict[str, Any] | None:
    view = context.investigation_view
    if view is None:
        return None
    timeline = []
    for item in view.evidence_timeline[:_MAX_EVIDENCE_ITEMS]:
        item_payload = item.model_dump(mode="json", exclude_none=True)
        item_payload.pop("item_id", None)
        timeline.append(item_payload)
    return {
        "runtime_verdict": view.runtime_verdict.value if view.runtime_verdict is not None else None,
        "runtime_confidence": view.runtime_confidence,
        "needs_review": view.needs_review,
        "automation_allowed": view.automation_allowed,
        "primary_summary": view.primary_summary,
        "primary_reason": view.primary_reason,
        "counts": view.counts,
        "boundary_notes": view.boundary_notes,
        "timeline": timeline,
    }
