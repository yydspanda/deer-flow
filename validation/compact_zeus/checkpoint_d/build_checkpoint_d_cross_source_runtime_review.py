#!/usr/bin/env python3
"""Build Checkpoint D-10 cross-source live-model Runtime review artifacts."""

from __future__ import annotations

import argparse
import json
import os
import statistics
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

from soc_agent.contracts import AnalysisRun, AnalysisRunStatus  # noqa: E402
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService  # noqa: E402
from soc_agent.llm import (  # noqa: E402
    SocAnalyzerMode,
    SocLLMSettings,
    build_configured_analyzer,
)

SCHEMA_VERSION = "soc.validation.checkpoint_d.cross_source_runtime_review.v2"
SAMPLE_SCHEMA_VERSION = "soc.validation.checkpoint_d.cross_source_runtime_sample.v2"
SELECTION_POLICY_VERSION = "soc.validation.representative_selection.v1"
DEFAULT_MODEL_NAME = "deepseek-v4-flash"
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
    / "step-d10-cross-source-runtime"
)
EXPECTED_RUNTIME_STEPS = (
    "normalize",
    "entity_extract",
    "fact_reconstruct",
    "build_analysis_input",
    "skill_context",
    "analyze_llm",
    "schema_validate",
    "evidence_grounding",
    "decide",
)
_ALLOWED_D0_STATUSES = {"passed", "passed_with_known_input_gaps"}
_REPRESENTATIVE_METRICS = (
    "hit_log_count",
    "raw_event_count",
    "non_empty_message_count",
)


