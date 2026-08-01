#!/usr/bin/env python3
"""Build Checkpoint D-11 full-corpus Runtime compatibility artifacts."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (  # noqa: E402
    EXPECTED_SOURCE_TYPE_BY_TOPIC,
    canonical_sha256,
    sha256_file,
    write_json_atomic,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import (  # noqa: E402
    AnalysisRun,
    AnalysisRunStatus,
    SensitiveEvidenceMode,
)
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService  # noqa: E402

SCHEMA_VERSION = "soc.validation.checkpoint_d.full_corpus_runtime_review.v1"
DIAGNOSTIC_SCHEMA_VERSION = (
    "soc.validation.checkpoint_d.full_corpus_runtime_diagnostic.v1"
)
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_INVENTORY_PATH = (
    ROOT
    / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
    / "step-d0-corpus-inventory/corpus-inventory.json"
)
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "backend/.deer-flow/soc-runtime-validation/checkpoint-d"
    / "step-d11-full-corpus-runtime"
)
EXPECTED_RUNTIME_STEPS = (
    "normalize",
    "entity_extract",
    "fact_reconstruct",
    "build_analysis_input",
    "skill_context",
    "analyze_stub",
    "schema_validate",
    "evidence_grounding",
    "decide",
)
_ALLOWED_D0_STATUSES = {"passed", "passed_with_known_input_gaps"}


def build_full_corpus_runtime_review(
    corpus: pd.DataFrame,
    corpus_inventory: Mapping[str, Any],
    *,
    corpus_path: Path,
    corpus_file_sha256: str,
    analysis_service: SocAnalysisService,
    sensitive_evidence_mode: SensitiveEvidenceMode,
    expected_rows: int,
    expected_source_type_by_topic: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Run every D0 row twice through the non-persistent stub Runtime."""

    topic_sources = dict(expected_source_type_by_topic or EXPECTED_SOURCE_TYPE_BY_TOPIC)
    inventory_acceptance = _required_mapping(
        corpus_inventory,
        "acceptance",
        "D-0",
    )
    inventory_input = _required_mapping(corpus_inventory, "input", "D-0")
    inventory_rows = _mapping_list(corpus_inventory.get("rows"), "D-0 rows")
    corpus_rows, corpus_index_failures = _index_corpus(corpus)
    indexed_inventory, inventory_index_failures = _index_inventory(inventory_rows)

    records: list[dict[str, Any]] = []
    diagnostics: dict[str, dict[str, Any]] = {}
    runtime_exceptions: list[dict[str, Any]] = []

    for alert_id, inventory_row in sorted(
        indexed_inventory.items(),
        key=lambda item: _alert_id_sort_key(item[0]),
    ):
        corpus_row = corpus_rows.get(alert_id)
        if corpus_row is None:
            runtime_exceptions.append(
                {
                    "alert_id": alert_id,
                    "topic": inventory_row.get("topic"),
                    "error_type": "MissingCorpusRow",
                    "error": "D-0 alert id is absent from the corpus",
                }
            )
            continue

        try:
            full_data = _required_mapping(
                corpus_row,
                "alert_full_data",
                f"alert {alert_id}",
            )
            payload = _required_mapping(full_data, "alert_data", f"alert {alert_id}")
            full_data_hash = canonical_sha256(full_data)
            payload_hash = canonical_sha256(payload)
            first_run = analysis_service.analyze(payload)
            second_run = analysis_service.analyze(payload)
            record = _review_run_pair(
                inventory_row,
                first_run=first_run,
                second_run=second_run,
                full_data_hash=full_data_hash,
                payload_hash=payload_hash,
            )
            records.append(record)
            if record["failed_checks"]:
                diagnostic_name = f"diagnostics/{alert_id}.runtime-diagnostic.json"
                record["diagnostic"] = diagnostic_name
                diagnostics[diagnostic_name] = {
                    "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
                    "generated_at": datetime.now(UTC).isoformat(),
                    "inventory_row": dict(inventory_row),
                    "failed_checks": record["failed_checks"],
                    "first_run": first_run.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                    "second_run": second_run.model_dump(
                        mode="json",
                        exclude_none=True,
                    ),
                }
        except Exception as exc:  # noqa: BLE001 - retain every corpus failure
            runtime_exceptions.append(
                {
                    "alert_id": alert_id,
                    "topic": inventory_row.get("topic"),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    expected_ids = set(indexed_inventory)
    actual_ids = {item["alert_id"] for item in records}
    known_gap_ids = {
        alert_id
        for alert_id, row in indexed_inventory.items()
        if "evidence_unavailable" in (row.get("issue_codes") or [])
    }
    replayed_gap_ids = {
        item["alert_id"] for item in records if item["input"]["known_input_gap"]
    }
    failed_records = [item for item in records if item["failed_checks"]]
    stable_records = [item for item in records if item["reexecution"]["stable"]]

    checks = {
        "d0_acceptance_allows_continuation": (
            inventory_acceptance.get("status") in _ALLOWED_D0_STATUSES
        ),
        "d0_links_exact_corpus_file": (
            inventory_input.get("corpus_sha256") == corpus_file_sha256
        ),
        "expected_corpus_row_count_matches": len(corpus) == expected_rows,
        "d0_row_count_matches_expected": len(indexed_inventory) == expected_rows,
        "corpus_index_is_unique": not corpus_index_failures,
        "d0_index_is_unique": not inventory_index_failures,
        "every_d0_row_was_replayed": actual_ids == expected_ids,
        "no_runtime_exceptions": not runtime_exceptions,
        "all_row_contracts_pass": bool(records) and not failed_records,
        "all_rows_are_semantically_stable": (
            len(stable_records) == len(records) == expected_rows
        ),
        "all_known_input_gaps_are_replayed": replayed_gap_ids == known_gap_ids,
        "all_known_topics_are_covered": (
            {item["topic"] for item in records} == set(topic_sources)
        ),
        "stub_only_no_live_model_calls": bool(records)
        and all(
            item["runtime"]["analyzer_step"] == "analyze_stub"
            and item["runtime"]["model_name"] == "stub"
            for item in records
        ),
        **_evidence_quality_contract_checks(records),
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    status = "failed" if failed_checks else "passed"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "d0_lineage_validation",
                "full_corpus_non_persistent_runtime_reexecution",
                "production_normalization_entity_fact_and_bounded_input",
                "production_skill_resolution_stub_analysis_grounding_and_decision",
                "semantic_step_hash_stability_comparison",
                "known_input_gap_fail_closed_validation",
            ],
            "not_performed": [
                "live_llm_invocation",
                "model_accuracy_or_confidence_evaluation",
                "database_or_repository_replay",
                "tenant_disposition_policy",
                "correlation_or_memory_retrieval",
                "tool_or_mcp_invocation",
                "persistence_review_queue_or_action",
            ],
            "classification": "deterministic_compatibility_gate_not_runtime_node",
        },
        "input": {
            "corpus_path": _relative_path(corpus_path),
            "corpus_sha256": corpus_file_sha256,
            "corpus_row_count": len(corpus),
            "d0_schema_version": corpus_inventory.get("schema_version"),
            "d0_status": inventory_acceptance.get("status"),
            "expected_rows": expected_rows,
            "sensitive_evidence_mode": sensitive_evidence_mode.value,
            "reexecution_count_per_alert": 2,
        },
        "stability_policy": {
            "comparison": "canonical semantic projection SHA-256",
            "included": [
                "alert/status/pipeline/model/prompt/input hashes",
                "semantic step names/status/output hashes/warnings/metadata",
                "normalization report hash",
                "extraction report hash",
                "runtime failure contract",
            ],
            "excluded": [
                "run_id",
                "started_at",
                "ended_at",
                "step durations",
                "all step input hashes because they duplicate prior outputs",
                (
                    "normalize output hash because source-missing "
                    "AlertEventRef.received_at is assigned at ingestion time"
                ),
            ],
            "meaning": (
                "same payload is analyzed twice in-process without persistence; "
                "this is not SocAnalysisService.replay(run_id)"
            ),
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "processed_row_count": len(records),
            "stable_row_count": len(stable_records),
            "failed_row_count": len(failed_records),
            "runtime_exception_count": len(runtime_exceptions),
            "diagnostic_count": len(diagnostics),
            "known_input_gap_count": len(replayed_gap_ids),
        },
        "coverage": _aggregate_coverage(records),
        "failures": {
            "corpus_index": corpus_index_failures,
            "d0_index": inventory_index_failures,
            "runtime_exceptions": runtime_exceptions,
            "rows": [
                {
                    "alert_id": item["alert_id"],
                    "topic": item["topic"],
                    "failed_checks": item["failed_checks"],
                    "diagnostic": item.get("diagnostic"),
                }
                for item in failed_records
            ],
        },
        "rows": records,
    }
    return report, diagnostics


