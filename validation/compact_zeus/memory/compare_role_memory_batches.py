#!/usr/bin/env python3
"""Compare a Role-Verifier baseline with a confirmed-Memory replay batch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.contracts import LLMAnalysisRequest  # noqa: E402
from soc_agent.core import SocMemoryService  # noqa: E402
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    to_sync_database_url,
)
from soc_agent.memory import memory_query_from_analysis_request  # noqa: E402

REPORT_SCHEMA_VERSION = "soc.validation.role_memory_batch_comparison.v1"


def load_batch(root: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load one completed internal-batch directory indexed by alert ID."""

    manifest = _read_object(root / "manifest.json")
    if manifest.get("status") != "completed":
        raise ValueError(f"batch is not completed: {root}")
    items: dict[str, dict[str, Any]] = {}
    for path in sorted((root / "items").glob("*.json")):
        item = _read_object(path)
        run = _mapping(item.get("analysis_run"))
        alert_id = str(run.get("alert_id") or "").strip()
        if not alert_id:
            raise ValueError(f"batch item has no alert_id: {path}")
        if alert_id in items:
            raise ValueError(f"duplicate alert_id {alert_id} in {root}")
        items[alert_id] = item
    expected = int(_mapping(manifest.get("summary")).get("recorded_count") or 0)
    if expected != len(items):
        raise ValueError(
            f"batch item count mismatch for {root}: manifest={expected}, files={len(items)}"
        )
    return manifest, items


def replay_memory_retrieval(
    items: Mapping[str, Mapping[str, Any]],
    *,
    database_url: str,
) -> dict[str, dict[str, Any]]:
    """Re-run the production read-only Retrieval v2 selector for frozen requests."""

    engine = create_engine(to_sync_database_url(database_url), pool_pre_ping=True)
    try:
        repository = SqlAlchemyAlertRepository(
            sessionmaker(bind=engine, expire_on_commit=False)
        )
        service = SocMemoryService(record_repository=repository)
        results: dict[str, dict[str, Any]] = {}
        for alert_id, item in items.items():
            run = _mapping(item.get("analysis_run"))
            request = LLMAnalysisRequest.model_validate(
                _mapping(run.get("llm_analysis_request"))
            )
            result = service.find_relevant_records(
                memory_query_from_analysis_request(request)
            )
            results[alert_id] = {
                "policy_version": result.policy_version,
                "query": result.query.model_dump(mode="json"),
                "total_candidate_count": result.total_candidate_count,
                "returned_count": result.returned_count,
                "total_token_estimate": result.total_token_estimate,
                "skipped_missing_strong_anchor": (result.skipped_missing_strong_anchor),
                "skipped_below_min_score": result.skipped_below_min_score,
                "matches": [
                    {
                        "memory_id": match.memory_id,
                        "memory_version": match.version,
                        "score": match.score,
                        "match_reasons": match.match_reasons,
                        "matched_facets": match.matched_facets,
                        "anchor_match_reasons": match.anchor_match_reasons,
                        "matched_anchor_facets": match.matched_anchor_facets,
                        "token_estimate": match.token_estimate,
                    }
                    for match in result.matches
                ],
            }
        return results
    finally:
        engine.dispose()


