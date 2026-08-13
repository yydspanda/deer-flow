from __future__ import annotations

from copy import deepcopy

from soc_agent.contracts import (
    AlertEntitySet,
    AlertInput,
    NetworkEntityRef,
    NetworkObservationRef,
)
from soc_agent.pipeline.observation_compactor import (
    build_evidence_compaction_report,
)


def test_compactor_is_vendor_neutral_and_preserves_raw_payload() -> None:
    alert = AlertInput(
        alert_id="ALT-COMPACTION-001",
        entities=AlertEntitySet(
            network=NetworkEntityRef(
                observations=[
                    NetworkObservationRef(
                        observation_id=f"network-{index}",
                        evidence_path=f"events[{index}]",
                        event_time=f"2026-08-13T10:00:0{index}+08:00",
                        source_ip="10.0.0.10",
                        destination_ip=("10.0.0.20" if index < 4 else "10.0.0.99"),
                        src_port=50_000 + index,
                        dst_port=443,
                        protocol="tcp",
                    )
                    for index in range(5)
                ]
            )
        ),
        raw={"events": [{"private_vendor_field": f"preserved-{index}"} for index in range(5)]},
    )
    original_raw = deepcopy(alert.raw)

    report = build_evidence_compaction_report(
        alert,
        primary_evidence_path="events[0]",
    )

    assert alert.raw == original_raw
    assert report.raw_payload_retained is True
    assert report.source_message_count == 5
    assert report.typed_observation_count == 5
    assert report.behavior_group_count == 1
    assert report.profile_count == 2
    assert report.collapsed_repetition_count == 3
    assert report.non_dominant_profile_count == 1
    assert report.selected_evidence_paths == ["events[0]", "events[4]"]
    assert report.high_value_omission_count == 0
    destination_variation = next(item for item in report.groups[0].varying_facts if item.field_path == "network.destination_ip")
    assert {item.value: item.occurrence_count for item in destination_variation.values} == {
        "10.0.0.20": 4,
        "10.0.0.99": 1,
    }