def _review_run_pair(
    inventory_row: Mapping[str, Any],
    *,
    first_run: AnalysisRun,
    second_run: AnalysisRun,
    full_data_hash: str,
    payload_hash: str,
) -> dict[str, Any]:
    alert_id = _alert_id_text(inventory_row.get("alert_id"))
    expected_source_type = str(inventory_row.get("expected_source_type") or "other")
    known_input_gap = "evidence_unavailable" in (inventory_row.get("issue_codes") or [])
    first_checks = _run_contract_checks(
        first_run,
        alert_id=alert_id,
        expected_source_type=expected_source_type,
        payload_hash=payload_hash,
        known_input_gap=known_input_gap,
    )
    second_checks = _run_contract_checks(
        second_run,
        alert_id=alert_id,
        expected_source_type=expected_source_type,
        payload_hash=payload_hash,
        known_input_gap=known_input_gap,
    )
    first_projection = _semantic_run_projection(first_run)
    second_projection = _semantic_run_projection(second_run)
    first_hash = canonical_sha256(first_projection)
    second_hash = canonical_sha256(second_projection)
    differing_steps = _differing_semantic_step_outputs(first_run, second_run)
    raw_trace_differences = _differing_raw_step_hashes(first_run, second_run)
    pair_checks = {
        "d0_payload_hash_matches": (
            inventory_row.get("canonical_payload_sha256") == full_data_hash
        ),
        "first_run_contract_pass": all(first_checks.values()),
        "second_run_contract_pass": all(second_checks.values()),
        "semantic_reexecution_stable": first_hash == second_hash,
        "semantic_step_outputs_stable": not differing_steps,
    }
    failed_checks = [
        *(f"first.{name}" for name, passed in first_checks.items() if not passed),
        *(f"second.{name}" for name, passed in second_checks.items() if not passed),
        *(name for name, passed in pair_checks.items() if not passed),
    ]
    return {
        "alert_id": alert_id,
        "topic": str(inventory_row.get("topic") or "unknown"),
        "input": {
            "expected_source_type": expected_source_type,
            "evidence_input_shape": inventory_row.get("evidence_input_shape"),
            "known_input_gap": known_input_gap,
            "d0_issue_codes": list(inventory_row.get("issue_codes") or []),
        },
        "runtime": _summarize_run(first_run),
        "reexecution": {
            "stable": first_hash == second_hash,
            "first_semantic_sha256": first_hash,
            "second_semantic_sha256": second_hash,
            "differing_semantic_step_outputs": differing_steps,
            "raw_trace_hash_differences": raw_trace_differences,
        },
        "checks": {
            "first_run": first_checks,
            "second_run": second_checks,
            "pair": pair_checks,
        },
        "failed_checks": failed_checks,
    }


