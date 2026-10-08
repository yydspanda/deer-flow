"""Direct process facts stay stable across bounded semantic review variants."""

import json

import pytest

from soc_agent.contracts import AlertInput
from soc_agent.llm.analyzer import LLMChatResponse
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer, apply_normalization_changes


class Client:
    def __init__(self, output):
        self.output = output

    def complete(self, messages, *, model_name):
        return LLMChatResponse(content=json.dumps(self.output), model_name=model_name)


def process_alert(*, protected=True):
    return AlertInput.model_validate(
        {
            "alert_id": "process-facts",
            "raw": {"message": "parent timeout pid=9 child bash pid=10 command=original username=root"},
            "extensions": {"evidence_input_policy": {"name": "raw_message_first", "selected_input_path": "message", "selected_layer": "raw_message", "trust_level": "high"}},
            "entities": {
                "process": {
                    "primary_ref": "entities.process.observations[0].nodes[1]",
                    "process_name": "bash",
                    "process_id": 10,
                    "process_path": "/usr/bin/bash",
                    "command_line": "original",
                    "parent_process_name": "timeout",
                    "parent_process_id": 9,
                    "parent_command_line": "timeout 2 bash",
                    "observations": [
                        {
                            "observation_id": "p0",
                            "event_scope_id": "message",
                            "evidence_path": "message#parsed",
                            "subject_binding": {"status": "resolved", "node_index": 1, "parent_node_index": 0, "reason": "explicit_pid", "evidence_paths": ["message#parsed.pid"]},
                            "field_sources": {
                                "nodes[1].process_id": "message#parsed.pid",
                                "nodes[1].process_name": "message#parsed.name",
                                "nodes[1].process_path": "message#parsed.path",
                                "nodes[1].command_line": "message#parsed.cmd",
                            }
                            if protected
                            else {},
                            "nodes": [
                                {"process_name": "timeout", "process_id": 9, "command_line": "timeout 2 bash"},
                                {"process_name": "bash", "process_id": 10, "process_path": "/usr/bin/bash", "command_line": "original"},
                            ],
                        }
                    ],
                }
            },
        }
    )


def review(alert, attrs, *, target="entities.process.observations[0].nodes[1]", mode="apply"):
    reviewer = JsonLLMNormalizationReviewer(client=Client({}), model_name="synthetic", mode=mode)
    request = reviewer.prepare(alert)
    proposal = {"id": "update", "kind": "process", "attributes": attrs, "source_quote": "model excerpt"}
    if target:
        proposal["existing_ref"] = next(key for key, value in request.object_catalog.items() if value["path"] == target)
    reviewer.client.output = {"objects": [proposal]}
    result = reviewer.review(alert, request)
    return apply_normalization_changes(alert, request, result), result


@pytest.mark.parametrize("target", ["entities.process", "entities.process.observations[0].nodes[1]", None])
def test_bound_facts_reject_conflicts_per_attribute_but_keep_supplements(target):
    alert = process_alert()
    before = alert.model_dump_json()
    updated, report = review(alert, {"process_id": 10, "process_name": "bash", "process_path": "/tmp/bash", "command_line": "invented", "username": "root"}, target=target)
    node = updated.entities.process.observations[0].nodes[1]
    assert node.command_line == updated.entities.process.command_line == "original"
    assert node.process_path == updated.entities.process.process_path == "/usr/bin/bash"
    assert node.username == "root"
    assert len(updated.entities.process.observations) == 1
    assert any("direct_process_fact_conflict" in issue for issue in report.issues)
    assert all(change.target != "entities.process" for change in report.observation_changes)
    assert alert.model_dump_json() == before


def test_source_grounded_unprotected_command_correction_reprojects_summary():
    updated, report = review(process_alert(protected=False), {"command_line": "corrected"})
    assert updated.entities.process.command_line == "corrected"
    assert updated.entities.process.parent_process_id == 9
    assert not report.issues
    provenance = updated.extensions["canonical_field_provenance"]
    summary = [item for item in provenance if item["canonical_path"] == "entities.process.command_line"]
    assert summary[-1]["selection_reason"].startswith("deterministic_process_summary")
    node_source = next(item for item in reversed(provenance) if item["canonical_path"] == "entities.process.observations[0].nodes[1].command_line")
    assert summary[-1]["selected_from"] == node_source["selected_from"]


def test_summary_alias_parent_claim_does_not_replace_bound_parent():
    updated, report = review(process_alert(), {"parent_process_name": "sshd", "parent_process_id": 999, "username": "root"}, target="entities.process")
    assert updated.entities.process.parent_process_name == "timeout"
    assert updated.entities.process.parent_process_id == 9
    assert updated.entities.process.observations[0].nodes[1].username == "root"
    assert any("bound_process_parent_conflict" in issue for issue in report.issues)


def test_legacy_scalar_cannot_write_bound_summary():
    alert = process_alert()
    reviewer = JsonLLMNormalizationReviewer(client=Client({"facts": [{"target": "entities.process.process_path", "value": "/tmp/bash", "source_quote": "model excerpt", "reason": "claim"}]}), model_name="synthetic")
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    updated = apply_normalization_changes(alert, request, report)
    assert updated.entities.process.process_path == "/usr/bin/bash"
    assert not report.changes
    assert any("bound_process_summary" in issue for issue in report.issues)