def compare_batches(
    *,
    baseline_manifest: Mapping[str, Any],
    baseline_items: Mapping[str, Mapping[str, Any]],
    current_manifest: Mapping[str, Any],
    current_items: Mapping[str, Mapping[str, Any]],
    seed_report: Mapping[str, Any],
    retrievals: Mapping[str, Mapping[str, Any]],
    baseline_path: str,
    current_path: str,
) -> dict[str, Any]:
    """Build an auditable same-cohort comparison without causal overclaiming."""

    _validate_comparison_boundary(
        baseline_manifest,
        baseline_items,
        current_manifest,
        current_items,
    )
    seed_by_memory_id = {
        str(item.get("memory_id")): dict(item)
        for item in seed_report.get("items", [])
        if isinstance(item, Mapping) and item.get("memory_id")
    }
    cases = [
        _compare_case(
            alert_id,
            baseline_items[alert_id],
            current_items[alert_id],
            seed_by_memory_id=seed_by_memory_id,
            retrieval=_mapping(retrievals.get(alert_id)),
        )
        for alert_id in baseline_items
    ]

    baseline_usage = _sum_usage(item["baseline"] for item in cases)
    current_usage = _sum_usage(item["current"] for item in cases)
    baseline_durations = [
        float(item["baseline"].get("duration_ms") or 0.0) for item in cases
    ]
    current_durations = [
        float(item["current"].get("duration_ms") or 0.0) for item in cases
    ]
    selected_memories = [
        memory for item in cases for memory in item["memory_selection"]["selected"]
    ]

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "experiment": {
            "design": "same-alert in-sample confirmed-Memory wiring comparison",
            "baseline_path": baseline_path,
            "current_path": current_path,
            "source_corpus_sha256": _mapping(baseline_manifest.get("source")).get(
                "sha256"
            ),
            "model_name": _mapping(current_manifest.get("execution")).get("model_name"),
            "thinking_enabled": _mapping(current_manifest.get("execution")).get(
                "thinking_enabled_requested"
            ),
            "role_verifier_enabled": True,
            "role_verifier_model_name": _mapping(current_manifest.get("execution")).get(
                "role_verifier_model_name"
            ),
            "memory_policy_versions": sorted(
                {str(item["memory_selection"].get("policy_version")) for item in cases}
            ),
            "decision_directive_count": int(
                seed_report.get("decision_directive_count") or 0
            ),
            "independent_truth_labels_present": False,
            "causal_attribution_allowed": False,
        },
        "summary": {
            "case_count": len(cases),
            "same_input_hash_count": sum(item["same_input_hash"] for item in cases),
            "baseline_status_counts": _counts(
                item["baseline"].get("runtime_status") for item in cases
            ),
            "current_status_counts": _counts(
                item["current"].get("runtime_status") for item in cases
            ),
            "baseline_verdict_counts": _counts(
                item["baseline"].get("verdict") for item in cases
            ),
            "current_verdict_counts": _counts(
                item["current"].get("verdict") for item in cases
            ),
            "verdict_changed_alert_ids": [
                item["alert_id"] for item in cases if item["changes"]["verdict"]
            ],
            "review_changed_alert_ids": [
                item["alert_id"] for item in cases if item["changes"]["needs_review"]
            ],
            "direction_changed_alert_ids": [
                item["alert_id"]
                for item in cases
                if item["changes"]["network_direction"]
            ],
            "roles_changed_alert_ids": [
                item["alert_id"] for item in cases if item["changes"]["roles"]
            ],
            "quality_changed_alert_ids": [
                item["alert_id"] for item in cases if item["changes"]["quality_status"]
            ],
            "baseline_quality_status_counts": _counts(
                item["baseline"].get("quality_status") for item in cases
            ),
            "current_quality_status_counts": _counts(
                item["current"].get("quality_status") for item in cases
            ),
            "baseline_repair_count": sum(
                item["baseline"].get("repair_attempted", False) for item in cases
            ),
            "current_repair_count": sum(
                item["current"].get("repair_attempted", False) for item in cases
            ),
            "baseline_fallback_count": sum(
                item["baseline"].get("fallback_used", False) for item in cases
            ),
            "current_fallback_count": sum(
                item["current"].get("fallback_used", False) for item in cases
            ),
            "baseline_role_verifier_triggered_count": sum(
                item["baseline"]["role_verifier"]["triggered"] for item in cases
            ),
            "current_role_verifier_triggered_count": sum(
                item["current"]["role_verifier"]["triggered"] for item in cases
            ),
            "baseline_role_verifier_status_counts": _counts(
                item["baseline"]["role_verifier"].get("status") for item in cases
            ),
            "current_role_verifier_status_counts": _counts(
                item["current"]["role_verifier"].get("status") for item in cases
            ),
            "memory_cases_with_selection": sum(
                bool(item["memory_selection"]["selected"]) for item in cases
            ),
            "memory_total_selected": len(selected_memories),
            "memory_unique_selected": len(
                {item["memory_id"] for item in selected_memories}
            ),
            "own_memory_selected_count": sum(
                item["memory_selection"]["own_memory_selected"] for item in cases
            ),
            "own_memory_rank_one_count": sum(
                item["memory_selection"]["own_memory_rank"] == 1 for item in cases
            ),
            "model_cited_any_memory_count": sum(
                item["memory_selection"]["model_cited_any"] for item in cases
            ),
            "model_cited_own_memory_count": sum(
                item["memory_selection"]["model_cited_own"] for item in cases
            ),
            "model_core_cited_any_memory_count": sum(
                item["memory_selection"]["model_core_cited_any"] for item in cases
            ),
            "model_core_cited_own_memory_count": sum(
                item["memory_selection"]["model_core_cited_own"] for item in cases
            ),
            "memory_projection_mismatch_alert_ids": [
                item["alert_id"]
                for item in cases
                if not item["memory_selection"]["projection_matches_replay"]
            ],
            "baseline_usage": baseline_usage,
            "current_usage": current_usage,
            "usage_delta": _usage_delta(baseline_usage, current_usage),
            "baseline_duration_ms": _duration_summary(baseline_durations),
            "current_duration_ms": _duration_summary(current_durations),
        },
        "interpretation": [
            "All confirmed records were created from this same cohort, so this intentionally tests retrieval wiring and consistency, not generalization.",
            "A selected M-* item was available to the model; only an M-* reference in accepted model output proves explicit citation.",
            "Separate live model calls are stochastic, so an output delta cannot be attributed to Memory without repeated runs or independent labels.",
            "These records contain no typed decision directive. They cannot deterministically override the base decision or authorize an action.",
        ],
        "cases": cases,
    }