def _run_contract_checks(
    run: AnalysisRun,
    *,
    alert_id: str,
    expected_source_type: str,
    payload_hash: str,
    known_input_gap: bool,
) -> dict[str, bool]:
    actual_steps = [item.step_name for item in run.steps]
    analyzer_step = next(
        (item for item in run.steps if item.step_name == "analyze_stub"),
        None,
    )
    request = run.llm_analysis_request
    has_bounded_evidence = bool(
        request
        and (
            request.primary_evidence is not None
            or request.supplementary_evidence
            or request.evidence_highlights
        )
    )
    normalization = run.normalization_report
    grounding = run.analysis_evidence_grounding
    decision = run.decision
    return {
        "alert_id_matches": run.alert_id == alert_id,
        "input_payload_preserved": (
            run.input_payload is not None
            and canonical_sha256(run.input_payload) == payload_hash
        ),
        "expected_source_type_matches": (
            normalization is not None
            and normalization.source_type.value == expected_source_type
        ),
        "production_step_sequence_matches": tuple(actual_steps)
        == EXPECTED_RUNTIME_STEPS,
        "all_runtime_steps_succeeded": bool(run.steps)
        and all(item.status.value == "success" for item in run.steps),
        "stub_analyzer_is_explicit": (
            run.model_name == "stub"
            and run.prompt_version == "stub"
            and analyzer_step is not None
            and analyzer_step.metadata.get("analyzer") == "stub"
            and "analyze_llm" not in actual_steps
        ),
        "analysis_grounding_and_decision_exist": (
            run.analysis is not None
            and grounding is not None
            and decision is not None
            and grounding.total_count == len(run.analysis.evidence)
        ),
        "decision_is_fail_closed": (
            run.status is AnalysisRunStatus.NEEDS_REVIEW
            and decision is not None
            and decision.needs_review
            and decision.automation_allowed is False
        ),
        "non_gap_has_bounded_evidence": known_input_gap or has_bounded_evidence,
        "known_gap_has_no_bounded_evidence": (
            not known_input_gap or not has_bounded_evidence
        ),
        "known_gap_is_explicit": (
            not known_input_gap or _has_explicit_runtime_input_gap_reason(run)
        ),
    }


