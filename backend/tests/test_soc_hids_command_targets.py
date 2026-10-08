"""Deterministic command evidence; synthetic inputs never execute a shell."""

import json

import pytest

from soc_agent.normalizers import normalize_alert_payload


def payload(*commands):
    messages = []
    for index, command in enumerate(commands):
        fields = {
            "event_type": "web_command",
            "host_name": "test-host",
            "detail.process_name": "bash",
            "detail.hit_rule_name": "exec connection detection",
            "event_content": "bash进程发现异常执行行为，其执行命令为：" + command,
            "detail.process_tree": f"bash(10)->timeout({20 + index})->bash({30 + index})",
        }
        for node, (name, pid, cmd) in enumerate((("bash", 10, "bash"), ("timeout", 20 + index, "timeout 2 " + command), ("bash", 30 + index, command))):
            for key, value in {"proc_name": name, "proc_pid": str(pid), "proc_path": "/usr/bin/" + name, "proc_cmd": cmd, "proc_euidUsername": "root"}.items():
                fields[f"detail.proctree.{node}.{key}"] = value
        messages.append({"message": json.dumps(fields, ensure_ascii=False)})
    return {"tenant_id": "pingan", "alert": {"alertId": "synthetic-hids-targets", "hitLog": [{"topic": "security_qthids", "ruleCode": "test", "zeusRawLogs": messages}]}}


def test_two_command_targets_preserve_subjects_without_wire_observations():
    raw = payload("bash -c exec 3<>/dev/tcp/10.0.0.1/18443", "bash -c exec 3<>/dev/tcp/10.0.0.2/31443")
    before = json.dumps(raw)
    alert = normalize_alert_payload(raw)
    assert json.dumps(raw) == before
    assert alert.entities.process.primary_ref == "entities.process.observations[0].nodes[2]"
    assert alert.entities.process.process_id == 30
    assert alert.entities.process.parent_process_id == 20
    assert alert.entities.process.process_path == "/usr/bin/bash"
    facts = [f for f in alert.entities.supplementary_facts if f.process_behavior]
    assert [(f.process_behavior.destination_ip, f.process_behavior.dst_port) for f in facts] == [("10.0.0.1", 18443), ("10.0.0.2", 31443)]
    assert [f.subject_ref for f in facts] == [f"entities.process.observations[{i}].nodes[2]" for i in range(2)]
    assert all(f.process_behavior_verification == "source_bound_v1" for f in facts)
    assert not alert.entities.network.observations
    assert alert.entities.network.protocol is None
    assert alert.entities.network.direction is None
    assert [d.subject_refs for d in alert.entities.detections] == [[f.subject_ref] for f in facts]


@pytest.mark.parametrize("body", ["exec 3<>/dev/tcp/10.0.0.1/31443", "'exec 3<>/dev/tcp/10.0.0.1/31443'", '"exec 3<>/dev/tcp/10.0.0.1/31443"'])
def test_quoted_or_display_flattened_bash_command_can_prove_literal_target(body):
    alert = normalize_alert_payload(payload("bash -c " + body))
    assert len([f for f in alert.entities.supplementary_facts if f.process_behavior]) == 1


@pytest.mark.parametrize(
    "command",
    [
        "echo /dev/tcp/10.0.0.1/31443",
        "bash -c echo /dev/tcp/10.0.0.1/31443",
        "bash -c exec 3<>/dev/tcp/10.0.0.1/0",
        "bash -c exec 3<>/dev/tcp/10.0.0.1/65536",
        "bash -c exec 3<>/dev/tcp/$HOST/31443",
        "bash -c exec 3<>/dev/tcp/10.0.0.1/31443; exec 4<>/dev/tcp/10.0.0.2/31443",
        "bash -c 'echo exec 3<>/dev/tcp/10.0.0.1/31443'",
    ],
)
def test_unproven_command_target_is_not_promoted_to_a_connection(command):
    alert = normalize_alert_payload(payload(command))
    assert not [f for f in alert.entities.supplementary_facts if f.process_behavior]
    assert not alert.entities.network.observations
    binding = alert.entities.process.observations[0].subject_binding
    assert "command_target_not_proven" in binding.gaps


def test_ambiguous_process_keeps_a_gap_and_no_guessed_primary():
    raw = payload("bash -c exec 3<>/dev/tcp/10.0.0.1/31443")
    message = json.loads(raw["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"])
    message["detail.proctree.0.proc_cmd"] = message["detail.proctree.2.proc_cmd"]
    raw["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] = json.dumps(message)
    alert = normalize_alert_payload(raw)
    assert alert.entities.process.primary_ref is None
    assert alert.entities.process.process_id is None
    assert alert.entities.process.command_line is None
    assert alert.entities.process.observations[0].subject_binding.status == "ambiguous"
    assert not [f for f in alert.entities.supplementary_facts if f.process_behavior]


@pytest.mark.parametrize("type_field", ["event_type", "datatype"])
def test_ambiguous_command_subject_still_cannot_be_promoted_to_wire_traffic(type_field):
    from soc_agent.llm.analyzer import LLMChatResponse
    from soc_agent.llm.normalization import JsonLLMNormalizationReviewer, apply_normalization_changes

    raw = payload("bash -c exec 3<>/dev/tcp/10.0.0.1/31443")
    message = json.loads(raw["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"])
    message[type_field] = message.pop("event_type")
    message["detail.proctree.0.proc_cmd"] = message["detail.proctree.2.proc_cmd"]
    raw["alert"]["hitLog"][0]["zeusRawLogs"][0]["message"] = json.dumps(message)
    alert = normalize_alert_payload(raw)

    class Client:
        def complete(self, messages, *, model_name):
            return LLMChatResponse(
                content=json.dumps(
                    {
                        "objects": [
                            {"id": "network", "kind": "network", "attributes": {"destination_ip": "10.0.0.1", "dst_port": 31443, "protocol": "tcp", "direction": "to_server"}, "source_id": "L0", "source_quote": "/dev/tcp/10.0.0.1/31443"}
                        ]
                    }
                )
            )

    reviewer = JsonLLMNormalizationReviewer(client=Client(), model_name="fake")
    request = reviewer.prepare(alert)
    result = reviewer.review(alert, request)
    updated = apply_normalization_changes(alert, request, result)
    assert not updated.entities.network.observations
    assert any("command_target_is_not_wire_observation" in issue for issue in result.issues)