def _compare_case(
    alert_id: str,
    baseline_item: Mapping[str, Any],
    current_item: Mapping[str, Any],
    *,
    seed_by_memory_id: Mapping[str, Mapping[str, Any]],
    retrieval: Mapping[str, Any],
) -> dict[str, Any]:
    baseline = _snapshot(baseline_item)
    current = _snapshot(current_item)
    current_run = _mapping(current_item.get("analysis_run"))
    request = _mapping(current_run.get("llm_analysis_request"))
    projected = {
        str(_mapping(item.get("metadata")).get("memory_id")): item
        for item in request.get("context_catalog", [])
        if isinstance(item, Mapping)
        and item.get("kind") == "confirmed_memory"
        and _mapping(item.get("metadata")).get("memory_id")
    }
    cited_paths = _collect_memory_reference_paths(
        {
            "analysis": current_run.get("analysis"),
            "role_adjudication_verification": current_run.get(
                "role_adjudication_verification"
            ),
        }
    )
    cited_refs = set(cited_paths)
    analysis = _mapping(current_run.get("analysis"))
    core_refs = {
        str(context_ref)
        for reasoning in analysis.get("reasoning", [])
        if isinstance(reasoning, Mapping) and reasoning.get("reasoning_id") == "R-00"
        for context_ref in reasoning.get("context_refs", [])
        if str(context_ref).startswith("M-")
    }
    selected: list[dict[str, Any]] = []
    for rank, match_value in enumerate(retrieval.get("matches", []), start=1):
        match = _mapping(match_value)
        memory_id = str(match.get("memory_id") or "")
        context = _mapping(projected.get(memory_id))
        context_ref = str(context.get("context_ref") or "")
        seed = _mapping(seed_by_memory_id.get(memory_id))
        source_alert_id = str(seed.get("alert_id") or "")
        selected.append(
            {
                "rank": rank,
                "context_ref": context_ref or None,
                "memory_id": memory_id,
                "memory_version": match.get("memory_version"),
                "source_alert_id": source_alert_id or None,
                "same_alert_memory": source_alert_id == alert_id,
                "score": match.get("score"),
                "anchor_match_reasons": list(match.get("anchor_match_reasons") or []),
                "matched_anchor_facets": _mapping(match.get("matched_anchor_facets")),
                "match_reasons": list(match.get("match_reasons") or []),
                "matched_facets": _mapping(match.get("matched_facets")),
                "token_estimate": match.get("token_estimate"),
                "cited_by_model": context_ref in cited_refs,
                "cited_in_core_reasoning": context_ref in core_refs,
                "citation_paths": cited_paths.get(context_ref, []),
            }
        )
    projected_ids = set(projected)
    replayed_ids = {item["memory_id"] for item in selected}
    own = [item for item in selected if item["same_alert_memory"]]
    return {
        "alert_id": alert_id,
        "same_input_hash": baseline.get("input_hash") == current.get("input_hash"),
        "baseline": baseline,
        "current": current,
        "changes": {
            "verdict": baseline.get("verdict") != current.get("verdict"),
            "confidence": baseline.get("confidence") != current.get("confidence"),
            "needs_review": baseline.get("needs_review") != current.get("needs_review"),
            "network_direction": baseline.get("network_direction")
            != current.get("network_direction"),
            "roles": baseline.get("roles") != current.get("roles"),
            "quality_status": baseline.get("quality_status")
            != current.get("quality_status"),
            "role_verifier_triggered": baseline["role_verifier"]["triggered"]
            != current["role_verifier"]["triggered"],
            "role_verifier_status": baseline["role_verifier"].get("status")
            != current["role_verifier"].get("status"),
        },
        "memory_selection": {
            "policy_version": retrieval.get("policy_version"),
            "eligible_candidate_count": retrieval.get("total_candidate_count"),
            "returned_count": retrieval.get("returned_count"),
            "selected_token_estimate": retrieval.get("total_token_estimate"),
            "skipped_missing_strong_anchor": retrieval.get(
                "skipped_missing_strong_anchor"
            ),
            "skipped_below_min_score": retrieval.get("skipped_below_min_score"),
            "projection_matches_replay": projected_ids == replayed_ids,
            "projected_memory_ids": sorted(projected_ids),
            "replayed_memory_ids": [item["memory_id"] for item in selected],
            "own_memory_selected": bool(own),
            "own_memory_rank": own[0]["rank"] if own else None,
            "own_memory_score": own[0]["score"] if own else None,
            "model_cited_any": any(item["cited_by_model"] for item in selected),
            "model_cited_own": any(
                item["cited_by_model"] and item["same_alert_memory"]
                for item in selected
            ),
            "model_core_cited_any": any(
                item["cited_in_core_reasoning"] for item in selected
            ),
            "model_core_cited_own": any(
                item["cited_in_core_reasoning"] and item["same_alert_memory"]
                for item in selected
            ),
            "selected": selected,
        },
    }