def _semantic_run_projection(run: AnalysisRun) -> dict[str, Any]:
    return {
        "alert_id": run.alert_id,
        "status": run.status.value,
        "pipeline_version": run.pipeline_version,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "input_hash": run.input_hash,
        "steps": [
            {
                "step_name": item.step_name,
                "status": item.status.value,
                "semantic_output_hash": (
                    None if item.step_name == "normalize" else item.output_hash
                ),
                "error": item.error,
                "warnings": item.warnings,
                "metadata": item.metadata,
            }
            for item in run.steps
        ],
        "normalization_report_sha256": _model_sha256(run.normalization_report),
        "extraction_report_sha256": _model_sha256(run.extraction_report),
        "failure": (
            run.failure.model_dump(mode="json", exclude_none=True)
            if run.failure
            else None
        ),
    }


def _model_sha256(value: Any) -> str | None:
    if value is None:
        return None
    return canonical_sha256(value.model_dump(mode="json", exclude_none=True))


def _differing_semantic_step_outputs(
    first_run: AnalysisRun,
    second_run: AnalysisRun,
) -> list[str]:
    first = {
        item.step_name: (
            item.status.value,
            None if item.step_name == "normalize" else item.output_hash,
        )
        for item in first_run.steps
    }
    second = {
        item.step_name: (
            item.status.value,
            None if item.step_name == "normalize" else item.output_hash,
        )
        for item in second_run.steps
    }
    return sorted(
        step_name
        for step_name in set(first).union(second)
        if first.get(step_name) != second.get(step_name)
    )


def _differing_raw_step_hashes(
    first_run: AnalysisRun,
    second_run: AnalysisRun,
) -> list[str]:
    first = {
        item.step_name: (item.status.value, item.input_hash, item.output_hash)
        for item in first_run.steps
    }
    second = {
        item.step_name: (item.status.value, item.input_hash, item.output_hash)
        for item in second_run.steps
    }
    return sorted(
        step_name
        for step_name in set(first).union(second)
        if first.get(step_name) != second.get(step_name)
    )


