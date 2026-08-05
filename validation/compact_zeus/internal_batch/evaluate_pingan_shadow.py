#!/usr/bin/env python3
"""Evaluate paired PingAn Runtime-only and investigation shadow batches."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from math import ceil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.actions.mcp import (  # noqa: E402
    SocMcpToolDescriptor,
    build_mcp_action_adapter_registry,
    load_mcp_action_adapter_configs,
)
from soc_agent.application import (  # noqa: E402
    load_soc_enrichment_composition_config,
    validate_soc_enrichment_registry,
)
from soc_agent.contracts import SocEnrichmentResultMode  # noqa: E402

REPORT_SCHEMA_VERSION = "soc.pingan_shadow_acceptance.v2"
REPORT_PROJECTION_VERSION = "pingan-shadow-acceptance-v2"
BATCH_MANIFEST_SCHEMA_VERSION = "soc.pingan_internal_runtime_batch_manifest.v1"
BATCH_ITEM_SCHEMA_VERSION = "soc.pingan_internal_runtime_batch_item.v1"
_RAMP_STAGES = frozenset({"5", "50", "all"})
_DETERMINISTIC_RUNTIME_FIELDS = (
    "normalization_report",
    "entities",
    "extraction_report",
    "fact_reconstruction",
    "llm_analysis_request",
)


@dataclass(frozen=True)
class BatchArtifacts:
    """Validated local artifact handles for one internal batch."""

    manifest: dict[str, Any]
    manifest_sha256: str
    items: dict[int, dict[str, Any]]
    cohort_sha256: str


@dataclass(frozen=True)
class EnrichmentConfigAudit:
    """Secret-free static audit of the exact enrichment composition."""

    composition_sha256: str
    action_config_sha256s: tuple[str, ...]
    extensions_config_sha256: str
    required_result_mode: str | None
    selected_routes: tuple[str, ...]
    selected_adapter_ids: tuple[str, ...]
    selected_server_names: tuple[str, ...]
    provider_modes: dict[str, str]
    tenant_id: str | None
    internal_network_count: int
    asset_lookup_disabled: bool
    asset_locate_selected: bool
    registry_exact_match: bool
    extensions_exact_match: bool


class ShadowAcceptanceMode(StrEnum):
    """Evidence class for a paired shadow gate."""

    EXTERNAL_SIMULATION = "external_simulation"
    INTERNAL_REAL = "internal_real"


class _StaticOnlyMcpProvider:
    """Adapter-construction dependency that refuses all MCP discovery and IO."""

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        raise RuntimeError("PI-01E static config audit must not discover MCP tools")

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
        server_name: str | None = None,
    ) -> Mapping[str, Any]:
        del tool_name, payload, timeout_seconds, server_name
        raise RuntimeError("PI-01E static config audit must not invoke MCP tools")


def evaluate_shadow_batches(
    *,
    runtime_batch_dir: Path,
    investigation_batch_dir: Path,
    composition_path: Path,
    action_config_paths: Sequence[Path],
    extensions_config_path: Path,
    ramp_stage: str,
    acceptance_mode: ShadowAcceptanceMode,
) -> dict[str, Any]:
    """Build one secret-free, review-gated PI-01E report without external calls."""

    if ramp_stage not in _RAMP_STAGES:
        raise ValueError(
            f"unsupported ramp stage {ramp_stage!r}; expected one of {sorted(_RAMP_STAGES)}"
        )
    if not action_config_paths:
        raise ValueError(
            "PI-01E evaluation requires at least one action adapter config"
        )

    runtime = _load_batch_artifacts(runtime_batch_dir)
    investigation = _load_batch_artifacts(investigation_batch_dir)
    config_audit = _audit_enrichment_config(
        composition_path=composition_path,
        action_config_paths=action_config_paths,
        extensions_config_path=extensions_config_path,
    )

    runtime_manifest = runtime.manifest
    investigation_manifest = investigation.manifest
    runtime_source = _mapping(runtime_manifest.get("source"))
    investigation_source = _mapping(investigation_manifest.get("source"))
    runtime_execution = _mapping(runtime_manifest.get("execution"))
    investigation_execution = _mapping(investigation_manifest.get("execution"))
    runtime_metrics = _runtime_metrics(runtime)
    investigation_runtime_metrics = _runtime_metrics(investigation)
    investigation_metrics = _investigation_metrics(investigation)
    compatibility = _compatibility_metrics(runtime, investigation)

    selected_count = int(runtime_source.get("selected_count") or 0)
    row_count = int(runtime_source.get("row_count") or 0)
    same_profile = all(
        runtime_execution.get(key) == investigation_execution.get(key)
        for key in (
            "analyzer_mode",
            "model_name",
            "sensitive_evidence_mode",
            "default_tenant_id",
        )
    )
    runtime_complete = _batch_complete(runtime)
    investigation_complete = _batch_complete(investigation)
    report_count = int(investigation_metrics["report_count"])
    selected_routes = set(config_audit.selected_routes)
    configured_fingerprints_match = (
        investigation_execution.get("enrichment_composition_sha256")
        == config_audit.composition_sha256
        and tuple(investigation_execution.get("enrichment_action_config_sha256s") or ())
        == config_audit.action_config_sha256s
        and investigation_execution.get("enrichment_extensions_config_sha256")
        == config_audit.extensions_config_sha256
    )
    required_modes = set(investigation_metrics["required_result_mode_counts"])
    route_metrics = _mapping(investigation_metrics["routes"])
    expected_result_mode = (
        SocEnrichmentResultMode.MOCK.value
        if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
        else SocEnrichmentResultMode.REAL.value
    )
    expected_result_field = (
        "mock_result_count"
        if expected_result_mode == SocEnrichmentResultMode.MOCK.value
        else "real_result_count"
    )
    expected_provider_mode = (
        "fake"
        if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
        else "internal"
    )
    routes_with_expected_results = {
        route
        for route, metrics in route_metrics.items()
        if int(_mapping(metrics).get("planned_action_count") or 0) > 0
        and int(_mapping(metrics).get(expected_result_field) or 0) > 0
    }
    provider_modes_match = bool(config_audit.provider_modes) and all(
        mode == expected_provider_mode for mode in config_audit.provider_modes.values()
    )
    runtime_tenant_ids = _runtime_tenant_ids(runtime)
    investigation_tenant_ids = _runtime_tenant_ids(investigation)
    tenant_scope_matches = (
        config_audit.tenant_id is not None
        and runtime_tenant_ids == {config_audit.tenant_id}
        and investigation_tenant_ids == {config_audit.tenant_id}
    )

    checks = [
        _gate(
            "same_source",
            runtime_source.get("sha256") == investigation_source.get("sha256")
            and runtime_source.get("row_count")
            == investigation_source.get("row_count"),
            "Both batches must use the same source SHA-256 and source row count.",
        ),
        _gate(
            "same_exact_cohort",
            runtime.cohort_sha256 == investigation.cohort_sha256
            and set(runtime.items) == set(investigation.items),
            "Both batches must contain the same source indexes and payload/row fingerprints.",
        ),
        _gate(
            "ramp_stage_size",
            _ramp_size_matches(
                ramp_stage, selected_count=selected_count, row_count=row_count
            ),
            f"Stage {ramp_stage!r} must contain its exact approved cohort size.",
        ),
        _gate(
            "same_runtime_profile",
            same_profile,
            "Analyzer mode, model, sensitive-evidence mode and ingress tenant default must match across paired batches.",
        ),
        _gate(
            "tenant_scope_matches",
            tenant_scope_matches,
            "Every paired AnalysisRun tenant must match the explicit enrichment-policy tenant.",
        ),
        _gate(
            "live_llm_profile",
            runtime_execution.get("analyzer_mode") == "llm"
            and bool(runtime_execution.get("model_name")),
            "PI-01E evidence requires a named live LLM profile, not the deterministic stub.",
        ),
        _gate(
            "runtime_batch_isolation",
            runtime_execution.get("investigation_enrichment_enabled") is False,
            "The compatibility batch must remain Runtime-only.",
        ),
        _gate(
            "investigation_batch_enabled",
            investigation_execution.get("investigation_enrichment_enabled") is True
            and investigation_execution.get("persist") is True,
            "The investigation batch must explicitly enable enrichment and persistence.",
        ),
        _gate(
            "expected_result_composition",
            config_audit.required_result_mode == expected_result_mode,
            f"The {acceptance_mode.value} composition must require {expected_result_mode} Provider results.",
        ),
        _gate(
            "asset_lookup_disabled",
            config_audit.asset_lookup_disabled
            and "asset.lookup" not in selected_routes,
            "PingAn shadow must use asset.locate or no asset route; development asset.lookup is forbidden.",
        ),
        _gate(
            "exact_adapter_registry",
            config_audit.registry_exact_match,
            "Enabled action configs must exactly match the composition bindings.",
        ),
        _gate(
            "exact_extensions_config",
            config_audit.extensions_exact_match,
            "The explicit extensions config must enable exactly the MCP servers selected by the action bindings.",
        ),
        _gate(
            "expected_provider_modes",
            provider_modes_match,
            f"Every selected MCP server must explicitly declare provider mode {expected_provider_mode!r}.",
        ),
        _gate(
            "config_fingerprints_match_batch",
            configured_fingerprints_match,
            "The reviewed config fingerprints must match those sealed into the investigation manifest.",
        ),
        _gate(
            "runtime_batch_complete",
            runtime_complete,
            "The Runtime-only batch must finish with no pending, failed or invalid source rows.",
        ),
        _gate(
            "investigation_batch_complete",
            investigation_complete,
            "The investigation batch must finish with no pending, failed or invalid source rows.",
        ),
        _gate(
            "deterministic_runtime_compatibility",
            compatibility["deterministic_projection_mismatch_count"] == 0,
            "Normalization, extraction, fact and bounded analysis-input projections must remain identical.",
        ),
        _gate(
            "shadow_report_coverage",
            report_count == selected_count,
            "Every selected investigation item must have a recomputable D4 shadow report.",
        ),
        _gate(
            "expected_result_reports",
            required_modes == {expected_result_mode},
            f"Every shadow report must retain required_result_mode={expected_result_mode}.",
        ),
        _gate(
            "no_final_provider_failures",
            investigation_metrics["failed_count"] == 0,
            "Provider/contract failures must be resolved before expanding the cohort.",
        ),
        _gate(
            "investigation_evidence_complete",
            investigation_metrics["missing_evidence_count"] == 0,
            "Every terminal successful/not-found evidence reference must resolve from persistence.",
        ),
        _gate(
            "measurement_boundaries_explicit",
            investigation_metrics["cost_measurement_status_counts"].get("not_measured")
            == report_count
            and investigation_metrics["measurement_gap_counts"].get(
                "provider_cost_not_measured"
            )
            == report_count
            and investigation_metrics["measurement_gap_counts"].get(
                "provider_network_latency_not_isolated_from_action_latency"
            )
            == investigation_metrics["provider_invoking_report_count"],
            "Every D4 report must explicitly declare unmeasured Provider cost and non-isolated network latency.",
        ),
        _gate(
            "zero_unauthorized_side_effects",
            all(
                value == 0
                for value in investigation_metrics[
                    "unauthorized_side_effect_counts"
                ].values()
            ),
            "Verdict mutation, auto-close, confirmed-memory writes and high-risk actions must all remain zero.",
        ),
        _gate(
            "shadow_automation_disabled",
            investigation_runtime_metrics["automation_allowed_count"] == 0,
            "Base Runtime automation must remain disabled for the shadow cohort.",
        ),
    ]
    if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION:
        checks.extend(
            [
                _gate(
                    "no_real_results",
                    investigation_metrics["real_result_count"] == 0,
                    "External rehearsal must not contain any result presented as a real Provider result.",
                ),
                _gate(
                    "mock_provider_observed",
                    investigation_metrics["provider_invocation_count"] > 0
                    and investigation_metrics["mock_result_count"] > 0,
                    "At least one fake Provider invocation and mocked terminal result must be observed.",
                ),
                _gate(
                    "configured_route_mock_coverage",
                    routes_with_expected_results == selected_routes,
                    "Every configured route must have at least one planned action and mocked terminal result.",
                ),
            ]
        )
    else:
        checks.extend(
            [
                _gate(
                    "no_mock_results",
                    investigation_metrics["mock_result_count"] == 0,
                    "Internal real acceptance must not contain mocked=true results.",
                ),
                _gate(
                    "real_provider_observed",
                    investigation_metrics["provider_invocation_count"] > 0
                    and investigation_metrics["real_result_count"] > 0,
                    "At least one real Provider invocation and real terminal result must be observed.",
                ),
                _gate(
                    "configured_route_real_coverage",
                    routes_with_expected_results == selected_routes,
                    "Every configured route must have at least one planned action and real terminal result.",
                ),
            ]
        )
    blocking_failures = [
        item["check_id"] for item in checks if item["status"] == "failed"
    ]
    gate_status = "passed" if not blocking_failures else "failed"
    if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION:
        next_stage = {
            "5": "external_simulation_50",
            "50": "internal_real_5",
            "all": "internal_real_5",
        }[ramp_stage]
    else:
        next_stage = {
            "5": "internal_real_50",
            "50": "internal_real_all",
            "all": "pilot_readiness_review",
        }[ramp_stage]
    attention = _attention_items(
        runtime_metrics=runtime_metrics,
        compatibility=compatibility,
        investigation_metrics=investigation_metrics,
    )
    source_sha256 = str(runtime_source.get("sha256") or "")
    report_identity = _canonical_sha256(
        {
            "projection_version": REPORT_PROJECTION_VERSION,
            "acceptance_mode": acceptance_mode.value,
            "ramp_stage": ramp_stage,
            "runtime_manifest_sha256": runtime.manifest_sha256,
            "investigation_manifest_sha256": investigation.manifest_sha256,
            "composition_sha256": config_audit.composition_sha256,
            "action_config_sha256s": list(config_audit.action_config_sha256s),
        }
    )
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "projection_version": REPORT_PROJECTION_VERSION,
        "report_id": f"PI01E-{report_identity[:16].upper()}",
        "generated_at": datetime.now(UTC).isoformat(),
        "acceptance_mode": acceptance_mode.value,
        "evidence_class": "simulated"
        if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
        else "real",
        "ramp_stage": ramp_stage,
        "gate_status": gate_status,
        "blocking_failure_ids": blocking_failures,
        "inputs": {
            "source_sha256": source_sha256,
            "source_row_count": row_count,
            "selected_count": selected_count,
            "cohort_sha256": runtime.cohort_sha256,
            "runtime_manifest_sha256": runtime.manifest_sha256,
            "investigation_manifest_sha256": investigation.manifest_sha256,
            "composition_sha256": config_audit.composition_sha256,
            "action_config_sha256s": list(config_audit.action_config_sha256s),
            "extensions_config_sha256": config_audit.extensions_config_sha256,
            "paths_included": False,
            "raw_payloads_included": False,
            "provider_responses_included": False,
            "secrets_included": False,
        },
        "configuration": {
            "required_result_mode": config_audit.required_result_mode,
            "selected_routes": list(config_audit.selected_routes),
            "selected_adapter_ids": list(config_audit.selected_adapter_ids),
            "selected_server_names": list(config_audit.selected_server_names),
            "provider_modes": config_audit.provider_modes,
            "tenant_id": config_audit.tenant_id,
            "internal_network_count": config_audit.internal_network_count,
            "asset_lookup_disabled": config_audit.asset_lookup_disabled,
            "asset_locate_selected": config_audit.asset_locate_selected,
            "registry_exact_match": config_audit.registry_exact_match,
            "extensions_exact_match": config_audit.extensions_exact_match,
        },
        "checks": checks,
        "metrics": {
            "runtime_compatibility_batch": runtime_metrics,
            "investigation_base_runtime": investigation_runtime_metrics,
            "paired_compatibility": compatibility,
            "investigation_shadow": investigation_metrics,
        },
        "review_attention": attention,
        "claims": {
            "technical_shadow_gate_passed": gate_status == "passed",
            "external_simulation_passed": (
                gate_status == "passed"
                and acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
            ),
            "internal_real_gate_passed": gate_status == "passed"
            and acceptance_mode is ShadowAcceptanceMode.INTERNAL_REAL,
            "real_provider_evidence": gate_status == "passed"
            and acceptance_mode is ShadowAcceptanceMode.INTERNAL_REAL,
            "closes_real_provider_gate": False,
            "model_accuracy_evaluated": False,
            "pilot_ready": False,
            "automatic_expansion_allowed": False,
            "next_stage": next_stage,
            "next_stage_requires_human_review": True,
            "statement": (
                "A passed external simulation proves delivery shape, fake Provider routing, persistence and safety only; it cannot close any real-provider gate."
                if acceptance_mode is ShadowAcceptanceMode.EXTERNAL_SIMULATION
                else "A passed internal-real report proves paired technical shadow behavior and real Provider evidence for the reviewed bindings only; provider-specific success/not-found/error acceptance, PI-03 human labels and remaining gates are separate."
            ),
        },
    }


def _load_batch_artifacts(batch_dir: Path) -> BatchArtifacts:
    root = batch_dir.expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_object(manifest_path)
    if manifest.get("schema_version") != BATCH_MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported batch manifest schema in {manifest_path}")
    source_sha256 = str(_mapping(manifest.get("source")).get("sha256") or "")
    if not _is_sha256(source_sha256):
        raise ValueError(f"batch manifest has invalid source SHA-256: {manifest_path}")

    items: dict[int, dict[str, Any]] = {}
    items_dir = root / "items"
    if not items_dir.is_dir():
        raise ValueError(f"batch items directory does not exist: {items_dir}")
    for path in sorted(items_dir.glob("*.json")):
        item = _read_object(path)
        if item.get("schema_version") != BATCH_ITEM_SCHEMA_VERSION:
            raise ValueError(f"unsupported batch item schema: {path}")
        source = _mapping(item.get("source"))
        source_index = source.get("source_index")
        if not isinstance(source_index, int) or source_index < 0:
            raise ValueError(f"batch item has invalid source_index: {path}")
        if source_index in items:
            raise ValueError(f"duplicate batch source_index {source_index}")
        if source.get("source_file_sha256") != source_sha256:
            raise ValueError(f"batch item source fingerprint mismatch: {path}")
        if not _is_sha256(source.get("row_sha256")) or not _is_sha256(
            source.get("payload_sha256")
        ):
            raise ValueError(f"batch item has invalid row/payload fingerprint: {path}")
        items[source_index] = item

    cohort_sha256 = _canonical_sha256(
        [
            {
                "source_index": source_index,
                "row_sha256": _mapping(item.get("source")).get("row_sha256"),
                "payload_sha256": _mapping(item.get("source")).get("payload_sha256"),
            }
            for source_index, item in sorted(items.items())
        ]
    )
    return BatchArtifacts(
        manifest=manifest,
        manifest_sha256=_sha256_file(manifest_path),
        items=items,
        cohort_sha256=cohort_sha256,
    )


def _audit_enrichment_config(
    *,
    composition_path: Path,
    action_config_paths: Sequence[Path],
    extensions_config_path: Path,
) -> EnrichmentConfigAudit:
    composition = load_soc_enrichment_composition_config(composition_path)
    configs = [
        config
        for path in action_config_paths
        for config in load_mcp_action_adapter_configs(path)
    ]
    registry = build_mcp_action_adapter_registry(configs, _StaticOnlyMcpProvider())
    selected = validate_soc_enrichment_registry(composition, registry)
    selected_identities = {
        (descriptor.route, descriptor.action, descriptor.adapter_id)
        for descriptor in selected
    }
    enabled_identities = {
        (config.route, config.action or config.route, config.adapter_id)
        for config in configs
        if config.enabled
    }
    selected_routes = tuple(sorted(descriptor.route for descriptor in selected))
    selected_configs = [
        config
        for config in configs
        if (config.route, config.action or config.route, config.adapter_id)
        in selected_identities
    ]
    selected_server_names = tuple(
        sorted({str(config.mcp.server or "") for config in selected_configs})
    )
    extensions = _read_object(extensions_config_path)
    servers = _mapping(extensions.get("mcpServers"))
    enabled_server_names = {
        str(name)
        for name, server in servers.items()
        if _mapping(server).get("enabled") is True
    }
    provider_modes: dict[str, str] = {}
    for server_name in selected_server_names:
        env = _mapping(_mapping(servers.get(server_name)).get("env"))
        declared_modes = {
            str(value)
            for key, value in env.items()
            if str(key).endswith("_PROVIDER_MODE") and isinstance(value, str) and value
        }
        provider_modes[server_name] = (
            next(iter(declared_modes)) if len(declared_modes) == 1 else "unknown"
        )
    return EnrichmentConfigAudit(
        composition_sha256=_sha256_file(composition_path),
        action_config_sha256s=tuple(_sha256_file(path) for path in action_config_paths),
        extensions_config_sha256=_sha256_file(extensions_config_path),
        required_result_mode=(
            composition.required_result_mode.value
            if composition.required_result_mode is not None
            else None
        ),
        selected_routes=selected_routes,
        selected_adapter_ids=tuple(
            sorted(descriptor.adapter_id for descriptor in selected)
        ),
        selected_server_names=selected_server_names,
        provider_modes=dict(sorted(provider_modes.items())),
        tenant_id=composition.policy.tenant_id,
        internal_network_count=len(composition.policy.internal_networks),
        asset_lookup_disabled="asset.lookup" not in composition.policy.enabled_routes,
        asset_locate_selected="asset.locate" in composition.policy.enabled_routes,
        registry_exact_match=selected_identities == enabled_identities,
        extensions_exact_match=(
            bool(selected_server_names)
            and all(selected_server_names)
            and enabled_server_names == set(selected_server_names)
        ),
    )


def _batch_complete(batch: BatchArtifacts) -> bool:
    manifest = batch.manifest
    summary = _mapping(manifest.get("summary"))
    source = _mapping(manifest.get("source"))
    selected_count = int(source.get("selected_count") or 0)
    return (
        manifest.get("status") == "completed"
        and len(batch.items) == selected_count
        and all(item.get("outcome") == "completed" for item in batch.items.values())
        and int(summary.get("recorded_count") or 0) == selected_count
        and int(summary.get("completed_count") or 0) == selected_count
        and int(summary.get("pending_count") or 0) == 0
        and int(summary.get("failed_count") or 0) == 0
        and int(summary.get("source_error_count") or 0) == 0
    )


def _runtime_tenant_ids(batch: BatchArtifacts) -> set[str | None]:
    return {
        _mapping(_mapping(item.get("analysis_run")).get("llm_analysis_request")).get(
            "tenant_id"
        )
        for item in batch.items.values()
    }


def _runtime_metrics(batch: BatchArtifacts) -> dict[str, Any]:
    records = list(batch.items.values())
    summaries = [_mapping(record.get("summary")) for record in records]
    durations = [
        value
        for record in records
        if (
            value := _non_negative_number(
                _mapping(record.get("execution")).get("duration_ms")
            )
        )
        is not None
    ]
    observations = [
        _mapping(observation)
        for record in records
        for observation in _mapping(
            _mapping(record.get("analysis_run")).get("normalization_report")
        ).get("message_schemas")
        or []
        if isinstance(observation, Mapping)
    ]
    schema_statuses = Counter(
        str(item.get("status") or "unknown") for item in observations
    )
    schema_warnings = Counter(
        str(warning) for item in observations for warning in item.get("warnings") or []
    )
    schema_fingerprints = {
        str(item.get("schema_fingerprint"))
        for item in observations
        if _is_sha256(item.get("schema_fingerprint"))
    }
    high_value_gap_count = sum(
        len(
            _mapping(
                _mapping(
                    _mapping(record.get("analysis_run")).get("llm_analysis_request")
                ).get("evidence_coverage")
            ).get("high_value_gaps")
            or []
        )
        for record in records
    )
    review_count = sum(item.get("needs_review") is True for item in summaries)
    automation_count = sum(item.get("automation_allowed") is True for item in summaries)
    return {
        "record_count": len(records),
        "outcome_counts": dict(
            sorted(
                Counter(
                    str(record.get("outcome") or "unknown") for record in records
                ).items()
            )
        ),
        "runtime_status_counts": dict(
            sorted(
                Counter(
                    str(item.get("runtime_status") or "unknown") for item in summaries
                ).items()
            )
        ),
        "verdict_counts": dict(
            sorted(
                Counter(
                    str(item.get("verdict") or "unknown") for item in summaries
                ).items()
            )
        ),
        "needs_review_count": review_count,
        "review_rate": _ratio(review_count, len(records)),
        "automation_allowed_count": automation_count,
        "duration_ms": _latency_metrics(durations),
        "llm_usage": _usage_metrics(records),
        "schema_drift": {
            "observation_count": len(observations),
            "status_counts": dict(sorted(schema_statuses.items())),
            "unique_fingerprint_count": len(schema_fingerprints),
            "warning_counts": dict(sorted(schema_warnings.items())),
            "high_value_gap_count": high_value_gap_count,
            "accepted_baseline_comparison_status": "not_measured",
        },
    }


def _investigation_metrics(batch: BatchArtifacts) -> dict[str, Any]:
    records = list(batch.items.values())
    reports = [
        _mapping(record.get("investigation_shadow_report"))
        for record in records
        if _mapping(record.get("investigation_shadow_report"))
    ]
    route_counts: Counter[str] = Counter()
    route_metrics: dict[str, Counter[str]] = {}
    required_modes: Counter[str] = Counter()
    cost_statuses: Counter[str] = Counter()
    measurement_gaps: Counter[str] = Counter()
    attempts: list[float] = []
    real_result_count = 0
    mock_result_count = 0
    for report in reports:
        required_modes[str(report.get("required_result_mode") or "unknown")] += 1
        cost_statuses[str(report.get("cost_measurement_status") or "missing")] += 1
        measurement_gaps.update(
            str(item) for item in report.get("measurement_gaps") or []
        )
        for route in report.get("routes") or []:
            route_payload = _mapping(route)
            route_name = str(route_payload.get("route") or "unknown")
            route_counts[route_name] += int(
                route_payload.get("planned_action_count") or 0
            )
            metrics = route_metrics.setdefault(route_name, Counter())
            for key in (
                "planned_action_count",
                "attempt_count",
                "provider_invocation_count",
                "success_count",
                "not_found_count",
                "final_failure_count",
                "provider_failure_attempt_count",
                "contract_failure_attempt_count",
                "denied_attempt_count",
                "interrupted_attempt_count",
                "persisted_evidence_count",
                "missing_evidence_count",
                "real_result_count",
                "mock_result_count",
            ):
                metrics[key] += int(route_payload.get(key) or 0)
            real_result_count += int(route_payload.get("real_result_count") or 0)
            mock_result_count += int(route_payload.get("mock_result_count") or 0)
    for record in records:
        workflow = _mapping(record.get("investigation_workflow"))
        for attempt in workflow.get("attempts") or []:
            duration = _attempt_duration_ms(_mapping(attempt))
            if duration is not None:
                attempts.append(duration)

    planned = sum(int(report.get("planned_action_count") or 0) for report in reports)
    success = sum(int(report.get("success_count") or 0) for report in reports)
    not_found = sum(int(report.get("not_found_count") or 0) for report in reports)
    failed = sum(int(report.get("failed_count") or 0) for report in reports)
    evidence = sum(
        int(report.get("persisted_evidence_count") or 0) for report in reports
    )
    missing_evidence = sum(
        int(report.get("missing_evidence_count") or 0) for report in reports
    )
    attempt_count = sum(int(report.get("attempt_count") or 0) for report in reports)
    provider_failure_attempt_count = sum(
        metrics["provider_failure_attempt_count"] for metrics in route_metrics.values()
    )
    contract_failure_attempt_count = sum(
        metrics["contract_failure_attempt_count"] for metrics in route_metrics.values()
    )
    side_effect_counts = {
        "base_run_mutation": sum(
            report.get("base_run_mutated") is not False for report in reports
        ),
        "auto_close_allowed": sum(
            report.get("auto_close_allowed") is not False for report in reports
        ),
        "confirmed_memory_write_allowed": sum(
            report.get("confirmed_memory_write_allowed") is not False
            for report in reports
        ),
        "high_risk_actions_allowed": sum(
            report.get("high_risk_actions_allowed") is not False for report in reports
        ),
    }
    return {
        "report_count": len(reports),
        "execution_status_counts": dict(
            sorted(
                Counter(
                    str(report.get("execution_status") or "unknown")
                    for report in reports
                ).items()
            )
        ),
        "required_result_mode_counts": dict(sorted(required_modes.items())),
        "route_planned_action_counts": dict(sorted(route_counts.items())),
        "routes": {
            route: dict(sorted(metrics.items()))
            for route, metrics in sorted(route_metrics.items())
        },
        "planned_action_count": planned,
        "attempt_count": attempt_count,
        "provider_invoking_report_count": sum(
            int(report.get("provider_invocation_count") or 0) > 0 for report in reports
        ),
        "provider_invocation_count": sum(
            int(report.get("provider_invocation_count") or 0) for report in reports
        ),
        "success_count": success,
        "not_found_count": not_found,
        "failed_count": failed,
        "retry_count": sum(int(report.get("retry_count") or 0) for report in reports),
        "real_result_count": real_result_count,
        "mock_result_count": mock_result_count,
        "provider_hit_rate": _ratio(success, planned),
        "provider_not_found_rate": _ratio(not_found, planned),
        "final_failure_rate": _ratio(failed, planned),
        "provider_failure_attempt_count": provider_failure_attempt_count,
        "contract_failure_attempt_count": contract_failure_attempt_count,
        "provider_error_attempt_rate": _ratio(
            provider_failure_attempt_count + contract_failure_attempt_count,
            attempt_count,
        ),
        "persisted_evidence_count": evidence,
        "missing_evidence_count": missing_evidence,
        "effective_evidence_rate": _ratio(evidence, planned),
        "action_attempt_latency_ms": _latency_metrics(attempts),
        "provider_network_latency_measurement_status": "not_measured",
        "cost_measurement_status_counts": dict(sorted(cost_statuses.items())),
        "measurement_gap_counts": dict(sorted(measurement_gaps.items())),
        "unauthorized_side_effect_counts": side_effect_counts,
    }


def _compatibility_metrics(
    runtime: BatchArtifacts,
    investigation: BatchArtifacts,
) -> dict[str, Any]:
    shared_indexes = sorted(set(runtime.items).intersection(investigation.items))
    deterministic_mismatches = 0
    verdict_mismatches = 0
    review_mismatches = 0
    for source_index in shared_indexes:
        runtime_record = runtime.items[source_index]
        investigation_record = investigation.items[source_index]
        if _deterministic_runtime_sha256(
            runtime_record
        ) != _deterministic_runtime_sha256(investigation_record):
            deterministic_mismatches += 1
        runtime_summary = _mapping(runtime_record.get("summary"))
        investigation_summary = _mapping(investigation_record.get("summary"))
        if runtime_summary.get("verdict") != investigation_summary.get("verdict"):
            verdict_mismatches += 1
        if runtime_summary.get("needs_review") != investigation_summary.get(
            "needs_review"
        ):
            review_mismatches += 1
    return {
        "shared_item_count": len(shared_indexes),
        "runtime_only_item_count": len(
            set(runtime.items).difference(investigation.items)
        ),
        "investigation_only_item_count": len(
            set(investigation.items).difference(runtime.items)
        ),
        "deterministic_projection_mismatch_count": deterministic_mismatches,
        "llm_verdict_difference_count": verdict_mismatches,
        "review_routing_difference_count": review_mismatches,
        "llm_output_equality_required": False,
    }


def _deterministic_runtime_sha256(record: Mapping[str, Any]) -> str | None:
    run = _mapping(record.get("analysis_run"))
    if not run:
        return None
    return _canonical_sha256(
        {key: run.get(key) for key in _DETERMINISTIC_RUNTIME_FIELDS}
    )


def _usage_metrics(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    analysis_records = [
        record for record in records if _mapping(record.get("analysis_run"))
    ]
    usage_records = [
        _mapping(_mapping(record.get("summary")).get("usage"))
        for record in analysis_records
    ]
    token_samples = 0
    cost_samples = 0
    input_tokens = 0.0
    output_tokens = 0.0
    total_tokens = 0.0
    provider_reported_cost = 0.0
    for usage in usage_records:
        input_value = _first_number(usage, ("input_tokens", "prompt_tokens"))
        output_value = _first_number(usage, ("output_tokens", "completion_tokens"))
        total_value = _first_number(usage, ("total_tokens",))
        if total_value is None and (
            input_value is not None or output_value is not None
        ):
            total_value = (input_value or 0.0) + (output_value or 0.0)
        if total_value is not None:
            token_samples += 1
            input_tokens += input_value or 0.0
            output_tokens += output_value or 0.0
            total_tokens += total_value
        cost_value = _first_number(usage, ("total_cost", "cost", "response_cost"))
        if cost_value is not None:
            cost_samples += 1
            provider_reported_cost += cost_value
    return {
        "token_measurement_status": _measurement_status(
            token_samples, len(analysis_records)
        ),
        "token_sample_count": token_samples,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "total_tokens": int(total_tokens),
        "monetary_cost_measurement_status": _measurement_status(
            cost_samples, len(analysis_records)
        ),
        "monetary_cost_sample_count": cost_samples,
        "provider_reported_cost": (
            round(provider_reported_cost, 8) if cost_samples else None
        ),
        "currency": None,
    }


def _attention_items(
    *,
    runtime_metrics: Mapping[str, Any],
    compatibility: Mapping[str, Any],
    investigation_metrics: Mapping[str, Any],
) -> list[dict[str, str]]:
    items = [
        {
            "code": "model_quality_not_evaluated",
            "severity": "info",
            "message": "Technical batch completion is not accuracy evidence; PI-03 requires human labels.",
        }
    ]
    schema_statuses = _mapping(
        _mapping(runtime_metrics.get("schema_drift")).get("status_counts")
    )
    if int(schema_statuses.get("degraded") or 0) or int(
        schema_statuses.get("unsupported") or 0
    ):
        items.append(
            {
                "code": "schema_attention_required",
                "severity": "warning",
                "message": "The cohort contains degraded or unsupported outer message schemas; review normalization maintenance issues before expansion.",
            }
        )
    if int(compatibility.get("llm_verdict_difference_count") or 0) or int(
        compatibility.get("review_routing_difference_count") or 0
    ):
        items.append(
            {
                "code": "paired_llm_output_variation",
                "severity": "info",
                "message": "Live LLM outputs differed across paired runs; deterministic pre-LLM compatibility still has its own blocking check.",
            }
        )
    llm_usage = _mapping(runtime_metrics.get("llm_usage"))
    if llm_usage.get("token_measurement_status") != "measured":
        items.append(
            {
                "code": "llm_token_usage_incomplete",
                "severity": "warning",
                "message": "LLM token usage is partial or not measured for the compatibility cohort.",
            }
        )
    if llm_usage.get("monetary_cost_measurement_status") != "measured":
        items.append(
            {
                "code": "llm_monetary_cost_not_measured",
                "severity": "warning",
                "message": "LLM monetary cost is partial or not measured and cannot be inferred from token counts without an approved price source.",
            }
        )
    if int(investigation_metrics.get("provider_failure_attempt_count") or 0) or int(
        investigation_metrics.get("contract_failure_attempt_count") or 0
    ):
        items.append(
            {
                "code": "provider_or_contract_retry_observed",
                "severity": "warning",
                "message": "One or more Provider/contract failure attempts occurred before the terminal result; review retry distribution before expansion.",
            }
        )
    if int(investigation_metrics.get("provider_invocation_count") or 0) and not int(
        investigation_metrics.get("success_count") or 0
    ):
        items.append(
            {
                "code": "provider_hit_path_not_observed",
                "severity": "warning",
                "message": "The cohort exercised Provider calls but observed no hit/success result; provider-specific hit mapping remains a separate acceptance requirement.",
            }
        )
    gap_counts = _mapping(investigation_metrics.get("measurement_gap_counts"))
    if int(gap_counts.get("provider_cost_not_measured") or 0):
        items.append(
            {
                "code": "provider_cost_not_measured",
                "severity": "warning",
                "message": "Provider/tool monetary cost is explicitly not measured and cannot be treated as zero.",
            }
        )
    if int(
        gap_counts.get("provider_network_latency_not_isolated_from_action_latency") or 0
    ):
        items.append(
            {
                "code": "provider_network_latency_not_isolated",
                "severity": "warning",
                "message": "Only end-to-end action-attempt latency is measured; Provider network latency remains a separate telemetry gap.",
            }
        )
    return items


def _gate(check_id: str, passed: bool, requirement: str) -> dict[str, str]:
    return {
        "check_id": check_id,
        "status": "passed" if passed else "failed",
        "requirement": requirement,
    }


def _ramp_size_matches(stage: str, *, selected_count: int, row_count: int) -> bool:
    if stage == "all":
        return row_count > 0 and selected_count == row_count
    return selected_count == int(stage)


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


def _latency_metrics(values: Sequence[float]) -> dict[str, Any]:
    return {
        "sample_count": len(values),
        "p50": _nearest_rank_percentile(values, 0.50),
        "p95": _nearest_rank_percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _nearest_rank_percentile(
    values: Sequence[float], percentile: float
) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, ceil(percentile * len(ordered)) - 1)]


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _measurement_status(sample_count: int, expected_count: int) -> str:
    if not sample_count:
        return "not_measured"
    return "measured" if sample_count == expected_count else "partial"


def _first_number(value: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        number = _non_negative_number(value.get(key))
        if number is not None:
            return number
    return None


def _non_negative_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unable to read JSON object {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON artifact must contain one object: {path}")
    return value


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value.lower())
    )


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
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise ValueError(f"unable to hash file {path}: {exc}") from exc
    return digest.hexdigest()


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    target = path.expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    payload = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        target.chmod(0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-batch-dir", type=Path, required=True)
    parser.add_argument("--investigation-batch-dir", type=Path, required=True)
    parser.add_argument("--enrichment-composition", type=Path, required=True)
    parser.add_argument(
        "--enrichment-action-config",
        action="append",
        default=[],
        type=Path,
        required=True,
    )
    parser.add_argument("--enrichment-extensions-config", type=Path, required=True)
    parser.add_argument(
        "--acceptance-mode",
        choices=[mode.value for mode in ShadowAcceptanceMode],
        required=True,
    )
    parser.add_argument("--ramp-stage", choices=sorted(_RAMP_STAGES), required=True)
    parser.add_argument("--report-path", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = evaluate_shadow_batches(
            runtime_batch_dir=args.runtime_batch_dir,
            investigation_batch_dir=args.investigation_batch_dir,
            composition_path=args.enrichment_composition,
            action_config_paths=args.enrichment_action_config,
            extensions_config_path=args.enrichment_extensions_config,
            ramp_stage=args.ramp_stage,
            acceptance_mode=ShadowAcceptanceMode(args.acceptance_mode),
        )
        _write_private_json(args.report_path, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["gate_status"] == "passed" else 1
    except (OSError, TypeError, ValueError, RuntimeError) as exc:
        print(f"error: {str(exc).strip() or type(exc).__name__}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
