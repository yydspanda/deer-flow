"""Additive process facts preserve old payloads and constrain new bindings."""

import pytest
from pydantic import ValidationError

from soc_agent.contracts import ProcessEntityRef, ProcessObservationRef
from soc_agent.contracts.normalization import ProcessBehaviorDescriptor, ProcessSubjectBinding, SupplementaryFactRef


def test_process_fact_metadata_is_absent_from_legacy_serialization():
    process = ProcessEntityRef()
    observation = ProcessObservationRef(observation_id="old", evidence_path="message")
    fact = SupplementaryFactRef(observation_id="old", evidence_path="message", event_scope_id="message", name="old", value="old", meaning="old")
    assert "primary_ref" not in process.model_dump(mode="json")
    assert "subject_binding" not in observation.model_dump(mode="json")
    assert "field_sources" not in observation.model_dump(mode="json")
    assert "process_behavior" not in fact.model_dump(mode="json")
    assert "process_behavior_verification" not in fact.model_dump(mode="json")


@pytest.mark.parametrize("values", [{"status": "resolved"}, {"status": "ambiguous", "node_index": 0}, {"status": "conflict", "parent_node_index": 0}])
def test_process_binding_cannot_claim_an_unresolved_subject(values):
    with pytest.raises(ValidationError):
        ProcessSubjectBinding(reason="test", **values)


@pytest.mark.parametrize("port", [0, 65536, True])
def test_command_targets_require_a_real_numeric_port(port):
    with pytest.raises(ValidationError):
        ProcessBehaviorDescriptor(kind="shell_tcp_connection_target", protocol="tcp", destination_ip="10.0.0.1", dst_port=port)


def test_command_descriptor_does_not_admit_wire_direction_or_success():
    with pytest.raises(ValidationError):
        ProcessBehaviorDescriptor(kind="shell_tcp_connection_target", protocol="tcp", destination_ip="10.0.0.1", dst_port=31443, direction="to_server", success=True)
