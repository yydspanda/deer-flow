#!/usr/bin/env python3
"""Run a resumable PingAn PKL batch through the production SOC Runtime."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.actions.mcp import (  # noqa: E402
    DeerFlowCachedMcpToolProvider,
    build_mcp_action_adapter_registry_from_files,
    load_mcp_action_adapter_configs,
)
from soc_agent.application import (  # noqa: E402
    build_soc_analysis_service,
    build_soc_investigation_reporting_service,
    build_soc_investigation_workflow_service,
    load_soc_enrichment_composition_config,
    validate_soc_enrichment_registry,
)
from soc_agent.contracts import (  # noqa: E402
    ActorContext,
    ActorType,
    EntrySurface,
    ServiceRequestContext,
    SocEnrichmentExecutionCommand,
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
)
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    resolve_database_url,
    to_sync_database_url,
)
from soc_agent.llm import (  # noqa: E402
    SocAnalyzerMode,
    SocLLMSettings,
    resolve_soc_model_name,
)

MANIFEST_SCHEMA_VERSION = "soc.pingan_internal_runtime_batch_manifest.v1"
ITEM_SCHEMA_VERSION = "soc.pingan_internal_runtime_batch_item.v1"
RESULTS_SCHEMA_VERSION = "soc.pingan_internal_runtime_batch_results.v1"
DEFAULT_SOURCE = ROOT / "datas/source/full_alert_2026_month_forth_sample_200.pkl"
DEFAULT_OUTPUT_ROOT = (
    BACKEND_ROOT / ".deer-flow/soc-internal-validation/runtime-batches"
)
_SUCCESS_RUN_STATUSES = frozenset({"success", "needs_review"})


@dataclass(frozen=True)
class BatchItem:
    source_index: int
    alert_id: str
    payload: dict[str, Any]
    payload_sha256: str
    row_sha256: str


@dataclass(frozen=True)
class BatchExecutionConfig:
    source_path: Path
    source_sha256: str
    output_dir: Path
    analyzer_mode: str
    model_name: str | None
    sensitive_evidence_mode: str
    persist: bool
    database_kind: str
    workers: int
    resume: bool
    retry_failures: bool
    fail_fast: bool
    checkpoint_every: int
    default_tenant_id: str | None = None
    investigation_enrichment_enabled: bool = False
    enrichment_composition_sha256: str | None = None
    enrichment_action_config_sha256s: tuple[str, ...] = ()
    enrichment_extensions_config_sha256: str | None = None


def prepare_batch_items(
    frame: pd.DataFrame,
    *,
    start_index: int = 0,
    limit: int | None = None,
    default_tenant_id: str | None = None,
) -> tuple[list[BatchItem], list[dict[str, Any]]]:
    """Validate source wrappers and return bounded row selections plus input errors."""

    if start_index < 0:
        raise ValueError("start_index must be >= 0")
    if limit is not None and limit < 1:
        raise ValueError("limit must be >= 1")
    stop = len(frame) if limit is None else min(len(frame), start_index + limit)
    items: list[BatchItem] = []
    errors: list[dict[str, Any]] = []
    for source_index in range(start_index, stop):
        row = frame.iloc[source_index]
        wrapper = row.get("alert_full_data")
        try:
            if not isinstance(wrapper, Mapping):
                raise TypeError("alert_full_data must be an object")
            payload = wrapper.get("alert_data")
            if not isinstance(payload, Mapping):
                raise TypeError("alert_full_data.alert_data must be an object")
            alert_id = _alert_id(row, wrapper, payload, source_index=source_index)
            payload_dict = dict(payload)
            source_tenant_id = payload_dict.get("tenant_id") or payload_dict.get(
                "tenantId"
            )
            if default_tenant_id is not None:
                if (
                    source_tenant_id is not None
                    and str(source_tenant_id) != default_tenant_id
                ):
                    raise ValueError(
                        f"source tenant_id {source_tenant_id!r} does not match default tenant {default_tenant_id!r}"
                    )
                if source_tenant_id is None:
                    payload_dict["tenant_id"] = default_tenant_id
            items.append(
                BatchItem(
                    source_index=source_index,
                    alert_id=alert_id,
                    payload=payload_dict,
                    payload_sha256=_canonical_sha256(payload_dict),
                    row_sha256=_canonical_sha256(dict(wrapper)),
                )
            )
        except Exception as exc:  # noqa: BLE001 - retain every invalid source row
            errors.append(
                {
                    "source_index": source_index,
                    "error_type": type(exc).__name__,
                    "error": _safe_error(exc),
                }
            )
    return items, errors


def execute_batch(
    items: Sequence[BatchItem],
    *,
    analysis_service: Any,
    config: BatchExecutionConfig,
    source_row_count: int,
    source_errors: Sequence[Mapping[str, Any]] = (),
    investigation_service: Any | None = None,
    investigation_reporting_service: Any | None = None,
) -> dict[str, Any]:
    """Execute selected rows, checkpoint atomically, and support exact resume."""

    if config.investigation_enrichment_enabled:
        if not config.persist:
            raise ValueError("investigation enrichment requires persisted batch runs")
        if investigation_service is None:
            raise ValueError(
                "investigation enrichment is enabled but no investigation service was provided"
            )
        if investigation_reporting_service is None:
            raise ValueError(
                "investigation enrichment is enabled but no investigation reporting service was provided"
            )
        if (
            not config.enrichment_composition_sha256
            or not config.enrichment_action_config_sha256s
            or not config.enrichment_extensions_config_sha256
        ):
            raise ValueError(
                "investigation enrichment requires composition, action-config and extensions-config fingerprints"
            )
    elif (
        investigation_service is not None or investigation_reporting_service is not None
    ):
        raise ValueError(
            "investigation services were provided while investigation enrichment is disabled"
        )

    output_dir = config.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    output_dir.chmod(0o700)
    items_dir = output_dir / "items"
    items_dir.mkdir(exist_ok=True, mode=0o700)
    items_dir.chmod(0o700)
    manifest_path = output_dir / "manifest.json"

    with _directory_lock(output_dir / ".batch.lock"):
        previous_manifest = _load_previous_manifest(manifest_path)
        _validate_resume(previous_manifest, config=config)
        existing = _load_existing_items(items_dir, config=config)
        selected_indexes = {item.source_index for item in items}
        stale_indexes = sorted(
            index for index in existing if index not in selected_indexes
        )
        if stale_indexes:
            raise ValueError(
                "output directory contains item indexes outside this selection; use a new output directory or the original start/limit"
            )

        pending = [
            item
            for item in items
            if not _should_skip_existing(
                existing.get(item.source_index),
                item=item,
                retry_failures=config.retry_failures,
            )
        ]
        resumed_count = len(items) - len(pending)
        started_at = datetime.now(UTC)
        manifest = _build_manifest(
            config=config,
            source_row_count=source_row_count,
            selected_count=len(items),
            source_errors=source_errors,
            started_at=started_at,
            status="running",
            resumed_count=resumed_count,
        )
        if previous_manifest is not None:
            manifest["batch_id"] = previous_manifest["batch_id"]
            manifest["started_at"] = previous_manifest["started_at"]
            manifest["resumed_at"] = started_at.isoformat()
        _write_json_atomic(manifest_path, manifest)

        completed_since_checkpoint = 0
        stop_after_failure = False
        if config.workers == 1:
            for item in pending:
                record = _analyze_item(
                    item,
                    analysis_service=analysis_service,
                    config=config,
                    previous_record=existing.get(item.source_index),
                    investigation_service=investigation_service,
                    investigation_reporting_service=investigation_reporting_service,
                )
                _write_item(items_dir, record)
                existing[item.source_index] = record
                completed_since_checkpoint += 1
                if completed_since_checkpoint >= config.checkpoint_every:
                    _checkpoint_manifest(manifest_path, manifest, existing)
                    completed_since_checkpoint = 0
                if record["outcome"] == "failed" and config.fail_fast:
                    stop_after_failure = True
                    break
        else:
            with ThreadPoolExecutor(
                max_workers=config.workers, thread_name_prefix="soc-batch"
            ) as executor:
                futures: dict[Future[dict[str, Any]], BatchItem] = {
                    executor.submit(
                        _analyze_item,
                        item,
                        analysis_service=analysis_service,
                        config=config,
                        previous_record=existing.get(item.source_index),
                        investigation_service=investigation_service,
                        investigation_reporting_service=investigation_reporting_service,
                    ): item
                    for item in pending
                }
                for future in as_completed(futures):
                    record = future.result()
                    _write_item(items_dir, record)
                    existing[futures[future].source_index] = record
                    completed_since_checkpoint += 1
                    if completed_since_checkpoint >= config.checkpoint_every:
                        _checkpoint_manifest(manifest_path, manifest, existing)
                        completed_since_checkpoint = 0
                    if record["outcome"] == "failed" and config.fail_fast:
                        stop_after_failure = True
                        for queued in futures:
                            queued.cancel()
                        break

        item_records = existing
        results = _write_results(output_dir / "results.jsonl", item_records)
        summary = _summarize_records(
            list(item_records.values()),
            selected_count=len(items),
            source_error_count=len(source_errors),
        )
        final_status = (
            "interrupted"
            if stop_after_failure
            else (
                "completed_with_failures"
                if summary["failed_count"] or source_errors
                else "completed"
            )
        )
        manifest.update(
            {
                "status": final_status,
                "updated_at": datetime.now(UTC).isoformat(),
                "ended_at": datetime.now(UTC).isoformat(),
                "summary": summary,
                "artifacts": {
                    "items_directory": "items",
                    "results_jsonl": "results.jsonl",
                    "results_sha256": _sha256_file(output_dir / "results.jsonl"),
                    "result_record_count": results,
                },
            }
        )
        _write_json_atomic(manifest_path, manifest)
        return manifest


def _analyze_item(
    item: BatchItem,
    *,
    analysis_service: Any,
    config: BatchExecutionConfig,
    previous_record: Mapping[str, Any] | None = None,
    investigation_service: Any | None = None,
    investigation_reporting_service: Any | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="pingan-internal-batch",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.CLI,
            roles=["soc_batch_runner"],
        ),
        trace_id=f"batch-{config.source_sha256[:12]}-{item.source_index}",
        idempotency_key=(
            f"pingan-batch:{config.source_sha256[:16]}:{item.source_index}:{item.payload_sha256[:16]}"
        ),
    )
    source = {
        "source_file_sha256": config.source_sha256,
        "source_index": item.source_index,
        "alert_id": item.alert_id,
        "row_sha256": item.row_sha256,
        "payload_sha256": item.payload_sha256,
    }
    retry_of_run_id = (
        _failed_analysis_run_id(previous_record)
        if config.persist and config.resume and config.retry_failures
        else None
    )
    analysis_context = context
    if retry_of_run_id is not None:
        analysis_context = context.model_copy(
            update={
                "idempotency_key": (
                    f"{context.idempotency_key}:analysis-retry:{retry_of_run_id}"
                )
            }
        )
    try:
        if retry_of_run_id is None:
            run = analysis_service.analyze(item.payload, context=analysis_context)
        else:
            run = analysis_service.replay(
                retry_of_run_id,
                context=analysis_context,
            )
    except Exception as exc:  # noqa: BLE001 - one row must not lose the batch
        execution = {
            "analyzer_mode": config.analyzer_mode,
            "requested_model_name": config.model_name,
            "persisted": config.persist,
            "investigation_enrichment_enabled": config.investigation_enrichment_enabled,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "completed_at": datetime.now(UTC).isoformat(),
        }
        if retry_of_run_id is not None:
            execution["analysis_retry_of_run_id"] = retry_of_run_id
        return {
            "schema_version": ITEM_SCHEMA_VERSION,
            "outcome": "failed",
            "source": source,
            "execution": execution,
            "summary": {"runtime_status": "exception"},
            "error": {
                "stage": (
                    "analysis_replay"
                    if retry_of_run_id is not None
                    else "analysis_runtime"
                ),
                "error_type": type(exc).__name__,
                "message": _safe_error(exc),
            },
        }

    run_payload = run.model_dump(mode="json", exclude_none=True)
    run_status = str(run_payload.get("status") or "unknown")
    outcome = "completed" if run_status in _SUCCESS_RUN_STATUSES else "failed"
    record: dict[str, Any] = {
        "schema_version": ITEM_SCHEMA_VERSION,
        "outcome": outcome,
        "source": source,
        "execution": {
            "analyzer_mode": config.analyzer_mode,
            "requested_model_name": config.model_name,
            "persisted": config.persist,
            "investigation_enrichment_enabled": config.investigation_enrichment_enabled,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "completed_at": datetime.now(UTC).isoformat(),
        },
        "summary": _run_summary(run_payload),
        "analysis_run": run_payload,
    }
    if retry_of_run_id is not None:
        record["execution"]["analysis_retry_of_run_id"] = retry_of_run_id
    if investigation_service is None or outcome != "completed":
        return record

    investigation_context = context.model_copy(
        update={"idempotency_key": f"{context.idempotency_key}:investigation"}
    )
    try:
        workflow_result = investigation_service.execute(
            SocEnrichmentExecutionCommand(
                run_id=run.run_id,
                thread_id=f"THR-{run.run_id}",
                trigger=SocEnrichmentExecutionTrigger.INTERNAL_BATCH,
            ),
            context=investigation_context,
        )
    except Exception as exc:  # noqa: BLE001 - preserve the completed base run
        record["outcome"] = "failed"
        record["summary"]["investigation_status"] = "exception"
        record["error"] = {
            "stage": "investigation_workflow",
            "error_type": type(exc).__name__,
            "message": _safe_error(exc),
        }
        record["execution"]["duration_ms"] = round(
            (time.monotonic() - started) * 1000, 3
        )
        record["execution"]["completed_at"] = datetime.now(UTC).isoformat()
        return record

    workflow_payload = workflow_result.model_dump(mode="json", exclude_none=True)
    record["investigation_workflow"] = workflow_payload
    record["summary"].update(_investigation_summary(workflow_payload))
    if investigation_reporting_service is None:
        raise ValueError(
            "investigation reporting service is required after workflow execution"
        )
    try:
        bundle = investigation_reporting_service.get_report_bundle(
            workflow_result.execution.execution_id
        )
        if bundle is None:
            raise RuntimeError(
                "persisted investigation could not be projected into a D4 report"
            )
        shadow_report, addendum = bundle
    except Exception as exc:  # noqa: BLE001 - retain D3 state when D4 projection fails
        record["outcome"] = "failed"
        record["error"] = {
            "stage": "investigation_reporting",
            "error_type": type(exc).__name__,
            "message": _safe_error(exc),
        }
        record["execution"]["duration_ms"] = round(
            (time.monotonic() - started) * 1000, 3
        )
        record["execution"]["completed_at"] = datetime.now(UTC).isoformat()
        return record
    shadow_payload = shadow_report.model_dump(mode="json", exclude_none=True)
    record["investigation_shadow_report"] = shadow_payload
    record["investigation_addendum"] = addendum.model_dump(
        mode="json", exclude_none=True
    )
    record["summary"].update(_investigation_reporting_summary(shadow_payload))
    if workflow_result.execution.status in {
        SocEnrichmentExecutionStatus.RETRYABLE_FAILED,
        SocEnrichmentExecutionStatus.FAILED,
    }:
        record["outcome"] = "failed"
        record["error"] = {
            "stage": "investigation_workflow",
            "error_type": workflow_result.execution.last_error_type
            or "InvestigationWorkflowFailed",
            "message": workflow_result.execution.last_error
            or (
                f"investigation workflow ended as {workflow_result.execution.status.value}"
            ),
            "retryable": workflow_result.execution.retryable,
        }
    record["execution"]["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
    record["execution"]["completed_at"] = datetime.now(UTC).isoformat()
    return record


def _run_summary(run: Mapping[str, Any]) -> dict[str, Any]:
    analysis = _mapping(run.get("analysis"))
    decision = _mapping(run.get("decision"))
    normalization = _mapping(run.get("normalization_report"))
    grounding = _mapping(run.get("analysis_evidence_grounding"))
    usage = _analysis_usage(run.get("steps"))
    return {
        "run_id": run.get("run_id"),
        "runtime_status": run.get("status"),
        "source_type": normalization.get("source_type"),
        "normalizer_adapter": normalization.get("adapter"),
        "model_name": run.get("model_name"),
        "prompt_version": run.get("prompt_version"),
        "verdict": analysis.get("verdict"),
        "confidence": analysis.get("confidence"),
        "recommended_action": analysis.get("recommended_action"),
        "evidence_state": decision.get("evidence_state"),
        "needs_review": decision.get("needs_review"),
        "automation_allowed": decision.get("automation_allowed"),
        "grounded_evidence_count": grounding.get("grounded_count"),
        "ungrounded_evidence_count": grounding.get("ungrounded_count"),
        "usage": usage,
    }


def _investigation_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    execution = _mapping(result.get("execution"))
    plan = _mapping(execution.get("plan"))
    return {
        "investigation_execution_id": execution.get("execution_id"),
        "investigation_status": execution.get("status"),
        "investigation_trigger": execution.get("trigger"),
        "investigation_plan_status": plan.get("status"),
        "investigation_planned_action_count": len(plan.get("actions") or []),
        "investigation_attempt_count": execution.get("attempt_count"),
        "investigation_success_count": execution.get("success_count"),
        "investigation_not_found_count": execution.get("not_found_count"),
        "investigation_failed_count": execution.get("failed_count"),
        "investigation_evidence_count": execution.get("evidence_count"),
        "investigation_provider_invocation_count": result.get(
            "provider_invocation_count"
        ),
        "investigation_idempotent_replay": result.get("idempotent_replay"),
    }


def _investigation_reporting_summary(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "investigation_report_id": report.get("report_id"),
        "investigation_retry_count": report.get("retry_count"),
        "investigation_evidence_coverage_ratio": report.get("evidence_coverage_ratio"),
        "investigation_persisted_evidence_count": report.get(
            "persisted_evidence_count"
        ),
        "investigation_missing_evidence_count": report.get("missing_evidence_count"),
        "investigation_attempt_latency_ms_p95": report.get("attempt_latency_ms_p95"),
        "investigation_cost_measurement_status": report.get("cost_measurement_status"),
        "investigation_measurement_gaps": list(report.get("measurement_gaps") or []),
    }


def _analysis_usage(steps: Any) -> dict[str, Any]:
    if not isinstance(steps, list):
        return {}
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        metadata = _mapping(step.get("metadata"))
        usage = metadata.get("usage")
        if isinstance(usage, Mapping):
            return dict(usage)
    return {}


def _build_manifest(
    *,
    config: BatchExecutionConfig,
    source_row_count: int,
    selected_count: int,
    source_errors: Sequence[Mapping[str, Any]],
    started_at: datetime,
    status: str,
    resumed_count: int,
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "batch_id": f"PB-{config.source_sha256[:12].upper()}-{started_at.strftime('%Y%m%dT%H%M%SZ')}",
        "status": status,
        "started_at": started_at.isoformat(),
        "updated_at": started_at.isoformat(),
        "source": {
            "path": str(config.source_path.resolve()),
            "sha256": config.source_sha256,
            "row_count": source_row_count,
            "selected_count": selected_count,
            "source_error_count": len(source_errors),
            "source_errors": [dict(item) for item in source_errors[:100]],
            "source_errors_truncated": len(source_errors) > 100,
        },
        "execution": {
            "analyzer_mode": config.analyzer_mode,
            "model_name": config.model_name,
            "sensitive_evidence_mode": config.sensitive_evidence_mode,
            "persist": config.persist,
            "database_kind": config.database_kind,
            "workers": config.workers,
            "default_tenant_id": config.default_tenant_id,
            "resume": config.resume,
            "retry_failures": config.retry_failures,
            "fail_fast": config.fail_fast,
            "resumed_completed_count": resumed_count,
            "investigation_enrichment_enabled": config.investigation_enrichment_enabled,
            "enrichment_composition_sha256": config.enrichment_composition_sha256,
            "enrichment_action_config_sha256s": list(
                config.enrichment_action_config_sha256s
            ),
            "enrichment_extensions_config_sha256": config.enrichment_extensions_config_sha256,
            "fixed_runtime_independently_usable": True,
            "secrets_included": False,
        },
        "safety": {
            "live_model_requires_explicit_confirmation": True,
            "investigation_provider_calls_require_explicit_confirmation": True,
            "investigation_actions_are_exact_allowlisted_read_only": True,
            "investigation_cannot_mutate_base_runtime_decision": True,
            "source_pickle_loaded_with_restricted_unpickler": True,
            "raw_payloads_are_local_sensitive_artifacts": True,
            "artifact_file_mode": "0600",
            "output_directory_mode": "0700",
        },
    }


def _checkpoint_manifest(
    manifest_path: Path,
    manifest: dict[str, Any],
    records: Mapping[int, Mapping[str, Any]],
) -> None:
    manifest["updated_at"] = datetime.now(UTC).isoformat()
    manifest["summary"] = _summarize_records(
        list(records.values()),
        selected_count=int(_mapping(manifest.get("source")).get("selected_count") or 0),
        source_error_count=int(
            _mapping(manifest.get("source")).get("source_error_count") or 0
        ),
    )
    _write_json_atomic(manifest_path, manifest)


def _summarize_records(
    records: Sequence[Mapping[str, Any]],
    *,
    selected_count: int,
    source_error_count: int,
) -> dict[str, Any]:
    outcomes = Counter(str(item.get("outcome") or "unknown") for item in records)
    runtime_statuses = Counter(
        str(_mapping(item.get("summary")).get("runtime_status") or "unknown")
        for item in records
    )
    verdicts = Counter(
        str(_mapping(item.get("summary")).get("verdict") or "unknown")
        for item in records
    )
    investigation_statuses = Counter(
        str(_mapping(item.get("summary")).get("investigation_status"))
        for item in records
        if _mapping(item.get("summary")).get("investigation_status") is not None
    )
    investigation_reports = [
        _mapping(item.get("investigation_shadow_report"))
        for item in records
        if _mapping(item.get("investigation_shadow_report"))
    ]
    investigation_routes = Counter()
    investigation_measurement_gaps = Counter()
    investigation_attempt_latencies: list[float] = []
    real_result_count = 0
    mock_result_count = 0
    for record in records:
        workflow = _mapping(record.get("investigation_workflow"))
        for attempt in workflow.get("attempts") or []:
            duration = _attempt_duration_ms(_mapping(attempt))
            if duration is not None:
                investigation_attempt_latencies.append(duration)
    for report in investigation_reports:
        investigation_measurement_gaps.update(
            str(item) for item in report.get("measurement_gaps") or []
        )
        for route in report.get("routes") or []:
            route_payload = _mapping(route)
            route_name = str(route_payload.get("route") or "unknown")
            investigation_routes[route_name] += int(
                route_payload.get("planned_action_count") or 0
            )
            real_result_count += int(route_payload.get("real_result_count") or 0)
            mock_result_count += int(route_payload.get("mock_result_count") or 0)
    planned_action_count = sum(
        int(item.get("planned_action_count") or 0) for item in investigation_reports
    )
    persisted_evidence_count = sum(
        int(item.get("persisted_evidence_count") or 0) for item in investigation_reports
    )
    review_count = sum(
        _mapping(item.get("summary")).get("needs_review") is True for item in records
    )
    automation_allowed_count = sum(
        _mapping(item.get("summary")).get("automation_allowed") is True
        for item in records
    )
    return {
        "selected_count": selected_count,
        "recorded_count": len(records),
        "pending_count": max(0, selected_count - len(records)),
        "completed_count": outcomes.get("completed", 0),
        "failed_count": outcomes.get("failed", 0),
        "source_error_count": source_error_count,
        "needs_review_count": review_count,
        "automation_allowed_count": automation_allowed_count,
        "runtime_status_counts": dict(sorted(runtime_statuses.items())),
        "verdict_counts": dict(sorted(verdicts.items())),
        "investigation_status_counts": dict(sorted(investigation_statuses.items())),
        "investigation_shadow": {
            "report_count": len(investigation_reports),
            "planned_action_count": planned_action_count,
            "success_count": sum(
                int(item.get("success_count") or 0) for item in investigation_reports
            ),
            "not_found_count": sum(
                int(item.get("not_found_count") or 0) for item in investigation_reports
            ),
            "failed_count": sum(
                int(item.get("failed_count") or 0) for item in investigation_reports
            ),
            "retry_count": sum(
                int(item.get("retry_count") or 0) for item in investigation_reports
            ),
            "provider_invocation_count": sum(
                int(item.get("provider_invocation_count") or 0)
                for item in investigation_reports
            ),
            "persisted_evidence_count": persisted_evidence_count,
            "missing_evidence_count": sum(
                int(item.get("missing_evidence_count") or 0)
                for item in investigation_reports
            ),
            "evidence_coverage_ratio": (
                persisted_evidence_count / planned_action_count
                if planned_action_count
                else 0.0
            ),
            "attempt_latency_sample_count": len(investigation_attempt_latencies),
            "attempt_latency_ms_p50": _nearest_rank_percentile(
                investigation_attempt_latencies, 0.50
            ),
            "attempt_latency_ms_p95": _nearest_rank_percentile(
                investigation_attempt_latencies, 0.95
            ),
            "route_planned_action_counts": dict(sorted(investigation_routes.items())),
            "real_result_count": real_result_count,
            "mock_result_count": mock_result_count,
            "cost_measurement_status_counts": dict(
                sorted(
                    Counter(
                        str(item.get("cost_measurement_status") or "not_measured")
                        for item in investigation_reports
                    ).items()
                )
            ),
            "measurement_gap_counts": dict(
                sorted(investigation_measurement_gaps.items())
            ),
            "unauthorized_base_run_mutation_count": sum(
                item.get("base_run_mutated") is not False
                for item in investigation_reports
            ),
            "auto_close_allowed_count": sum(
                item.get("auto_close_allowed") is not False
                for item in investigation_reports
            ),
            "confirmed_memory_write_allowed_count": sum(
                item.get("confirmed_memory_write_allowed") is not False
                for item in investigation_reports
            ),
            "high_risk_actions_allowed_count": sum(
                item.get("high_risk_actions_allowed") is not False
                for item in investigation_reports
            ),
        },
    }


def _attempt_duration_ms(attempt: Mapping[str, Any]) -> float | None:
    started_at = attempt.get("started_at")
    ended_at = attempt.get("ended_at")
    if not isinstance(started_at, str) or not isinstance(ended_at, str):
        return None
    try:
        started = datetime.fromisoformat(started_at)
        ended = datetime.fromisoformat(ended_at)
    except ValueError:
        return None
    return max(0.0, round((ended - started).total_seconds() * 1000, 3))


def _nearest_rank_percentile(
    values: Sequence[float], percentile: float
) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


def _load_existing_items(
    items_dir: Path,
    *,
    config: BatchExecutionConfig | None,
) -> dict[int, dict[str, Any]]:
    records: dict[int, dict[str, Any]] = {}
    if not items_dir.is_dir():
        return records
    for path in sorted(items_dir.glob("*.json")):
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(loaded, dict)
            or loaded.get("schema_version") != ITEM_SCHEMA_VERSION
        ):
            raise ValueError(f"invalid batch item artifact: {path}")
        source = _mapping(loaded.get("source"))
        source_index = source.get("source_index")
        if not isinstance(source_index, int):
            raise ValueError(f"batch item has invalid source_index: {path}")
        if source_index in records:
            raise ValueError(f"duplicate batch source_index {source_index}")
        if (
            config is not None
            and source.get("source_file_sha256") != config.source_sha256
        ):
            raise ValueError(f"batch item source fingerprint mismatch: {path}")
        records[source_index] = loaded
    return records


def _should_skip_existing(
    existing: Mapping[str, Any] | None,
    *,
    item: BatchItem,
    retry_failures: bool,
) -> bool:
    if existing is None:
        return False
    source = _mapping(existing.get("source"))
    if (
        source.get("payload_sha256") != item.payload_sha256
        or source.get("row_sha256") != item.row_sha256
    ):
        raise ValueError(
            f"resume payload fingerprint mismatch at source_index={item.source_index}"
        )
    if existing.get("outcome") == "completed":
        return True
    return not retry_failures


def _failed_analysis_run_id(
    previous_record: Mapping[str, Any] | None,
) -> str | None:
    """Return the persisted failed run that an explicit batch resume may replay."""

    if previous_record is None or previous_record.get("outcome") != "failed":
        return None
    analysis_run = previous_record.get("analysis_run")
    if not isinstance(analysis_run, Mapping) or analysis_run.get("status") != "failed":
        return None
    run_id = analysis_run.get("run_id")
    return run_id if isinstance(run_id, str) and run_id else None


def _validate_resume(
    previous: Mapping[str, Any] | None,
    *,
    config: BatchExecutionConfig,
) -> None:
    if previous is None:
        if config.resume:
            raise ValueError("--resume requires an existing manifest.json")
        return
    if not config.resume:
        raise ValueError(
            "output directory already contains a batch; pass --resume or choose a new directory"
        )
    source = _mapping(previous.get("source"))
    execution = _mapping(previous.get("execution"))
    expected = {
        "source.sha256": (source.get("sha256"), config.source_sha256),
        "execution.analyzer_mode": (
            execution.get("analyzer_mode"),
            config.analyzer_mode,
        ),
        "execution.model_name": (execution.get("model_name"), config.model_name),
        "execution.sensitive_evidence_mode": (
            execution.get("sensitive_evidence_mode"),
            config.sensitive_evidence_mode,
        ),
        "execution.persist": (execution.get("persist"), config.persist),
        "execution.database_kind": (
            execution.get("database_kind"),
            config.database_kind,
        ),
        "execution.default_tenant_id": (
            execution.get("default_tenant_id"),
            config.default_tenant_id,
        ),
        "execution.investigation_enrichment_enabled": (
            execution.get("investigation_enrichment_enabled", False),
            config.investigation_enrichment_enabled,
        ),
        "execution.enrichment_composition_sha256": (
            execution.get("enrichment_composition_sha256"),
            config.enrichment_composition_sha256,
        ),
        "execution.enrichment_action_config_sha256s": (
            tuple(execution.get("enrichment_action_config_sha256s") or ()),
            config.enrichment_action_config_sha256s,
        ),
        "execution.enrichment_extensions_config_sha256": (
            execution.get("enrichment_extensions_config_sha256"),
            config.enrichment_extensions_config_sha256,
        ),
    }
    mismatches = [
        name for name, (actual, wanted) in expected.items() if actual != wanted
    ]
    if mismatches:
        raise ValueError(f"resume configuration mismatch: {', '.join(mismatches)}")


def _load_previous_manifest(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(loaded, dict)
        or loaded.get("schema_version") != MANIFEST_SCHEMA_VERSION
    ):
        raise ValueError("existing batch manifest has an unsupported schema")
    return loaded


def _write_item(items_dir: Path, record: Mapping[str, Any]) -> None:
    source = _mapping(record.get("source"))
    source_index = int(source["source_index"])
    alert_id = _safe_filename(str(source.get("alert_id") or "unknown"))
    path = items_dir / f"{source_index:07d}-{alert_id}.json"
    _write_json_atomic(path, dict(record))


def _write_results(path: Path, records: Mapping[int, Mapping[str, Any]]) -> int:
    lines = []
    for source_index in sorted(records):
        record = records[source_index]
        lines.append(
            json.dumps(
                {
                    "schema_version": RESULTS_SCHEMA_VERSION,
                    "outcome": record.get("outcome"),
                    "source": record.get("source"),
                    "execution": record.get("execution"),
                    "summary": record.get("summary"),
                    "error": record.get("error"),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    _write_text_atomic(path, "\n".join(lines) + ("\n" if lines else ""))
    return len(lines)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


class _directory_lock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: Any = None

    def __enter__(self) -> None:
        self.handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.handle.close()
            raise RuntimeError(f"another batch process holds {self.path}") from exc
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(
            f"pid={os.getpid()} started_at={datetime.now(UTC).isoformat()}\n"
        )
        self.handle.flush()
        os.fchmod(self.handle.fileno(), 0o600)
        return None

    def __exit__(self, *_args: object) -> None:
        if self.handle is not None:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            self.handle.close()


def _alert_id(
    row: pd.Series,
    wrapper: Mapping[str, Any],
    payload: Mapping[str, Any],
    *,
    source_index: int,
) -> str:
    for value in (
        row.get("alert_id"),
        wrapper.get("alert_id"),
        payload.get("alert_id"),
    ):
        if value is not None and str(value).strip() and str(value).lower() != "nan":
            return str(value).strip()
    return f"row-{source_index}"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, (datetime, Path)):
        return str(value)
    raise TypeError(f"value is not JSON serializable: {type(value).__name__}")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)[:100] or "unknown"


def _safe_error(exc: Exception) -> str:
    return (str(exc).strip() or type(exc).__name__)[:1000]


def _database_kind(database_url: str | None, *, persist: bool) -> str:
    if not persist:
        return "none"
    if not database_url:
        return "unknown"
    if database_url.startswith("sqlite"):
        return "sqlite"
    if database_url.startswith("postgresql"):
        return "postgresql"
    return "other"


def _default_output_dir(source_sha256: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_OUTPUT_ROOT / f"{timestamp}-{source_sha256[:12]}"


def _plan_payload(
    *,
    source: Path,
    source_sha256: str,
    source_row_count: int,
    items: Sequence[BatchItem],
    source_errors: Sequence[Mapping[str, Any]],
    settings: SocLLMSettings,
    persist: bool,
    database_kind: str,
    workers: int,
    output_dir: Path,
    default_tenant_id: str | None = None,
    investigation_enrichment_enabled: bool = False,
    enrichment_composition_sha256: str | None = None,
    enrichment_action_config_sha256s: Sequence[str] = (),
    enrichment_extensions_config_sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "soc.pingan_internal_runtime_batch_plan.v1",
        "source": {
            "path": str(source.resolve()),
            "sha256": source_sha256,
            "row_count": source_row_count,
            "selected_count": len(items),
            "input_error_count": len(source_errors),
        },
        "execution": {
            "analyzer_mode": settings.mode.value,
            "model_name": settings.model_name,
            "estimated_model_call_count": len(items)
            if settings.mode is SocAnalyzerMode.LLM
            else 0,
            "sensitive_evidence_mode": settings.sensitive_evidence_mode.value,
            "persist": persist,
            "database_kind": database_kind,
            "workers": workers,
            "default_tenant_id": default_tenant_id,
            "output_dir": str(output_dir.resolve()),
            "investigation_enrichment_enabled": investigation_enrichment_enabled,
            "enrichment_composition_sha256": enrichment_composition_sha256,
            "enrichment_action_config_sha256s": list(enrichment_action_config_sha256s),
            "enrichment_extensions_config_sha256": enrichment_extensions_config_sha256,
            "fixed_runtime_independently_usable": True,
        },
        "recommended_ramp": [5, 50, "all"],
        "secrets_included": False,
    }


def _validate_live_mcp_tool_inventory(
    provider: DeerFlowCachedMcpToolProvider,
    action_config_paths: Sequence[Path],
) -> tuple[str, ...]:
    """Fail before any LLM call when a configured MCP tool is unavailable."""

    expected = {
        (config.mcp.server, config.mcp.tool)
        for path in action_config_paths
        for config in load_mcp_action_adapter_configs(path)
        if config.enabled
    }
    available = {
        (descriptor.server, descriptor.name) for descriptor in provider.list_tools()
    }
    missing = sorted(expected - available, key=lambda item: (item[0] or "", item[1]))
    if missing:
        rendered = ", ".join(
            f"{server or '<unknown-server>'}/{tool}" for server, tool in missing
        )
        raise ValueError(
            f"live investigation MCP preflight is missing configured tools: {rendered}"
        )
    return tuple(sorted(tool for _server, tool in expected))


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--analyzer-mode", choices=["stub", "llm"])
    parser.add_argument("--model-name")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument(
        "--default-tenant-id",
        help="Trusted ingress tenant used only when a source alert has no tenant_id",
    )
    parser.add_argument("--persist", action="store_true")
    parser.add_argument("--database-url")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-existing-failures", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--checkpoint-every", type=int, default=25)
    parser.add_argument(
        "--enrichment-composition",
        type=Path,
        help="Explicit PI-01D3 enrichment composition; omitted keeps Runtime-only mode",
    )
    parser.add_argument(
        "--enrichment-action-config",
        action="append",
        default=[],
        type=Path,
        help="Explicit MCP action-adapter config; repeat for multiple providers",
    )
    parser.add_argument(
        "--enrichment-extensions-config",
        type=Path,
        help="Explicit DeerFlow MCP extensions config used by this investigation batch",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument(
        "--confirm-live",
        action="store_true",
        help="Required before any selected row calls a real LLM",
    )
    parser.add_argument(
        "--confirm-investigation",
        action="store_true",
        help="Required before explicitly enabled read-only investigation providers are called",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    engine = None
    previous_extensions_config_path = os.environ.get("DEER_FLOW_EXTENSIONS_CONFIG_PATH")
    extensions_config_overridden = False
    try:
        source = args.source.expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"source pickle does not exist: {source}")
        if args.workers < 1:
            raise ValueError("workers must be >= 1")
        if args.checkpoint_every < 1:
            raise ValueError("checkpoint_every must be >= 1")
        default_tenant_id = (
            args.default_tenant_id.strip() if args.default_tenant_id else None
        )
        if args.default_tenant_id is not None and not default_tenant_id:
            raise ValueError("--default-tenant-id must not be blank")
        source_sha256 = _sha256_file(source)
        frame = load_dataframe_pickle(source, required_columns={"alert_full_data"})
        items, source_errors = prepare_batch_items(
            frame,
            start_index=args.start_index,
            limit=args.limit,
            default_tenant_id=default_tenant_id,
        )
        settings = SocLLMSettings.from_env().with_overrides(
            mode=args.analyzer_mode,
            model_name=args.model_name,
        )
        if settings.mode is SocAnalyzerMode.LLM:
            settings = settings.with_overrides(
                model_name=resolve_soc_model_name(settings.model_name)
            )
        if (
            settings.mode is SocAnalyzerMode.LLM
            and items
            and not args.plan_only
            and not args.confirm_live
        ):
            raise ValueError("live LLM batch requires --confirm-live")
        if (
            settings.mode is SocAnalyzerMode.LLM
            and args.workers > settings.max_concurrency
        ):
            raise ValueError(
                "workers cannot exceed SOC_LLM_MAX_CONCURRENCY for a live batch"
            )

        composition_path = (
            args.enrichment_composition.expanduser().resolve()
            if args.enrichment_composition is not None
            else None
        )
        action_config_paths = [
            path.expanduser().resolve() for path in args.enrichment_action_config
        ]
        extensions_config_path = (
            args.enrichment_extensions_config.expanduser().resolve()
            if args.enrichment_extensions_config is not None
            else None
        )
        investigation_enabled = (
            composition_path is not None
            or bool(action_config_paths)
            or extensions_config_path is not None
        )
        if investigation_enabled and (
            composition_path is None
            or not action_config_paths
            or extensions_config_path is None
        ):
            raise ValueError(
                "--enrichment-composition, at least one --enrichment-action-config, and "
                "--enrichment-extensions-config must be provided together"
            )
        if investigation_enabled and not args.persist and not args.plan_only:
            raise ValueError("investigation enrichment requires --persist")
        if (
            investigation_enabled
            and items
            and not args.plan_only
            and not args.confirm_investigation
        ):
            raise ValueError(
                "investigation enrichment requires --confirm-investigation"
            )
        for config_path in [
            *([composition_path] if composition_path is not None else []),
            *action_config_paths,
            *([extensions_config_path] if extensions_config_path is not None else []),
        ]:
            if not config_path.is_file():
                raise ValueError(f"enrichment config does not exist: {config_path}")

        enrichment_composition_sha256 = (
            _sha256_file(composition_path) if composition_path is not None else None
        )
        enrichment_action_config_sha256s = tuple(
            _sha256_file(path) for path in action_config_paths
        )
        enrichment_extensions_config_sha256 = (
            _sha256_file(extensions_config_path)
            if extensions_config_path is not None
            else None
        )
        composition = None
        registry = None
        if investigation_enabled:
            os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = str(extensions_config_path)
            extensions_config_overridden = True
            composition = load_soc_enrichment_composition_config(composition_path)
            if not composition.enabled:
                raise ValueError(
                    "investigation enrichment requires an enabled composition"
                )
            mcp_provider = DeerFlowCachedMcpToolProvider(use_one_shot_invocation=True)
            if not args.plan_only:
                _validate_live_mcp_tool_inventory(
                    mcp_provider,
                    action_config_paths,
                )
            registry = build_mcp_action_adapter_registry_from_files(
                action_config_paths,
                mcp_provider,
            )
            validate_soc_enrichment_registry(composition, registry)

        database_url = resolve_database_url(args.database_url) if args.persist else None
        database_kind = _database_kind(database_url, persist=args.persist)
        if args.persist and database_kind == "sqlite" and args.workers != 1:
            raise ValueError("persisted SQLite batch requires --workers 1")
        if args.fail_fast and args.workers != 1:
            raise ValueError("--fail-fast requires --workers 1")
        output_dir = (
            args.output_dir.expanduser().resolve()
            if args.output_dir
            else _default_output_dir(source_sha256)
        )
        plan = _plan_payload(
            source=source,
            source_sha256=source_sha256,
            source_row_count=len(frame),
            items=items,
            source_errors=source_errors,
            settings=settings,
            persist=args.persist,
            database_kind=database_kind,
            workers=args.workers,
            output_dir=output_dir,
            default_tenant_id=default_tenant_id,
            investigation_enrichment_enabled=investigation_enabled,
            enrichment_composition_sha256=enrichment_composition_sha256,
            enrichment_action_config_sha256s=enrichment_action_config_sha256s,
            enrichment_extensions_config_sha256=enrichment_extensions_config_sha256,
        )
        if args.plan_only:
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0

        repository = None
        if database_url:
            engine = create_engine(
                to_sync_database_url(database_url),
                pool_pre_ping=True,
            )
            repository = SqlAlchemyAlertRepository(
                sessionmaker(bind=engine, expire_on_commit=False)
            )
        service = build_soc_analysis_service(repository, settings=settings)
        investigation_service = None
        investigation_reporting_service = None
        if investigation_enabled:
            if repository is None or composition is None or registry is None:
                raise ValueError(
                    "investigation enrichment requires a persisted repository and validated config"
                )
            investigation_service = build_soc_investigation_workflow_service(
                composition=composition,
                action_adapter_registry=registry,
                run_repository=repository,
                execution_repository=repository,
                evidence_repository=repository,
            )
            investigation_reporting_service = build_soc_investigation_reporting_service(
                run_repository=repository,
                execution_repository=repository,
                evidence_repository=repository,
            )
        manifest = execute_batch(
            items,
            analysis_service=service,
            investigation_service=investigation_service,
            investigation_reporting_service=investigation_reporting_service,
            config=BatchExecutionConfig(
                source_path=source,
                source_sha256=source_sha256,
                output_dir=output_dir,
                analyzer_mode=settings.mode.value,
                model_name=settings.model_name,
                sensitive_evidence_mode=settings.sensitive_evidence_mode.value,
                persist=args.persist,
                database_kind=database_kind,
                workers=args.workers,
                resume=args.resume,
                retry_failures=not args.skip_existing_failures,
                fail_fast=args.fail_fast,
                checkpoint_every=args.checkpoint_every,
                default_tenant_id=default_tenant_id,
                investigation_enrichment_enabled=investigation_enabled,
                enrichment_composition_sha256=enrichment_composition_sha256,
                enrichment_action_config_sha256s=enrichment_action_config_sha256s,
                enrichment_extensions_config_sha256=enrichment_extensions_config_sha256,
            ),
            source_row_count=len(frame),
            source_errors=source_errors,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "completed" else 1
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {_safe_error(exc)}", file=sys.stderr)
        return 2
    finally:
        if engine is not None:
            engine.dispose()
        if extensions_config_overridden:
            if previous_extensions_config_path is None:
                os.environ.pop("DEER_FLOW_EXTENSIONS_CONFIG_PATH", None)
            else:
                os.environ["DEER_FLOW_EXTENSIONS_CONFIG_PATH"] = (
                    previous_extensions_config_path
                )


if __name__ == "__main__":
    raise SystemExit(main())
