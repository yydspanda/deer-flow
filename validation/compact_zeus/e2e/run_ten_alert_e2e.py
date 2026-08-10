#!/usr/bin/env python3
"""Run and package one chronological ten-alert SOC end-to-end validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.context_bridge import (  # noqa: E402
    build_lead_agent_review_context_artifact,
)
from soc_agent.core import SocReviewService  # noqa: E402
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    to_sync_database_url,
)
from validation.compact_zeus.e2e.knowledge_review import (  # noqa: E402
    compile_case_knowledge_review,
    compile_knowledge_review_package,
    render_knowledge_review_markdown,
)

CASES_SCHEMA_VERSION = "soc.validation.e2e_ten_alert_cases.v1"
REPORT_SCHEMA_VERSION = "soc.validation.e2e_ten_alert_report.v1"
CASE_SCHEMA_VERSION = "soc.validation.e2e_ten_alert_case.v1"
RUN_MANIFEST_SCHEMA_VERSION = "soc.validation.e2e_ten_alert_run.v1"
DEFAULT_CASES = Path(__file__).with_name("ten-alert-cases.json")
DEFAULT_SOURCE = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_ROOT = BACKEND_ROOT / ".deer-flow/soc-validation/e2e-ten-current"
RUNTIME_BATCH_RUNNER = (
    ROOT / "validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py"
)
PINGAN_POLICY = (
    BACKEND_ROOT / "soc_agent/integrations/pingan/policies/tenant-disposition-v1.json"
)
ENRICHMENT_COMPOSITION = (
    BACKEND_ROOT / "samples/enrichment/pingan-external-simulation.yaml"
)
ENRICHMENT_ACTION_CONFIGS = (
    BACKEND_ROOT / "samples/mcp/pingan_asset/action_adapters.json",
    BACKEND_ROOT / "samples/mcp/pingan_security_tag/action_adapters.json",
)
ENRICHMENT_EXTENSIONS = (
    BACKEND_ROOT / "samples/mcp/pingan_shadow/extensions.simulated.json"
)
EXPECTED_RUNTIME_STEPS = (
    "normalize",
    "entity_extract",
    "fact_reconstruct",
    "build_analysis_input",
    "skill_context",
    "reference_catalog",
    "analyze_llm",
    "schema_validate",
    "evidence_grounding",
    "decide",
)


@dataclass(frozen=True)
class CaseSpec:
    alert_id: str
    expected_topic: str
    expected_source_type: str
    purpose: str


@dataclass(frozen=True)
class E2EPaths:
    root: Path
    batch: Path
    database: Path
    cases: Path
    summary_json: Path
    summary_markdown: Path
    run_manifest: Path
    knowledge_review_json: Path
    knowledge_review_markdown: Path

    @property
    def database_url(self) -> str:
        return f"sqlite:///{self.database.as_posix()}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--model-name", default="deepseek-v4-flash")
    parser.add_argument(
        "--python-executable",
        type=Path,
        default=BACKEND_ROOT / ".venv/bin/python",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute migrations, ten live LLM calls, and simulated read-only investigation",
    )
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--confirm-investigation", action="store_true")
    output_mode = parser.add_mutually_exclusive_group()
    output_mode.add_argument("--resume", action="store_true")
    output_mode.add_argument(
        "--replace",
        action="store_true",
        help="Delete an existing output root before a fresh execution",
    )
    return parser.parse_args(argv)


def load_case_manifest(path: Path) -> tuple[dict[str, Any], tuple[CaseSpec, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != CASES_SCHEMA_VERSION
    ):
        raise ValueError("unsupported ten-alert case manifest")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or len(raw_cases) != 10:
        raise ValueError("ten-alert case manifest must contain exactly 10 cases")
    cases = tuple(
        CaseSpec(
            alert_id=_required_text(item, "alert_id"),
            expected_topic=_required_text(item, "expected_topic"),
            expected_source_type=_required_text(item, "expected_source_type"),
            purpose=_required_text(item, "purpose"),
        )
        for item in raw_cases
        if isinstance(item, Mapping)
    )
    if len(cases) != 10:
        raise ValueError("every ten-alert case must be an object")
    alert_ids = [item.alert_id for item in cases]
    duplicates = sorted(
        alert_id for alert_id, count in Counter(alert_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError("duplicate ten-alert case ids: " + ", ".join(duplicates))
    excluded_ids = {
        _required_text(item, "alert_id")
        for item in payload.get("excluded") or []
        if isinstance(item, Mapping)
    }
    overlap = sorted(excluded_ids.intersection(alert_ids))
    if overlap:
        raise ValueError("excluded alerts cannot be selected: " + ", ".join(overlap))
    return payload, cases


def build_paths(output_root: Path) -> E2EPaths:
    root = output_root.expanduser().resolve()
    return E2EPaths(
        root=root,
        batch=root / "runtime-batch",
        database=root / "soc-e2e.sqlite",
        cases=root / "cases",
        summary_json=root / "summary.json",
        summary_markdown=root / "SUMMARY.md",
        run_manifest=root / "run-manifest.json",
        knowledge_review_json=root / "knowledge-review/candidates.json",
        knowledge_review_markdown=root / "knowledge-review/REVIEW.md",
    )


def build_batch_command(
    *,
    python_executable: Path,
    source: Path,
    paths: E2EPaths,
    cases: Sequence[CaseSpec],
    model_name: str,
    execute: bool,
    resume: bool,
) -> tuple[str, ...]:
    command = [
        str(python_executable),
        str(RUNTIME_BATCH_RUNNER),
        "--source",
        str(source),
        "--output-dir",
        str(paths.batch),
        "--analyzer-mode",
        "llm",
        "--model-name",
        model_name,
        "--default-tenant-id",
        "pingan",
        "--persist",
        "--database-url",
        paths.database_url,
        "--workers",
        "1",
        "--checkpoint-every",
        "1",
    ]
    for case in cases:
        command.extend(("--alert-id", case.alert_id))
    command.extend(
        (
            "--enrichment-composition",
            str(ENRICHMENT_COMPOSITION),
        )
    )
    for config in ENRICHMENT_ACTION_CONFIGS:
        command.extend(("--enrichment-action-config", str(config)))
    command.extend(
        (
            "--enrichment-extensions-config",
            str(ENRICHMENT_EXTENSIONS),
        )
    )
    if execute:
        command.extend(("--confirm-live", "--confirm-investigation"))
    else:
        command.append("--plan-only")
    if resume:
        command.append("--resume")
    return tuple(command)


def build_dossier(
    *,
    paths: E2EPaths,
    cases_manifest: Mapping[str, Any],
    cases_path: Path,
    cases: Sequence[CaseSpec],
    source: Path,
    model_name: str,
) -> dict[str, Any]:
    records = _load_batch_records(paths.batch)
    missing = [case.alert_id for case in cases if case.alert_id not in records]
    unexpected = sorted(set(records).difference(case.alert_id for case in cases))
    if missing or unexpected:
        raise ValueError(
            f"batch cohort mismatch; missing={missing or []}, unexpected={unexpected}"
        )

    engine = create_engine(to_sync_database_url(paths.database_url), pool_pre_ping=True)
    repository = SqlAlchemyAlertRepository(
        sessionmaker(bind=engine, expire_on_commit=False)
    )
    review_service = SocReviewService(
        repository=repository,
        summary_repository=repository,
        audit_repository=repository,
        review_queue_repository=repository,
        evidence_repository=repository,
        enrichment_execution_repository=repository,
        authorization_enrichment_repository=repository,
        disposition_proposal_repository=repository,
        disposition_evaluation_repository=repository,
        external_disposition_repository=repository,
        memory_candidate_repository=repository,
        memory_record_repository=repository,
    )
    case_results: list[dict[str, Any]] = []
    try:
        _ensure_private_directory(paths.cases)
        for case in cases:
            result = _build_case_dossier(
                case=case,
                record=records[case.alert_id],
                repository=repository,
                review_service=review_service,
                output_dir=paths.cases / case.alert_id,
            )
            case_results.append(result)
    finally:
        engine.dispose()

    case_knowledge_reviews = [
        item.pop("_knowledge_candidate_review") for item in case_results
    ]
    knowledge_review = compile_knowledge_review_package(case_knowledge_reviews)
    _write_json(paths.knowledge_review_json, knowledge_review)
    _write_text(
        paths.knowledge_review_markdown,
        render_knowledge_review_markdown(knowledge_review),
    )

    statuses = Counter(item["acceptance_status"] for item in case_results)
    quality_statuses = Counter(item["quality_status"] for item in case_results)
    verdicts = Counter(
        str(_mapping(item.get("final_conclusion")).get("verdict") or "unknown")
        for item in case_results
    )
    source_types = Counter(
        str(_mapping(item.get("source")).get("source_type") or "unknown")
        for item in case_results
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "cohort_id": cases_manifest.get("cohort_id"),
        "acceptance_status": (
            "passed"
            if len(case_results) == 10 and statuses.get("passed") == 10
            else "failed"
        ),
        "quality_status": (
            "review_required"
            if quality_statuses.get("review_required", 0)
            else "no_findings"
        ),
        "scope": {
            "authoritative_path": "ingress -> fixed Runtime -> persisted decision -> ReviewQueue",
            "acceptance_semantics": "structural and safety acceptance; not a model-accuracy claim",
            "investigation": "simulated read-only PingAn MCP providers",
            "tenant_policy": "shadow-only post-Runtime advice",
            "lead_agent": "bounded context projection only; no advisory chat is treated as Runtime truth",
            "knowledge_candidates": "human-review package only; no automatic memory, Skill, adapter, or policy mutation",
            "mocked_provider_results_are_real_integration_evidence": False,
        },
        "input": {
            "source": str(source.resolve()),
            "source_sha256": _sha256_file(source),
            "cases_manifest": _display_path(cases_path),
            "cases_sha256": _canonical_sha256(cases_manifest),
            "requested_model_name": model_name,
            "database": str(paths.database),
        },
        "summary": {
            "case_count": len(case_results),
            "passed_count": statuses.get("passed", 0),
            "failed_count": statuses.get("failed", 0),
            "quality_finding_case_count": quality_statuses.get("review_required", 0),
            "zero_grounded_case_count": sum(
                int(_mapping(item.get("grounding")).get("grounded_count") or 0) == 0
                for item in case_results
            ),
            "verdict_counts": dict(sorted(verdicts.items())),
            "source_type_counts": dict(sorted(source_types.items())),
            "review_queue_count": sum(
                _mapping(item.get("review")).get("queue_id") is not None
                for item in case_results
            ),
            "tenant_policy_decision_count": sum(
                int(_mapping(item.get("tenant_policy")).get("decision_count") or 0)
                for item in case_results
            ),
            "investigation_evidence_count": sum(
                int(_mapping(item.get("investigation")).get("evidence_count") or 0)
                for item in case_results
            ),
            "ungrounded_evidence_count": sum(
                int(_mapping(item.get("grounding")).get("ungrounded_count") or 0)
                for item in case_results
            ),
            "description_leakage_count": sum(
                int(
                    _mapping(item.get("grounding")).get("description_leakage_count")
                    or 0
                )
                for item in case_results
            ),
            "ungrounded_reasoning_count": sum(
                int(
                    _mapping(item.get("grounding")).get("reasoning_ungrounded_count")
                    or 0
                )
                for item in case_results
            ),
            "raw_knowledge_candidate_count": _mapping(
                knowledge_review.get("summary")
            ).get("raw_candidate_count", 0),
            "knowledge_review_candidate_count": _mapping(
                knowledge_review.get("summary")
            ).get("review_candidate_count", 0),
        },
        "knowledge_review": {
            "status": knowledge_review.get("status"),
            "json": str(paths.knowledge_review_json),
            "markdown": str(paths.knowledge_review_markdown),
            "summary": knowledge_review.get("summary"),
        },
        "excluded": list(cases_manifest.get("excluded") or []),
        "cases": case_results,
    }
    _write_json(paths.summary_json, report)
    _write_text(paths.summary_markdown, _render_summary_markdown(report))
    return report


def _build_case_dossier(
    *,
    case: CaseSpec,
    record: Mapping[str, Any],
    repository: SqlAlchemyAlertRepository,
    review_service: SocReviewService,
    output_dir: Path,
) -> dict[str, Any]:
    _ensure_private_directory(output_dir)
    run_payload = _required_mapping(record, "analysis_run")
    run_id = _required_text(run_payload, "run_id")
    persisted_run = repository.get_run(run_id)
    if persisted_run is None:
        raise ValueError(f"persisted run is missing for alert {case.alert_id}")
    persisted_payload = persisted_run.model_dump(mode="json", exclude_none=True)
    queue_item = repository.get_open_review_item_by_run(run_id)
    alert_summary = repository.get_alert_summary(run_id)
    audit_records = repository.list_audit_records(run_id)
    policy_decisions = repository.list_tenant_policy_decisions(
        run_id=run_id,
        limit=20,
    )
    evidence = repository.list_evidence(
        run_id=run_id,
        alert_id=case.alert_id,
        limit=100,
    )
    investigation_context = None
    lead_agent_artifact = None
    if queue_item is not None:
        investigation_context = review_service.get_investigation_context(
            queue_item.queue_id
        )
        lead_agent_artifact = build_lead_agent_review_context_artifact(
            investigation_context
        )

    request = _mapping(run_payload.get("llm_analysis_request"))
    analysis = _mapping(run_payload.get("analysis"))
    grounding = _mapping(run_payload.get("analysis_evidence_grounding"))
    decision = _mapping(run_payload.get("decision"))
    normalization = _mapping(run_payload.get("normalization_report"))
    source = _mapping(request.get("source"))
    classification = _mapping(request.get("classification"))
    labels = _mapping(classification.get("labels"))
    evidence_coverage = _mapping(request.get("evidence_coverage"))
    coverage_counts = _mapping(evidence_coverage.get("counts"))
    steps = (
        run_payload.get("steps") if isinstance(run_payload.get("steps"), list) else []
    )
    step_names = [str(_mapping(item).get("step_name")) for item in steps]
    checks = {
        "batch_completed": record.get("outcome") == "completed",
        "alert_id_matches": str(run_payload.get("alert_id")) == case.alert_id,
        "source_type_matches": source.get("source_type") == case.expected_source_type,
        "topic_matches": labels.get("topic") == case.expected_topic,
        "input_payload_preserved": isinstance(
            run_payload.get("input_payload"), Mapping
        ),
        "no_high_value_input_gap": int(coverage_counts.get("high_value_gap_count") or 0)
        == 0,
        "runtime_step_sequence_complete": tuple(step_names) == EXPECTED_RUNTIME_STEPS,
        "all_runtime_steps_succeeded": bool(steps)
        and all(_mapping(item).get("status") == "success" for item in steps),
        "live_model_used": run_payload.get("model_name") not in {None, "", "stub"},
        "analysis_exists": bool(analysis),
        "grounding_exists": bool(grounding),
        "decision_exists": bool(decision),
        "persistence_round_trip_matches": (
            persisted_payload.get("input_hash") == run_payload.get("input_hash")
            and persisted_payload.get("decision") == run_payload.get("decision")
        ),
        "review_queue_consistent": (
            (queue_item is not None) == bool(decision.get("needs_review"))
        ),
        "tenant_policy_shadow_recorded": len(policy_decisions) == 1,
        "investigation_workflow_recorded": bool(record.get("investigation_workflow")),
        "investigation_did_not_mutate_runtime": _mapping(
            record.get("investigation_shadow_report")
        ).get("base_run_mutated")
        is False,
        "lead_agent_context_available_when_reviewable": (
            queue_item is None or lead_agent_artifact is not None
        ),
        "automation_remains_disabled": decision.get("automation_allowed") is False,
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    primary_scenario = _primary_scenario(analysis)
    candidate_source = {
        "source_type": source.get("source_type"),
        "source_system": source.get("source_system"),
        "product": source.get("product"),
        "topic": labels.get("topic"),
        "rule_code": _mapping(request.get("detection")).get("rule_code"),
        "rule_name": _mapping(request.get("detection")).get("rule_name"),
    }
    knowledge_candidate_review = compile_case_knowledge_review(
        alert_id=case.alert_id,
        run_id=run_id,
        source=candidate_source,
        analysis=analysis,
        grounding=grounding,
    )
    policy_payloads = [
        item.model_dump(mode="json", exclude_none=True) for item in policy_decisions
    ]
    investigation_report = _mapping(record.get("investigation_shadow_report"))
    final_conclusion = {
        "verdict": analysis.get("verdict"),
        "confidence": analysis.get("confidence"),
        "summary": analysis.get("summary"),
        "reason": analysis.get("reason"),
        "recommended_action": analysis.get("recommended_action"),
        "primary_scenario": primary_scenario,
        "activity_stage": (
            primary_scenario.get("activity_stage")
            if primary_scenario is not None
            else None
        ),
        "evidence_state": decision.get("evidence_state"),
        "needs_review": decision.get("needs_review"),
        "automation_allowed": decision.get("automation_allowed"),
        "grounded_evidence_count": grounding.get("grounded_count"),
        "ungrounded_evidence_count": grounding.get("ungrounded_count"),
        "tenant_policy": _policy_conclusion(policy_payloads),
        "investigation": {
            "result_mode": investigation_report.get("required_result_mode"),
            "planned_action_count": investigation_report.get("planned_action_count"),
            "persisted_evidence_count": investigation_report.get(
                "persisted_evidence_count"
            ),
            "base_run_mutated": investigation_report.get("base_run_mutated"),
        },
        "review_queue_id": queue_item.queue_id if queue_item is not None else None,
    }
    quality_findings = _grounding_quality_findings(grounding)

    stages = {
        "00-ingress.json": {
            "schema_version": CASE_SCHEMA_VERSION,
            "case": case.__dict__,
            "source": record.get("source"),
            "input_hash": run_payload.get("input_hash"),
            "input_payload": run_payload.get("input_payload"),
        },
        "01-normalization.json": {
            "normalization_report": normalization,
            "source": source,
            "classification": classification,
            "detection": request.get("detection"),
            "canonical_entities": request.get("canonical_entities"),
            "source_field_semantics": request.get("source_field_semantics"),
            "evidence_coverage": request.get("evidence_coverage"),
        },
        "02-entity-extraction.json": {
            "entities": run_payload.get("entities"),
            "extraction_report": run_payload.get("extraction_report"),
        },
        "03-fact-reconstruction.json": run_payload.get("fact_reconstruction") or {},
        "04-bounded-analysis-input.json": request,
        "05-runtime-trace.json": {
            "run_id": run_id,
            "model_name": run_payload.get("model_name"),
            "prompt_version": run_payload.get("prompt_version"),
            "pipeline_version": run_payload.get("pipeline_version"),
            "steps": steps,
        },
        "06-llm-analysis.json": analysis,
        "07-evidence-grounding.json": grounding,
        "08-decision.json": decision,
        "09-investigation.json": {
            "workflow": record.get("investigation_workflow"),
            "shadow_report": record.get("investigation_shadow_report"),
            "addendum": record.get("investigation_addendum"),
            "evidence": [
                item.model_dump(mode="json", exclude_none=True) for item in evidence
            ],
        },
        "10-review-and-agent-context.json": {
            "alert_summary": (
                alert_summary.model_dump(mode="json", exclude_none=True)
                if alert_summary is not None
                else None
            ),
            "review_queue": (
                queue_item.model_dump(mode="json", exclude_none=True)
                if queue_item is not None
                else None
            ),
            "decision_audit": [
                item.model_dump(mode="json", exclude_none=True)
                for item in audit_records
            ],
            "tenant_policy_decisions": policy_payloads,
            "lead_agent_context": (
                lead_agent_artifact.model_dump(mode="json", exclude_none=True)
                if lead_agent_artifact is not None
                else None
            ),
            "lead_agent_note": (
                "This is the bounded context available to DeerFlow Lead Agent. "
                "No advisory chat response is treated as the authoritative Runtime conclusion."
            ),
        },
        "11-knowledge-candidates.json": knowledge_candidate_review,
    }
    for filename, payload in stages.items():
        _write_json(output_dir / filename, _mapping(payload))

    case_report = {
        "schema_version": CASE_SCHEMA_VERSION,
        "alert_id": case.alert_id,
        "purpose": case.purpose,
        "acceptance_status": "passed" if not failed_checks else "failed",
        "quality_status": "review_required" if quality_findings else "no_findings",
        "quality_findings": quality_findings,
        "failed_checks": failed_checks,
        "checks": checks,
        "lineage": {
            "run_id": run_id,
            "input_hash": run_payload.get("input_hash"),
            "model_name": run_payload.get("model_name"),
            "prompt_version": run_payload.get("prompt_version"),
            "pipeline_version": run_payload.get("pipeline_version"),
        },
        "source": {
            "source_type": source.get("source_type"),
            "source_system": source.get("source_system"),
            "product": source.get("product"),
            "topic": labels.get("topic"),
            "rule_code": _mapping(request.get("detection")).get("rule_code"),
            "rule_name": _mapping(request.get("detection")).get("rule_name"),
        },
        "grounding": {
            "grounded_count": grounding.get("grounded_count"),
            "ungrounded_count": grounding.get("ungrounded_count"),
            "description_leakage_count": grounding.get("description_leakage_count"),
            "reasoning_grounded_count": grounding.get("reasoning_grounded_count"),
            "reasoning_ungrounded_count": grounding.get("reasoning_ungrounded_count"),
        },
        "knowledge_candidates": {
            "candidate_count": knowledge_candidate_review["candidate_count"],
            "grounded_candidate_count": knowledge_candidate_review[
                "grounded_candidate_count"
            ],
            "review_status": "pending_review",
            "artifact": str(output_dir / "11-knowledge-candidates.json"),
            "memory_write_performed": False,
        },
        "investigation": {
            "status": _mapping(record.get("summary")).get("investigation_status"),
            "evidence_count": len(evidence),
            "mock_result_count": investigation_report.get("mock_result_count"),
            "real_result_count": investigation_report.get("real_result_count"),
        },
        "tenant_policy": {
            "decision_count": len(policy_payloads),
            **_policy_conclusion(policy_payloads),
        },
        "review": {
            "queue_id": queue_item.queue_id if queue_item is not None else None,
            "status": queue_item.status.value if queue_item is not None else None,
            "priority": queue_item.priority.value if queue_item is not None else None,
            "lead_agent_context_hash": (
                lead_agent_artifact.context_hash
                if lead_agent_artifact is not None
                else None
            ),
        },
        "final_conclusion": final_conclusion,
        "artifact_directory": str(output_dir),
    }
    _write_json(output_dir / "final-conclusion.json", case_report)
    case_report["_knowledge_candidate_review"] = knowledge_candidate_review
    return case_report


def _primary_scenario(analysis: Mapping[str, Any]) -> dict[str, Any] | None:
    scenarios = analysis.get("scenario_assessments")
    if not isinstance(scenarios, list):
        return None
    for scenario in scenarios:
        if isinstance(scenario, Mapping) and scenario.get("is_primary") is True:
            return dict(scenario)
    return None


def _policy_conclusion(decisions: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not decisions:
        return {
            "evaluation_status": None,
            "selected_rule_id": None,
            "response_posture": None,
            "recommended_disposition": None,
        }
    decision = decisions[0]
    return {
        "evaluation_status": decision.get("evaluation_status"),
        "selected_rule_id": decision.get("selected_rule_id"),
        "response_posture": decision.get("response_posture"),
        "recommended_disposition": decision.get("recommended_disposition"),
        "shadow_only": decision.get("shadow_only"),
    }


def _grounding_quality_findings(grounding: Mapping[str, Any]) -> list[str]:
    total = int(grounding.get("total_count") or 0)
    grounded = int(grounding.get("grounded_count") or 0)
    ungrounded = int(grounding.get("ungrounded_count") or 0)
    leakage = int(grounding.get("description_leakage_count") or 0)
    reasoning_ungrounded = int(grounding.get("reasoning_ungrounded_count") or 0)
    findings: list[str] = []
    if total == 0:
        findings.append("analyzer_produced_no_evidence")
    elif grounded == 0:
        findings.append("no_analyzer_evidence_grounded")
    if ungrounded:
        findings.append(f"{ungrounded}_analyzer_evidence_items_rejected")
    if leakage:
        findings.append(f"{leakage}_evidence_descriptions_leaked_sibling_facts")
    if reasoning_ungrounded:
        findings.append(f"{reasoning_ungrounded}_analysis_reasoning_items_rejected")
    return findings


def _load_batch_records(batch_dir: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted((batch_dir / "items").glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        source = _required_mapping(payload, "source")
        alert_id = _required_text(source, "alert_id")
        if alert_id in records:
            raise ValueError(f"duplicate batch artifact for alert {alert_id}")
        records[alert_id] = payload
    return records


def _render_summary_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# SOC Ten-Alert End-to-End Validation",
        "",
        f"- Status: `{report.get('acceptance_status')}`",
        f"- Quality: `{report.get('quality_status')}`",
        f"- Generated: `{report.get('generated_at')}`",
        "- Pass meaning: structural/safety chain passed; this is not a model-accuracy claim",
        "- Authoritative result: fixed SOC Runtime decision persisted to ReviewQueue",
        "- Provider mode: simulated read-only; results do not close real integration debt",
        "- Lead Agent: bounded context is generated, but no chat text replaces Runtime truth",
        "",
        "| Alert | Source | Topic | Primary scenario | Verdict | Confidence | Evidence | Queue priority | Quality | Status |",
        "| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for item in report.get("cases") or []:
        if not isinstance(item, Mapping):
            continue
        source = _mapping(item.get("source"))
        conclusion = _mapping(item.get("final_conclusion"))
        scenario = _mapping(conclusion.get("primary_scenario"))
        grounding = _mapping(item.get("grounding"))
        review = _mapping(item.get("review"))
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.get("alert_id")),
                    _md(source.get("source_type")),
                    _md(source.get("topic")),
                    _md(scenario.get("scenario_name") or scenario.get("scenario_key")),
                    _md(conclusion.get("verdict")),
                    _md(conclusion.get("confidence")),
                    _md(
                        f"{grounding.get('grounded_count', 0)} grounded / "
                        f"{grounding.get('ungrounded_count', 0)} rejected"
                    ),
                    _md(review.get("priority") or "none"),
                    _md(item.get("quality_status")),
                    _md(item.get("acceptance_status")),
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Per-alert files",
            "",
            "Each `cases/<alert_id>/` directory contains `00-ingress.json` through "
            "`11-knowledge-candidates.json`, followed by `final-conclusion.json`.",
            "The numbered files are chronological; no separate historical validation directory "
            "is needed to understand one alert.",
            "Candidate knowledge is consolidated in `knowledge-review/REVIEW.md`; it remains pending human review and is never written to Memory automatically.",
            "",
        ]
    )
    return "\n".join(lines)


def _execution_environment(python_executable: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "SOC_LLM_SENSITIVE_EVIDENCE_MODE": "full",
            "SOC_TENANT_DISPOSITION_POLICY_PATH": str(PINGAN_POLICY),
            "SOC_TENANT_POLICY_ENVIRONMENT": "dev",
            "SOC_TENANT_POLICY_EVENT_TIMEZONE": "Asia/Shanghai",
            "SOC_PINGAN_ASSET_MCP_PYTHON": str(python_executable),
            "SOC_PINGAN_ASSET_MCP_SERVER": str(
                BACKEND_ROOT / "scripts/soc_pingan_asset_mcp_server.py"
            ),
            "SOC_PINGAN_SECURITY_TAG_MCP_PYTHON": str(python_executable),
            "SOC_PINGAN_SECURITY_TAG_MCP_SERVER": str(
                BACKEND_ROOT / "scripts/soc_pingan_security_tag_mcp_server.py"
            ),
        }
    )
    return env


def _prepare_output(paths: E2EPaths, *, resume: bool, replace: bool) -> None:
    if replace and paths.root.exists():
        shutil.rmtree(paths.root)
    if paths.root.exists() and any(paths.root.iterdir()) and not resume:
        raise ValueError(
            f"output root already exists: {paths.root}; use --resume or --replace"
        )
    _ensure_private_directory(paths.root)


def _run(command: Sequence[str], *, cwd: Path, env: Mapping[str, str]) -> None:
    completed = subprocess.run(command, cwd=cwd, env=dict(env), check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"command failed with exit code {completed.returncode}: {command[0]} {command[1]}"
        )


def _write_run_manifest(
    *,
    paths: E2EPaths,
    cases_manifest: Mapping[str, Any],
    source: Path,
    model_name: str,
    batch_command: Sequence[str],
    report: Mapping[str, Any],
) -> None:
    payload = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "source": str(source),
        "source_sha256": _sha256_file(source),
        "cases_sha256": _canonical_sha256(cases_manifest),
        "model_name": model_name,
        "database": str(paths.database),
        "batch_command": list(batch_command),
        "summary": report.get("summary"),
        "acceptance_status": report.get("acceptance_status"),
        "contains_configuration_secrets": False,
        "may_contain_source_secrets": True,
        "contains_sensitive_alert_payloads": True,
        "provider_mode": "simulated_read_only",
    }
    _write_json(paths.run_manifest, payload)


def _git_commit() -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    value = completed.stdout.strip()
    return value or None


def _ensure_private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text(path: Path, content: str) -> None:
    _ensure_private_directory(path.parent)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_mapping(payload: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return dict(value)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if value is None or not str(value).strip():
        raise ValueError(f"{key} must be non-empty")
    return str(value).strip()


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        source = args.source.expanduser().resolve()
        cases_path = args.cases.expanduser().resolve()
        # Keep the virtual-environment launcher path instead of resolving its
        # symlink to the system interpreter.
        python_executable = args.python_executable.expanduser().absolute()
        if not source.is_file():
            raise ValueError(f"source corpus does not exist: {source}")
        if not cases_path.is_file():
            raise ValueError(f"case manifest does not exist: {cases_path}")
        if not python_executable.is_file():
            raise ValueError(f"python executable does not exist: {python_executable}")
        if args.execute and (not args.confirm_live or not args.confirm_investigation):
            raise ValueError(
                "--execute requires --confirm-live and --confirm-investigation"
            )
        cases_manifest, cases = load_case_manifest(cases_path)
        paths = build_paths(args.output_root)
        batch_command = build_batch_command(
            python_executable=python_executable,
            source=source,
            paths=paths,
            cases=cases,
            model_name=args.model_name,
            execute=args.execute,
            resume=args.resume,
        )
        if not args.execute:
            print(
                json.dumps(
                    {
                        "schema_version": "soc.validation.e2e_ten_alert_plan.v1",
                        "source": str(source),
                        "output_root": str(paths.root),
                        "database": str(paths.database),
                        "model_name": args.model_name,
                        "alert_ids": [case.alert_id for case in cases],
                        "excluded": cases_manifest.get("excluded"),
                        "batch_command": list(batch_command),
                        "live_model_call_count": len(cases),
                        "provider_mode": "simulated_read_only",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        _prepare_output(paths, resume=args.resume, replace=args.replace)
        env = _execution_environment(python_executable)
        _run(
            (
                str(python_executable),
                "-m",
                "soc_agent.cli",
                "db",
                "upgrade",
                "--database-url",
                paths.database_url,
            ),
            cwd=BACKEND_ROOT,
            env=env,
        )
        _run(batch_command, cwd=ROOT, env=env)
        report = build_dossier(
            paths=paths,
            cases_manifest=cases_manifest,
            cases_path=cases_path,
            cases=cases,
            source=source,
            model_name=args.model_name,
        )
        _write_run_manifest(
            paths=paths,
            cases_manifest=cases_manifest,
            source=source,
            model_name=args.model_name,
            batch_command=batch_command,
            report=report,
        )
        print(
            json.dumps(
                {
                    "output_root": str(paths.root),
                    "summary": str(paths.summary_json),
                    "summary_markdown": str(paths.summary_markdown),
                    "acceptance_status": report["acceptance_status"],
                    **_mapping(report.get("summary")),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if report["acceptance_status"] == "passed" else 1
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
