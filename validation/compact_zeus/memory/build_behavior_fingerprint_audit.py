#!/usr/bin/env python3
"""Compare legacy fingerprint grouping with the current PingAn Memory Profile v4."""

from __future__ import annotations

import argparse
import ipaddress
import json
import math
import sys
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.corpus.build_alert_validation_corpus import (  # noqa: E402
    canonical_sha256,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.application import build_soc_memory_profile_registry  # noqa: E402
from soc_agent.contracts import SensitiveEvidenceMode  # noqa: E402
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.behavior_fingerprint_audit.v2"
DEFAULT_CORPUS_PATH = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEFAULT_OUTPUT_DIR = (
    BACKEND_ROOT / ".deer-flow/soc-validation/behavior-fingerprint-audit-v2"
)
DEFAULT_SAMPLE_ORIGIN = "full_alert_sample"
_WEAK_COMPONENTS = frozenset(
    {
        "scenario:web_attack",
    }
)
_WEAK_COMPONENT_PREFIXES = ("http_method:", "protocol:")


def extract_fingerprint_records(
    rows: Sequence[Mapping[str, Any]],
    *,
    tenant_id: str,
    environment: str,
    sample_origin: str | None = DEFAULT_SAMPLE_ORIGIN,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Replay canonical requests and extract the exact production v1 facets."""

    registry = build_soc_memory_profile_registry()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_payload_mutation_count = 0

    for source_index, row in enumerate(rows):
        origin = _text(row.get("sample_origin"))
        if sample_origin is not None and origin != sample_origin:
            continue
        alert_id = _text(row.get("alert_id")) or f"row-{source_index}"
        try:
            payload = _alert_payload(row)
            input_hash = canonical_sha256(payload)
            request = build_analysis_request_for_payload(
                payload,
                sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
            ).model_copy(
                update={
                    "tenant_id": tenant_id,
                    "environment": environment,
                },
                deep=True,
            )
            if canonical_sha256(payload) != input_hash:
                raw_payload_mutation_count += 1
            profile = registry.resolve_request(request)
            facets = profile.project_query_facets(request)
            components = sorted(facets.get("behavior_component", []))
            strong_components = sorted(facets.get("behavior_component_strong", []))
            weak_components = sorted(facets.get("behavior_component_weak", []))
            fingerprints = facets.get("behavior_fingerprint", [])
            fingerprint = fingerprints[0] if fingerprints else None
            role_entities = sorted(facets.get("role_entity", []))
            entities = sorted(facets.get("entity", []))
            ip_role_entities = [
                value for value in role_entities if _role_entity_is_ip(value)
            ]
            ip_entities = [
                value for value in entities if value.casefold().startswith("ip:")
            ]
            historical = _historical_outcome(row.get("agent_response"))
            records.append(
                {
                    "source_index": source_index,
                    "alert_id": alert_id,
                    "sample_origin": origin,
                    "topic": _text(row.get("topic")),
                    "source_type": request.source.source_type.value,
                    "source_system": request.source.source_system,
                    "product": request.source.product,
                    "profile_id": profile.identity.profile_id,
                    "profile_version": profile.identity.profile_version,
                    "feature_schema_version": (profile.identity.feature_schema_version),
                    "tenant_id": tenant_id,
                    "environment": environment,
                    "detection_key": request.detection.detection_key,
                    "detection_signature": _first_value(
                        facets.get("detection_signature")
                    ),
                    "rule_code": request.detection.rule_code,
                    "rule_name": request.detection.rule_name,
                    "category": request.classification.category,
                    "severity": request.classification.severity,
                    "behavior_fingerprint": fingerprint,
                    "behavior_components": components,
                    "component_count": len(components),
                    "weak_components": weak_components,
                    "strong_components": strong_components,
                    "behavior_strength": _first_value(facets.get("behavior_strength")),
                    "scenario_keys": sorted(facets.get("scenario_key", [])),
                    "role_entities": role_entities,
                    "ip_role_entities": ip_role_entities,
                    "ip_entities": ip_entities,
                    "endpoint_signature": _endpoint_signature(
                        ip_role_entities,
                        ip_entities,
                    ),
                    "dual_ip_facet_count": _dual_ip_facet_count(
                        ip_role_entities,
                        ip_entities,
                    ),
                    "historical_output": historical,
                    "ground_label_present": _text(row.get("ground_label")) is not None,
                }
            )
        except Exception as exc:  # noqa: BLE001 - audit retains row-level failures
            errors.append(
                {
                    "source_index": source_index,
                    "alert_id": alert_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:500],
                }
            )
    return records, errors, raw_payload_mutation_count


def build_behavior_fingerprint_audit(
    records: Sequence[Mapping[str, Any]],
    *,
    errors: Sequence[Mapping[str, Any]] = (),
    raw_payload_mutation_count: int = 0,
    corpus_path: str | None = None,
    sample_origin: str | None = DEFAULT_SAMPLE_ORIGIN,
) -> dict[str, Any]:
    """Build structural coverage, fragmentation and similarity diagnostics."""

    records = [dict(item) for item in records]
    fingerprinted = [item for item in records if item.get("behavior_fingerprint")]
    missing = [item for item in records if not item.get("behavior_fingerprint")]
    detection_records = [item for item in records if item.get("detection_key")]

    component_counts: Counter[str] = Counter()
    source_counts: dict[str, Counter[str]] = defaultdict(Counter)
    topic_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for item in records:
        components = _string_list(item.get("behavior_components"))
        component_counts.update(components)
        for key, target in (
            (str(item.get("source_type") or "unknown"), source_counts),
            (str(item.get("topic") or "unknown"), topic_counts),
        ):
            target[key]["alerts"] += 1
            if item.get("detection_key"):
                target[key]["with_detection_key"] += 1
            if item.get("behavior_fingerprint"):
                target[key]["with_fingerprint"] += 1
            if item.get("dual_ip_facet_count"):
                target[key]["with_dual_ip_facets"] += 1

    legacy_cohorts = _build_exact_cohorts(
        fingerprinted,
        require_detection_signature=False,
    )
    cohorts = _build_exact_cohorts(
        fingerprinted,
        require_detection_signature=True,
    )
    rules = _build_rule_summaries(records, cohorts)
    legacy_context_pairs = _build_context_only_pairs(
        fingerprinted,
        require_detection_signature=False,
        component_key="behavior_components",
    )
    context_pairs = _build_context_only_pairs(
        fingerprinted,
        require_detection_signature=True,
        component_key="strong_components",
    )
    cross_rule = _build_cross_rule_fingerprints(fingerprinted)
    findings = _build_findings(
        missing=missing,
        rules=rules,
        cohorts=cohorts,
        context_pairs=context_pairs,
        errors=errors,
        raw_payload_mutation_count=raw_payload_mutation_count,
    )

    recurrent_cohorts = [item for item in cohorts if item["alert_count"] >= 2]
    candidate_threshold_cohorts = [item for item in cohorts if item["alert_count"] >= 5]
    cross_ip_cohorts = [item for item in recurrent_cohorts if item["cross_ip"]]
    decision_eligible_cohorts = [
        item
        for item in cohorts
        if item.get("detection_signature") and not item["weak_only"]
    ]
    recurrent_decision_eligible_cohorts = [
        item for item in decision_eligible_cohorts if item["alert_count"] >= 2
    ]
    threshold_decision_eligible_cohorts = [
        item for item in decision_eligible_cohorts if item["alert_count"] >= 5
    ]
    historical_divergent = [
        item for item in cohorts if item["historical_action_divergent"]
    ]
    ambiguous_rule_groups = [item for item in rules if item["rule_identity_ambiguous"]]
    ambiguous_exact_cohorts = [
        item for item in cohorts if item["rule_identity_ambiguous"]
    ]
    legacy_ambiguous_exact_cohorts = [
        item for item in legacy_cohorts if item["rule_identity_ambiguous"]
    ]
    weak_only_records = [
        item
        for item in fingerprinted
        if item.get("behavior_components") and not item.get("strong_components")
    ]
    context_alert_pairs = sum(item["alert_pair_count"] for item in context_pairs)
    low_only_context_alert_pairs = sum(
        item["alert_pair_count"] for item in context_pairs if item["low_signal_only"]
    )
    legacy_context_alert_pairs = sum(
        item["alert_pair_count"] for item in legacy_context_pairs
    )
    legacy_low_only_context_alert_pairs = sum(
        item["alert_pair_count"]
        for item in legacy_context_pairs
        if item["low_signal_only"]
    )
    cross_ip_alert_ids = {
        alert_id for cohort in cross_ip_cohorts for alert_id in cohort["alert_ids"]
    }
    historical_coverage = sum(
        item.get("historical_output", {}).get("status") == "parsed" for item in records
    )
    ground_truth_coverage = sum(
        bool(item.get("ground_label_present")) for item in records
    )
    metrics = {
        "selected_alert_count": len(records),
        "extraction_error_count": len(errors),
        "raw_payload_mutation_count": raw_payload_mutation_count,
        "detection_key_coverage_count": len(detection_records),
        "detection_key_coverage_ratio": _ratio(
            len(detection_records),
            len(records),
        ),
        "fingerprint_coverage_count": len(fingerprinted),
        "fingerprint_missing_count": len(missing),
        "fingerprint_coverage_ratio": _ratio(len(fingerprinted), len(records)),
        "unique_behavior_fingerprint_count": len(
            {item["behavior_fingerprint"] for item in fingerprinted}
        ),
        "weak_only_fingerprint_alert_count": len(weak_only_records),
        "weak_only_fingerprint_alert_ratio": _ratio(
            len(weak_only_records),
            len(fingerprinted),
        ),
        "exact_compound_cohort_count": len(cohorts),
        "recurrent_exact_cohort_count": len(recurrent_cohorts),
        "candidate_threshold_cohort_count": len(candidate_threshold_cohorts),
        "decision_eligible_exact_cohort_count": len(decision_eligible_cohorts),
        "recurrent_decision_eligible_cohort_count": len(
            recurrent_decision_eligible_cohorts
        ),
        "candidate_threshold_decision_eligible_cohort_count": len(
            threshold_decision_eligible_cohorts
        ),
        "cross_ip_recurrent_cohort_count": len(cross_ip_cohorts),
        "cross_ip_recurrent_alert_count": len(cross_ip_alert_ids),
        "cross_ip_recurrent_alert_ratio": _ratio(
            len(cross_ip_alert_ids),
            len(fingerprinted),
        ),
        "rule_group_count": len(rules),
        "high_fragmentation_rule_count": sum(
            item["high_fragmentation"] for item in rules
        ),
        "ambiguous_rule_identity_group_count": len(ambiguous_rule_groups),
        "ambiguous_rule_identity_cohort_count": len(ambiguous_exact_cohorts),
        "legacy_v1_ambiguous_rule_identity_cohort_count": len(
            legacy_ambiguous_exact_cohorts
        ),
        "cross_rule_fingerprint_count": len(cross_rule),
        "historical_output_coverage_count": historical_coverage,
        "historical_output_coverage_ratio": _ratio(
            historical_coverage,
            len(records),
        ),
        "historical_action_divergent_cohort_count": len(historical_divergent),
        "ground_truth_label_count": ground_truth_coverage,
        "ground_truth_label_ratio": _ratio(ground_truth_coverage, len(records)),
        "context_only_fingerprint_pair_count": len(context_pairs),
        "context_only_alert_pair_count": context_alert_pairs,
        "low_signal_only_context_alert_pair_count": low_only_context_alert_pairs,
        "low_signal_only_context_alert_pair_ratio": _ratio(
            low_only_context_alert_pairs,
            context_alert_pairs,
        ),
        "legacy_v1_context_only_alert_pair_count": legacy_context_alert_pairs,
        "legacy_v1_low_signal_only_context_alert_pair_count": (
            legacy_low_only_context_alert_pairs
        ),
        "alerts_with_dual_ip_facets": sum(
            bool(item.get("dual_ip_facet_count")) for item in records
        ),
        "duplicated_ip_facet_occurrences": sum(
            int(item.get("dual_ip_facet_count") or 0) for item in records
        ),
    }
    status = "passed" if not errors and raw_payload_mutation_count == 0 else "degraded"
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "audit_only": True,
        "runtime_decision_changed": False,
        "llm_calls": 0,
        "corpus_path": corpus_path,
        "sample_origin_filter": sample_origin,
        "metrics": metrics,
        "component_frequency": _counter_rows(component_counts),
        "source_type_summary": _summary_rows(source_counts),
        "topic_summary": _summary_rows(topic_counts),
        "rules": rules,
        "cohorts": cohorts,
        "legacy_v1_cohorts": legacy_cohorts,
        "cross_rule_fingerprints": cross_rule,
        "context_only_pairs": context_pairs,
        "legacy_v1_context_only_pairs": legacy_context_pairs,
        "findings": findings,
        "errors": [dict(item) for item in errors],
        "claim_boundaries": [
            "This report compares legacy v1 grouping with PingAn profile v4 facets; running it does not modify Runtime or persisted Memory.",
            "agent_response is historical model output and is not analyst ground truth.",
            "Historical action divergence is a review-priority signal, not a measured model error.",
            "Structural cross-IP recurrence is not production precision or recall.",
            "Exact IP values remain local sensitive validation data.",
        ],
    }


def write_audit_artifacts(
    report: Mapping[str, Any],
    records: Sequence[Mapping[str, Any]],
    *,
    output_dir: Path,
) -> None:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise ValueError(f"audit output directory is not empty: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)
    output_dir.chmod(0o700)

    summary = {
        key: value
        for key, value in report.items()
        if key
        not in {
            "cohorts",
            "legacy_v1_cohorts",
            "rules",
            "context_only_pairs",
            "legacy_v1_context_only_pairs",
        }
    }
    summary["artifacts"] = {
        "summary": "fingerprint-audit.json",
        "rules": "rule-fragmentation.json",
        "cohorts": "exact-cohorts.json",
        "legacy_v1_cohorts": "legacy-v1-exact-cohorts.json",
        "context_only_pairs": "context-only-pairs.json",
        "legacy_v1_context_only_pairs": "legacy-v1-context-only-pairs.json",
        "alerts": "alert-fingerprints.jsonl",
        "human_summary": "SUMMARY.md",
    }
    _write_json(output_dir / "fingerprint-audit.json", summary)
    _write_json(output_dir / "rule-fragmentation.json", report["rules"])
    _write_json(output_dir / "exact-cohorts.json", report["cohorts"])
    _write_json(
        output_dir / "legacy-v1-exact-cohorts.json",
        report["legacy_v1_cohorts"],
    )
    _write_json(
        output_dir / "context-only-pairs.json",
        report["context_only_pairs"],
    )
    _write_json(
        output_dir / "legacy-v1-context-only-pairs.json",
        report["legacy_v1_context_only_pairs"],
    )
    _write_jsonl(output_dir / "alert-fingerprints.jsonl", records)
    _write_summary(output_dir / "SUMMARY.md", report)


def _build_exact_cohorts(
    records: Sequence[Mapping[str, Any]],
    *,
    require_detection_signature: bool,
) -> list[dict[str, Any]]:
    grouped: dict[
        tuple[str, str, str, str],
        list[Mapping[str, Any]],
    ] = defaultdict(list)
    for item in records:
        detection_key = _text(item.get("detection_key"))
        fingerprint = _text(item.get("behavior_fingerprint"))
        detection_signature = _text(item.get("detection_signature"))
        environment = _text(item.get("environment")) or "unknown"
        if (
            detection_key
            and fingerprint
            and (detection_signature or not require_detection_signature)
        ):
            grouped[
                (
                    detection_key,
                    detection_signature if require_detection_signature else "legacy-v1",
                    fingerprint,
                    environment,
                )
            ].append(item)

    cohorts: list[dict[str, Any]] = []
    for (
        detection_key,
        detection_signature,
        fingerprint,
        environment,
    ), items in grouped.items():
        rule_names = sorted(
            {value for item in items if (value := _text(item.get("rule_name")))}
        )
        endpoint_signatures = sorted(
            {
                str(item["endpoint_signature"])
                for item in items
                if item.get("endpoint_signature")
            }
        )
        actions = Counter(
            action
            for item in items
            if (action := _text(item.get("historical_output", {}).get("alert_action")))
        )
        components = sorted(
            {
                component
                for item in items
                for component in _string_list(item.get("behavior_components"))
            }
        )
        cohorts.append(
            {
                "detection_key": detection_key,
                "detection_signature": (
                    detection_signature if require_detection_signature else None
                ),
                "behavior_fingerprint": fingerprint,
                "environment": environment,
                "rule_code": _first_text(items, "rule_code"),
                "rule_name": rule_names[0] if len(rule_names) == 1 else None,
                "rule_names": rule_names,
                "rule_name_count": len(rule_names),
                "rule_identity_ambiguous": len(rule_names) >= 2,
                "source_type": _first_text(items, "source_type"),
                "behavior_components": components,
                "weak_only": bool(components)
                and all(_is_weak_component(item) for item in components),
                "alert_count": len(items),
                "alert_ids": sorted(str(item["alert_id"]) for item in items),
                "distinct_endpoint_signature_count": len(endpoint_signatures),
                "endpoint_signatures": endpoint_signatures[:20],
                "cross_ip": len(endpoint_signatures) >= 2,
                "historical_action_counts": dict(sorted(actions.items())),
                "historical_action_divergent": len(actions) >= 2,
            }
        )
    return sorted(
        cohorts,
        key=lambda item: (
            -item["alert_count"],
            item["detection_key"],
            item["behavior_fingerprint"],
        ),
    )


def _build_rule_summaries(
    records: Sequence[Mapping[str, Any]],
    cohorts: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in records:
        detection_key = _text(item.get("detection_key"))
        if detection_key:
            grouped[detection_key].append(item)
    cohorts_by_rule: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for cohort in cohorts:
        cohorts_by_rule[str(cohort["detection_key"])].append(cohort)

    result: list[dict[str, Any]] = []
    for detection_key, items in grouped.items():
        rule_names = sorted(
            {value for item in items if (value := _text(item.get("rule_name")))}
        )
        fingerprints = Counter(
            str(item["behavior_fingerprint"])
            for item in items
            if item.get("behavior_fingerprint")
        )
        fingerprinted_count = sum(fingerprints.values())
        singleton_count = sum(count for count in fingerprints.values() if count == 1)
        endpoint_to_fingerprints: dict[str, set[str]] = defaultdict(set)
        for item in items:
            endpoint = _text(item.get("endpoint_signature"))
            fingerprint = _text(item.get("behavior_fingerprint"))
            if endpoint and fingerprint:
                endpoint_to_fingerprints[endpoint].add(fingerprint)
        multi_fingerprint_endpoints = {
            endpoint: sorted(values)
            for endpoint, values in endpoint_to_fingerprints.items()
            if len(values) >= 2
        }
        actions = Counter(
            action
            for item in items
            if (action := _text(item.get("historical_output", {}).get("alert_action")))
        )
        fragmentation_ratio = _ratio(len(fingerprints), fingerprinted_count)
        singleton_ratio = _ratio(singleton_count, fingerprinted_count)
        high_fragmentation = (
            fingerprinted_count >= 3
            and len(fingerprints) >= 2
            and singleton_ratio >= 0.5
        )
        rule_cohorts = cohorts_by_rule.get(detection_key, [])
        result.append(
            {
                "detection_key": detection_key,
                "rule_code": _first_text(items, "rule_code"),
                "rule_name": rule_names[0] if len(rule_names) == 1 else None,
                "rule_names": rule_names,
                "rule_name_count": len(rule_names),
                "rule_identity_ambiguous": len(rule_names) >= 2,
                "source_type": _first_text(items, "source_type"),
                "alert_count": len(items),
                "fingerprinted_alert_count": fingerprinted_count,
                "missing_fingerprint_count": len(items) - fingerprinted_count,
                "distinct_fingerprint_count": len(fingerprints),
                "fragmentation_ratio": fragmentation_ratio,
                "singleton_alert_count": singleton_count,
                "singleton_alert_ratio": singleton_ratio,
                "largest_cohort_size": max(fingerprints.values(), default=0),
                "recurrent_cohort_count": sum(
                    cohort["alert_count"] >= 2 for cohort in rule_cohorts
                ),
                "cross_ip_cohort_count": sum(
                    bool(cohort["cross_ip"]) for cohort in rule_cohorts
                ),
                "historical_action_counts": dict(sorted(actions.items())),
                "historical_action_divergent_cohort_count": sum(
                    bool(cohort["historical_action_divergent"])
                    for cohort in rule_cohorts
                ),
                "same_endpoint_multi_fingerprint_count": len(
                    multi_fingerprint_endpoints
                ),
                "same_endpoint_multi_fingerprint_samples": [
                    {
                        "endpoint_signature": endpoint,
                        "fingerprints": values,
                    }
                    for endpoint, values in list(
                        sorted(multi_fingerprint_endpoints.items())
                    )[:10]
                ],
                "high_fragmentation": high_fragmentation,
                "sample_alert_ids": sorted(str(item["alert_id"]) for item in items)[
                    :20
                ],
            }
        )
    return sorted(
        result,
        key=lambda item: (
            not item["high_fragmentation"],
            -item["alert_count"],
            -item["fragmentation_ratio"],
            item["detection_key"],
        ),
    )


def _build_context_only_pairs(
    records: Sequence[Mapping[str, Any]],
    *,
    require_detection_signature: bool,
    component_key: str,
) -> list[dict[str, Any]]:
    by_rule: dict[
        tuple[str, str, str],
        dict[str, list[Mapping[str, Any]]],
    ] = defaultdict(lambda: defaultdict(list))
    for item in records:
        detection_key = _text(item.get("detection_key"))
        fingerprint = _text(item.get("behavior_fingerprint"))
        detection_signature = _text(item.get("detection_signature"))
        environment = _text(item.get("environment")) or "unknown"
        if (
            detection_key
            and fingerprint
            and (detection_signature or not require_detection_signature)
        ):
            by_rule[
                (
                    detection_key,
                    detection_signature if require_detection_signature else "legacy-v1",
                    environment,
                )
            ][fingerprint].append(item)

    result: list[dict[str, Any]] = []
    for (
        detection_key,
        detection_signature,
        environment,
    ), fingerprints in by_rule.items():
        for (left_fingerprint, left), (right_fingerprint, right) in combinations(
            sorted(fingerprints.items()),
            2,
        ):
            left_components = {
                component
                for item in left
                for component in _string_list(item.get(component_key))
            }
            right_components = {
                component
                for item in right
                for component in _string_list(item.get(component_key))
            }
            shared = sorted(left_components & right_components)
            if not shared:
                continue
            result.append(
                {
                    "detection_key": detection_key,
                    "detection_signature": (
                        detection_signature if require_detection_signature else None
                    ),
                    "environment": environment,
                    "left_fingerprint": left_fingerprint,
                    "right_fingerprint": right_fingerprint,
                    "left_alert_count": len(left),
                    "right_alert_count": len(right),
                    "alert_pair_count": len(left) * len(right),
                    "shared_components": shared,
                    "low_signal_only": all(
                        _is_weak_component(component) for component in shared
                    ),
                    "left_alert_ids": sorted(str(item["alert_id"]) for item in left)[
                        :10
                    ],
                    "right_alert_ids": sorted(str(item["alert_id"]) for item in right)[
                        :10
                    ],
                }
            )
    return sorted(
        result,
        key=lambda item: (
            not item["low_signal_only"],
            -item["alert_pair_count"],
            item["detection_key"],
        ),
    )


def _build_cross_rule_fingerprints(
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for item in records:
        fingerprint = _text(item.get("behavior_fingerprint"))
        if fingerprint:
            grouped[fingerprint].append(item)
    result: list[dict[str, Any]] = []
    for fingerprint, items in grouped.items():
        detection_keys = sorted(
            {value for item in items if (value := _text(item.get("detection_key")))}
        )
        if len(detection_keys) < 2:
            continue
        result.append(
            {
                "behavior_fingerprint": fingerprint,
                "behavior_components": sorted(
                    {
                        component
                        for item in items
                        for component in _string_list(item.get("behavior_components"))
                    }
                ),
                "alert_count": len(items),
                "detection_key_count": len(detection_keys),
                "detection_keys": detection_keys,
                "sample_alert_ids": sorted(str(item["alert_id"]) for item in items)[
                    :20
                ],
            }
        )
    return sorted(
        result,
        key=lambda item: (
            -item["detection_key_count"],
            -item["alert_count"],
            item["behavior_fingerprint"],
        ),
    )


def _build_findings(
    *,
    missing: Sequence[Mapping[str, Any]],
    rules: Sequence[Mapping[str, Any]],
    cohorts: Sequence[Mapping[str, Any]],
    context_pairs: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    raw_payload_mutation_count: int,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if errors:
        findings.append(
            {
                "finding_type": "extraction_errors",
                "priority": "high",
                "count": len(errors),
                "interpretation_zh": "存在无法生成规范化分析请求的样本，先修输入或 Adapter。",
                "sample_alert_ids": [str(item["alert_id"]) for item in errors[:20]],
            }
        )
    if raw_payload_mutation_count:
        findings.append(
            {
                "finding_type": "raw_payload_mutation",
                "priority": "high",
                "count": raw_payload_mutation_count,
                "interpretation_zh": "审计构建过程修改了原始输入，违反回放契约。",
            }
        )
    if missing:
        findings.append(
            {
                "finding_type": "missing_behavior_fingerprint",
                "priority": "medium",
                "count": len(missing),
                "interpretation_zh": "行为组件不足两个，当前无法形成 v1 行为指纹。",
                "sample_alert_ids": [str(item["alert_id"]) for item in missing[:20]],
            }
        )
    ambiguous_rules = [item for item in rules if item["rule_identity_ambiguous"]]
    if ambiguous_rules:
        findings.append(
            {
                "finding_type": "ambiguous_detection_identity",
                "priority": "high",
                "count": len(ambiguous_rules),
                "interpretation_zh": "同一 detection_key 对应多个规则名称，不能仅凭 rule_code 把这些告警视为同类。",
                "sample_rules": [
                    {
                        "detection_key": item["detection_key"],
                        "rule_names": item["rule_names"][:10],
                    }
                    for item in ambiguous_rules[:20]
                ],
            }
        )
    fragmented = [item for item in rules if item["high_fragmentation"]]
    if fragmented:
        findings.append(
            {
                "finding_type": "high_rule_fragmentation",
                "priority": "medium",
                "count": len(fragmented),
                "interpretation_zh": "同一检测规则被切成较多单例指纹，可能影响经验复用。",
                "sample_detection_keys": [
                    str(item["detection_key"]) for item in fragmented[:20]
                ],
            }
        )
    divergent = [item for item in cohorts if item["historical_action_divergent"]]
    if divergent:
        findings.append(
            {
                "finding_type": "historical_action_divergence",
                "priority": "review",
                "count": len(divergent),
                "interpretation_zh": "同一复合指纹下旧模型动作不一致；旧结果不是真值，但应优先人工抽样。",
                "sample_cohorts": [
                    {
                        "detection_key": item["detection_key"],
                        "behavior_fingerprint": item["behavior_fingerprint"],
                        "historical_action_counts": item["historical_action_counts"],
                    }
                    for item in divergent[:20]
                ],
            }
        )
    low_pairs = [item for item in context_pairs if item["low_signal_only"]]
    if low_pairs:
        findings.append(
            {
                "finding_type": "low_signal_context_only_similarity",
                "priority": "medium",
                "count": len(low_pairs),
                "alert_pair_count": sum(item["alert_pair_count"] for item in low_pairs),
                "interpretation_zh": "不同指纹仅因 TCP/UDP/HTTP 方法等弱组件相似，当前可能产生低价值 context-only 召回。",
                "sample_detection_keys": sorted(
                    {str(item["detection_key"]) for item in low_pairs}
                )[:20],
            }
        )
    return findings


def _historical_outcome(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {"status": "invalid_json"}
    if not isinstance(value, Mapping):
        return {"status": "missing"}
    result = value.get("analysis_result")
    if not isinstance(result, Mapping):
        return {"status": "missing_analysis_result"}
    return {
        "status": "parsed",
        "alert_action": _text(result.get("alert_action")),
        "alert_type": _text(result.get("alert_type")),
        "warning_flag": result.get("warning_flag"),
    }


def _alert_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapper = row.get("alert_full_data")
    if not isinstance(wrapper, Mapping):
        raise TypeError("alert_full_data must be an object")
    payload = wrapper.get("alert_data")
    if not isinstance(payload, Mapping):
        raise TypeError("alert_full_data.alert_data must be an object")
    return payload


def _role_entity_is_ip(value: str) -> bool:
    _, separator, entity = value.partition(":")
    if not separator:
        return False
    try:
        ipaddress.ip_address(entity)
    except ValueError:
        return False
    return True


def _endpoint_signature(
    ip_role_entities: Sequence[str],
    ip_entities: Sequence[str],
) -> str | None:
    if ip_role_entities:
        return "|".join(sorted(value.casefold() for value in ip_role_entities))
    if ip_entities:
        return "|".join(sorted(value.casefold() for value in ip_entities))
    return None


def _dual_ip_facet_count(
    ip_role_entities: Sequence[str],
    ip_entities: Sequence[str],
) -> int:
    role_ips = {
        value.partition(":")[2].casefold()
        for value in ip_role_entities
        if value.partition(":")[2]
    }
    entity_ips = {
        value.partition(":")[2].casefold()
        for value in ip_entities
        if value.partition(":")[2]
    }
    return len(role_ips & entity_ips)


def _is_weak_component(value: str) -> bool:
    normalized = value.strip().casefold()
    return normalized in _WEAK_COMPONENTS or normalized.startswith(
        _WEAK_COMPONENT_PREFIXES
    )


def _first_text(items: Sequence[Mapping[str, Any]], key: str) -> str | None:
    for item in items:
        if value := _text(item.get(key)):
            return value
    return None


def _first_value(values: Sequence[str] | None) -> str | None:
    if not values:
        return None
    return _text(values[0])


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [str(item) for item in value if _text(item)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    normalized = str(value).strip()
    return normalized or None


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def _counter_rows(counter: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(
            counter.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]


def _summary_rows(
    values: Mapping[str, Counter[str]],
) -> list[dict[str, Any]]:
    return [
        {
            "value": key,
            **dict(sorted(counter.items())),
            "fingerprint_coverage_ratio": _ratio(
                counter["with_fingerprint"],
                counter["alerts"],
            ),
        }
        for key, counter in sorted(values.items())
    ]


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.write_text(
        "".join(
            json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)


def _write_summary(path: Path, report: Mapping[str, Any]) -> None:
    metrics = report["metrics"]
    rules = report["rules"]
    cohorts = report["cohorts"]
    context_pairs = report["context_only_pairs"]
    lines = [
        "# Behavior Fingerprint v1 to PingAn Profile v4 Audit",
        "",
        f"- Status: `{report['status']}`",
        f"- Selected alerts: `{metrics['selected_alert_count']}`",
        f"- Fingerprint coverage: `{metrics['fingerprint_coverage_count']}/{metrics['selected_alert_count']}` (`{metrics['fingerprint_coverage_ratio']}`)",
        f"- Exact compound cohorts: `{metrics['exact_compound_cohort_count']}`",
        f"- Recurrent exact cohorts: `{metrics['recurrent_exact_cohort_count']}`",
        f"- Cross-IP recurrent cohorts: `{metrics['cross_ip_recurrent_cohort_count']}`",
        f"- Candidate-threshold cohorts (>=5): `{metrics['candidate_threshold_cohort_count']}`",
        f"- Recurrent decision-eligible cohorts: `{metrics['recurrent_decision_eligible_cohort_count']}`",
        f"- Candidate-threshold decision-eligible cohorts (>=5): `{metrics['candidate_threshold_decision_eligible_cohort_count']}`",
        f"- High-fragmentation rules: `{metrics['high_fragmentation_rule_count']}`",
        f"- Ambiguous detection identities: `{metrics['ambiguous_rule_identity_group_count']}`",
        f"- Ambiguous exact cohorts, legacy v1 -> profile v4: `{metrics['legacy_v1_ambiguous_rule_identity_cohort_count']}` -> `{metrics['ambiguous_rule_identity_cohort_count']}`",
        f"- Low-signal context-only alert pairs: `{metrics['low_signal_only_context_alert_pair_count']}`",
        f"- Context-only alert pairs, legacy v1 -> profile v4: `{metrics['legacy_v1_context_only_alert_pair_count']}` -> `{metrics['context_only_alert_pair_count']}`",
        f"- Low-signal context-only alert pairs, legacy v1 -> profile v4: `{metrics['legacy_v1_low_signal_only_context_alert_pair_count']}` -> `{metrics['low_signal_only_context_alert_pair_count']}`",
        f"- Historical divergent cohorts: `{metrics['historical_action_divergent_cohort_count']}`",
        f"- Human ground-truth labels: `{metrics['ground_truth_label_count']}`",
        "",
        "## Highest Fragmentation",
        "",
        "| Detection key | Alerts | Fingerprints | Singleton ratio | Largest cohort |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for item in sorted(
        rules,
        key=lambda value: (
            -value["fragmentation_ratio"],
            -value["alert_count"],
        ),
    )[:15]:
        lines.append(
            f"| `{item['detection_key']}` | {item['alert_count']} | "
            f"{item['distinct_fingerprint_count']} | "
            f"{item['singleton_alert_ratio']} | {item['largest_cohort_size']} |"
        )
    lines.extend(
        [
            "",
            "## Recurrent Exact Cohorts",
            "",
            "| Detection key | Alerts | Weak only | Rule identity ambiguous | Historical actions |",
            "| --- | ---: | --- | --- | --- |",
        ]
    )
    for item in [value for value in cohorts if value["alert_count"] >= 2][:15]:
        actions = ", ".join(
            f"{key}:{value}" for key, value in item["historical_action_counts"].items()
        )
        lines.append(
            f"| `{item['detection_key']}` | {item['alert_count']} | "
            f"{str(item['weak_only']).lower()} | "
            f"{str(item['rule_identity_ambiguous']).lower()} | {actions or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Ambiguous Detection Identities",
            "",
            "| Detection key | Alerts | Rule names |",
            "| --- | ---: | ---: |",
        ]
    )
    for item in [value for value in rules if value["rule_identity_ambiguous"]][:15]:
        lines.append(
            f"| `{item['detection_key']}` | {item['alert_count']} | "
            f"{item['rule_name_count']} |"
        )
    lines.extend(
        [
            "",
            "## Largest Low-Signal Context Pairs",
            "",
            "| Detection key | Alert pairs | Shared components |",
            "| --- | ---: | --- |",
        ]
    )
    for item in [value for value in context_pairs if value["low_signal_only"]][:15]:
        shared = ", ".join(item["shared_components"])
        lines.append(
            f"| `{item['detection_key']}` | {item['alert_pair_count']} | `{shared}` |"
        )
    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            *[f"- {item}" for item in report["claim_boundaries"]],
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    path.chmod(0o600)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--tenant-id", default="pingan")
    parser.add_argument("--environment", required=True)
    parser.add_argument(
        "--sample-origin",
        default=DEFAULT_SAMPLE_ORIGIN,
        help="Filter corpus sample_origin; use 'all' to include legacy demos",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    corpus_path = args.corpus.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    sample_origin = (
        None if args.sample_origin.strip().casefold() == "all" else args.sample_origin
    )
    frame = load_dataframe_pickle(corpus_path)
    records, errors, mutation_count = extract_fingerprint_records(
        frame.to_dict(orient="records"),
        tenant_id=args.tenant_id.strip(),
        environment=args.environment.strip().casefold(),
        sample_origin=sample_origin,
    )
    report = build_behavior_fingerprint_audit(
        records,
        errors=errors,
        raw_payload_mutation_count=mutation_count,
        corpus_path=str(corpus_path),
        sample_origin=sample_origin,
    )
    write_audit_artifacts(report, records, output_dir=output_dir)
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