def _snapshot(item: Mapping[str, Any]) -> dict[str, Any]:
    run = _mapping(item.get("analysis_run"))
    analysis = _mapping(run.get("analysis"))
    decision = _mapping(run.get("decision"))
    direction = _mapping(analysis.get("network_direction"))
    role_adjudication = _mapping(analysis.get("role_adjudication"))
    quality = _mapping(run.get("analysis_output_quality"))
    trigger = _mapping(run.get("role_verification_trigger"))
    verification = _mapping(run.get("role_adjudication_verification"))
    execution = _mapping(item.get("execution"))
    summary = _mapping(item.get("summary"))
    return {
        "run_id": run.get("run_id"),
        "input_hash": run.get("input_hash"),
        "runtime_status": run.get("status"),
        "source_type": summary.get("source_type"),
        "verdict": analysis.get("verdict"),
        "confidence": analysis.get("confidence"),
        "summary": analysis.get("summary"),
        "reason": analysis.get("reason"),
        "needs_review": bool(decision.get("needs_review")),
        "review_reasons": list(decision.get("review_reasons") or []),
        "evidence_state": decision.get("evidence_state"),
        "quality_status": quality.get("status"),
        "repair_attempted": bool(quality.get("repair_attempted")),
        "fallback_used": bool(quality.get("deterministic_fallback_used")),
        "degraded_sections": list(quality.get("degraded_sections") or []),
        "network_direction": {
            key: direction.get(key)
            for key in (
                "observed_flow",
                "boundary_direction",
                "semantic_direction",
                "connection_initiator",
            )
        },
        "roles": [
            {key: role.get(key) for key in ("role", "value", "status", "confidence")}
            for role in role_adjudication.get("roles", [])
            if isinstance(role, Mapping)
        ],
        "role_verifier": {
            "triggered": bool(trigger.get("triggered")),
            "trigger_reasons": list(trigger.get("reasons") or []),
            "claim_count": int(trigger.get("claim_count") or 0),
            "status": verification.get("status")
            or ("not_triggered" if not trigger.get("triggered") else "missing"),
            "repair_applied": bool(verification.get("repair_applied")),
            "warnings": list(verification.get("warnings") or []),
        },
        "usage": _usage(summary),
        "duration_ms": execution.get("end_to_end_total_duration_ms")
        or execution.get("duration_ms")
        or run.get("total_duration_ms"),
    }


