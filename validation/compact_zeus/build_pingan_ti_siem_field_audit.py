#!/usr/bin/env python3
"""Audit PingAn Threat Intel and SIEM fields through SOC contracts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    import_path = str(import_root)
    if import_path not in sys.path:
        sys.path.insert(0, import_path)

from validation.compact_zeus.build_alert_validation_corpus import (  # noqa: E402
    canonical_sha256,
)
from validation.compact_zeus.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.contracts import (  # noqa: E402
    AlertSourceType,
    EvidenceLayer,
    EvidenceTrustLevel,
    SensitiveEvidenceMode,
)
from soc_agent.core.runtime import build_analysis_request_for_payload  # noqa: E402
from soc_agent.normalizers import normalize_alert_payload  # noqa: E402

SCHEMA_VERSION = "soc.validation.pingan_ti_siem_field_audit.v1"
DEFAULT_CORPUS_PATH = ROOT / "validation/compact_zeus/data/full_alert_validation_corpus.pkl"
DEFAULT_OUTPUT_PATH = ROOT / "validation/compact_zeus/data/pingan-ti-siem-field-audit.json"


def build_ti_siem_field_audit(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    source_alert_counts: Counter[str] = Counter()
    source_raw_event_counts: Counter[str] = Counter()
    subtype_alert_counts: Counter[str] = Counter()
    subtype_raw_event_counts: Counter[str] = Counter()
    canonical_target_counts: Counter[str] = Counter()
    role_counts: Counter[str] = Counter()
    semantic_type_counts: Counter[str] = Counter()
    high_value_gap_counts: Counter[str] = Counter()
    sample_ids: dict[str, list[str]] = {}

    parsed_message_count = 0
    network_observation_count = 0
    email_observation_count = 0
    canonical_provenance_count = 0
    raw_payload_mutation_count = 0
    threat_intel_asset_scope_leak_count = 0
    threat_intel_structured_role_claim_count = 0
    siem_directional_network_count = 0
    siem_pipeline_actor_leak_count = 0
    siem_non_high_primary_evidence_count = 0
    siem_unselected_fact_claim_count = 0

    for row in rows:
        payload = _alert_payload(row)
        input_hash = canonical_sha256(payload)
        alert = normalize_alert_payload(payload)
        source_type = alert.source.source_type
        if source_type not in {
            AlertSourceType.THREAT_INTEL,
            AlertSourceType.SIEM,
        }:
            continue

        request = build_analysis_request_for_payload(
            payload,
            sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        )
        if canonical_sha256(payload) != input_hash:
            raw_payload_mutation_count += 1

        source_key = source_type.value
        source_alert_counts[source_key] += 1
        _append_sample_id(sample_ids, source_key, alert.alert_id)
        raw_events = _iter_raw_events(payload)
        source_raw_event_counts[source_key] += len(raw_events)
        parsed_messages = alert.extensions.get("parsed_raw_messages", [])
        parsed_message_count += len(parsed_messages)
        network_observation_count += len(alert.entities.network.observations)
        if alert.entities.email is not None:
            email_observation_count += len(alert.entities.email.observations)
        canonical_provenance_count += len(request.fact_reconstruction.canonical_field_provenance)
        for claim in request.fact_reconstruction.role_claims:
            role_counts[f"{source_key}:{claim.role}:{claim.claim_type.value}"] += 1
        for semantic in alert.extensions.get("source_field_semantics", []):
            if isinstance(semantic, Mapping):
                semantic_type = semantic.get("semantic_type")
                if isinstance(semantic_type, str) and semantic_type:
                    semantic_type_counts[f"{source_key}:{semantic_type}"] += 1
        for gap in request.evidence_coverage.high_value_gaps:
            high_value_gap_counts[gap.rule_id or gap.expected_target] += 1

        if source_type is AlertSourceType.THREAT_INTEL:
            _count_target(
                canonical_target_counts,
                "threat_intel.network_session",
                bool(alert.entities.network.source_ip and alert.entities.network.destination_ip),
            )
            _count_target(
                canonical_target_counts,
                "threat_intel.host",
                bool(alert.entities.host.ip_addresses),
            )
            _count_target(
                canonical_target_counts,
                "threat_intel.ioc",
                bool(alert.entities.threat.iocs),
            )
            _count_target(
                canonical_target_counts,
                "threat_intel.malware",
                bool(alert.entities.threat.malware_family),
            )
            _count_target(
                canonical_target_counts,
                "threat_intel.mitre",
                bool(alert.classification.technique),
            )
            threat_intel_asset_scope_leak_count += sum(1 for value in alert.entities.host.ip_addresses if "/" in value or "-" in value)
            threat_intel_structured_role_claim_count += sum(1 for claim in request.fact_reconstruction.role_claims if claim.source_layer is EvidenceLayer.RAW_STRUCTURED)
            continue

        subtypes = {str(event.get("subtype") or "unknown").strip().lower() for event in raw_events}
        for subtype in subtypes:
            subtype_alert_counts[subtype] += 1
            _append_sample_id(sample_ids, f"siem:{subtype}", alert.alert_id)
        for event in raw_events:
            subtype = str(event.get("subtype") or "unknown").strip().lower()
            subtype_raw_event_counts[subtype] += 1
        selected_subtype = str(raw_events[0].get("subtype") or "unknown").strip().lower() if raw_events else "empty"
        if selected_subtype == "suspicious_email":
            _count_target(
                canonical_target_counts,
                "siem.email",
                alert.entities.email is not None,
            )
        elif selected_subtype == "standard_machine_copy":
            _count_target(
                canonical_target_counts,
                "siem.machine_host",
                bool(alert.entities.host.host_name),
            )
            _count_target(
                canonical_target_counts,
                "siem.machine_ips",
                bool(alert.entities.host.ip_addresses),
            )
        if alert.entities.network.source_ip or alert.entities.network.destination_ip:
            siem_directional_network_count += 1
        if alert.entities.user.username:
            siem_pipeline_actor_leak_count += 1
        evidence_policy = alert.extensions.get("evidence_input_policy")
        selected_path = evidence_policy.get("selected_input_path") if isinstance(evidence_policy, Mapping) else None
        siem_unselected_fact_claim_count += sum(
            1 for claim in request.fact_reconstruction.role_claims if claim.source_layer is EvidenceLayer.RAW_STRUCTURED and (not isinstance(selected_path, str) or not claim.evidence_path.startswith(selected_path))
        )
        if request.primary_evidence is None or request.primary_evidence.layer is not EvidenceLayer.RAW_STRUCTURED or request.primary_evidence.trust_level is not EvidenceTrustLevel.HIGH:
            siem_non_high_primary_evidence_count += 1

    checks = {
        "raw_payload_unchanged": raw_payload_mutation_count == 0,
        "threat_intel_asset_scope_not_host": (threat_intel_asset_scope_leak_count == 0),
        "threat_intel_roles_from_message_only": (threat_intel_structured_role_claim_count == 0),
        "siem_does_not_invent_network_direction": (siem_directional_network_count == 0),
        "siem_pipeline_identity_not_actor": siem_pipeline_actor_leak_count == 0,
        "siem_fact_claims_use_selected_event_only": siem_unselected_fact_claim_count == 0,
        "siem_selected_evidence_is_high_trust_structured": (siem_non_high_primary_evidence_count == 0),
        "known_fields_have_no_high_value_gaps": not high_value_gap_counts,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "passed" if all(checks.values()) else "failed",
        "source_alert_counts": dict(sorted(source_alert_counts.items())),
        "source_raw_event_counts": dict(sorted(source_raw_event_counts.items())),
        "siem_subtype_alert_counts": dict(sorted(subtype_alert_counts.items())),
        "siem_subtype_raw_event_counts": dict(sorted(subtype_raw_event_counts.items())),
        "parsed_message_count": parsed_message_count,
        "network_observation_count": network_observation_count,
        "email_observation_count": email_observation_count,
        "canonical_target_counts": dict(sorted(canonical_target_counts.items())),
        "canonical_provenance_count": canonical_provenance_count,
        "role_counts": dict(sorted(role_counts.items())),
        "semantic_type_counts": dict(sorted(semantic_type_counts.items())),
        "high_value_gap_counts": dict(sorted(high_value_gap_counts.items())),
        "raw_payload_mutation_count": raw_payload_mutation_count,
        "threat_intel_asset_scope_leak_count": (threat_intel_asset_scope_leak_count),
        "threat_intel_structured_role_claim_count": (threat_intel_structured_role_claim_count),
        "siem_directional_network_count": siem_directional_network_count,
        "siem_pipeline_actor_leak_count": siem_pipeline_actor_leak_count,
        "siem_unselected_fact_claim_count": siem_unselected_fact_claim_count,
        "siem_non_high_primary_evidence_count": (siem_non_high_primary_evidence_count),
        "checks": checks,
        "representative_sample_ids": {cohort: values for cohort, values in sorted(sample_ids.items())},
    }


def _alert_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    wrapper = row.get("alert_full_data")
    if not isinstance(wrapper, Mapping):
        raise ValueError("alert_full_data must be an object")
    payload = wrapper.get("alert_data")
    if not isinstance(payload, Mapping):
        raise ValueError("alert_full_data.alert_data must be an object")
    return payload


def _iter_raw_events(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    alert = payload.get("alert")
    if not isinstance(alert, Mapping):
        return []
    result: list[Mapping[str, Any]] = []
    hit_logs = alert.get("hitLog")
    if not isinstance(hit_logs, list):
        return result
    for hit_log in hit_logs:
        if not isinstance(hit_log, Mapping):
            continue
        raw_logs = hit_log.get("zeusRawLogs")
        if not isinstance(raw_logs, list):
            continue
        result.extend(item for item in raw_logs if isinstance(item, Mapping))
    return result


def _count_target(target: Counter[str], name: str, present: bool) -> None:
    if present:
        target[name] += 1


def _append_sample_id(
    target: dict[str, list[str]],
    cohort: str,
    alert_id: str,
    *,
    limit: int = 5,
) -> None:
    values = target.setdefault(cohort, [])
    if alert_id not in values and len(values) < limit:
        values.append(alert_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    frame = load_dataframe_pickle(args.corpus)
    report = build_ti_siem_field_audit(frame.to_dict(orient="records"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "source_alert_counts": report["source_alert_counts"],
                "source_raw_event_counts": report["source_raw_event_counts"],
                "output": str(args.output.resolve().relative_to(ROOT)),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
