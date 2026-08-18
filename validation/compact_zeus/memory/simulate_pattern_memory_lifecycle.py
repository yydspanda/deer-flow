#!/usr/bin/env python3
"""Simulate one governed 5+1 Pattern Memory lifecycle without calling an LLM."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.application import build_soc_memory_profile_registry  # noqa: E402
from soc_agent.contracts import (  # noqa: E402
    ActorContext,
    ActorType,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisRun,
    AnalysisRunStatus,
    DecisionConfidenceSource,
    DecisionEvidenceState,
    EntrySurface,
    EvidenceItem,
    MemoryPatternAggregationPolicy,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    ServiceRequestContext,
    SocMemoryBusinessLesson,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryDecisionDirective,
    SocMemoryDecisionEffect,
    SocMemoryReviewEffect,
    Verdict,
)
from soc_agent.core import (  # noqa: E402
    SocAutomationService,
    SocMemoryPatternService,
    SocMemoryService,
)
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    to_sync_database_url,
    upgrade_soc_schema,
)
from soc_agent.memory import (  # noqa: E402
    ConfirmedMemoryAnalysisRequestEnricher,
    memory_query_from_analysis_request,
)
from soc_agent.utils.hashing import stable_hash  # noqa: E402

REPORT_SCHEMA_VERSION = "soc.validation.pattern_memory_lifecycle.v1"
DEFAULT_REVIEW_SUMMARY = (
    "同规则且同行为指纹的反弹 Shell 告警，经模拟运营复核后形成一致的风险经验。"
)
DEFAULT_REVIEW_REASON = "模拟运营复核确认：当前检测规则、反向连接行为指纹和角色事实共同构成可复用的风险模式。"


def load_analysis_run(path: Path) -> AnalysisRun:
    """Load either a Runtime batch item or a direct AnalysisRun JSON object."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    run_payload = payload.get("analysis_run") if isinstance(payload, Mapping) else None
    return AnalysisRun.model_validate(
        run_payload if isinstance(run_payload, Mapping) else payload
    )