def _summarize_run(run: AnalysisRun) -> dict[str, Any]:
    normalization = run.normalization_report
    request = run.llm_analysis_request
    analysis = run.analysis
    grounding = run.analysis_evidence_grounding
    decision = run.decision
    coverage = request.evidence_coverage if request else None
    message_schema_status_counts = (
        Counter(item.status.value for item in normalization.message_schemas)
        if normalization
        else Counter()
    )
    omission_reason_counts = (
        Counter(item.reason for item in coverage.omissions) if coverage else Counter()
    )
    analyzer_step = next(
        (item for item in run.steps if item.step_name == "analyze_stub"),
        None,
    )
    return {
        "status": run.status.value,
        "pipeline_version": run.pipeline_version,
        "model_name": run.model_name,
        "prompt_version": run.prompt_version,
        "analyzer_step": analyzer_step.step_name if analyzer_step else None,
        "step_names": [item.step_name for item in run.steps],
        "actual_source_type": (
            normalization.source_type.value if normalization else "unknown"
        ),
        "adapter": normalization.adapter if normalization else None,
        "normalization": {
            "missing_field_count": (
                len(normalization.missing_fields) if normalization else 0
            ),
            "warning_count": len(normalization.warnings) if normalization else 0,
            "message_schema_count": (
                len(normalization.message_schemas) if normalization else 0
            ),
            "message_schema_status_counts": dict(
                sorted(message_schema_status_counts.items())
            ),
            "message_parser_warning_count": (
                sum(
                    len(item.warnings)
                    for item in normalization.message_schemas
                    if item.status.value == "recognized" and item.parser_name
                )
                if normalization
                else 0
            ),
        },
        "entities": {
            "mention_count": (
                run.extraction_report.mention_count if run.extraction_report else 0
            ),
            "entity_counts": (
                dict(run.extraction_report.entity_counts)
                if run.extraction_report
                else {}
            ),
        },
        "facts": {
            "conflict_count": (
                len(run.fact_reconstruction.conflict_reports)
                if run.fact_reconstruction
                else 0
            ),
            "scenario_hypothesis_count": (
                len(run.fact_reconstruction.scenario_hypotheses)
                if run.fact_reconstruction
                else 0
            ),
        },
        "bounded_evidence": {
            "primary_present": bool(request and request.primary_evidence),
            "supplementary_count": (
                len(request.supplementary_evidence) if request else 0
            ),
            "highlight_count": len(request.evidence_highlights) if request else 0,
            "coverage_counts": (dict(coverage.counts) if coverage else {}),
            "truncated_evidence_count": (
                len(coverage.llm_truncated_evidence_paths) if coverage else 0
            ),
            "compacted_encoded_count": (
                len(coverage.llm_compacted_encoded_paths) if coverage else 0
            ),
            "omission_reason_counts": dict(sorted(omission_reason_counts.items())),
            "high_value_gap_count": (len(coverage.high_value_gaps) if coverage else 0),
            "selected_skills": (
                [item.skill_name for item in request.skill_context.selected_skills]
                if request
                else []
            ),
        },
        "analysis": (
            {
                "verdict": analysis.verdict.value,
                "confidence": analysis.confidence,
                "evidence_count": len(analysis.evidence),
                "scenario_count": len(analysis.scenario_assessments),
            }
            if analysis
            else None
        ),
        "grounding": (
            {
                "total_count": grounding.total_count,
                "grounded_count": grounding.grounded_count,
                "ungrounded_count": grounding.ungrounded_count,
            }
            if grounding
            else None
        ),
        "decision": (
            {
                "evidence_state": decision.evidence_state.value,
                "needs_review": decision.needs_review,
                "automation_allowed": decision.automation_allowed,
                "review_reasons": [item.value for item in decision.review_reasons],
            }
            if decision
            else None
        ),
        "failure": (
            run.failure.model_dump(mode="json", exclude_none=True)
            if run.failure
            else None
        ),
    }