def build_cross_source_runtime_review(
    corpus: pd.DataFrame,
    corpus_inventory: Mapping[str, Any],
    *,
    corpus_path: Path,
    corpus_file_sha256: str,
    analysis_service: SocAnalysisService,
    expected_model_name: str,
    expected_source_type_by_topic: Mapping[str, str] | None = None,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Run one typical row per topic plus every known D0 input gap through a live model."""

    topic_sources = dict(expected_source_type_by_topic or EXPECTED_SOURCE_TYPE_BY_TOPIC)
    inventory_acceptance = _required_mapping(
        corpus_inventory,
        "acceptance",
        "D-0",
    )
    inventory_input = _required_mapping(corpus_inventory, "input", "D-0")
    inventory_rows = _mapping_list(corpus_inventory.get("rows"), "D-0 rows")
    selections, selection_failures = _select_rows(
        inventory_rows,
        known_input_gaps=_mapping_list(
            corpus_inventory.get("known_input_gaps"),
            "D-0 known_input_gaps",
        ),
        expected_source_type_by_topic=topic_sources,
    )
    corpus_rows, corpus_index_failures = _index_corpus(corpus)
    if not expected_model_name or expected_model_name == "stub":
        raise ValueError("D10 requires a non-stub expected_model_name")

    records: list[dict[str, Any]] = []
    sample_artifacts: dict[str, dict[str, Any]] = {}
    runtime_failures: list[dict[str, Any]] = []
    quality_findings: list[dict[str, Any]] = []

    for selection in selections:
        alert_id = selection["alert_id"]
        row = corpus_rows.get(alert_id)
        if row is None:
            runtime_failures.append(
                {
                    "alert_id": alert_id,
                    "topic": selection["topic"],
                    "sample_kind": selection["sample_kind"],
                    "error_type": "MissingCorpusRow",
                    "error": "selected D-0 alert id is absent from the corpus",
                }
            )
            continue

        try:
            full_data = _required_mapping(row, "alert_full_data", f"alert {alert_id}")
            payload = _required_mapping(full_data, "alert_data", f"alert {alert_id}")
            full_data_hash = canonical_sha256(full_data)
            payload_hash = canonical_sha256(payload)
            run = analysis_service.analyze(payload)
            sample_record, sample_artifact = _review_run(
                selection,
                run=run,
                full_data_hash=full_data_hash,
                payload_hash=payload_hash,
                expected_model_name=expected_model_name,
            )
            records.append(sample_record)
            artifact_name = f"runs/{alert_id}.{selection['sample_kind']}.runtime.json"
            sample_record["artifact"] = artifact_name
            sample_artifacts[artifact_name] = sample_artifact

            if (
                selection["sample_kind"] == "known_input_gap"
                and not sample_record["runtime_gap_visibility"][
                    "explicit_runtime_input_gap_reason"
                ]
            ):
                quality_findings.append(
                    {
                        "code": "known_input_gap_not_explicit_in_runtime_reason",
                        "alert_id": alert_id,
                        "topic": selection["topic"],
                        "meaning": (
                            "D-0 exposes evidence_unavailable and Runtime fails closed, "
                            "but the Decision review reasons do not name the upstream "
                            "input gap explicitly"
                        ),
                    }
                )
            grounding = sample_record["runtime"]["grounding"]
            if grounding and grounding["ungrounded_count"]:
                quality_findings.append(
                    {
                        "code": "ungrounded_live_model_evidence",
                        "alert_id": alert_id,
                        "topic": selection["topic"],
                        "count": grounding["ungrounded_count"],
                        "status_counts": grounding["status_counts"],
                        "meaning": (
                            "the live model emitted evidence that production Grounding "
                            "did not admit; Decision must remain review-only"
                        ),
                    }
                )
            analyzer_summary = sample_record["runtime"]["analyzer"]
            if analyzer_summary.get("repair_applied"):
                quality_findings.append(
                    {
                        "code": "live_model_json_repair_applied",
                        "alert_id": alert_id,
                        "topic": selection["topic"],
                        "meaning": (
                            "the configured model response required conservative JSON repair"
                        ),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - report every selected row
            runtime_failures.append(
                {
                    "alert_id": alert_id,
                    "topic": selection["topic"],
                    "sample_kind": selection["sample_kind"],
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )

    normal_records = [
        item for item in records if item["sample_kind"] == "representative"
    ]
    gap_records = [item for item in records if item["sample_kind"] == "known_input_gap"]
    selected_topics = {item["topic"] for item in normal_records}
    expected_gap_ids = {
        _alert_id_text(item.get("alert_id"))
        for item in _mapping_list(
            corpus_inventory.get("known_input_gaps"),
            "D-0 known_input_gaps",
        )
    }
    actual_gap_ids = {item["alert_id"] for item in gap_records}
    source_counts = Counter(item["actual_source_type"] for item in normal_records)
    model_counts = Counter(
        item["runtime"]["analyzer"]["model_name"] or "model_unavailable"
        for item in records
    )
    verdict_counts = Counter(
        (
            item["runtime"]["analysis"]["verdict"]
            if item["runtime"]["analysis"]
            else "analysis_unavailable"
        )
        for item in records
    )
    grounding_totals = {
        "evidence_count": sum(
            (item["runtime"]["grounding"] or {}).get("total_count", 0)
            for item in records
        ),
        "grounded_count": sum(
            (item["runtime"]["grounding"] or {}).get("grounded_count", 0)
            for item in records
        ),
        "ungrounded_count": sum(
            (item["runtime"]["grounding"] or {}).get("ungrounded_count", 0)
            for item in records
        ),
        "description_leakage_count": sum(
            (item["runtime"]["grounding"] or {}).get(
                "description_leakage_count",
                0,
            )
            for item in records
        ),
    }
    token_usage = _aggregate_token_usage(records)
    all_sample_checks_pass = all(all(item["checks"].values()) for item in records)

    checks = {
        "d0_acceptance_allows_continuation": (
            inventory_acceptance.get("status") in _ALLOWED_D0_STATUSES
        ),
        "d0_links_exact_corpus_file": (
            inventory_input.get("corpus_sha256") == corpus_file_sha256
        ),
        "corpus_index_is_unique": not corpus_index_failures,
        "selection_has_no_failures": not selection_failures,
        "every_known_topic_has_one_representative": (
            selected_topics == set(topic_sources)
            and len(normal_records) == len(topic_sources)
        ),
        "every_known_source_family_is_covered": (
            set(source_counts) == set(topic_sources.values())
        ),
        "all_d0_known_input_gaps_are_replayed": actual_gap_ids == expected_gap_ids,
        "no_selected_runtime_failures": not runtime_failures,
        "every_sample_used_requested_live_model": bool(records)
        and all(
            item["runtime"]["analyzer"]["model_name"] == expected_model_name
            and item["runtime"]["analyzer"]["step_name"] == "analyze_llm"
            for item in records
        ),
        "all_sample_runtime_guards_pass": all_sample_checks_pass,
    }
    failed_checks = sorted(name for name, passed in checks.items() if not passed)
    if failed_checks:
        status = "failed"
    elif quality_findings:
        status = "passed_with_quality_findings"
    else:
        status = "passed"

    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "scope": {
            "performed": [
                "d0_lineage_validation",
                "deterministic_topic_representative_selection",
                "all_known_input_gap_selection",
                "configured_live_llm_runtime_replay",
                "production_prompt_parser_grounding_and_decision",
                "cross_source_model_evidence_and_decision_guard_audit",
            ],
            "not_performed": [
                "human_ground_truth_or_model_accuracy_evaluation",
                "tenant_disposition_policy",
                "correlation_or_memory_retrieval",
                "tool_or_mcp_invocation",
                "persistence",
                "review_queue_or_action",
                "full_212_row_runtime_replay",
            ],
            "classification": "live_model_evaluation_not_runtime_node",
        },
        "input": {
            "corpus_path": _relative_path(corpus_path),
            "corpus_sha256": corpus_file_sha256,
            "corpus_row_count": len(corpus),
            "d0_schema_version": corpus_inventory.get("schema_version"),
            "d0_status": inventory_acceptance.get("status"),
            "selection_policy_version": SELECTION_POLICY_VERSION,
            "requested_model_name": expected_model_name,
        },
        "selection_policy": {
            "representative_unit": "one_per_known_topic",
            "eligible_representative": (
                "D-0 row with no issue_codes and evidence_input_shape other "
                "than evidence_unavailable"
            ),
            "ranking": (
                "minimum Manhattan distance from topic medians for "
                "hit_log_count/raw_event_count/non_empty_message_count; "
                "numeric alert_id tie-break"
            ),
            "known_input_gap_policy": "include every D-0 known_input_gap row",
            "expected_topic_sources": dict(sorted(topic_sources.items())),
        },
        "acceptance": {
            "status": status,
            "failed_checks": failed_checks,
            "checks": checks,
            "representative_count": len(normal_records),
            "known_input_gap_count": len(gap_records),
            "runtime_failure_count": len(runtime_failures),
            "quality_finding_count": len(quality_findings),
        },
        "coverage": {
            "topic_count": len(selected_topics),
            "topics": sorted(selected_topics),
            "source_type_counts": dict(sorted(source_counts.items())),
            "source_family_count": len(source_counts),
            "model_counts": dict(sorted(model_counts.items())),
            "verdict_counts": dict(sorted(verdict_counts.items())),
            "grounding_totals": grounding_totals,
            "token_usage": token_usage,
        },
        "quality_findings": quality_findings,
        "failures": {
            "selection": selection_failures,
            "corpus_index": corpus_index_failures,
            "runtime": runtime_failures,
        },
        "samples": records,
    }
    return report, sample_artifacts


def _select_rows(
    inventory_rows: Sequence[Mapping[str, Any]],
    *,
    known_input_gaps: Sequence[Mapping[str, Any]],
    expected_source_type_by_topic: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selections: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    rows_by_id = {_alert_id_text(item.get("alert_id")): item for item in inventory_rows}

    for topic, expected_source_type in sorted(expected_source_type_by_topic.items()):
        topic_rows = [item for item in inventory_rows if item.get("topic") == topic]
        eligible = [
            item
            for item in topic_rows
            if not item.get("issue_codes")
            and item.get("evidence_input_shape") != "evidence_unavailable"
        ]
        if not eligible:
            failures.append(
                {
                    "topic": topic,
                    "error": "no eligible representative row",
                }
            )
            continue
        medians = {
            metric: statistics.median(
                _non_negative_int(item.get(metric)) for item in eligible
            )
            for metric in _REPRESENTATIVE_METRICS
        }
        selected = min(
            eligible,
            key=lambda item: (
                sum(
                    abs(_non_negative_int(item.get(metric)) - medians[metric])
                    for metric in _REPRESENTATIVE_METRICS
                ),
                *(
                    abs(_non_negative_int(item.get(metric)) - medians[metric])
                    for metric in _REPRESENTATIVE_METRICS
                ),
                _alert_id_sort_key(item.get("alert_id")),
            ),
        )
        selections.append(
            {
                "sample_kind": "representative",
                "alert_id": _alert_id_text(selected.get("alert_id")),
                "topic": topic,
                "expected_source_type": expected_source_type,
                "evidence_input_shape": selected.get("evidence_input_shape"),
                "d0_issue_codes": list(selected.get("issue_codes") or []),
                "d0_canonical_payload_sha256": selected.get("canonical_payload_sha256"),
                "selection_metrics": {
                    metric: _non_negative_int(selected.get(metric))
                    for metric in _REPRESENTATIVE_METRICS
                },
                "topic_medians": medians,
            }
        )

    for gap in sorted(
        known_input_gaps,
        key=lambda item: _alert_id_sort_key(item.get("alert_id")),
    ):
        alert_id = _alert_id_text(gap.get("alert_id"))
        row = rows_by_id.get(alert_id)
        if row is None:
            failures.append(
                {
                    "alert_id": alert_id,
                    "error": "known D-0 input gap row is missing from rows",
                }
            )
            continue
        topic = str(row.get("topic") or "unknown")
        selections.append(
            {
                "sample_kind": "known_input_gap",
                "alert_id": alert_id,
                "topic": topic,
                "expected_source_type": expected_source_type_by_topic.get(
                    topic,
                    str(row.get("expected_source_type") or "other"),
                ),
                "evidence_input_shape": row.get("evidence_input_shape"),
                "d0_issue_codes": list(row.get("issue_codes") or []),
                "d0_canonical_payload_sha256": row.get("canonical_payload_sha256"),
                "selection_metrics": {
                    metric: _non_negative_int(row.get(metric))
                    for metric in _REPRESENTATIVE_METRICS
                },
                "topic_medians": None,
            }
        )
    return selections, failures


def _review_run(
    selection: Mapping[str, Any],
    *,
    run: AnalysisRun,
    full_data_hash: str,
    payload_hash: str,
    expected_model_name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    alert_id = str(selection["alert_id"])
    normalization = run.normalization_report
    request = run.llm_analysis_request
    analysis = run.analysis
    grounding = run.analysis_evidence_grounding
    decision = run.decision
    actual_steps = [item.step_name for item in run.steps]
    step_statuses = {item.step_name: item.status.value for item in run.steps}
    analyzer_step = next(
        (item for item in run.steps if item.step_name == "analyze_llm"),
        None,
    )
    analyzer_metadata = dict(analyzer_step.metadata) if analyzer_step else {}
    coverage_counts = dict(request.evidence_coverage.counts) if request else {}
    selected_skills = (
        [item.skill_name for item in request.skill_context.selected_skills]
        if request
        else []
    )
    has_bounded_evidence = bool(
        request
        and (
            request.primary_evidence is not None
            or request.supplementary_evidence
            or request.evidence_highlights
        )
    )
    expected_gap = selection["sample_kind"] == "known_input_gap"
    explicit_gap_reason = _has_explicit_runtime_input_gap_reason(run)
    primary_scenarios = (
        [item for item in analysis.scenario_assessments if item.is_primary]
        if analysis
        else []
    )
    grounding_status_counts = (
        Counter(item.status.value for item in grounding.items)
        if grounding
        else Counter()
    )

    checks = {
        "alert_id_matches": run.alert_id == alert_id,
        "d0_payload_hash_matches": (
            selection.get("d0_canonical_payload_sha256") == full_data_hash
        ),
        "input_payload_preserved": (
            run.input_payload is not None
            and canonical_sha256(run.input_payload) == payload_hash
        ),
        "expected_source_type_matches": (
            normalization is not None
            and normalization.source_type.value == selection["expected_source_type"]
        ),
        "production_step_sequence_matches": (
            tuple(actual_steps) == EXPECTED_RUNTIME_STEPS
        ),
        "all_runtime_steps_succeeded": bool(run.steps)
        and all(status == "success" for status in step_statuses.values()),
        "requested_live_model_is_explicit": (
            run.model_name == expected_model_name
            and "analyze_llm" in step_statuses
            and "analyze_stub" not in step_statuses
            and analyzer_metadata.get("analyzer") == "json_llm"
        ),
        "prompt_and_parser_versions_are_recorded": (
            bool(run.prompt_version)
            and run.prompt_version != "stub"
            and bool(analyzer_metadata.get("parser_version"))
        ),
        "analysis_and_grounding_exist": (
            analysis is not None
            and grounding is not None
            and grounding.total_count == len(analysis.evidence)
        ),
        "analysis_result_v2_is_complete": (
            analysis is not None
            and analysis.schema_version == "soc.analysis_result.v2"
            and bool(analysis.evidence)
            and bool(analysis.scenario_assessments)
            and len(primary_scenarios) == 1
            and bool(analysis.manual_checks)
        ),
        "decision_is_fail_closed": (
            run.status is AnalysisRunStatus.NEEDS_REVIEW
            and decision is not None
            and decision.needs_review
            and decision.automation_allowed is False
        ),
        "representative_has_bounded_evidence": expected_gap or has_bounded_evidence,
        "known_input_gap_has_no_bounded_evidence": (
            not expected_gap or not has_bounded_evidence
        ),
        "known_input_gap_is_not_sufficient": (
            not expected_gap
            or (decision is not None and decision.evidence_state.value != "sufficient")
        ),
    }
    summary = {
        "sample_kind": selection["sample_kind"],
        "alert_id": alert_id,
        "topic": selection["topic"],
        "expected_source_type": selection["expected_source_type"],
        "actual_source_type": (
            normalization.source_type.value if normalization else "unknown"
        ),
        "adapter": normalization.adapter if normalization else None,
        "evidence_input_shape": selection["evidence_input_shape"],
        "selection_metrics": selection["selection_metrics"],
        "topic_medians": selection["topic_medians"],
        "semantic_run_sha256": canonical_sha256(
            {
                "alert_id": run.alert_id,
                "status": run.status.value,
                "pipeline_version": run.pipeline_version,
                "model_name": run.model_name,
                "steps": actual_steps,
                "normalization": (
                    normalization.model_dump(mode="json", exclude_none=True)
                    if normalization
                    else None
                ),
                "request": (
                    request.model_dump(mode="json", exclude_none=True)
                    if request
                    else None
                ),
                "analysis": (
                    analysis.model_dump(mode="json", exclude_none=True)
                    if analysis
                    else None
                ),
                "grounding": (
                    grounding.model_dump(mode="json", exclude_none=True)
                    if grounding
                    else None
                ),
                "decision": (
                    decision.model_dump(mode="json", exclude_none=True)
                    if decision
                    else None
                ),
            }
        ),
        "runtime": {
            "status": run.status.value,
            "pipeline_version": run.pipeline_version,
            "model_name": run.model_name,
            "prompt_version": run.prompt_version,
            "step_names": actual_steps,
            "step_statuses": step_statuses,
            "normalization_missing_fields": (
                normalization.missing_fields if normalization else []
            ),
            "normalization_warnings": (normalization.warnings if normalization else []),
            "entity_counts": (
                dict(run.extraction_report.entity_counts)
                if run.extraction_report
                else {}
            ),
            "fact_conflict_count": (
                len(run.fact_reconstruction.conflict_reports)
                if run.fact_reconstruction
                else 0
            ),
            "bounded_evidence": {
                "primary_present": bool(request and request.primary_evidence),
                "supplementary_count": (
                    len(request.supplementary_evidence) if request else 0
                ),
                "highlight_count": (len(request.evidence_highlights) if request else 0),
                "coverage_counts": coverage_counts,
            },
            "analyzer": {
                "step_name": analyzer_step.step_name if analyzer_step else None,
                "model_name": run.model_name,
                "prompt_version": run.prompt_version,
                "parser_version": analyzer_metadata.get("parser_version"),
                "repair_applied": analyzer_metadata.get("repair_applied", False),
                "usage": analyzer_metadata.get("usage", {}),
            },
            "analysis": (
                {
                    "schema_version": analysis.schema_version,
                    "verdict": analysis.verdict.value,
                    "confidence": analysis.confidence,
                    "summary": analysis.summary,
                    "evidence_count": len(analysis.evidence),
                    "scenario_count": len(analysis.scenario_assessments),
                    "primary_scenario": (
                        primary_scenarios[0].model_dump(
                            mode="json",
                            exclude_none=True,
                        )
                        if len(primary_scenarios) == 1
                        else None
                    ),
                    "evidence_gaps": analysis.evidence_gaps,
                    "manual_checks": analysis.manual_checks,
                }
                if analysis
                else None
            ),
            "selected_skills": selected_skills,
            "grounding": (
                {
                    "total_count": grounding.total_count,
                    "grounded_count": grounding.grounded_count,
                    "ungrounded_count": grounding.ungrounded_count,
                    "description_leakage_count": (grounding.description_leakage_count),
                    "status_counts": dict(sorted(grounding_status_counts.items())),
                }
                if grounding
                else None
            ),
            "decision": (
                decision.model_dump(mode="json", exclude_none=True)
                if decision
                else None
            ),
        },
        "runtime_gap_visibility": {
            "d0_issue_codes": selection["d0_issue_codes"],
            "bounded_evidence_present": has_bounded_evidence,
            "llm_projected_count": coverage_counts.get("llm_projected_count", 0),
            "explicit_runtime_input_gap_reason": explicit_gap_reason,
        },
        "checks": checks,
    }
    artifact = {
        "schema_version": SAMPLE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "selection": dict(selection),
        "checks": checks,
        "runtime_gap_visibility": summary["runtime_gap_visibility"],
        "analysis_run": run.model_dump(mode="json", exclude_none=True),
    }
    return summary, artifact


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


def _aggregate_token_usage(records: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for item in records:
        usage = item["runtime"]["analyzer"].get("usage") or {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(key)
            if isinstance(value, int) and value >= 0:
                totals[key] += value
    return {
        "input_tokens": totals["input_tokens"],
        "output_tokens": totals["output_tokens"],
        "total_tokens": totals["total_tokens"],
    }


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
        raise ValueError(
            f"representative metric must be an integer: {value!r}"
        ) from exc
    if parsed < 0:
        raise ValueError(f"representative metric must be non-negative: {parsed}")
    return parsed


def _alert_id_text(value: Any) -> str:
    if value is None:
        raise ValueError("alert_id is required")
    text = str(value).strip()
    if not text:
        raise ValueError("alert_id is required")
    if text.endswith(".0"):
        prefix = text[:-2]
        if prefix.isdigit():
            return prefix
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--inventory", type=Path, default=DEFAULT_INVENTORY_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--model-name",
        default=os.environ.get("SOC_VALIDATION_MODEL", DEFAULT_MODEL_NAME),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = SocLLMSettings.from_env().with_overrides(
        mode=SocAnalyzerMode.LLM,
        model_name=args.model_name,
    )
    analyzer = build_configured_analyzer(settings=settings)
    if analyzer.step_name != "analyze_llm" or analyzer.model_name == "stub":
        raise RuntimeError("D10 requires the configured live LLM analyzer")
    analysis_service = SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=analyzer,
            sensitive_evidence_mode=settings.sensitive_evidence_mode,
        )
    )
    corpus = load_dataframe_pickle(args.corpus)
    inventory = json.loads(args.inventory.read_text(encoding="utf-8"))
    report, sample_artifacts = build_cross_source_runtime_review(
        corpus,
        inventory,
        corpus_path=args.corpus,
        corpus_file_sha256=sha256_file(args.corpus),
        analysis_service=analysis_service,
        expected_model_name=analyzer.model_name,
    )
    for relative_name, artifact in sample_artifacts.items():
        write_json_atomic(artifact, args.output_dir / relative_name)
    output_path = args.output_dir / "representative-matrix.json"
    write_json_atomic(report, output_path)
    print(
        json.dumps(
            {
                "output": _relative_path(output_path),
                "status": report["acceptance"]["status"],
                "failed_checks": report["acceptance"]["failed_checks"],
                "representative_count": report["acceptance"]["representative_count"],
                "known_input_gap_count": report["acceptance"]["known_input_gap_count"],
                "runtime_failure_count": report["acceptance"]["runtime_failure_count"],
                "model_counts": report["coverage"]["model_counts"],
                "verdict_counts": report["coverage"]["verdict_counts"],
                "grounding_totals": report["coverage"]["grounding_totals"],
                "token_usage": report["coverage"]["token_usage"],
                "quality_findings": report["quality_findings"],
                "topics": report["coverage"]["topics"],
                "source_type_counts": report["coverage"]["source_type_counts"],
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 1 if report["acceptance"]["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