def simulate_pattern_memory_lifecycle(
    base_run: AnalysisRun,
    *,
    output_dir: Path,
    tenant_id: str,
    environment: str,
    support_count: int = 5,
    confirmed_verdict: Verdict = Verdict.SUSPICIOUS,
    clear_review_on_match: bool = True,
    reviewed_summary: str = DEFAULT_REVIEW_SUMMARY,
    reviewed_reason: str = DEFAULT_REVIEW_REASON,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run aggregation, review, activation, held-out retrieval, and decision replay."""

    _validate_inputs(
        base_run,
        output_dir=output_dir,
        tenant_id=tenant_id,
        environment=environment,
        support_count=support_count,
        reviewed_summary=reviewed_summary,
        reviewed_reason=reviewed_reason,
    )
    output_dir.mkdir(parents=True, mode=0o700)
    output_dir.chmod(0o700)
    database_path = output_dir / "soc-memory-lifecycle.sqlite"
    if database_path.exists():
        raise ValueError(f"simulation database already exists: {database_path}")
    database_url = f"sqlite+pysqlite:///{database_path.resolve().as_posix()}"
    upgrade_soc_schema(database_url)
    engine = create_engine(to_sync_database_url(database_url))
    repository = SqlAlchemyAlertRepository(
        sessionmaker(bind=engine, expire_on_commit=False)
    )

    reviewed_at = (now or datetime.now(UTC)).astimezone(UTC)
    event_start = (reviewed_at - timedelta(hours=1)).replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    registry = build_soc_memory_profile_registry()
    policy = MemoryPatternAggregationPolicy(
        minimum_support=support_count,
        minimum_distinct_sources=support_count,
        minimum_conclusive_support=support_count,
    )
    pattern_service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=policy,
        profile_registry=registry,
        now_provider=lambda: reviewed_at,
    )
    memory_service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
        now_provider=lambda: reviewed_at,
    )

    plan = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "simulation": True,
        "data_class": MemoryPatternDataClass.SIMULATION.value,
        "base_run_id": base_run.run_id,
        "base_alert_id": base_run.alert_id,
        "tenant_id": tenant_id,
        "environment": environment,
        "support_count": support_count,
        "confirmed_verdict": confirmed_verdict.value,
        "clear_review_on_exact_match": clear_review_on_match,
        "llm_calls": 0,
        "external_actions_allowed": False,
        "database_path": str(database_path.resolve()),
        "boundary": (
            "This isolated simulation proves Memory lifecycle wiring only. "
            "It is not production truth, model-quality evidence, or permission to act."
        ),
    }
    _write_json(output_dir / "00-simulation-plan.json", plan)

    aggregation_items: list[dict[str, Any]] = []
    candidate = None
    try:
        for index in range(1, support_count + 1):
            occurrence = _build_simulation_run(
                base_run,
                index=index,
                event_time=event_start + timedelta(minutes=index),
                tenant_id=tenant_id,
                environment=environment,
                reviewed_verdict=confirmed_verdict,
                reviewed_summary=reviewed_summary,
                reviewed_reason=reviewed_reason,
            )
            result = pattern_service.observe_run(
                occurrence,
                source_type=MemoryPatternSourceType.BATCH_ALERT,
                transport_ref=(
                    f"simulation:{base_run.alert_id}:pattern-occurrence:{index}"
                ),
                environment=environment,
                data_class=MemoryPatternDataClass.SIMULATION,
                context=_service_context(
                    actor_id="pattern-memory-simulator",
                    roles=["soc_batch_runner"],
                    idempotency_key=f"simulate-pattern:{base_run.alert_id}:{index}",
                ),
            )
            candidate = result.candidate or candidate
            aggregation_items.append(
                {
                    "sequence": index,
                    "run_id": occurrence.run_id,
                    "alert_id": occurrence.alert_id,
                    "event_time": (event_start + timedelta(minutes=index)).isoformat(),
                    "result": result.model_dump(mode="json", exclude_none=True),
                }
            )

        _write_json(
            output_dir / "01-pattern-observations.json",
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "simulation": True,
                "items": aggregation_items,
            },
        )
        if candidate is None:
            raise RuntimeError("pattern threshold did not create a review candidate")
        _write_json(
            output_dir / "02-pattern-candidate.json",
            candidate.model_dump(mode="json", exclude_none=True),
        )

        applicability = candidate.applicability
        if applicability is None:
            raise RuntimeError("pattern candidate has no governed applicability")
        review_reason = (
            "Simulation only: reviewer approved this exact PingAn rule and "
            "behavior cohort for a governed 5+1 lifecycle test."
        )
        review_command = SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision=SocMemoryCandidateReviewDecision.CONFIRM,
            reason=review_reason,
            record_lesson=SocMemoryBusinessLesson(
                conclusion=reviewed_summary,
                business_rationale=[reviewed_reason],
                applicability_conditions=[
                    f"Required canonical facet {key}: {', '.join(values)}"
                    for key, values in sorted(applicability.required_facets.items())
                ],
                generalization_boundaries=[
                    "Only dimensions outside the reviewed required facets may vary."
                ],
                invalidation_conditions=[
                    "Any required facet mismatch or current-alert counterevidence invalidates this lesson."
                ],
                handling_guidance=[
                    "Apply the reviewed verdict only after every applicability and directive gate passes."
                ],
            ),
            decision_directive=SocMemoryDecisionDirective(
                effect=SocMemoryDecisionEffect.OVERRIDE,
                target_verdict=confirmed_verdict,
                review_effect=(
                    SocMemoryReviewEffect.CLEAR
                    if clear_review_on_match
                    else SocMemoryReviewEffect.PRESERVE
                ),
                suggested_action="apply reviewed tenant response policy",
                required_facet_keys=sorted(applicability.required_facets),
                rationale=review_reason,
            ),
            activate_retrieval=True,
            activation_valid_until=reviewed_at + timedelta(days=30),
            activation_review_after_days=7,
            metadata={
                "simulation": True,
                "source": "pattern_memory_lifecycle_validation",
            },
        )
        review_result = memory_service.review_candidate(
            review_command,
            context=_user_context(
                actor_id="simulation-memory-reviewer",
                idempotency_key=f"simulate-review:{candidate.candidate_id}",
            ),
        )
        record = review_result.memory_record
        if record is None:
            raise RuntimeError("candidate confirmation did not create a Memory record")
        _write_json(
            output_dir / "03-human-review.json",
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "simulation": True,
                "command": review_command.model_dump(mode="json", exclude_none=True),
                "result": review_result.model_dump(mode="json", exclude_none=True),
            },
        )
        _write_json(
            output_dir / "04-confirmed-memory.json",
            record.model_dump(mode="json", exclude_none=True),
        )

        held_out = _build_held_out_run(
            base_run,
            index=support_count + 1,
            event_time=event_start + timedelta(minutes=support_count + 1),
            tenant_id=tenant_id,
            environment=environment,
        )
        profile = registry.resolve_request(held_out.llm_analysis_request)
        query = memory_query_from_analysis_request(
            held_out.llm_analysis_request,
            profile=profile,
        )
        retrieval = memory_service.find_relevant_records(query)
        enricher = ConfirmedMemoryAnalysisRequestEnricher(
            memory_service,
            profile_registry=registry,
            environment=environment,
        )
        enriched_request = enricher(held_out.llm_analysis_request)
        held_out = held_out.model_copy(
            update={"llm_analysis_request": enriched_request},
            deep=True,
        )
        memory_context = [
            item
            for item in enriched_request.context_catalog
            if item.kind.value == "confirmed_memory"
        ]
        _write_json(
            output_dir / "05-held-out-retrieval.json",
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "simulation": True,
                "held_out_run": held_out.model_dump(mode="json", exclude_none=True),
                "query": query.model_dump(mode="json", exclude_none=True),
                "retrieval": retrieval.model_dump(mode="json", exclude_none=True),
                "projected_memory_context": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in memory_context
                ],
            },
        )

        automation = SocAutomationService(
            repository=repository,
            policy=None,
            environment=environment,
            memory_repository=repository,
            now_provider=lambda: reviewed_at + timedelta(seconds=1),
        ).evaluate(
            held_out,
            context=_service_context(
                actor_id="memory-decision-simulator",
                roles=["soc_automation"],
                idempotency_key=f"simulate-decision:{held_out.run_id}",
            ),
        )
        transitions = repository.list_decision_transitions(
            run_id=held_out.run_id,
            limit=10,
        )
        _write_json(
            output_dir / "06-decision-lineage.json",
            {
                "schema_version": REPORT_SCHEMA_VERSION,
                "simulation": True,
                "evaluation": automation.model_dump(mode="json", exclude_none=True),
                "persisted_transitions": [
                    item.model_dump(mode="json", exclude_none=True)
                    for item in transitions
                ],
            },
        )

        observations = repository.list_memory_pattern_observations(limit=10_000)
        candidates = repository.list_memory_candidates(limit=10_000)
        records = repository.list_memory_records(limit=10_000)
        transition = automation.decision_transition
        checks = {
            "five_distinct_observations": len(observations) == support_count,
            "one_pattern_candidate": len(candidates) == 1,
            "one_confirmed_memory": len(records) == 1,
            "retrieval_enabled": record.retrieval_enabled,
            "held_out_exact_match": len(retrieval.matches) == 1,
            "memory_context_projected": len(memory_context) == 1,
            "base_decision_preserved": (
                transition.before.verdict == base_run.decision.verdict
            ),
            "effective_decision_uses_reviewed_memory": (
                transition.after.verdict == confirmed_verdict
            ),
            "decision_lineage_persisted": len(transitions) == 1,
            "no_action_authorized": automation.authorization is None,
            "no_external_action_executed": automation.execution is None,
        }
        summary = {
            "schema_version": REPORT_SCHEMA_VERSION,
            "status": "passed" if all(checks.values()) else "failed",
            "simulation": True,
            "llm_calls": 0,
            "pattern": {
                "observation_count": len(observations),
                "distinct_source_count": aggregation_items[-1]["result"][
                    "distinct_source_count"
                ],
                "candidate_count": len(candidates),
                "candidate_id": candidate.candidate_id,
                "aggregation_key": aggregation_items[-1]["result"]["observation"][
                    "aggregation_key"
                ],
            },
            "memory": {
                "record_count": len(records),
                "memory_id": record.memory_id,
                "record_version": record.version,
                "retrieval_enabled": record.retrieval_enabled,
                "target_verdict": (
                    record.decision_directive.target_verdict.value
                    if record.decision_directive is not None
                    else None
                ),
            },
            "held_out": {
                "alert_id": held_out.alert_id,
                "retrieval_match_count": len(retrieval.matches),
                "memory_context_refs": [item.context_ref for item in memory_context],
                "base_verdict": transition.before.verdict.value,
                "effective_verdict": transition.after.verdict.value,
                "base_needs_review": transition.before.needs_review,
                "effective_needs_review": transition.after.needs_review,
                "transition_kind": transition.transition_kind.value,
            },
            "checks": checks,
            "database_path": str(database_path.resolve()),
            "artifacts": [
                "00-simulation-plan.json",
                "01-pattern-observations.json",
                "02-pattern-candidate.json",
                "03-human-review.json",
                "04-confirmed-memory.json",
                "05-held-out-retrieval.json",
                "06-decision-lineage.json",
                "summary.json",
                "SUMMARY.md",
            ],
        }
        _write_json(output_dir / "summary.json", summary)
        _write_summary_markdown(output_dir / "SUMMARY.md", summary)
        return summary
    finally:
        engine.dispose()


def _build_simulation_run(
    base_run: AnalysisRun,
    *,
    index: int,
    event_time: datetime,
    tenant_id: str,
    environment: str,
    reviewed_verdict: Verdict,
    reviewed_summary: str,
    reviewed_reason: str,
) -> AnalysisRun:
    run = _clone_run_identity(
        base_run,
        index=index,
        event_time=event_time,
        tenant_id=tenant_id,
        environment=environment,
        suffix="OBS",
    )
    evidence_ref = f"E-{stable_hash({'alert_id': run.alert_id})[:12].upper()}"
    analysis = run.analysis.model_copy(
        update={
            "verdict": reviewed_verdict,
            "confidence": 0.9,
            "summary": reviewed_summary,
            "evidence": [
                EvidenceItem(
                    evidence_ref=evidence_ref,
                    source="simulation_review_fixture",
                    description="独立模拟事件已由运营复核并用于 Memory 生命周期验证",
                    value=run.alert_id,
                )
            ],
            "reasoning": [
                AnalysisReasoningItem(
                    reasoning_id="R-01",
                    statement=reviewed_reason,
                    basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                    evidence_refs=[evidence_ref],
                    confidence=0.9,
                )
            ],
            "decision_evidence_refs": [evidence_ref],
            "decision_reasoning_refs": ["R-01"],
            "evidence_gaps": [],
            "manual_checks": [],
            "reason": reviewed_reason,
            "recommended_action": "apply reviewed tenant response policy",
            "knowledge_candidates": [],
        },
        deep=True,
    )
    decision = run.decision.model_copy(
        update={
            "verdict": reviewed_verdict,
            "confidence": 0.9,
            "confidence_source": DecisionConfidenceSource.HUMAN_CONFIRMATION,
            "evidence_state": DecisionEvidenceState.SUFFICIENT,
            "suggested_action": "apply reviewed tenant response policy",
            "needs_review": False,
            "review_reasons": [],
            "reason": reviewed_reason,
            "confidence_explanation": (
                "Synthetic human-confirmed outcome used only by the isolated "
                "Memory lifecycle simulation."
            ),
        },
        deep=True,
    )
    return run.model_copy(
        update={
            "status": AnalysisRunStatus.SUCCESS,
            "model_name": "simulation-human-review",
            "prompt_version": "simulation-human-review-v1",
            "analysis": analysis,
            "decision": decision,
        },
        deep=True,
    )


def _build_held_out_run(
    base_run: AnalysisRun,
    *,
    index: int,
    event_time: datetime,
    tenant_id: str,
    environment: str,
) -> AnalysisRun:
    return _clone_run_identity(
        base_run,
        index=index,
        event_time=event_time,
        tenant_id=tenant_id,
        environment=environment,
        suffix="HOLDOUT",
    )


def _clone_run_identity(
    base_run: AnalysisRun,
    *,
    index: int,
    event_time: datetime,
    tenant_id: str,
    environment: str,
    suffix: str,
) -> AnalysisRun:
    if base_run.input_payload is None:
        raise ValueError("base AnalysisRun requires input_payload")
    if base_run.llm_analysis_request is None:
        raise ValueError("base AnalysisRun requires llm_analysis_request")
    if base_run.analysis is None or base_run.decision is None:
        raise ValueError("base AnalysisRun requires completed analysis and decision")

    alert_id = f"{base_run.alert_id}-SIM-{suffix}-{index:02d}"
    run_id = f"RUN-SIM-{stable_hash({'base': base_run.run_id, 'index': index, 'suffix': suffix})[:12].upper()}"
    payload = copy.deepcopy(base_run.input_payload)
    alert = payload.get("alert")
    if isinstance(alert, dict):
        alert["alertId"] = alert_id
        alert["createAt"] = event_time.isoformat()
    else:
        payload["alert_id"] = alert_id
        payload["event_time"] = event_time.isoformat()
    input_hash = stable_hash(
        {
            "simulation": True,
            "base_input_hash": base_run.input_hash,
            "alert_id": alert_id,
            "event_time": event_time.isoformat(),
        }
    )
    request = base_run.llm_analysis_request.model_copy(
        update={
            "alert_id": alert_id,
            "tenant_id": tenant_id,
            "environment": environment,
            "context_catalog": [],
            "warnings": [],
        },
        deep=True,
    )
    return base_run.model_copy(
        update={
            "run_id": run_id,
            "alert_id": alert_id,
            "input_payload": payload,
            "input_hash": input_hash,
            "started_at": event_time,
            "ended_at": event_time + timedelta(seconds=1),
            "total_duration_ms": 1000,
            "llm_analysis_request": request,
            "request_journal": None,
            "provider_request_journals": [],
            "replay_of_run_id": None,
            "corrections": [],
        },
        deep=True,
    )


def _service_context(
    *,
    actor_id: str,
    roles: list[str],
    idempotency_key: str,
) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=idempotency_key,
        actor=ActorContext(
            actor_id=actor_id,
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=roles,
        ),
    )


def _user_context(
    *,
    actor_id: str,
    idempotency_key: str,
) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=idempotency_key,
        actor=ActorContext(
            actor_id=actor_id,
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=["soc_memory_reviewer"],
        ),
    )


def _validate_inputs(
    base_run: AnalysisRun,
    *,
    output_dir: Path,
    tenant_id: str,
    environment: str,
    support_count: int,
    reviewed_summary: str,
    reviewed_reason: str,
) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"simulation output directory is not empty: {output_dir}")
    if support_count < 2 or support_count > 100:
        raise ValueError("support_count must be between 2 and 100")
    if not tenant_id.strip() or not environment.strip():
        raise ValueError("tenant_id and environment must not be blank")
    if not reviewed_summary.strip() or not reviewed_reason.strip():
        raise ValueError("reviewed summary and reason must not be blank")
    if base_run.analysis is None or base_run.decision is None:
        raise ValueError("base AnalysisRun must contain analysis and decision")
    if base_run.llm_analysis_request is None or base_run.input_payload is None:
        raise ValueError(
            "base AnalysisRun must contain bounded request and input payload"
        )


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _write_summary_markdown(path: Path, summary: Mapping[str, Any]) -> None:
    pattern = summary["pattern"]
    memory = summary["memory"]
    held_out = summary["held_out"]
    checks = summary["checks"]
    lines = [
        "# Pattern Memory 5+1 Simulation",
        "",
        f"- Status: `{summary['status']}`",
        "- Data class: `simulation`",
        "- LLM calls: `0`",
        f"- Pattern observations: `{pattern['observation_count']}`",
        f"- Pending candidate: `{pattern['candidate_id']}`",
        f"- Confirmed Memory: `{memory['memory_id']}@v{memory['record_version']}`",
        f"- Retrieval enabled: `{memory['retrieval_enabled']}`",
        f"- Held-out Memory matches: `{held_out['retrieval_match_count']}`",
        f"- Decision: `{held_out['base_verdict']}` -> `{held_out['effective_verdict']}`",
        f"- Review: `{held_out['base_needs_review']}` -> `{held_out['effective_needs_review']}`",
        f"- Transition: `{held_out['transition_kind']}`",
        "",
        "## Checks",
        "",
        *[f"- [{'x' if passed else ' '}] `{name}`" for name, passed in checks.items()],
        "",
        "This isolated run validates lifecycle wiring only. It does not establish production truth or authorize an external action.",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-item", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--tenant-id", default="pingan")
    parser.add_argument("--environment", default="prd")
    parser.add_argument("--support-count", type=int, default=5)
    parser.add_argument(
        "--confirmed-verdict",
        choices=[item.value for item in Verdict if item is not Verdict.UNKNOWN],
        default=Verdict.SUSPICIOUS.value,
    )
    parser.add_argument(
        "--preserve-review-on-match",
        action="store_true",
        help="Keep needs_review=true after an exact reviewed Memory match",
    )
    parser.add_argument("--reviewed-summary", default=DEFAULT_REVIEW_SUMMARY)
    parser.add_argument("--reviewed-reason", default=DEFAULT_REVIEW_REASON)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    summary = simulate_pattern_memory_lifecycle(
        load_analysis_run(args.input_item.expanduser().resolve()),
        output_dir=args.output_dir.expanduser().resolve(),
        tenant_id=args.tenant_id.strip(),
        environment=args.environment.strip().casefold(),
        support_count=args.support_count,
        confirmed_verdict=Verdict(args.confirmed_verdict),
        clear_review_on_match=not args.preserve_review_on_match,
        reviewed_summary=args.reviewed_summary,
        reviewed_reason=args.reviewed_reason,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
