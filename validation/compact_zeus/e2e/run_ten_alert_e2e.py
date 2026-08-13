#!/usr/bin/env python3
"""Run and package one chronological fixed-cohort SOC end-to-end validation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
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

from validation.compact_zeus.e2e.knowledge_review import (  # noqa: E402
    compile_case_knowledge_review,
    compile_knowledge_review_package,
    render_knowledge_review_markdown,
)

from soc_agent.context_bridge import (  # noqa: E402
    build_lead_agent_review_context_artifact,
)
from soc_agent.core import SocReviewService  # noqa: E402
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    to_sync_database_url,
)

CASES_SCHEMA_VERSION = "soc.validation.e2e_alert_cohort_cases.v1"
LEGACY_CASES_SCHEMA_VERSION = "soc.validation.e2e_ten_alert_cases.v1"
REPORT_SCHEMA_VERSION = "soc.validation.e2e_alert_cohort_report.v1"
CASE_SCHEMA_VERSION = "soc.validation.e2e_alert_cohort_case.v1"
RUN_MANIFEST_SCHEMA_VERSION = "soc.validation.e2e_alert_cohort_run.v1"
PLAN_SCHEMA_VERSION = "soc.validation.e2e_alert_cohort_plan.v1"
MAX_CASE_COUNT = 20
DEFAULT_CASES = Path(__file__).with_name("ten-alert-cases.json")
DEFAULT_SOURCE = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_ROOT = BACKEND_ROOT / ".deer-flow/soc-validation/e2e-ten-current"
DEFAULT_PRIMARY_MODEL = "globalai-deepseek-v4-flash-0731"
DEFAULT_ROLE_VERIFIER_MODEL = "globalai-deepseek-v4-pro"
RUNTIME_BATCH_RUNNER = (
    ROOT / "validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py"
)
PINGAN_POLICY = (
    BACKEND_ROOT / "soc_agent/integrations/pingan/policies/tenant-disposition-v2.json"
)
PINGAN_POLICY_SKILL = (
    BACKEND_ROOT / "soc_agent/integrations/pingan/policy_skills/disposition/SKILL.md"
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
BASE_RUNTIME_STEPS_BEFORE_DECISION = (
    "normalize",
    "entity_extract",
    "fact_reconstruct",
    "build_analysis_input",
    "skill_context",
    "reference_catalog",
    "analyze_llm",
    "schema_validate",
    "evidence_grounding",
)
EXPECTED_RUNTIME_STEPS = (*BASE_RUNTIME_STEPS_BEFORE_DECISION, "decide")
ROLE_VERIFIER_PIPELINE_VERSION = "soc-runtime-v2"
DEFAULT_ROLE_VERIFIER_MINIMUM_CONFIDENCE = 0.35


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
    parser.add_argument("--model-name", default=DEFAULT_PRIMARY_MODEL)
    parser.add_argument(
        "--thinking",
        choices=("disabled", "enabled"),
        default="disabled",
        help="Request provider reasoning for primary, repair, and verifier model calls",
    )
    parser.add_argument(
        "--output-retry-attempts",
        type=int,
        choices=(0, 1),
        default=1,
        help="Maximum contract/empty-output recovery calls per model node",
    )
    parser.add_argument(
        "--role-verifier",
        choices=("disabled", "enabled"),
        default="enabled",
        help="Explicitly disable or enable the conditional second-pass role verifier",
    )
    parser.add_argument(
        "--tenant-policy-advisor",
        choices=("disabled", "enabled"),
        default="enabled",
        help=(
            "Disable only the optional tenant-policy LLM advisor; deterministic tenant policy evaluation remains enabled"
        ),
    )
    parser.add_argument(
        "--role-verifier-model",
        default=DEFAULT_ROLE_VERIFIER_MODEL,
        help="Verifier model used when the conditional second pass is triggered",
    )
    parser.add_argument(
        "--role-verifier-min-confidence",
        type=float,
        default=DEFAULT_ROLE_VERIFIER_MINIMUM_CONFIDENCE,
    )
    parser.add_argument(
        "--python-executable",
        type=Path,
        default=BACKEND_ROOT / ".venv/bin/python",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Execute migrations, fixed-cohort live LLM calls, and simulated read-only investigation",
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
    if not isinstance(payload, dict) or payload.get("schema_version") not in {
        CASES_SCHEMA_VERSION,
        LEGACY_CASES_SCHEMA_VERSION,
    }:
        raise ValueError("unsupported fixed-cohort case manifest")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not 1 <= len(raw_cases) <= MAX_CASE_COUNT:
        raise ValueError(
            f"fixed-cohort case manifest must contain 1..{MAX_CASE_COUNT} cases"
        )
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
    if len(cases) != len(raw_cases):
        raise ValueError("every fixed-cohort case must be an object")
    alert_ids = [item.alert_id for item in cases]
    duplicates = sorted(
        alert_id for alert_id, count in Counter(alert_ids).items() if count > 1
    )
    if duplicates:
        raise ValueError("duplicate fixed-cohort case ids: " + ", ".join(duplicates))
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
    thinking_enabled: bool,
    role_verifier_enabled: bool,
    role_verifier_model_name: str | None,
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
        "--thinking",
        "enabled" if thinking_enabled else "disabled",
        "--role-verifier",
        "enabled" if role_verifier_enabled else "disabled",
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
    if role_verifier_enabled and role_verifier_model_name:
        command.extend(("--role-verifier-model", role_verifier_model_name))
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
    thinking_enabled: bool,
    role_verifier_enabled: bool,
    role_verifier_model_name: str | None,
    role_verifier_minimum_confidence: float,
    output_retry_attempts: int,
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
    memory_database_state: dict[str, Any] = {}
    try:
        _ensure_private_directory(paths.cases)
        for case in cases:
            result = _build_case_dossier(
                case=case,
                record=records[case.alert_id],
                repository=repository,
                review_service=review_service,
                output_dir=paths.cases / case.alert_id,
                role_verifier_enabled=role_verifier_enabled,
            )
            case_results.append(result)
        memory_database_state = _memory_database_state(repository)
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
    tenant_policy_items = [_mapping(item.get("tenant_policy")) for item in case_results]
    tenant_policy_sources = Counter(
        str(item.get("decision_source") or "unavailable")
        for item in tenant_policy_items
    )
    tenant_policy_statuses = Counter(
        str(item.get("evaluation_status") or "unavailable")
        for item in tenant_policy_items
    )
    tenant_policy_rules = Counter(
        str(item.get("selected_rule_id"))
        for item in tenant_policy_items
        if item.get("selected_rule_id")
    )
    tenant_policy_dispositions = Counter(
        str(item.get("recommended_disposition"))
        for item in tenant_policy_items
        if item.get("recommended_disposition")
    )
    tenant_policy_review_effects = Counter(
        str(item.get("review_effect") or "unavailable") for item in tenant_policy_items
    )
    tenant_policy_advisor_statuses = Counter(
        str(advisor.get("status"))
        for item in tenant_policy_items
        if (advisor := _mapping(item.get("advisor"))).get("status")
    )
    automation_items = [_mapping(item.get("automation")) for item in case_results]
    evidence_compaction_items = [
        _mapping(item.get("evidence_compaction")) for item in case_results
    ]
    role_verification_items = [
        _mapping(item.get("role_verification")) for item in case_results
    ]
    analysis_output_quality_items = [
        _mapping(item.get("analysis_output_quality")) for item in case_results
    ]
    analysis_output_quality_statuses = Counter(
        str(item.get("status") or "missing") for item in analysis_output_quality_items
    )
    degraded_output_sections = Counter(
        str(section)
        for item in analysis_output_quality_items
        for section in item.get("degraded_sections") or []
    )
    model_call_items = [_mapping(item.get("model_calls")) for item in case_results]
    reasoning_provenance = _reasoning_provenance_summary(model_call_items)
    model_usage_measurement = _model_usage_measurement(case_results)
    timing_measurement = _timing_measurement(case_results)
    transition_kinds = Counter(
        str(item.get("decision_transition_kind"))
        for item in automation_items
        if item.get("decision_transition_kind")
    )
    authorization_decisions = Counter(
        str(decision)
        for item in automation_items
        for decision in item.get("action_authorization_decisions") or []
        if decision
    )
    execution_statuses = Counter(
        str(status)
        for item in automation_items
        for status in item.get("action_execution_statuses") or []
        if status
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "cohort_id": cases_manifest.get("cohort_id"),
        "acceptance_status": (
            "passed"
            if len(case_results) == len(cases) and statuses.get("passed") == len(cases)
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
            "tenant_policy": (
                "default-off PingAn deterministic rules plus bounded LLM policy Skill; enabled explicitly for this validation"
            ),
            "lead_agent": "bounded context projection only; no advisory chat is treated as Runtime truth",
            "knowledge_candidates": "human-review package only; no automatic memory, Skill, adapter, or policy mutation",
            "memory": (
                "read existing formal Memory only; this validation never promotes knowledge-review files into database candidates or records"
            ),
            "governed_automation": (
                "effective-decision lineage only; no synthetic action policy or response adapter is installed"
            ),
            "mocked_provider_results_are_real_integration_evidence": False,
            "role_verification": (
                "conditional second-pass role verification is enabled"
                if role_verifier_enabled
                else "primary analysis only; second-pass role verification is disabled"
            ),
        },
        "input": {
            "source": str(source.resolve()),
            "source_sha256": _sha256_file(source),
            "cases_manifest": _display_path(cases_path),
            "cases_sha256": _canonical_sha256(cases_manifest),
            "requested_model_name": model_name,
            "thinking_enabled_requested": thinking_enabled,
            "role_verifier_enabled": role_verifier_enabled,
            "role_verifier_model_name": role_verifier_model_name,
            "role_verifier_minimum_confidence": role_verifier_minimum_confidence,
            "output_retry_attempts": output_retry_attempts,
            "database": str(paths.database),
        },
        "summary": {
            "case_count": len(case_results),
            "passed_count": statuses.get("passed", 0),
            "failed_count": statuses.get("failed", 0),
            "quality_finding_case_count": quality_statuses.get("review_required", 0),
            "quality_measurement_status": "structural_safety_only",
            "accuracy_measurement_status": "not_measured",
            "human_ground_truth_case_count": 0,
            "zero_grounded_case_count": sum(
                int(_mapping(item.get("grounding")).get("grounded_count") or 0) == 0
                for item in case_results
            ),
            "analysis_output_quality_status_counts": dict(
                sorted(analysis_output_quality_statuses.items())
            ),
            "analysis_output_first_pass_accepted_count": (
                analysis_output_quality_statuses.get("accepted", 0)
            ),
            "analysis_output_repaired_count": analysis_output_quality_statuses.get(
                "repaired",
                0,
            ),
            "analysis_output_degraded_count": analysis_output_quality_statuses.get(
                "degraded",
                0,
            ),
            "analysis_output_fallback_count": analysis_output_quality_statuses.get(
                "deterministic_fallback",
                0,
            ),
            "analysis_output_degraded_section_counts": dict(
                sorted(degraded_output_sections.items())
            ),
            "verdict_counts": dict(sorted(verdicts.items())),
            "source_type_counts": dict(sorted(source_types.items())),
            "evidence_compaction_source_message_count": sum(
                int(item.get("source_message_count") or 0)
                for item in evidence_compaction_items
            ),
            "evidence_compaction_behavior_group_count": sum(
                int(item.get("behavior_group_count") or 0)
                for item in evidence_compaction_items
            ),
            "evidence_compaction_profile_count": sum(
                int(item.get("profile_count") or 0)
                for item in evidence_compaction_items
            ),
            "evidence_compaction_collapsed_repetition_count": sum(
                int(item.get("collapsed_repetition_count") or 0)
                for item in evidence_compaction_items
            ),
            "evidence_compaction_non_dominant_profile_count": sum(
                int(item.get("non_dominant_profile_count") or 0)
                for item in evidence_compaction_items
            ),
            "evidence_compaction_high_value_omission_count": sum(
                int(item.get("high_value_omission_count") or 0)
                for item in evidence_compaction_items
            ),
            "evidence_compaction_unrepresented_source_count": sum(
                int(item.get("unrepresented_source_count") or 0)
                for item in evidence_compaction_items
            ),
            "review_queue_count": sum(
                _mapping(item.get("review")).get("queue_id") is not None
                for item in case_results
            ),
            "tenant_policy_decision_count": sum(
                int(_mapping(item.get("tenant_policy")).get("decision_count") or 0)
                for item in case_results
            ),
            "tenant_policy_decision_source_counts": dict(
                sorted(tenant_policy_sources.items())
            ),
            "tenant_policy_evaluation_status_counts": dict(
                sorted(tenant_policy_statuses.items())
            ),
            "tenant_policy_selected_rule_counts": dict(
                sorted(tenant_policy_rules.items())
            ),
            "tenant_policy_disposition_counts": dict(
                sorted(tenant_policy_dispositions.items())
            ),
            "tenant_policy_review_effect_counts": dict(
                sorted(tenant_policy_review_effects.items())
            ),
            "tenant_policy_advisor_status_counts": dict(
                sorted(tenant_policy_advisor_statuses.items())
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
            "decision_transition_count": sum(
                int(item.get("decision_transition_count") or 0)
                for item in automation_items
            ),
            "decision_transition_kind_counts": dict(sorted(transition_kinds.items())),
            "effective_decision_changed_count": sum(
                bool(item.get("effective_decision_changed"))
                for item in automation_items
            ),
            "memory_contributor_count": sum(
                int(item.get("memory_contributor_count") or 0)
                for item in automation_items
            ),
            "memory_database_candidate_count": memory_database_state.get(
                "candidate_count",
                0,
            ),
            "memory_database_record_count": memory_database_state.get(
                "record_count",
                0,
            ),
            "memory_database_retrieval_enabled_record_count": (
                memory_database_state.get("retrieval_enabled_record_count", 0)
            ),
            "memory_database_pattern_observation_count": memory_database_state.get(
                "pattern_observation_count",
                0,
            ),
            "disposition_transition_count": sum(
                int(item.get("disposition_transition_count") or 0)
                for item in automation_items
            ),
            "action_authorization_count": sum(
                int(item.get("action_authorization_count") or 0)
                for item in automation_items
            ),
            "action_authorization_decision_counts": dict(
                sorted(authorization_decisions.items())
            ),
            "automatic_authorization_without_memory_count": sum(
                int(item.get("automatic_authorization_without_memory_count") or 0)
                for item in automation_items
            ),
            "action_execution_count": sum(
                int(item.get("action_execution_count") or 0)
                for item in automation_items
            ),
            "action_execution_status_counts": dict(sorted(execution_statuses.items())),
            "mocked_action_execution_count": sum(
                int(item.get("mocked_execution_count") or 0)
                for item in automation_items
            ),
            "real_external_action_call_count": 0,
            "runtime_total_duration_ms": sum(
                int(item.get("runtime_total_duration_ms") or 0)
                for item in model_call_items
            ),
            "primary_model_total_duration_ms": sum(
                int(_mapping(item.get("primary")).get("duration_ms") or 0)
                for item in model_call_items
            ),
            "primary_model_total_usage": _aggregate_model_usage(
                _mapping(item.get("primary")) for item in model_call_items
            ),
            "primary_model_provider_invocation_count": sum(
                int(_mapping(item.get("primary")).get("provider_call_count") or 0)
                for item in model_call_items
            ),
            "reasoning_provenance": reasoning_provenance,
            "role_verifier_configured_case_count": sum(
                bool(item.get("configured")) for item in role_verification_items
            ),
            "role_verifier_triggered_case_count": sum(
                bool(item.get("triggered")) for item in role_verification_items
            ),
            "role_verifier_logical_review_count": sum(
                int(item.get("logical_review_count") or 0)
                for item in role_verification_items
            ),
            "role_verifier_projected_candidate_claim_count": sum(
                int(item.get("projected_candidate_claim_count") or 0)
                for item in role_verification_items
            ),
            "role_verifier_atomic_claim_count": sum(
                int(item.get("atomic_claim_count") or 0)
                for item in role_verification_items
            ),
            "role_verifier_call_count": sum(
                bool(_mapping(item.get("call")).get("present"))
                for item in role_verification_items
            ),
            "role_verifier_provider_invocation_count": sum(
                int(_mapping(item.get("call")).get("provider_call_count") or 0)
                for item in role_verification_items
            ),
            "role_verifier_output_retry_case_count": sum(
                _mapping(item.get("call")).get("output_retry_attempted") is True
                for item in role_verification_items
            ),
            "role_verifier_usage_incomplete_case_count": sum(
                _mapping(item.get("call")).get("present") is True
                and int(_mapping(item.get("call")).get("provider_call_count") or 0) > 0
                and not _call_usage_complete(_mapping(item.get("call")))
                for item in role_verification_items
            ),
            "role_verifier_status_counts": dict(
                sorted(
                    Counter(
                        str(item.get("status") or "disabled")
                        for item in role_verification_items
                    ).items()
                )
            ),
            "role_verifier_claim_status_counts": dict(
                sorted(
                    Counter(
                        str(status)
                        for item in role_verification_items
                        for status, count in _mapping(
                            item.get("claim_status_counts")
                        ).items()
                        for _ in range(int(count))
                    ).items()
                )
            ),
            "role_verifier_total_duration_ms": sum(
                int(_mapping(item.get("call")).get("duration_ms") or 0)
                for item in role_verification_items
            ),
            "role_verifier_total_usage": _aggregate_model_usage(
                _mapping(item.get("call")) for item in role_verification_items
            ),
            "tenant_policy_advisor_provider_invocation_count": sum(
                int(
                    _mapping(item.get("tenant_policy_advisor")).get(
                        "provider_call_count"
                    )
                    or 0
                )
                for item in model_call_items
            ),
            "tenant_policy_advisor_usage_incomplete_case_count": sum(
                _mapping(item.get("tenant_policy_advisor")).get("present") is True
                and int(
                    _mapping(item.get("tenant_policy_advisor")).get(
                        "provider_call_count"
                    )
                    or 0
                )
                > 0
                and not _call_usage_complete(
                    _mapping(item.get("tenant_policy_advisor"))
                )
                for item in model_call_items
            ),
            "tenant_policy_advisor_total_usage": _aggregate_model_usage(
                _mapping(item.get("tenant_policy_advisor")) for item in model_call_items
            ),
            "model_usage_measurement_status": model_usage_measurement["status"],
            "model_usage_is_lower_bound": model_usage_measurement["is_lower_bound"],
            "model_provider_invocation_count": model_usage_measurement[
                "provider_invocation_count"
            ],
            "model_usage_incomplete_case_count": model_usage_measurement[
                "incomplete_case_count"
            ],
            "model_usage_incomplete_lane_count": model_usage_measurement[
                "incomplete_lane_count"
            ],
            "model_usage_origin_status": model_usage_measurement["usage_origin_status"],
            "model_usage_contains_estimates": model_usage_measurement[
                "contains_estimates"
            ],
            "measured_model_total_usage": model_usage_measurement["observed_usage"],
            "cost_measurement_status": "not_measured",
            "cost_amount": None,
            "cost_currency": None,
            "end_to_end_total_duration_ms": timing_measurement[
                "end_to_end_total_duration_ms"
            ],
            "end_to_end_average_duration_ms": timing_measurement[
                "end_to_end_average_duration_ms"
            ],
            "runtime_step_duration_totals_ms": timing_measurement[
                "runtime_step_duration_totals_ms"
            ],
        },
        "quality_measurement": {
            "status": "structural_safety_only",
            "structural_acceptance_case_count": statuses.get("passed", 0),
            "quality_finding_case_count": quality_statuses.get(
                "review_required",
                0,
            ),
            "accuracy": {
                "status": "not_measured",
                "human_ground_truth_case_count": 0,
                "precision": None,
                "recall": None,
                "f1": None,
                "reason": "the fixed cohort has no independent analyst ground-truth labels",
            },
        },
        "model_usage_measurement": model_usage_measurement,
        "timing_measurement": timing_measurement,
        "memory_validation": {
            "mode": "read_existing_only",
            "knowledge_review_artifacts_are_formal_memory_candidates": False,
            "formal_candidate_write_performed": False,
            "formal_record_write_performed": False,
            "replace_resets_output_database": True,
            "database_state": memory_database_state,
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
    role_verifier_enabled: bool,
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
    policy_payloads = [
        item.model_dump(mode="json", exclude_none=True) for item in policy_decisions
    ]
    decision_transitions = repository.list_decision_transitions(
        run_id=run_id,
        limit=20,
    )
    disposition_transitions = repository.list_disposition_transitions(
        run_id=run_id,
        limit=20,
    )
    action_authorizations = repository.list_action_authorizations(
        run_id=run_id,
        limit=20,
    )
    action_executions = repository.list_action_executions(
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
    analysis_output_quality = _mapping(run_payload.get("analysis_output_quality"))
    normalization = _mapping(run_payload.get("normalization_report"))
    source = _mapping(request.get("source"))
    classification = _mapping(request.get("classification"))
    labels = _mapping(classification.get("labels"))
    evidence_coverage = _mapping(request.get("evidence_coverage"))
    coverage_counts = _mapping(evidence_coverage.get("counts"))
    evidence_compaction = _mapping(request.get("evidence_compaction"))
    steps = (
        run_payload.get("steps") if isinstance(run_payload.get("steps"), list) else []
    )
    step_names = [str(_mapping(item).get("step_name")) for item in steps]
    role_verification = _role_verification_summary(run_payload)
    model_calls = {
        "runtime_total_duration_ms": int(
            run_payload.get("total_duration_ms")
            or sum(int(_mapping(item).get("duration_ms") or 0) for item in steps)
        ),
        "primary": _model_step_call_summary(steps, "analyze_llm"),
        "role_verifier": _model_step_call_summary(steps, "verify_roles_llm"),
        "tenant_policy_advisor": _tenant_policy_advisor_call_summary(policy_payloads),
    }
    timing = _case_timing_summary(
        run_payload=run_payload,
        record=record,
        steps=steps,
        model_calls=model_calls,
    )
    decision_transition_payloads = [
        item.model_dump(mode="json", exclude_none=True) for item in decision_transitions
    ]
    disposition_transition_payloads = [
        item.model_dump(mode="json", exclude_none=True)
        for item in disposition_transitions
    ]
    action_authorization_payloads = [
        item.model_dump(mode="json", exclude_none=True)
        for item in action_authorizations
    ]
    action_execution_payloads = [
        item.model_dump(mode="json", exclude_none=True) for item in action_executions
    ]
    latest_decision_transition = (
        decision_transition_payloads[0] if decision_transition_payloads else {}
    )
    effective_decision = _mapping(latest_decision_transition.get("after")) or {
        "verdict": decision.get("verdict"),
        "confidence": decision.get("confidence"),
        "evidence_state": decision.get("evidence_state"),
        "suggested_action": decision.get("suggested_action"),
        "needs_review": decision.get("needs_review"),
        "policy_version": decision.get("policy_version"),
    }
    memory_contributors = [
        contributor
        for transition in decision_transition_payloads
        for contributor in transition.get("contributors") or []
        if _mapping(contributor).get("kind") == "confirmed_memory"
    ]
    automatic_authorizations_without_memory = [
        authorization
        for authorization in action_authorization_payloads
        if authorization.get("mode") == "automatic_policy"
        and not any(
            _mapping(contributor).get("kind") == "confirmed_memory"
            for contributor in authorization.get("contributors") or []
        )
    ]
    mocked_execution_count = sum(
        _automation_execution_is_mocked(item) for item in action_execution_payloads
    )
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
        "evidence_compaction_complete": (
            int(evidence_compaction.get("high_value_omission_count") or 0) == 0
            and int(evidence_compaction.get("unrepresented_source_count") or 0) == 0
        ),
        "runtime_step_sequence_complete": _runtime_step_sequence_complete(
            step_names,
            role_verifier_enabled=role_verifier_enabled,
            triggered=bool(role_verification.get("triggered")),
        ),
        "all_required_runtime_steps_acceptable": _runtime_steps_acceptable(steps),
        "live_model_used": (
            bool(_mapping(model_calls.get("primary")).get("present"))
            and int(
                _mapping(model_calls.get("primary")).get("provider_call_count") or 0
            )
            > 0
        ),
        "analysis_output_quality_recorded": bool(analysis_output_quality),
        "role_verifier_configuration_matches": (
            bool(role_verification.get("configured")) == role_verifier_enabled
        ),
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
        "tenant_policy_decision_recorded": len(policy_decisions) == 1,
        "investigation_workflow_recorded": bool(record.get("investigation_workflow")),
        "investigation_did_not_mutate_runtime": _mapping(
            record.get("investigation_shadow_report")
        ).get("base_run_mutated")
        is False,
        "lead_agent_context_available_when_reviewable": (
            queue_item is None or lead_agent_artifact is not None
        ),
        "base_runtime_automation_guarded": decision.get("automation_allowed") is False,
        "governed_decision_transition_recorded": (
            len(decision_transition_payloads) == 1
        ),
        "no_synthetic_action_authorization": not action_authorization_payloads,
        "no_synthetic_action_execution": not action_execution_payloads,
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
    investigation_report = _mapping(record.get("investigation_shadow_report"))
    base_runtime_decision = {
        "verdict": decision.get("verdict"),
        "confidence": decision.get("confidence"),
        "confidence_source": decision.get("confidence_source"),
        "evidence_state": decision.get("evidence_state"),
        "suggested_action": decision.get("suggested_action"),
        "needs_review": decision.get("needs_review"),
        "automation_allowed": decision.get("automation_allowed"),
        "policy_version": decision.get("policy_version"),
        "review_reasons": decision.get("review_reasons"),
    }
    automation_summary = {
        "simulation_enabled": False,
        "simulation_policy": None,
        "decision_transition_count": len(decision_transition_payloads),
        "decision_transition_kind": latest_decision_transition.get("transition_kind"),
        "decision_stages": latest_decision_transition.get("stages") or [],
        "memory_stage": _decision_stage(
            latest_decision_transition,
            "memory",
        ),
        "tenant_policy_stage": _decision_stage(
            latest_decision_transition,
            "tenant_policy",
        ),
        "effective_stage": _decision_stage(
            latest_decision_transition,
            "effective",
        ),
        "effective_disposition": latest_decision_transition.get(
            "effective_disposition"
        ),
        "memory_contributor_count": len(memory_contributors),
        "effective_decision_changed": bool(
            latest_decision_transition
            and _mapping(latest_decision_transition.get("before")) != effective_decision
        ),
        "selected_rule_id": _selected_automation_rule_id(
            disposition_transition_payloads,
            action_authorization_payloads,
        ),
        "disposition_transition_count": len(disposition_transition_payloads),
        "action_authorization_count": len(action_authorization_payloads),
        "action_authorization_decisions": [
            item.get("decision") for item in action_authorization_payloads
        ],
        "automatic_authorization_without_memory_count": len(
            automatic_authorizations_without_memory
        ),
        "action_execution_count": len(action_execution_payloads),
        "action_execution_statuses": [
            item.get("status") for item in action_execution_payloads
        ],
        "mocked_execution_count": mocked_execution_count,
        "real_external_call_count": 0,
    }
    final_conclusion = {
        "verdict": effective_decision.get("verdict"),
        "confidence": effective_decision.get("confidence"),
        "summary": analysis.get("summary"),
        "reason": analysis.get("reason"),
        "recommended_action": analysis.get("recommended_action"),
        "primary_scenario": primary_scenario,
        "activity_stage": (
            primary_scenario.get("activity_stage")
            if primary_scenario is not None
            else None
        ),
        "base_runtime_decision": base_runtime_decision,
        "effective_decision": effective_decision,
        "evidence_state": effective_decision.get("evidence_state"),
        "needs_review": effective_decision.get("needs_review"),
        "automation_allowed": decision.get("automation_allowed"),
        "automation": automation_summary,
        "grounded_evidence_count": grounding.get("grounded_count"),
        "ungrounded_evidence_count": grounding.get("ungrounded_count"),
        "role_verification": role_verification,
        "analysis_output_quality": analysis_output_quality,
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
        "timing": timing,
    }
    quality_findings = [
        *_analysis_output_quality_findings(analysis_output_quality),
        *_grounding_quality_findings(grounding),
        *_role_verification_quality_findings(role_verification),
    ]

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
            "timing": timing,
            "steps": steps,
            "request_journal": run_payload.get("request_journal"),
            "provider_request_journals": run_payload.get("provider_request_journals"),
            "role_verification_trigger": run_payload.get("role_verification_trigger"),
            "role_adjudication_verification": run_payload.get(
                "role_adjudication_verification"
            ),
            "analysis_output_quality": analysis_output_quality,
        },
        "06-llm-analysis.json": analysis,
        "06a-role-verification.json": {
            "summary": role_verification,
            "trigger": run_payload.get("role_verification_trigger"),
            "verification": run_payload.get("role_adjudication_verification"),
        },
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
                "This is the bounded context available to DeerFlow Lead Agent. No advisory chat response is treated as the authoritative Runtime conclusion."
            ),
        },
        "11-knowledge-candidates.json": knowledge_candidate_review,
        "12-effective-decision-and-automation.json": {
            "base_runtime_decision": base_runtime_decision,
            "effective_decision": effective_decision,
            "decision_transitions": decision_transition_payloads,
            "disposition_transitions": disposition_transition_payloads,
            "action_authorizations": action_authorization_payloads,
            "action_executions": action_execution_payloads,
            "summary": automation_summary,
            "authority_boundary": {
                "analysis_run_decision_mutated": False,
                "memory_is_action_authority": False,
                "automatic_policy_can_authorize_without_memory": True,
                "synthetic_automation_policy_installed": False,
                "simulation_external_calls_performed": 0,
            },
        },
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
        "role_verification": role_verification,
        "analysis_output_quality": analysis_output_quality,
        "evidence_compaction": evidence_compaction,
        "model_calls": model_calls,
        "timing": timing,
        "knowledge_candidates": {
            "candidate_count": knowledge_candidate_review["candidate_count"],
            "grounded_candidate_count": knowledge_candidate_review[
                "grounded_candidate_count"
            ],
            "review_status": "pending_review",
            "artifact": str(output_dir / "11-knowledge-candidates.json"),
            "memory_write_performed": False,
            "formal_memory_candidate_write_performed": False,
            "formal_memory_record_write_performed": False,
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
        "automation": automation_summary,
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
        "decision_source": decision.get("decision_source"),
        "selected_rule_id": decision.get("selected_rule_id"),
        "policy_mode": decision.get("policy_mode"),
        "response_posture": decision.get("response_posture"),
        "recommended_disposition": decision.get("recommended_disposition"),
        "review_effect": decision.get("review_effect"),
        "auto_apply_allowed": decision.get("auto_apply_allowed"),
        "shadow_only": decision.get("shadow_only"),
        "advisor": _advisor_conclusion(decision),
    }


def _decision_stage(
    transition: Mapping[str, Any],
    stage_name: str,
) -> dict[str, Any] | None:
    for item in transition.get("stages") or []:
        stage = _mapping(item)
        if stage.get("stage") == stage_name:
            return stage
    return None


def _advisor_conclusion(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    provenance = _mapping(decision.get("advisor_provenance"))
    advice = _mapping(decision.get("advisor_advice"))
    if not provenance and not advice:
        return None
    return {
        "status": provenance.get("status"),
        "model_name": provenance.get("model_name"),
        "prompt_version": provenance.get("prompt_version"),
        "prompt_hash": provenance.get("prompt_hash"),
        "skill_name": provenance.get("skill_name"),
        "skill_version": provenance.get("skill_version"),
        "skill_hash": provenance.get("skill_hash"),
        "repair_applied": provenance.get("repair_applied"),
        "error_code": provenance.get("error_code"),
        "usage": _mapping(provenance.get("usage")),
        "policy_signal_keys": advice.get("policy_signal_keys") or [],
        "evidence_refs": advice.get("evidence_refs") or [],
        "reasoning_refs": advice.get("reasoning_refs") or [],
        "context_refs": advice.get("context_refs") or [],
    }


def _tenant_policy_advisor_call_summary(
    decisions: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    decision = decisions[0] if decisions else {}
    provenance = _mapping(decision.get("advisor_provenance"))
    if not provenance:
        return {
            "present": False,
            "status": None,
            "model_name": None,
            "prompt_version": None,
            "usage": {},
            "usage_measurement": {},
            "provider_call_count": 0,
            "usage_complete": True,
            "error_code": None,
        }

    status = str(provenance.get("status") or "")
    error_code = str(provenance.get("error_code") or "")
    provider_call_count = int(
        status == "completed"
        or (status == "failed_closed" and not error_code.startswith("prompt_build."))
    )
    usage = _mapping(provenance.get("usage"))
    measurement_status = str(
        provenance.get("usage_measurement_status") or "unavailable"
    )
    return {
        "present": True,
        "status": status or None,
        "model_name": provenance.get("model_name"),
        "prompt_version": provenance.get("prompt_version"),
        "usage": usage,
        "usage_measurement": {
            "status": measurement_status,
            "method": provenance.get("usage_estimation_method"),
            "estimated": provenance.get("usage_is_estimated") is True,
        },
        "admission_wait_duration_ms": provenance.get("admission_wait_duration_ms"),
        "provider_duration_ms": provenance.get("provider_duration_ms"),
        "client_total_duration_ms": provenance.get("client_total_duration_ms"),
        "provider_call_count": provider_call_count,
        "usage_complete": provider_call_count == 0
        or measurement_status in {"reported", "estimated", "mixed"},
        "error_code": error_code or None,
    }


def _selected_automation_rule_id(
    dispositions: Sequence[Mapping[str, Any]],
    authorizations: Sequence[Mapping[str, Any]],
) -> str | None:
    for item in (*authorizations, *dispositions):
        value = item.get("selected_rule_id")
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _automation_execution_is_mocked(payload: Mapping[str, Any]) -> bool:
    result_payload = _mapping(payload.get("result_payload"))
    adapter_payload = _mapping(result_payload.get("payload"))
    return (
        adapter_payload.get("mocked") is True
        and adapter_payload.get("provider_mode") == "e2e_simulation"
        and adapter_payload.get("external_side_effect") == "simulated_only"
    )


def _analysis_output_quality_findings(
    quality: Mapping[str, Any],
) -> list[str]:
    status = str(quality.get("status") or "missing")
    if status == "missing":
        return ["analysis_output_quality_missing"]
    if status == "degraded":
        sections = ",".join(
            str(item) for item in quality.get("degraded_sections") or []
        )
        return [f"analysis_output_degraded:{sections or 'unknown'}"]
    if status == "deterministic_fallback":
        return ["analysis_output_used_deterministic_fallback"]
    return []


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


def _runtime_step_sequence_complete(
    step_names: Sequence[str],
    *,
    role_verifier_enabled: bool,
    triggered: bool,
) -> bool:
    if not role_verifier_enabled:
        return tuple(step_names) == EXPECTED_RUNTIME_STEPS
    verifier_steps = [*BASE_RUNTIME_STEPS_BEFORE_DECISION, "role_verification_gate"]
    if triggered:
        verifier_steps.append("verify_roles_llm")
    verifier_steps.append("decide")
    return tuple(step_names) == tuple(verifier_steps)


def _runtime_steps_acceptable(steps: Sequence[Any]) -> bool:
    if not steps:
        return False
    for item in steps:
        step = _mapping(item)
        if step.get("status") == "success":
            continue
        metadata = _mapping(step.get("metadata"))
        if (
            step.get("step_name") == "verify_roles_llm"
            and step.get("status") == "failed"
            and metadata.get("optional") is True
            and metadata.get("fail_closed") is True
        ):
            continue
        return False
    return True


def _role_verification_quality_findings(
    role_verification: Mapping[str, Any],
) -> list[str]:
    status = role_verification.get("status")
    if status == "challenged":
        return ["first_pass_role_claims_challenged"]
    if status == "unresolved":
        return ["role_verification_unresolved"]
    if status == "unavailable":
        return ["role_verifier_unavailable"]
    return []


def _model_step_call_summary(
    steps: Sequence[Any],
    step_name: str,
) -> dict[str, Any]:
    step = next(
        (
            _mapping(item)
            for item in steps
            if _mapping(item).get("step_name") == step_name
        ),
        {},
    )
    metadata = _mapping(step.get("metadata"))
    return {
        "present": bool(step),
        "status": step.get("status"),
        "model_name": metadata.get("model_name"),
        "duration_ms": step.get("duration_ms"),
        "usage": _mapping(metadata.get("usage")),
        "usage_measurement": _mapping(metadata.get("usage_measurement")),
        "provider_calls": (
            metadata.get("provider_calls")
            if isinstance(metadata.get("provider_calls"), list)
            else []
        ),
        "provider_call_measured_duration_ms": metadata.get(
            "provider_call_measured_duration_ms"
        ),
        "provider_call_count": int(metadata.get("provider_call_count") or 0),
        "usage_complete": (
            metadata.get("usage_complete") is True
            if "usage_complete" in metadata
            else int(metadata.get("provider_call_count") or 0) == 0
            or bool(_mapping(metadata.get("usage")))
        ),
        "output_retry_attempted": metadata.get("output_retry_attempted") is True,
        "output_retry_kind": metadata.get("output_retry_kind"),
    }


def _reasoning_provenance_summary(
    model_call_items: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    calls: list[Mapping[str, Any]] = []
    for item in model_call_items:
        for lane in ("primary", "role_verifier"):
            raw_calls = _mapping(item.get(lane)).get("provider_calls")
            if isinstance(raw_calls, list):
                calls.extend(_mapping(call) for call in raw_calls)

    completed = [call for call in calls if call.get("status") == "completed"]
    observed = [
        call for call in completed if call.get("response_reasoning_present") is True
    ]
    absent = [
        call for call in completed if call.get("response_reasoning_present") is False
    ]
    return {
        "provider_call_count": len(calls),
        "completed_call_count": len(completed),
        "thinking_enabled_requested_count": sum(
            call.get("thinking_enabled_requested") is True for call in calls
        ),
        "thinking_disabled_requested_count": sum(
            call.get("thinking_enabled_requested") is False for call in calls
        ),
        "thinking_request_unreported_count": sum(
            not isinstance(call.get("thinking_enabled_requested"), bool)
            for call in calls
        ),
        "response_reasoning_observed_count": len(observed),
        "response_reasoning_absent_count": len(absent),
        "response_reasoning_unreported_count": len(completed)
        - len(observed)
        - len(absent),
        "response_reasoning_total_chars": sum(
            int(call.get("response_reasoning_chars") or 0) for call in observed
        ),
        "interpretation": (
            "requested records the client option; observed records only reasoning content exposed by the provider response"
        ),
    }


def _case_timing_summary(
    *,
    run_payload: Mapping[str, Any],
    record: Mapping[str, Any],
    steps: Sequence[Any],
    model_calls: Mapping[str, Any],
) -> dict[str, Any]:
    execution = _mapping(record.get("execution"))
    phase_timings = _mapping(execution.get("phase_timings_ms"))
    step_timings = [
        {
            "step_name": step.get("step_name"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        }
        for item in steps
        if (step := _mapping(item))
    ]
    runtime_total = int(
        run_payload.get("total_duration_ms")
        or model_calls.get("runtime_total_duration_ms")
        or 0
    )
    end_to_end_total = execution.get("end_to_end_total_duration_ms")
    if not isinstance(end_to_end_total, (int, float)) or isinstance(
        end_to_end_total,
        bool,
    ):
        end_to_end_total = execution.get("duration_ms")
    return {
        "runtime_total_duration_ms": runtime_total,
        "runtime_step_duration_sum_ms": sum(
            int(item.get("duration_ms") or 0) for item in step_timings
        ),
        "runtime_steps": step_timings,
        "analysis_service_duration_ms": phase_timings.get(
            "analysis_service_duration_ms"
        ),
        "memory_pattern_duration_ms": phase_timings.get("memory_pattern_duration_ms"),
        "investigation_workflow_duration_ms": phase_timings.get(
            "investigation_workflow_duration_ms"
        ),
        "investigation_reporting_duration_ms": phase_timings.get(
            "investigation_reporting_duration_ms"
        ),
        "end_to_end_total_duration_ms": end_to_end_total,
        "primary_model_call_duration_ms": _mapping(model_calls.get("primary")).get(
            "provider_call_measured_duration_ms"
        ),
        "role_verifier_call_duration_ms": _mapping(
            model_calls.get("role_verifier")
        ).get("provider_call_measured_duration_ms"),
        "tenant_policy_advisor_client_duration_ms": _mapping(
            model_calls.get("tenant_policy_advisor")
        ).get("client_total_duration_ms"),
    }


def _role_verification_summary(run_payload: Mapping[str, Any]) -> dict[str, Any]:
    trigger = _mapping(run_payload.get("role_verification_trigger"))
    verification = _mapping(run_payload.get("role_adjudication_verification"))
    steps = (
        run_payload.get("steps") if isinstance(run_payload.get("steps"), list) else []
    )
    verifier_call = _model_step_call_summary(steps, "verify_roles_llm")
    verifier_step = next(
        (
            _mapping(item)
            for item in steps
            if _mapping(item).get("step_name") == "verify_roles_llm"
        ),
        {},
    )
    metadata = _mapping(verifier_step.get("metadata"))
    claim_reviews = (
        verification.get("claim_reviews")
        if isinstance(verification.get("claim_reviews"), list)
        else []
    )
    claim_statuses = Counter(
        str(_mapping(item).get("status") or "unknown") for item in claim_reviews
    )
    configured = run_payload.get("pipeline_version") == ROLE_VERIFIER_PIPELINE_VERSION
    triggered = trigger.get("triggered") is True
    if not configured:
        status = "disabled"
    elif not triggered:
        status = "not_triggered"
    else:
        status = str(verification.get("status") or "unavailable")
    journals = (
        run_payload.get("provider_request_journals")
        if isinstance(run_payload.get("provider_request_journals"), list)
        else []
    )
    verifier_journals = [
        _mapping(item)
        for item in journals
        if _mapping(item).get("provider_purpose")
        in {"role_verification", "role_verification_retry"}
    ]
    verifier_call = dict(verifier_call)
    verifier_call["provider_call_count"] = max(
        int(verifier_call.get("provider_call_count") or 0),
        len(verifier_journals),
    )
    verifier_call["output_retry_attempted"] = verifier_call.get(
        "output_retry_attempted"
    ) is True or any(
        item.get("provider_purpose") == "role_verification_retry"
        for item in verifier_journals
    )
    return {
        "configured": configured,
        "triggered": triggered,
        "logical_review_count": int(triggered),
        "projected_candidate_claim_count": int(trigger.get("claim_count") or 0),
        "atomic_claim_count": (
            int(trigger.get("claim_count") or 0) if triggered else 0
        ),
        "status": status,
        "trigger_reasons": trigger.get("reasons") or [],
        "minimum_confidence": trigger.get("minimum_confidence"),
        "claim_count": int(trigger.get("claim_count") or 0),
        "claim_status_counts": dict(sorted(claim_statuses.items())),
        "primary_model_name": verification.get("primary_model_name"),
        "verifier_model_name": (
            verification.get("verifier_model_name") or metadata.get("model_name")
        ),
        "same_model_verification": verification.get("same_model_verification"),
        "prompt_version": verification.get("prompt_version"),
        "parser_version": verification.get("parser_version"),
        "repair_applied": verification.get("repair_applied"),
        "failure_kind": verification.get("failure_kind"),
        "call": verifier_call,
        "provider_journals": [
            {
                "status": item.get("status"),
                "provider_purpose": item.get("provider_purpose"),
                "model_name": item.get("model_name"),
                "prompt_version": item.get("prompt_version"),
                "failure_kind": item.get("failure_kind"),
                "failure_retryable": item.get("failure_retryable"),
            }
            for item in verifier_journals
        ],
        "automation_allowed": verification.get("automation_allowed", False),
    }


def _aggregate_model_usage(calls: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for call in calls:
        for key, value in _mapping(call.get("usage")).items():
            if isinstance(value, int) and not isinstance(value, bool):
                totals[str(key)] += value
    return dict(sorted(totals.items()))


def _model_usage_measurement(
    case_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    lane_calls: dict[str, list[dict[str, Any]]] = {
        "primary_analysis": [],
        "role_verifier": [],
        "tenant_policy_advisor": [],
    }
    incomplete_cases: set[str] = set()
    incomplete_lanes: list[dict[str, str]] = []

    for item in case_results:
        alert_id = str(item.get("alert_id") or "unknown")
        model_calls = _mapping(item.get("model_calls"))
        role_verification = _mapping(item.get("role_verification"))
        calls = {
            "primary_analysis": _mapping(model_calls.get("primary")),
            "role_verifier": _mapping(role_verification.get("call")),
            "tenant_policy_advisor": _mapping(model_calls.get("tenant_policy_advisor")),
        }
        for lane_name, call in calls.items():
            lane_calls[lane_name].append(call)
            provider_call_count = int(call.get("provider_call_count") or 0)
            if provider_call_count > 0 and not _call_usage_complete(call):
                incomplete_cases.add(alert_id)
                incomplete_lanes.append(
                    {
                        "alert_id": alert_id,
                        "lane": lane_name,
                    }
                )

    lane_summary = {
        lane_name: {
            "provider_invocation_count": sum(
                int(call.get("provider_call_count") or 0) for call in calls
            ),
            "observed_usage": _aggregate_model_usage(calls),
            "usage_incomplete_case_count": sum(
                int(call.get("provider_call_count") or 0) > 0
                and not _call_usage_complete(call)
                for call in calls
            ),
            "usage_measurement_status_counts": dict(
                sorted(
                    Counter(
                        _call_usage_origin(call)
                        for call in calls
                        if int(call.get("provider_call_count") or 0) > 0
                    ).items()
                )
            ),
        }
        for lane_name, calls in lane_calls.items()
    }
    observed_usage = _aggregate_model_usage(
        {
            "usage": usage,
        }
        for usage in (lane["observed_usage"] for lane in lane_summary.values())
    )
    origin_counts = Counter(
        _call_usage_origin(call)
        for calls in lane_calls.values()
        for call in calls
        if int(call.get("provider_call_count") or 0) > 0
    )
    contains_estimates = any(
        origin_counts.get(status, 0) for status in ("estimated", "mixed")
    )
    if not origin_counts:
        usage_origin_status = "not_called"
    elif set(origin_counts) == {"reported"}:
        usage_origin_status = "reported"
    elif set(origin_counts) == {"estimated"}:
        usage_origin_status = "estimated"
    elif "unavailable" in origin_counts or "unknown" in origin_counts:
        usage_origin_status = "partial"
    else:
        usage_origin_status = "mixed"
    return {
        "status": "partial" if incomplete_lanes else "complete",
        "is_lower_bound": bool(incomplete_lanes),
        "usage_origin_status": usage_origin_status,
        "usage_origin_status_counts": dict(sorted(origin_counts.items())),
        "contains_estimates": contains_estimates,
        "provider_invocation_count": sum(
            int(lane["provider_invocation_count"]) for lane in lane_summary.values()
        ),
        "observed_usage": observed_usage,
        "incomplete_case_count": len(incomplete_cases),
        "incomplete_lane_count": len(incomplete_lanes),
        "incomplete_lanes": incomplete_lanes,
        "lanes": lane_summary,
        "cost_measurement": {
            "status": "not_measured",
            "amount": None,
            "currency": None,
            "reason": "no reviewed model price table is configured for this validation",
        },
    }


def _timing_measurement(
    case_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    end_to_end_values: list[float] = []
    runtime_values: list[float] = []
    step_totals: Counter[str] = Counter()
    step_counts: Counter[str] = Counter()
    for item in case_results:
        timing = _mapping(item.get("timing"))
        end_to_end = timing.get("end_to_end_total_duration_ms")
        if isinstance(end_to_end, (int, float)) and not isinstance(
            end_to_end,
            bool,
        ):
            end_to_end_values.append(float(end_to_end))
        runtime_total = timing.get("runtime_total_duration_ms")
        if isinstance(runtime_total, (int, float)) and not isinstance(
            runtime_total,
            bool,
        ):
            runtime_values.append(float(runtime_total))
        for raw_step in timing.get("runtime_steps") or []:
            step = _mapping(raw_step)
            name = step.get("step_name")
            duration = step.get("duration_ms")
            if (
                not isinstance(name, str)
                or not isinstance(
                    duration,
                    (int, float),
                )
                or isinstance(duration, bool)
            ):
                continue
            step_totals[name] += float(duration)
            step_counts[name] += 1
    return {
        "case_count": len(case_results),
        "end_to_end_measured_case_count": len(end_to_end_values),
        "end_to_end_total_duration_ms": round(sum(end_to_end_values), 3),
        "end_to_end_average_duration_ms": (
            round(sum(end_to_end_values) / len(end_to_end_values), 3)
            if end_to_end_values
            else None
        ),
        "runtime_total_duration_ms": round(sum(runtime_values), 3),
        "runtime_average_duration_ms": (
            round(sum(runtime_values) / len(runtime_values), 3)
            if runtime_values
            else None
        ),
        "runtime_step_duration_totals_ms": {
            key: round(float(value), 3) for key, value in sorted(step_totals.items())
        },
        "runtime_step_average_duration_ms": {
            key: round(float(step_totals[key]) / step_counts[key], 3)
            for key in sorted(step_totals)
        },
    }


def _memory_database_state(
    repository: SqlAlchemyAlertRepository,
) -> dict[str, Any]:
    candidates = repository.list_memory_candidates(limit=100_000)
    records = repository.list_memory_records(limit=100_000)
    observations = repository.list_memory_pattern_observations(limit=100_000)
    return {
        "candidate_count": len(candidates),
        "candidate_status_counts": dict(
            sorted(Counter(item.status.value for item in candidates).items())
        ),
        "record_count": len(records),
        "record_status_counts": dict(
            sorted(Counter(item.status.value for item in records).items())
        ),
        "retrieval_enabled_record_count": sum(
            item.retrieval_enabled for item in records
        ),
        "pattern_observation_count": len(observations),
    }


def _call_usage_complete(call: Mapping[str, Any]) -> bool:
    if "usage_complete" in call:
        return call.get("usage_complete") is True
    return int(call.get("provider_call_count") or 0) == 0 or bool(
        _mapping(call.get("usage"))
    )


def _call_usage_origin(call: Mapping[str, Any]) -> str:
    if int(call.get("provider_call_count") or 0) == 0:
        return "not_called"
    measurement = _mapping(call.get("usage_measurement"))
    status = str(measurement.get("status") or "")
    if status in {"reported", "estimated", "mixed", "unavailable"}:
        return status
    return "unknown" if _mapping(call.get("usage")) else "unavailable"


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
    summary = _mapping(report.get("summary"))
    lines = [
        "# SOC Fixed-Cohort End-to-End Validation",
        "",
        f"- Status: `{report.get('acceptance_status')}`",
        f"- Quality: `{report.get('quality_status')}`",
        f"- Generated: `{report.get('generated_at')}`",
        "- Pass meaning: structural/safety chain passed; this is not a model-accuracy claim",
        "- Authoritative result: immutable base Runtime decision plus append-only effective-decision/action lineage",
        "- Provider mode: simulated read-only; results do not close real integration debt",
        "- Lead Agent: bounded context is generated, but no chat text replaces Runtime truth",
        (
            "- Governed automation: "
            f"{summary.get('decision_transition_count', 0)} decision transitions, "
            f"{summary.get('automatic_authorization_without_memory_count', 0)} "
            "automatic authorizations without Memory, "
            f"{summary.get('mocked_action_execution_count', 0)} mocked executions, "
            "0 real external calls"
        ),
        (
            "- PingAn policy decisions: "
            f"sources={summary.get('tenant_policy_decision_source_counts', {})}, "
            f"rules={summary.get('tenant_policy_selected_rule_counts', {})}, "
            f"advisor={summary.get('tenant_policy_advisor_status_counts', {})}, "
            f"dispositions={summary.get('tenant_policy_disposition_counts', {})}"
        ),
        (
            "- Role verifier: "
            f"configured={summary.get('role_verifier_configured_case_count', 0)}, "
            f"triggered={summary.get('role_verifier_triggered_case_count', 0)}, "
            f"logical reviews={summary.get('role_verifier_logical_review_count', 0)}, "
            f"atomic claims={summary.get('role_verifier_atomic_claim_count', 0)}, "
            f"case calls={summary.get('role_verifier_call_count', 0)}, "
            f"provider invocations={summary.get('role_verifier_provider_invocation_count', 0)}, "
            f"retry cases={summary.get('role_verifier_output_retry_case_count', 0)}, "
            f"usage-incomplete cases={summary.get('role_verifier_usage_incomplete_case_count', 0)}, "
            f"statuses={summary.get('role_verifier_status_counts', {})}, "
            f"claim statuses={summary.get('role_verifier_claim_status_counts', {})}"
        ),
        (
            f"- Primary analysis output: statuses={summary.get('analysis_output_quality_status_counts', {})}, degraded sections={summary.get('analysis_output_degraded_section_counts', {})}"
        ),
        (
            "- Model usage: "
            f"observed={summary.get('measured_model_total_usage', {})}, "
            f"provider invocations={summary.get('model_provider_invocation_count', 0)}, "
            f"status={summary.get('model_usage_measurement_status')}, "
            f"origin={summary.get('model_usage_origin_status')}, "
            f"contains estimates={summary.get('model_usage_contains_estimates')}, "
            f"lower_bound={summary.get('model_usage_is_lower_bound')}, "
            f"incomplete cases={summary.get('model_usage_incomplete_case_count', 0)}"
        ),
        (
            "- Evidence compaction: "
            f"messages={summary.get('evidence_compaction_source_message_count', 0)}, "
            f"groups={summary.get('evidence_compaction_behavior_group_count', 0)}, "
            f"profiles={summary.get('evidence_compaction_profile_count', 0)}, "
            f"collapsed repetitions={summary.get('evidence_compaction_collapsed_repetition_count', 0)}, "
            f"non-dominant profiles={summary.get('evidence_compaction_non_dominant_profile_count', 0)}, "
            f"high-value omissions={summary.get('evidence_compaction_high_value_omission_count', 0)}, "
            f"unrepresented sources={summary.get('evidence_compaction_unrepresented_source_count', 0)}"
        ),
        (
            "- Monetary cost: not measured; no reviewed model-price table is configured for this validation"
        ),
        (
            f"- Timing: end-to-end total={summary.get('end_to_end_total_duration_ms')} ms, average={summary.get('end_to_end_average_duration_ms')} ms; per-step timings remain in each case's 05-runtime-trace.json"
        ),
        (
            "- Quality boundary: structural/safety only; accuracy is not measured without independent analyst labels"
        ),
        f"- Confirmed Memory contributors: {summary.get('memory_contributor_count', 0)}",
        (
            f"- Formal Memory DB: candidates={summary.get('memory_database_candidate_count', 0)}, records={summary.get('memory_database_record_count', 0)}, knowledge-review files are not database Memory"
        ),
        "",
        "| Alert | Source | Topic | Primary scenario | Compaction | Role verifier | Base -> Effective | Confidence | Evidence | PingAn policy | Action | Queue | Quality | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for item in report.get("cases") or []:
        if not isinstance(item, Mapping):
            continue
        source = _mapping(item.get("source"))
        conclusion = _mapping(item.get("final_conclusion"))
        scenario = _mapping(conclusion.get("primary_scenario"))
        grounding = _mapping(item.get("grounding"))
        review = _mapping(item.get("review"))
        base_decision = _mapping(conclusion.get("base_runtime_decision"))
        effective_decision = _mapping(conclusion.get("effective_decision"))
        automation = _mapping(item.get("automation"))
        tenant_policy = _mapping(item.get("tenant_policy"))
        role_verification = _mapping(item.get("role_verification"))
        analysis_output_quality = _mapping(item.get("analysis_output_quality"))
        evidence_compaction = _mapping(item.get("evidence_compaction"))
        tenant_policy_label = tenant_policy.get("selected_rule_id") or (
            f"{tenant_policy.get('decision_source')}:{tenant_policy.get('evaluation_status')}"
        )
        execution_statuses = automation.get("action_execution_statuses") or []
        automation_label = (
            ", ".join(str(value) for value in execution_statuses)
            or automation.get("selected_rule_id")
            or "no rule"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(item.get("alert_id")),
                    _md(source.get("source_type")),
                    _md(source.get("topic")),
                    _md(scenario.get("scenario_name") or scenario.get("scenario_key")),
                    _md(
                        f"{evidence_compaction.get('source_message_count', 0)} msg -> {evidence_compaction.get('behavior_group_count', 0)} group / {evidence_compaction.get('profile_count', 0)} profile"
                    ),
                    _md(
                        f"{role_verification.get('status')}"
                        + (
                            f" ({role_verification.get('claim_count')} claims)"
                            if role_verification.get("triggered")
                            else ""
                        )
                    ),
                    _md(
                        f"{base_decision.get('verdict') or conclusion.get('verdict')} -> {effective_decision.get('verdict') or conclusion.get('verdict')}"
                    ),
                    _md(conclusion.get("confidence")),
                    _md(
                        f"{grounding.get('grounded_count', 0)} grounded / {grounding.get('ungrounded_count', 0)} rejected"
                    ),
                    _md(tenant_policy_label),
                    _md(automation_label),
                    _md(review.get("priority") or "none"),
                    _md(
                        f"{item.get('quality_status')}/{analysis_output_quality.get('status') or 'missing'}"
                    ),
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
            "Each `cases/<alert_id>/` directory contains `00-ingress.json` through `12-effective-decision-and-automation.json`, plus `06a-role-verification.json` and `final-conclusion.json`.",
            "The numbered files are chronological; no separate historical validation directory is needed to understand one alert.",
            "Candidate knowledge is consolidated in `knowledge-review/REVIEW.md`; it remains pending human review and is never written to Memory automatically.",
            "",
        ]
    )
    return "\n".join(lines)


def _execution_environment(
    python_executable: Path,
    *,
    thinking_enabled: bool = False,
    tenant_policy_advisor_enabled: bool = True,
    role_verifier_enabled: bool = False,
    role_verifier_model_name: str | None = None,
    role_verifier_minimum_confidence: float = DEFAULT_ROLE_VERIFIER_MINIMUM_CONFIDENCE,
    output_retry_attempts: int = 1,
) -> dict[str, str]:
    env = os.environ.copy()
    for key in (
        "SOC_AUTOMATION_POLICY_PATH",
        "SOC_AUTOMATION_ENVIRONMENT",
        "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS",
        "SOC_ROLE_VERIFIER_MODEL",
        "SOC_TENANT_POLICY_SKILL_PATH",
    ):
        env.pop(key, None)
    env.update(
        {
            "SOC_LLM_SENSITIVE_EVIDENCE_MODE": "full",
            "SOC_LLM_THINKING_ENABLED": ("true" if thinking_enabled else "false"),
            "SOC_TENANT_POLICY_ENABLED": "true",
            "SOC_TENANT_DISPOSITION_POLICY_PATH": str(PINGAN_POLICY),
            "SOC_TENANT_POLICY_ENVIRONMENT": "dev",
            "SOC_TENANT_POLICY_EVENT_TIMEZONE": "Asia/Shanghai",
            "SOC_TENANT_POLICY_ADVISOR_MODE": (
                "llm" if tenant_policy_advisor_enabled else "off"
            ),
            "SOC_PINGAN_ASSET_MCP_PYTHON": str(python_executable),
            "SOC_PINGAN_ASSET_MCP_SERVER": str(
                BACKEND_ROOT / "scripts/soc_pingan_asset_mcp_server.py"
            ),
            "SOC_PINGAN_SECURITY_TAG_MCP_PYTHON": str(python_executable),
            "SOC_PINGAN_SECURITY_TAG_MCP_SERVER": str(
                BACKEND_ROOT / "scripts/soc_pingan_security_tag_mcp_server.py"
            ),
            "SOC_ROLE_VERIFIER_ENABLED": ("true" if role_verifier_enabled else "false"),
            "SOC_ROLE_VERIFIER_MIN_CONFIDENCE": str(role_verifier_minimum_confidence),
            "SOC_LLM_OUTPUT_RETRY_ATTEMPTS": str(output_retry_attempts),
        }
    )
    if tenant_policy_advisor_enabled:
        env["SOC_TENANT_POLICY_SKILL_PATH"] = str(PINGAN_POLICY_SKILL)
    if role_verifier_enabled and role_verifier_model_name:
        env["SOC_ROLE_VERIFIER_MODEL"] = role_verifier_model_name
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
    thinking_enabled: bool,
    batch_command: Sequence[str],
    report: Mapping[str, Any],
    role_verifier_enabled: bool,
    role_verifier_model_name: str | None,
    role_verifier_minimum_confidence: float,
    output_retry_attempts: int,
    tenant_policy_advisor_enabled: bool,
) -> None:
    payload = {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "git_commit": _git_commit(),
        "source": str(source),
        "source_sha256": _sha256_file(source),
        "cases_sha256": _canonical_sha256(cases_manifest),
        "model_name": model_name,
        "thinking_enabled_requested": thinking_enabled,
        "role_verifier_enabled": role_verifier_enabled,
        "role_verifier_model_name": role_verifier_model_name,
        "role_verifier_minimum_confidence": role_verifier_minimum_confidence,
        "output_retry_attempts": output_retry_attempts,
        "database": str(paths.database),
        "batch_command": list(batch_command),
        "summary": report.get("summary"),
        "acceptance_status": report.get("acceptance_status"),
        "contains_configuration_secrets": False,
        "may_contain_source_secrets": True,
        "contains_sensitive_alert_payloads": True,
        "provider_mode": "simulated_read_only",
        "tenant_policy_enabled": True,
        "tenant_policy": _display_path(PINGAN_POLICY),
        "tenant_policy_sha256": _sha256_file(PINGAN_POLICY),
        "tenant_policy_advisor_mode": (
            "llm" if tenant_policy_advisor_enabled else "off"
        ),
        "tenant_policy_skill": (
            _display_path(PINGAN_POLICY_SKILL)
            if tenant_policy_advisor_enabled
            else None
        ),
        "tenant_policy_skill_sha256": (
            _sha256_file(PINGAN_POLICY_SKILL) if tenant_policy_advisor_enabled else None
        ),
        "synthetic_automation_policy_installed": False,
        "real_external_action_call_count": 0,
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
        if not 0.0 <= args.role_verifier_min_confidence <= 1.0:
            raise ValueError("--role-verifier-min-confidence must be within [0, 1]")
        role_verifier_enabled = args.role_verifier == "enabled"
        thinking_enabled = args.thinking == "enabled"
        tenant_policy_advisor_enabled = args.tenant_policy_advisor == "enabled"
        role_verifier_model_name = (
            (args.role_verifier_model or args.model_name)
            if role_verifier_enabled
            else None
        )
        cases_manifest, cases = load_case_manifest(cases_path)
        paths = build_paths(args.output_root)
        batch_command = build_batch_command(
            python_executable=python_executable,
            source=source,
            paths=paths,
            cases=cases,
            model_name=args.model_name,
            thinking_enabled=thinking_enabled,
            role_verifier_enabled=role_verifier_enabled,
            role_verifier_model_name=role_verifier_model_name,
            execute=args.execute,
            resume=args.resume,
        )
        if not args.execute:
            print(
                json.dumps(
                    {
                        "schema_version": PLAN_SCHEMA_VERSION,
                        "source": str(source),
                        "output_root": str(paths.root),
                        "database": str(paths.database),
                        "model_name": args.model_name,
                        "thinking_enabled_requested": thinking_enabled,
                        "alert_ids": [case.alert_id for case in cases],
                        "excluded": cases_manifest.get("excluded"),
                        "batch_command": list(batch_command),
                        "runtime_live_model_call_count": len(cases),
                        "maximum_primary_output_retry_call_count": (
                            len(cases) * args.output_retry_attempts
                        ),
                        "role_verifier_enabled": role_verifier_enabled,
                        "role_verifier_model_name": role_verifier_model_name,
                        "role_verifier_minimum_confidence": args.role_verifier_min_confidence,
                        "maximum_role_verifier_call_count": (
                            len(cases) if role_verifier_enabled else 0
                        ),
                        "maximum_role_verifier_output_retry_call_count": (
                            len(cases) * args.output_retry_attempts
                            if role_verifier_enabled
                            else 0
                        ),
                        "tenant_policy_advisor_enabled": (
                            tenant_policy_advisor_enabled
                        ),
                        "maximum_policy_advisor_call_count": (
                            len(cases) if tenant_policy_advisor_enabled else 0
                        ),
                        "maximum_total_live_model_call_count": (
                            len(cases)
                            + len(cases) * args.output_retry_attempts
                            + (len(cases) if role_verifier_enabled else 0)
                            + (
                                len(cases) * args.output_retry_attempts
                                if role_verifier_enabled
                                else 0
                            )
                            + (len(cases) if tenant_policy_advisor_enabled else 0)
                        ),
                        "output_retry_attempts": args.output_retry_attempts,
                        "provider_mode": "simulated_read_only",
                        "tenant_policy": _display_path(PINGAN_POLICY),
                        "tenant_policy_skill": (
                            _display_path(PINGAN_POLICY_SKILL)
                            if tenant_policy_advisor_enabled
                            else None
                        ),
                        "synthetic_automation_policy_installed": False,
                        "real_external_action_call_count": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0

        _prepare_output(paths, resume=args.resume, replace=args.replace)
        env = _execution_environment(
            python_executable,
            thinking_enabled=thinking_enabled,
            tenant_policy_advisor_enabled=tenant_policy_advisor_enabled,
            role_verifier_enabled=role_verifier_enabled,
            role_verifier_model_name=role_verifier_model_name,
            role_verifier_minimum_confidence=args.role_verifier_min_confidence,
            output_retry_attempts=args.output_retry_attempts,
        )
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
            thinking_enabled=thinking_enabled,
            role_verifier_enabled=role_verifier_enabled,
            role_verifier_model_name=role_verifier_model_name,
            role_verifier_minimum_confidence=args.role_verifier_min_confidence,
            output_retry_attempts=args.output_retry_attempts,
        )
        _write_run_manifest(
            paths=paths,
            cases_manifest=cases_manifest,
            source=source,
            model_name=args.model_name,
            thinking_enabled=thinking_enabled,
            batch_command=batch_command,
            report=report,
            role_verifier_enabled=role_verifier_enabled,
            role_verifier_model_name=role_verifier_model_name,
            role_verifier_minimum_confidence=args.role_verifier_min_confidence,
            output_retry_attempts=args.output_retry_attempts,
            tenant_policy_advisor_enabled=tenant_policy_advisor_enabled,
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
