from __future__ import annotations

from validation.compact_zeus.memory.build_behavior_fingerprint_audit import (
    build_behavior_fingerprint_audit,
    write_audit_artifacts,
)


def _record(
    alert_id: str,
    *,
    detection_key: str | None = "vendor:rule:r1",
    fingerprint: str | None = "fp-a",
    components: list[str] | None = None,
    endpoint: str | None = "source:10.0.0.1|destination:10.0.0.2",
    action: str | None = "忽略",
    rule_name: str = "Rule one",
    detection_signature: str = "sig-rule-one",
) -> dict:
    components = (
        components
        if components is not None
        else [
            "protocol:tcp",
            "scenario:reverse_connection",
        ]
    )
    return {
        "alert_id": alert_id,
        "sample_origin": "full_alert_sample",
        "topic": "topic-a",
        "source_type": "ndr",
        "source_system": "vendor",
        "product": "sensor",
        "profile_id": "pingan.soc",
        "profile_version": "3",
        "feature_schema_version": "pingan.soc.memory_features.v3",
        "tenant_id": "pingan",
        "environment": "prd",
        "detection_key": detection_key,
        "detection_signature": detection_signature,
        "rule_code": "R1",
        "rule_name": rule_name,
        "category": "command_execution",
        "severity": "high",
        "behavior_fingerprint": fingerprint,
        "behavior_components": components,
        "component_count": len(components),
        "weak_components": [item for item in components if item == "protocol:tcp"],
        "strong_components": [item for item in components if item != "protocol:tcp"],
        "behavior_strength": (
            "strong"
            if any(item != "protocol:tcp" for item in components)
            else "weak_only"
        ),
        "scenario_keys": [
            item.removeprefix("scenario:")
            for item in components
            if item.startswith("scenario:")
        ],
        "role_entities": endpoint.split("|") if endpoint else [],
        "ip_role_entities": endpoint.split("|") if endpoint else [],
        "ip_entities": [],
        "endpoint_signature": endpoint,
        "dual_ip_facet_count": 0,
        "historical_output": {
            "status": "parsed",
            "alert_action": action,
        },
        "ground_label_present": False,
    }


def test_audit_separates_cross_ip_recurrence_from_low_signal_similarity(
    tmp_path,
) -> None:
    records = [
        _record("A-1"),
        _record(
            "A-2",
            endpoint="source:10.0.0.3|destination:10.0.0.4",
            action="转交",
        ),
        _record(
            "A-3",
            fingerprint="fp-b",
            components=["protocol:tcp", "scenario:lateral_movement"],
        ),
        _record(
            "A-4",
            rule_name="Rule one drifted",
            detection_signature="sig-rule-drifted",
        ),
        _record(
            "B-1",
            detection_key="vendor:rule:r2",
            fingerprint="fp-a",
        ),
        _record("MISSING", fingerprint=None, components=["protocol:tcp"]),
    ]

    report = build_behavior_fingerprint_audit(records)

    assert report["status"] == "passed"
    metrics = report["metrics"]
    assert metrics["selected_alert_count"] == 6
    assert metrics["fingerprint_coverage_count"] == 5
    assert metrics["fingerprint_missing_count"] == 1
    assert metrics["cross_ip_recurrent_cohort_count"] == 1
    assert metrics["recurrent_decision_eligible_cohort_count"] == 1
    assert metrics["historical_action_divergent_cohort_count"] == 1
    assert metrics["ambiguous_rule_identity_group_count"] == 1
    assert metrics["ambiguous_rule_identity_cohort_count"] == 0
    assert metrics["legacy_v1_ambiguous_rule_identity_cohort_count"] == 1
    assert metrics["cross_rule_fingerprint_count"] == 1
    assert metrics["low_signal_only_context_alert_pair_count"] == 0
    assert metrics["legacy_v1_low_signal_only_context_alert_pair_count"] == 3
    assert metrics["ground_truth_label_count"] == 0

    cohort = next(
        item
        for item in report["cohorts"]
        if item["detection_key"] == "vendor:rule:r1"
        and item["behavior_fingerprint"] == "fp-a"
    )
    assert cohort["cross_ip"] is True
    assert cohort["historical_action_counts"] == {"忽略": 1, "转交": 1}
    assert cohort["rule_identity_ambiguous"] is False

    assert report["context_only_pairs"] == []
    context_pair = report["legacy_v1_context_only_pairs"][0]
    assert context_pair["shared_components"] == ["protocol:tcp"]
    assert context_pair["low_signal_only"] is True
    assert "ambiguous_detection_identity" in {
        item["finding_type"] for item in report["findings"]
    }

    output_dir = tmp_path / "audit"
    write_audit_artifacts(report, records, output_dir=output_dir)
    assert (output_dir / "fingerprint-audit.json").exists()
    assert (output_dir / "legacy-v1-exact-cohorts.json").exists()
    assert (output_dir / "legacy-v1-context-only-pairs.json").exists()
    assert (output_dir / "alert-fingerprints.jsonl").exists()
    assert (output_dir / "SUMMARY.md").exists()


def test_audit_marks_errors_and_raw_mutation_as_degraded() -> None:
    report = build_behavior_fingerprint_audit(
        [_record("A-1")],
        errors=[{"alert_id": "BROKEN", "error_type": "ValueError"}],
        raw_payload_mutation_count=1,
    )

    assert report["status"] == "degraded"
    assert {item["finding_type"] for item in report["findings"]} >= {
        "extraction_errors",
        "raw_payload_mutation",
    }