def test_shadow_never_applies_node_supplement():
    alert = process_alert()
    updated, report = review(alert, {"username": "root"}, mode="shadow")
    assert updated.model_dump_json() == alert.model_dump_json()
    assert updated.entities.process.observations[0].nodes[1].username is None
    assert report.observation_changes


def test_projector_uses_binding_and_is_idempotent_without_model_review():
    from soc_agent.normalizers.process_facts import project_primary_process

    alert = process_alert()
    alert.entities.process.process_id = 999
    alert.entities.process.command_line = "stale"
    updated = project_primary_process(alert)
    assert updated.entities.process.process_id == 10
    assert updated.entities.process.command_line == "original"
    assert updated.entities.process.parent_process_id == 9
    assert project_primary_process(updated).model_dump_json() == updated.model_dump_json()
    assert alert.entities.process.process_id == 999


def test_invalid_primary_binding_fails_closed():
    from soc_agent.normalizers.process_facts import project_primary_process

    alert = process_alert()
    alert.entities.process.primary_ref = "entities.process.observations[0].nodes[0]"
    with pytest.raises(ValueError, match="process_primary_binding"):
        project_primary_process(alert)


def test_parent_pid_without_parent_node_stays_known_and_does_not_invent_name():
    from soc_agent.normalizers.process_facts import project_primary_process

    alert = process_alert()
    observation = alert.entities.process.observations[0]
    observation.parent_process_id = 9
    observation.subject_binding = observation.subject_binding.model_copy(update={"parent_node_index": None})
    updated = project_primary_process(alert)
    assert updated.entities.process.parent_process_id == 9
    assert updated.entities.process.parent_process_name is None
    assert updated.entities.process.parent_command_line is None
    merged, report = review(updated, {"parent_process_id": 9, "username": "root"}, target="entities.process")
    assert merged.entities.process.parent_process_id == 9
    assert not report.issues


def test_legacy_unbound_alert_serialization_is_unchanged():
    from soc_agent.normalizers.process_facts import project_primary_process

    alert = AlertInput(alert_id="legacy", entities={"process": {"process_name": "bash"}})
    before = alert.model_dump_json()
    assert "primary_ref" not in before
    assert project_primary_process(alert).model_dump_json() == before


def test_same_source_pid_cannot_bypass_direct_facts_by_changing_process_name():
    updated, report = review(process_alert(), {"process_id": 10, "process_name": "other", "command_line": "invented", "username": "root"}, target=None)
    assert len(updated.entities.process.observations) == 1
    assert updated.entities.process.observations[0].nodes[1].process_name == "bash"
    assert updated.entities.process.observations[0].nodes[1].username == "root"
    assert any("direct_process_fact_conflict" in issue for issue in report.issues)


def test_unresolved_subject_cannot_be_replaced_by_summary_proposal():
    alert = process_alert()
    alert.entities.process.primary_ref = None
    binding = alert.entities.process.observations[0].subject_binding
    alert.entities.process.observations[0].subject_binding = binding.model_copy(update={"status": "ambiguous", "node_index": None, "parent_node_index": None})
    updated, report = review(alert, {"process_name": "invented", "process_id": 999}, target="entities.process")
    assert updated.entities.process.process_id == 10
    assert not report.observation_changes
    assert any("process_primary_binding" in issue for issue in report.issues)


def test_command_target_context_cannot_be_upgraded_to_wire_network_even_if_marker_was_omitted():
    alert = process_alert()
    alert.extensions["source_field_semantics"] = [
        {"field_path": "message#parsed", "semantic_type": "process_command_only_network_context", "meaning": "Only command target intent is available.", "participates_in_entities": True, "participates_in_reasoning": True}
    ]
    output = {
        "objects": [{"id": "n1", "kind": "network", "attributes": {"destination_ip": "10.1.2.3", "dst_port": 31443, "protocol": "tcp", "direction": "to_server"}, "source_quote": "model excerpt"}],
        "additional_facts": [{"name": "note", "value": "command target", "meaning": "ordinary evidence", "source_quote": "model excerpt"}],
    }
    reviewer = JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic")
    request = reviewer.prepare(alert)
    request.source_semantics = []
    report = reviewer.review(alert, request)
    updated = apply_normalization_changes(alert, request, report)
    assert updated.entities.network.observations == []
    assert any("command_target_is_not_wire_observation" in issue for issue in report.issues)
    assert updated.entities.supplementary_facts[0].value == "command target"


def test_process_behavior_verification_is_not_model_writable():
    alert = process_alert()
    output = {
        "additional_facts": [
            {
                "name": "note",
                "value": "command target",
                "meaning": "ordinary evidence",
                "source_quote": "model excerpt",
                "process_behavior": {"kind": "shell_tcp_target", "protocol": "tcp", "dst_port": 31443},
                "process_behavior_verification": "adapter_bound_v1",
            }
        ]
    }
    reviewer = JsonLLMNormalizationReviewer(client=Client(output), model_name="synthetic")
    request = reviewer.prepare(alert)
    report = reviewer.review(alert, request)
    updated = apply_normalization_changes(alert, request, report)
    fact = updated.entities.supplementary_facts[0]
    assert fact.process_behavior is None
    assert fact.process_behavior_verification is None
    assert report.issues