def _collect_memory_reference_paths(value: Any) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}

    def visit(node: Any, path: str) -> None:
        if isinstance(node, str) and node.startswith("M-"):
            result.setdefault(node, []).append(path)
            return
        if isinstance(node, Mapping):
            for key, child in node.items():
                visit(child, f"{path}.{key}" if path else str(key))
            return
        if isinstance(node, list):
            for index, child in enumerate(node):
                visit(child, f"{path}[{index}]")

    visit(value, "")
    return result


def _validate_comparison_boundary(
    baseline_manifest: Mapping[str, Any],
    baseline_items: Mapping[str, Mapping[str, Any]],
    current_manifest: Mapping[str, Any],
    current_items: Mapping[str, Mapping[str, Any]],
) -> None:
    baseline_source = _mapping(baseline_manifest.get("source"))
    current_source = _mapping(current_manifest.get("source"))
    if baseline_source.get("sha256") != current_source.get("sha256"):
        raise ValueError("batches use different source corpus hashes")
    if list(baseline_items) != list(current_items):
        raise ValueError("batches do not contain the same ordered alert IDs")
    baseline_execution = _mapping(baseline_manifest.get("execution"))
    current_execution = _mapping(current_manifest.get("execution"))
    for key in (
        "model_name",
        "thinking_enabled_requested",
        "role_verifier_enabled",
        "role_verifier_model_name",
        "default_tenant_id",
    ):
        if baseline_execution.get(key) != current_execution.get(key):
            raise ValueError(f"execution configuration differs at {key}")
    if not current_execution.get("role_verifier_enabled"):
        raise ValueError("comparison requires Role Verifier enabled")


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = _mapping(report.get("summary"))
    experiment = _mapping(report.get("experiment"))
    lines = [
        "# Role Verifier + Confirmed Memory 20-Alert Comparison",
        "",
        "## Boundary",
        "",
        f"- Same input hash: `{summary.get('same_input_hash_count')}/{summary.get('case_count')}`.",
        f"- Primary model: `{experiment.get('model_name')}`; verifier: `{experiment.get('role_verifier_model_name')}`; reasoning: `{experiment.get('thinking_enabled')}`.",
        "- The 20 memories were confirmed from these same 20 earlier outputs. This is an in-sample wiring/consistency experiment, not an accuracy or generalization claim.",
        "- The memories have no typed decision directive, so they cannot bypass current-alert analysis, deterministically change a decision, or authorize an action.",
        "",
        "## Summary",
        "",
        f"- Runtime status: `{summary.get('baseline_status_counts')}` -> `{summary.get('current_status_counts')}`.",
        f"- Verdicts: `{summary.get('baseline_verdict_counts')}` -> `{summary.get('current_verdict_counts')}`; changed alerts: `{summary.get('verdict_changed_alert_ids')}`.",
        f"- Review changes: `{summary.get('review_changed_alert_ids')}`.",
        f"- Role Verifier triggered: `{summary.get('baseline_role_verifier_triggered_count')}` -> `{summary.get('current_role_verifier_triggered_count')}`; statuses: `{summary.get('baseline_role_verifier_status_counts')}` -> `{summary.get('current_role_verifier_status_counts')}`.",
        f"- Memory selected for `{summary.get('memory_cases_with_selection')}/{summary.get('case_count')}` alerts; `{summary.get('memory_total_selected')}` selections across `{summary.get('memory_unique_selected')}` records.",
        f"- Own-alert Memory selected: `{summary.get('own_memory_selected_count')}/{summary.get('case_count')}`; ranked first: `{summary.get('own_memory_rank_one_count')}/{summary.get('case_count')}`.",
        f"- Model explicitly cited any Memory: `{summary.get('model_cited_any_memory_count')}/{summary.get('case_count')}`; cited its own-alert Memory: `{summary.get('model_cited_own_memory_count')}/{summary.get('case_count')}`.",
        f"- Core decision reasoning (`R-00`) cited any/own Memory: `{summary.get('model_core_cited_any_memory_count')}/{summary.get('case_count')}` and `{summary.get('model_core_cited_own_memory_count')}/{summary.get('case_count')}`.",
        f"- Retrieval replay mismatches: `{summary.get('memory_projection_mismatch_alert_ids')}`.",
        f"- Output quality: `{summary.get('baseline_quality_status_counts')}` -> `{summary.get('current_quality_status_counts')}`; repairs `{summary.get('baseline_repair_count')} -> {summary.get('current_repair_count')}`; fallbacks `{summary.get('baseline_fallback_count')} -> {summary.get('current_fallback_count')}`.",
        f"- Usage: `{summary.get('baseline_usage')}` -> `{summary.get('current_usage')}`; delta `{summary.get('usage_delta')}`.",
        f"- End-to-end durations: `{summary.get('baseline_duration_ms')}` -> `{summary.get('current_duration_ms')}`.",
        "",
        "## Per Alert",
        "",
        "| Alert | Source | Verdict old -> new | Confidence | Review | Verifier old -> new | Memory | Own rank/score | Cited | Anchors of own Memory |",
        "| --- | --- | --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for case_value in report.get("cases", []):
        case = _mapping(case_value)
        baseline = _mapping(case.get("baseline"))
        current = _mapping(case.get("current"))
        selection = _mapping(case.get("memory_selection"))
        own = next(
            (
                _mapping(item)
                for item in selection.get("selected", [])
                if isinstance(item, Mapping) and item.get("same_alert_memory")
            ),
            {},
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    _md(case.get("alert_id")),
                    _md(baseline.get("source_type")),
                    _md(f"{baseline.get('verdict')} -> {current.get('verdict')}"),
                    _md(f"{baseline.get('confidence')} -> {current.get('confidence')}"),
                    _md(
                        f"{baseline.get('needs_review')} -> {current.get('needs_review')}"
                    ),
                    _md(
                        f"{_mapping(baseline.get('role_verifier')).get('status')} -> "
                        f"{_mapping(current.get('role_verifier')).get('status')}"
                    ),
                    _md(selection.get("returned_count")),
                    _md(
                        f"{selection.get('own_memory_rank')}/"
                        f"{selection.get('own_memory_score')}"
                    ),
                    _md(
                        "own"
                        if selection.get("model_cited_own")
                        else "other"
                        if selection.get("model_cited_any")
                        else "no"
                    ),
                    _md(
                        ", ".join(
                            sorted(_mapping(own.get("matched_anchor_facets")).keys())
                        )
                    ),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Selection Details", ""])
    for case_value in report.get("cases", []):
        case = _mapping(case_value)
        selection = _mapping(case.get("memory_selection"))
        lines.extend(
            [
                f"### Alert {case.get('alert_id')}",
                "",
                f"Retrieval v2 inspected `{selection.get('eligible_candidate_count')}` eligible records and returned `{selection.get('returned_count')}` within about `{selection.get('selected_token_estimate')}` tokens. Own-alert rank is `{selection.get('own_memory_rank')}`.",
                "",
                "| Rank | Memory | Source alert | Self | Score | Strong anchors | Cited | Match reasons |",
                "| ---: | --- | --- | --- | ---: | --- | --- | --- |",
            ]
        )
        for memory_value in selection.get("selected", []):
            memory = _mapping(memory_value)
            lines.append(
                "| "
                + " | ".join(
                    [
                        _md(memory.get("rank")),
                        _md(memory.get("memory_id")),
                        _md(memory.get("source_alert_id")),
                        _md(memory.get("same_alert_memory")),
                        _md(memory.get("score")),
                        _md(
                            ", ".join(
                                sorted(
                                    _mapping(memory.get("matched_anchor_facets")).keys()
                                )
                            )
                        ),
                        _md(memory.get("cited_by_model")),
                        _md(", ".join(memory.get("match_reasons") or [])),
                    ]
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines)


def write_report(output_dir: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    root = output_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    json_path = root / "memory-comparison.json"
    markdown_path = root / "MEMORY-COMPARISON.md"
    _write_private(
        json_path,
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
    )
    _write_private(markdown_path, render_markdown(report) + "\n")
    return json_path, markdown_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-dir", type=Path, required=True)
    parser.add_argument("--current-dir", type=Path, required=True)
    parser.add_argument("--seed-report", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        baseline_root = args.baseline_dir.expanduser().resolve()
        current_root = args.current_dir.expanduser().resolve()
        baseline_manifest, baseline_items = load_batch(baseline_root)
        current_manifest, current_items = load_batch(current_root)
        report = compare_batches(
            baseline_manifest=baseline_manifest,
            baseline_items=baseline_items,
            current_manifest=current_manifest,
            current_items=current_items,
            seed_report=_read_object(args.seed_report.expanduser().resolve()),
            retrievals=replay_memory_retrieval(
                current_items,
                database_url=args.database_url,
            ),
            baseline_path=str(baseline_root),
            current_path=str(current_root),
        )
        json_path, markdown_path = write_report(
            args.output_dir or current_root,
            report,
        )
        print(
            json.dumps(
                {
                    "json": str(json_path),
                    "markdown": str(markdown_path),
                    **_mapping(report.get("summary")),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _usage(summary: Mapping[str, Any]) -> dict[str, int]:
    usage = _mapping(summary.get("usage"))
    return {
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def _sum_usage(snapshots: Any) -> dict[str, int]:
    totals = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    for snapshot in snapshots:
        usage = _mapping(snapshot.get("usage"))
        for key in totals:
            totals[key] += int(usage.get(key) or 0)
    return totals


def _usage_delta(
    baseline: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, int]:
    return {
        key: int(current.get(key) or 0) - int(baseline.get(key) or 0)
        for key in ("input_tokens", "output_tokens", "total_tokens")
    }


def _duration_summary(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {"total": 0.0, "p50": 0.0, "p95": 0.0, "max": 0.0}
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "total": round(sum(ordered), 3),
        "p50": round(median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
    }


def _counts(values: Any) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values).items()))


def _write_private(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    path.chmod(0o600)


def _md(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


if __name__ == "__main__":
    raise SystemExit(main())