def _aggregate_coverage(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    topic_counts: Counter[str] = Counter()
    source_type_counts: Counter[str] = Counter()
    adapter_counts: Counter[str] = Counter()
    runtime_status_counts: Counter[str] = Counter()
    analyzer_step_counts: Counter[str] = Counter()
    verdict_counts: Counter[str] = Counter()
    evidence_state_counts: Counter[str] = Counter()
    review_reason_counts: Counter[str] = Counter()
    selected_skill_counts: Counter[str] = Counter()
    message_schema_status_counts: Counter[str] = Counter()
    omission_reason_totals: Counter[str] = Counter()
    evidence_quality_row_counts: Counter[str] = Counter()
    total_grounding = Counter()
    total_coverage = Counter()
    normalization = Counter()

    for item in records:
        runtime = item["runtime"]
        topic_counts[item["topic"]] += 1
        source_type_counts[runtime["actual_source_type"]] += 1
        adapter_counts[runtime["adapter"] or "adapter_unavailable"] += 1
        runtime_status_counts[runtime["status"]] += 1
        analyzer_step_counts[runtime["analyzer_step"] or "step_unavailable"] += 1
        analysis = runtime.get("analysis") or {}
        verdict_counts[analysis.get("verdict", "analysis_unavailable")] += 1
        decision = runtime.get("decision") or {}
        evidence_state_counts[
            decision.get("evidence_state", "decision_unavailable")
        ] += 1
        review_reason_counts.update(decision.get("review_reasons") or [])
        selected_skill_counts.update(runtime["bounded_evidence"]["selected_skills"])
        message_schema_status_counts.update(
            runtime["normalization"].get("message_schema_status_counts") or {}
        )
        bounded = runtime["bounded_evidence"]
        omission_reason_totals.update(bounded.get("omission_reason_counts") or {})
        grounding = runtime.get("grounding") or {}
        for key in ("total_count", "grounded_count", "ungrounded_count"):
            total_grounding[key] += _non_negative_int(grounding.get(key, 0))
        for key, value in runtime["bounded_evidence"]["coverage_counts"].items():
            if isinstance(value, int) and value >= 0:
                total_coverage[key] += value
        normalization["missing_field_count"] += runtime["normalization"][
            "missing_field_count"
        ]
        normalization["warning_count"] += runtime["normalization"]["warning_count"]
        normalization["message_schema_count"] += runtime["normalization"][
            "message_schema_count"
        ]
        normalization["message_parser_warning_count"] += runtime["normalization"].get(
            "message_parser_warning_count", 0
        )

        schema_statuses = (
            runtime["normalization"].get("message_schema_status_counts") or {}
        )
        if runtime["normalization"].get("message_parser_warning_count", 0):
            evidence_quality_row_counts["message_parser_warning"] += 1
        if schema_statuses.get("degraded", 0) or schema_statuses.get("unsupported", 0):
            evidence_quality_row_counts["degraded_or_unsupported_schema"] += 1
        if bounded.get("compacted_encoded_count", 0):
            evidence_quality_row_counts["encoded_compaction"] += 1
        if bounded.get("coverage_counts", {}).get("omission_count", 0):
            evidence_quality_row_counts["bounded_omission"] += 1
        if bounded.get("truncated_evidence_count", 0) and not bounded.get(
            "high_value_gap_count", 0
        ):
            evidence_quality_row_counts[
                "routine_truncation_without_high_value_gap"
            ] += 1
        if bounded.get("high_value_gap_count", 0):
            evidence_quality_row_counts["high_value_gap"] += 1
        if grounding.get("ungrounded_count", 0):
            evidence_quality_row_counts["ungrounded_analysis_evidence"] += 1
        if runtime["facts"].get("conflict_count", 0):
            evidence_quality_row_counts["fact_conflict"] += 1

    return {
        "topic_counts": dict(sorted(topic_counts.items())),
        "source_type_counts": dict(sorted(source_type_counts.items())),
        "adapter_counts": dict(sorted(adapter_counts.items())),
        "runtime_status_counts": dict(sorted(runtime_status_counts.items())),
        "analyzer_step_counts": dict(sorted(analyzer_step_counts.items())),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "evidence_state_counts": dict(sorted(evidence_state_counts.items())),
        "review_reason_counts": dict(sorted(review_reason_counts.items())),
        "selected_skill_counts": dict(sorted(selected_skill_counts.items())),
        "message_schema_status_counts": dict(
            sorted(message_schema_status_counts.items())
        ),
        "evidence_quality_row_counts": dict(
            sorted(evidence_quality_row_counts.items())
        ),
        "omission_reason_totals": dict(sorted(omission_reason_totals.items())),
        "grounding_totals": dict(sorted(total_grounding.items())),
        "bounded_evidence_coverage_totals": dict(sorted(total_coverage.items())),
        "normalization_totals": dict(sorted(normalization.items())),
        "stable_row_count": sum(item["reexecution"]["stable"] for item in records),
        "unstable_row_count": sum(
            not item["reexecution"]["stable"] for item in records
        ),
    }


def _evidence_quality_contract_checks(
    records: Sequence[Mapping[str, Any]],
) -> dict[str, bool]:
    routine_truncation_rows: list[Mapping[str, Any]] = []
    parser_warning_rows: list[Mapping[str, Any]] = []
    high_value_gap_rows: list[Mapping[str, Any]] = []
    encoded_compaction_rows: list[Mapping[str, Any]] = []

    for item in records:
        runtime = item["runtime"]
        bounded = runtime["bounded_evidence"]
        grounding = runtime.get("grounding") or {}
        statuses = runtime["normalization"].get("message_schema_status_counts") or {}
        if (
            bounded.get("truncated_evidence_count", 0)
            and not bounded.get("high_value_gap_count", 0)
            and not grounding.get("ungrounded_count", 0)
            and not runtime["facts"].get("conflict_count", 0)
            and not statuses.get("degraded", 0)
            and not statuses.get("unsupported", 0)
        ):
            routine_truncation_rows.append(item)
        if runtime["normalization"].get("message_parser_warning_count", 0):
            parser_warning_rows.append(item)
        if bounded.get("high_value_gap_count", 0):
            high_value_gap_rows.append(item)
        if bounded.get("compacted_encoded_count", 0):
            encoded_compaction_rows.append(item)

    return {
        "routine_truncation_is_not_directly_degraded": all(
            item["runtime"]["decision"]["evidence_state"] != "degraded"
            for item in routine_truncation_rows
        ),
        "nested_parser_warnings_preserve_outer_schema": all(
            not (
                item["runtime"]["normalization"]["message_schema_status_counts"].get(
                    "degraded", 0
                )
                or item["runtime"]["normalization"]["message_schema_status_counts"].get(
                    "unsupported", 0
                )
            )
            for item in parser_warning_rows
        ),
        "high_value_gaps_fail_closed": all(
            item["runtime"]["decision"]["evidence_state"] in {"degraded", "conflicted"}
            and "high_value_evidence_gap"
            in item["runtime"]["decision"]["review_reasons"]
            and item["runtime"]["decision"]["needs_review"]
            and not item["runtime"]["decision"]["automation_allowed"]
            for item in high_value_gap_rows
        ),
        "encoded_compaction_does_not_emit_truncation_review_reason": all(
            "truncated_analysis_evidence"
            not in item["runtime"]["decision"]["review_reasons"]
            for item in encoded_compaction_rows
        ),
    }


def _has_explicit_runtime_input_gap_reason(run: AnalysisRun) -> bool:
    values: list[str] = []
    if run.normalization_report:
        values.extend(run.normalization_report.warnings)
    if run.llm_analysis_request:
        values.extend(run.llm_analysis_request.warnings)
        values.extend(run.llm_analysis_request.evidence_coverage.warnings)
        for gap in run.llm_analysis_request.evidence_coverage.high_value_gaps:
            values.extend(
                value
                for value in (
                    gap.rule_id,
                    gap.field_path,
                    gap.expected_target,
                    gap.reason,
                )
                if value
            )
    if run.decision:
        values.extend(item.value for item in run.decision.review_reasons)
    normalized = " ".join(values).casefold()
    return any(
        marker in normalized
        for marker in (
            "evidence_unavailable",
            "evidence unavailable",
            "raw evidence unavailable",
            "upstream input gap",
        )
    )


def _index_corpus(
    corpus: pd.DataFrame,
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    rows: dict[str, Mapping[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for row_index, row in corpus.iterrows():
        alert_id = _alert_id_text(row.get("alert_id"))
        if alert_id in rows:
            failures.append(
                {
                    "row_index": str(row_index),
                    "alert_id": alert_id,
                    "error": "duplicate alert id",
                }
            )
            continue
        rows[alert_id] = row.to_dict()
    return rows, failures


def _index_inventory(
    inventory_rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Mapping[str, Any]], list[dict[str, Any]]]:
    rows: dict[str, Mapping[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for index, row in enumerate(inventory_rows):
        alert_id = _alert_id_text(row.get("alert_id"))
        if alert_id in rows:
            failures.append(
                {
                    "row_index": index,
                    "alert_id": alert_id,
                    "error": "duplicate D-0 alert id",
                }
            )
            continue
        rows[alert_id] = row
    return rows, failures


def _mapping_list(value: Any, name: str) -> list[Mapping[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(item, Mapping) for item in value
    ):
        raise ValueError(f"{name} must be a list of objects")
    return list(value)


def _required_mapping(
    value: Mapping[str, Any],
    key: str,
    artifact_name: str,
) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise ValueError(f"{artifact_name} artifact is missing {key}")
    return item


def _non_negative_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"coverage value must be an integer: {value!r}") from exc
    if parsed < 0:
        raise ValueError(f"coverage value must be non-negative: {parsed}")
    return parsed


def _alert_id_text(value: Any) -> str:
    if value is None:
        raise ValueError("alert_id is required")
    text = str(value).strip()
    if not text:
        raise ValueError("alert_id is required")
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _alert_id_sort_key(value: Any) -> tuple[int, int | str]:
    text = _alert_id_text(value)
    try:
        return (0, int(text))
    except ValueError:
        return (1, text)


def _relative_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _write_diagnostics(
    diagnostics: Mapping[str, Mapping[str, Any]],
    output_dir: Path,
) -> None:
    diagnostics_dir = output_dir / "diagnostics"
    expected_paths = {output_dir / relative_name for relative_name in diagnostics}
    if diagnostics_dir.exists():
        for stale_path in diagnostics_dir.glob("*.runtime-diagnostic.json"):
            if stale_path not in expected_paths:
                stale_path.unlink()
    for relative_name, diagnostic in diagnostics.items():
        write_json_atomic(diagnostic, output_dir / relative_name)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--expected-rows", type=int, default=212)
    parser.add_argument(
        "--sensitive-evidence-mode",
        choices=[item.value for item in SensitiveEvidenceMode],
        default=os.environ.get("SOC_VALIDATION_SENSITIVE_EVIDENCE_MODE", "full"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sensitive_evidence_mode = SensitiveEvidenceMode(args.sensitive_evidence_mode)
    analysis_service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            sensitive_evidence_mode=sensitive_evidence_mode,
        )
    )
    corpus = load_dataframe_pickle(args.corpus)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    report, diagnostics = build_full_corpus_runtime_review(
        corpus,
        inventory,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
        analysis_service=analysis_service,
        sensitive_evidence_mode=sensitive_evidence_mode,
        expected_rows=args.expected_rows,
    )
    _write_diagnostics(diagnostics, args.output_dir)
    output_path = args.output_dir / "full-corpus-runtime-matrix.json"
    write_json_atomic(report, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                **report["acceptance"],
                "topic_counts": report["coverage"]["topic_counts"],
                "source_type_counts": report["coverage"]["source_type_counts"],
                "runtime_status_counts": report["coverage"]["runtime_status_counts"],
                "verdict_counts": report["coverage"]["verdict_counts"],
                "grounding_totals": report["coverage"]["grounding_totals"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if report["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
