from __future__ import annotations

import json
from copy import deepcopy

import pytest

from soc_agent.normalizers import normalize_alert_payload
from soc_agent.normalizers.pingan_hids import build_hids_process_observations, hids_primary_process
from soc_agent.normalizers.pingan_messages import parse_pingan_raw_message

COMMAND = "bash -c exec 3<>/dev/tcp/10.10.20.30/31443"
SOURCE = "alert.hitLog[0].zeusRawLogs[0].message"


def _fields() -> dict[str, str]:
    nodes = [
        ("systemd", 1, "/usr/lib/systemd/systemd", "/usr/lib/systemd/systemd --system"),
        ("crond", 2, "/usr/sbin/crond", "/usr/sbin/crond -n"),
        ("sh", 3, "/usr/bin/bash", "sh -c curl -sS http://10.10.1.2/install.sh | bash"),
        ("bash", 4, "/usr/bin/bash", "bash"),
        ("bash", 5, "/usr/bin/bash", "bash install.sh install"),
        ("timeout", 6, "/usr/bin/timeout", "timeout 2 " + COMMAND),
        ("bash", 7, "/usr/bin/bash", COMMAND),
    ]
    fields = {
        "event_type": "web_command",
        "host_name": "synthetic-hids",
        "detail.process_name": "bash",
        "detail.process_tree": "->".join(f"{name}({pid})" for name, pid, _, _ in nodes),
        "event_content": "bash进程发现异常执行行为，其执行命令为：" + COMMAND,
    }
    for index, (name, pid, path, command) in enumerate(nodes):
        prefix = f"detail.proctree.{index}."
        fields.update(
            {
                prefix + "proc_name": name,
                prefix + "proc_pid": str(pid),
                prefix + "proc_path": path,
                prefix + "proc_cmd": command,
                prefix + "proc_euidUsername": "root",
            }
        )
    return fields


def _message(fields: dict[str, str]) -> str:
    return " ".join(f'{key}="{value}"' for key, value in fields.items())


def _parsed(fields: dict[str, str]):
    parsed = parse_pingan_raw_message(_message(fields), source_path=SOURCE)
    assert parsed is not None
    return parsed


def test_indexed_nodes_supply_complete_facts_without_semantic_review() -> None:
    parsed = _parsed(_fields())
    nodes = build_hids_process_observations([parsed])[0]["nodes"]

    assert len(nodes) == 7
    assert nodes[3] == {"process_name": "bash", "process_id": 4, "process_path": "/usr/bin/bash", "command_line": "bash", "username": "root"}
    assert nodes[-1] == {"process_name": "bash", "process_id": 7, "process_path": "/usr/bin/bash", "command_line": COMMAND, "username": "root"}
    assert hids_primary_process([parsed]) == {
        "process_name": "bash",
        "process_id": 7,
        "process_path": "/usr/bin/bash",
        "command_line": COMMAND,
        "parent_process_name": "timeout",
        "parent_process_id": 6,
        "parent_command_line": "timeout 2 " + COMMAND,
    }


def test_indexed_facts_have_exact_field_provenance() -> None:
    alert = normalize_alert_payload({"alert": {"alertId": "synthetic", "hitLog": [{"topic": "security_qthids", "zeusRawLogs": [{"message": _message(_fields())}]}]}})
    provenance = {item["canonical_path"]: item for item in alert.extensions["canonical_field_provenance"]}

    for target, source in {
        "entities.process.process_id": "detail.proctree.6.proc_pid",
        "entities.process.process_path": "detail.proctree.6.proc_path",
        "entities.process.command_line": "detail.proctree.6.proc_cmd",
        "entities.process.parent_process_id": "detail.proctree.5.proc_pid",
        "entities.process.parent_command_line": "detail.proctree.5.proc_cmd",
        "entities.process.observations[0].nodes[3].command_line": "detail.proctree.3.proc_cmd",
        "entities.process.observations[0].nodes[6].username": "detail.proctree.6.proc_euidUsername",
    }.items():
        selected_from = provenance[target]["selected_from"]
        if selected_from.startswith("entities.process.observations["):
            selected_from = provenance[selected_from]["selected_from"]
        assert selected_from == f"{SOURCE}#parsed.{source}"


@pytest.mark.parametrize("cause", ["duplicate_command", "pid_command_conflict", "tree_identity_conflict"])
def test_conflicting_or_ambiguous_subject_does_not_guess_a_pid(cause: str) -> None:
    fields = _fields()
    if cause == "duplicate_command":
        fields["detail.proctree.3.proc_cmd"] = COMMAND
    elif cause == "pid_command_conflict":
        fields["detail.pid"] = "4"
    else:
        fields["detail.proctree.6.proc_pid"] = "99"
    parsed = _parsed(fields)
    before = deepcopy(parsed.fields)
    primary = hids_primary_process([parsed])

    assert primary.get("process_id") is None
    assert primary.get("parent_process_id") is None
    assert parsed.fields == before


def test_explicit_pid_selects_the_matching_node_and_parent() -> None:
    fields = _fields()
    fields["detail.pid"] = "5"
    fields["event_content"] = "bash进程发现异常执行行为，其执行命令为：bash install.sh install"
    primary = hids_primary_process([_parsed(fields)])

    assert primary["process_id"] == 5
    assert primary["command_line"] == "bash install.sh install"
    assert primary["parent_process_id"] == 4
    assert primary["parent_command_line"] == "bash"


def test_indexed_nodes_work_without_the_redundant_short_tree() -> None:
    fields = _fields()
    del fields["detail.process_tree"]
    assert hids_primary_process([_parsed(fields)])["process_id"] == 7
    assert len(build_hids_process_observations([_parsed(fields)])[0]["nodes"]) == 7


def test_short_tree_repeated_name_does_not_overwrite_the_first_ancestor() -> None:
    parsed = _parsed({"detail.process_name": "bash", "detail.process_tree": "sh(1)->bash(2)->bash(3)", "event_content": "bash进程发现异常执行行为，其执行命令为：" + COMMAND})
    primary = hids_primary_process([parsed])
    nodes = build_hids_process_observations([parsed])[0]["nodes"]

    assert primary.get("process_id") is None
    assert primary.get("parent_process_id") is None
    assert all(node.get("command_line") is None for node in nodes)


def test_short_tree_unique_name_keeps_legacy_command_binding() -> None:
    parsed = _parsed({"detail.process_name": "bash", "detail.process_tree": "sh(1)->bash(2)", "event_content": "bash进程发现异常执行行为，其执行命令为：" + COMMAND})
    primary = hids_primary_process([parsed])
    assert primary["process_id"] == 2
    assert primary["parent_process_id"] == 1
    assert primary["command_line"] == COMMAND


def test_explicit_pid_does_not_require_a_redundant_process_name() -> None:
    fields = _fields()
    del fields["detail.process_name"]
    fields["detail.pid"] = "7"
    primary = hids_primary_process([_parsed(fields)])
    assert primary["process_id"] == 7
    assert primary["process_path"] == "/usr/bin/bash"
    assert primary["parent_process_id"] == 6


def test_parent_conflict_remains_visible_in_the_binding() -> None:
    from soc_agent.normalizers.pingan_hids import hids_process_subject_binding

    fields = _fields()
    fields.update({"detail.ppname": "sh", "detail.ppid": "3"})
    parsed = _parsed(fields)
    observation = build_hids_process_observations([parsed])[0]
    assert hids_process_subject_binding(parsed, observation)["status"] == "conflict"
    assert hids_primary_process([parsed]).get("parent_process_id") is None


def test_indexed_only_missing_ancestor_does_not_invent_an_adjacent_parent() -> None:
    fields = _fields()
    del fields["detail.process_tree"]
    fields = {key: value for key, value in fields.items() if not key.startswith("detail.proctree.5.")}
    primary = hids_primary_process([_parsed(fields)])
    assert primary["process_id"] == 7
    assert primary.get("parent_process_id") is None


def test_subject_and_field_sources_are_explicit_and_source_scoped() -> None:
    observation = build_hids_process_observations([_parsed(_fields())])[0]
    assert observation["subject_binding"]["status"] == "resolved"
    assert observation["subject_binding"]["node_index"] == 6
    assert observation["subject_binding"]["parent_node_index"] == 5
    assert observation["field_sources"]["nodes[6].process_id"] == f"{SOURCE}#parsed.detail.proctree.6.proc_pid"
    assert observation["field_sources"]["nodes[6].command_line"] == f"{SOURCE}#parsed.detail.proctree.6.proc_cmd"


def test_repeated_name_only_chain_keeps_ambiguity() -> None:
    parsed = _parsed({"detail.process_name": "bash", "detail.process_chain": "sh->bash->bash", "event_content": "bash进程发现异常执行行为，其执行命令为：" + COMMAND})
    observation = build_hids_process_observations([parsed])[0]
    assert [node["process_name"] for node in observation["nodes"]] == ["sh", "bash", "bash"]
    assert observation["subject_binding"]["status"] == "ambiguous"


def test_conflicting_duplicate_pid_keeps_both_observed_commands() -> None:
    fields = _fields()
    fields["detail.proctree.6.proc_pid"] = "4"
    parsed = _parsed(fields)
    observation = build_hids_process_observations([parsed])[0]
    assert observation["subject_binding"]["status"] == "conflict"
    assert {node.get("command_line") for node in observation["nodes"] if node.get("process_id") == 4} == {"bash", COMMAND}


def test_unique_command_without_pids_keeps_its_own_parent() -> None:
    fields = {key: value for key, value in _fields().items() if key != "detail.process_tree" and not key.endswith(".proc_pid")}
    observation = build_hids_process_observations([_parsed(fields)])[0]
    assert observation["subject_binding"]["node_index"] == 6
    assert observation["subject_binding"]["parent_node_index"] == 5
    assert hids_primary_process([_parsed(fields)])["parent_process_name"] == "timeout"


def event_tree_fields() -> dict[str, str]:
    """The event names Java while its abbreviated tree ends at a descendant."""
    nodes = [
        ("systemd", 1, "/usr/lib/systemd/systemd", "systemd --system"),
        ("sidecar", 10, "/opt/sidecar", "sidecar --run"),
        ("java", 20, "/opt/jdk/bin/java", "java -jar /srv/service.jar"),
        ("sh", 30, "/usr/bin/bash", "sh /srv/check.sh"),
        ("timeout", 40, "/usr/bin/timeout", "timeout 2 telnet 192.0.2.10 443"),
        ("telnet", 50, "/usr/bin/telnet", "telnet 192.0.2.10 443"),
    ]
    fields = {
        "event_type": "web_command",
        "host_name": "synthetic-event-tree",
        "detail.process_name": "java",
        "detail.hit_rule_name": "[web命令执行]Java服务执行网络连接命令",
        "event_content": "java进程发现异常执行行为，其进程树为：java(20)->telnet(50)",
        "detail.process_tree": "->".join(f"{name}({pid})" for name, pid, _, _ in nodes),
    }
    for index, (name, pid, path, command) in enumerate(nodes):
        for key, value in {"proc_name": name, "proc_pid": str(pid), "proc_path": path, "proc_cmd": command, "proc_euidUsername": "root"}.items():
            fields[f"detail.proctree.{index}.{key}"] = value
    return fields


def event_tree_payload(*messages: dict[str, str]) -> dict:
    return {
        "tenant_id": "pingan",
        "alert": {
            "alertId": "synthetic-event-tree",
            "hitLog": [{"topic": "security_qthids", "ruleCode": "synthetic-java", "zeusRawLogs": [{"message": json.dumps(fields, ensure_ascii=False)} for fields in messages]}],
        },
    }


@pytest.mark.parametrize("arrow", ["->", "-&gt;"])
def test_event_tree_binds_declared_java_with_its_real_command_and_parent(arrow: str) -> None:
    fields = event_tree_fields()
    fields["event_content"] = fields["event_content"].replace("->", arrow)
    fields["detail.process_tree"] = fields["detail.process_tree"].replace("->", arrow)
    raw = event_tree_payload(fields)
    before = deepcopy(raw)
    alert = normalize_alert_payload(raw)
    process = alert.entities.process
    observation = process.observations[0]

    assert raw == before
    assert observation.subject_binding.status == "resolved"
    assert observation.subject_binding.node_index == 2
    assert observation.subject_binding.parent_node_index == 1
    assert process.primary_ref == "entities.process.observations[0].nodes[2]"
    assert (process.process_name, process.process_id, process.command_line) == ("java", 20, "java -jar /srv/service.jar")
    assert (process.parent_process_name, process.parent_process_id, process.parent_command_line) == ("sidecar", 10, "sidecar --run")
    assert observation.nodes[-1].command_line == "telnet 192.0.2.10 443"
    assert f"{SOURCE}#parsed.event_content" in observation.subject_binding.evidence_paths
    assert observation.field_sources["nodes[2].command_line"] == f"{SOURCE}#parsed.detail.proctree.2.proc_cmd"
    assert [item.subject_refs for item in alert.entities.detections] == [[process.primary_ref]]
    assert not alert.entities.network.observations


@pytest.mark.parametrize("explicit_pid", [None, "20", "30"])
def test_event_tree_repeated_subject_name_requires_pid_disambiguation(explicit_pid: str | None) -> None:
    fields = event_tree_fields()
    fields["detail.process_tree"] = fields["detail.process_tree"].replace("sh(30)", "java(30)")
    fields["detail.proctree.3.proc_name"] = "java"
    fields["detail.proctree.3.proc_path"] = "/opt/jdk/bin/java"
    fields["detail.proctree.3.proc_cmd"] = "java -jar /srv/worker.jar"
    fields["event_content"] = "java进程发现异常执行行为，其进程树为：java(20)->java(30)->telnet(50)"
    if explicit_pid is not None:
        fields["detail.pid"] = explicit_pid
    alert = normalize_alert_payload(event_tree_payload(fields))
    process = alert.entities.process
    if explicit_pid is None:
        assert process.observations[0].subject_binding.status == "ambiguous"
        assert process.primary_ref is None
    else:
        assert process.observations[0].subject_binding.status == "resolved"
        assert process.process_id == int(explicit_pid)
        assert process.parent_process_id == (10 if explicit_pid == "20" else 20)
        assert process.command_line == ("java -jar /srv/service.jar" if explicit_pid == "20" else "java -jar /srv/worker.jar")


def test_event_tree_unique_subject_does_not_require_a_unique_name_in_the_full_tree() -> None:
    fields = event_tree_fields()
    fields["detail.process_tree"] = fields["detail.process_tree"].replace("sh(30)", "java(30)")
    fields["detail.proctree.3.proc_name"] = "java"
    fields["detail.proctree.3.proc_path"] = "/opt/jdk/bin/java"
    fields["detail.proctree.3.proc_cmd"] = "java -jar /srv/worker.jar"
    alert = normalize_alert_payload(event_tree_payload(fields))
    assert alert.entities.process.observations[0].subject_binding.status == "resolved"
    assert alert.entities.process.process_id == 20
    assert alert.entities.process.command_line == "java -jar /srv/service.jar"


@pytest.mark.parametrize(
    "changes",
    [
        {"detail.process_name": "telnet"},
        {"event_content": "python进程发现异常执行行为，其进程树为：java(20)->telnet(50)"},
        {"detail.pid": "30"},
        {"detail.pid": "999"},
        {"detail.cmd": "telnet 192.0.2.10 443"},
        {"detail.pid": "20", "detail.cmd": "java -jar /different.jar"},
        {"detail.pid": "20", "event_content": "java进程发现异常执行行为，其进程树为：java(21)->telnet(50)"},
        {"detail.pid": "20", "event_content": "java进程发现异常执行行为，其进程树为：java(20)->telnet(51)"},
        {"detail.pid": "20", "event_content": "java进程发现异常执行行为，其进程树为：java(20)->nmap(50)"},
        {"detail.pid": "20", "event_content": "java进程发现异常执行行为，其进程树为：telnet(50)->java(20)"},
        {"detail.pid": "20", "event_content": "java进程发现异常执行行为，其进程树为：java(20)->java(20)->telnet(50)"},
        {"detail.proctree.5.proc_pid": "20"},
    ],
    ids=["declared-name", "prefix-name", "different-pid", "absent-pid", "child-command", "pid-command", "pid-short-tree", "missing-descendant", "member-name-conflict", "reversed-chain", "duplicate-short-pid", "duplicate-full-pid"],
)
def test_event_tree_conflicts_never_select_a_convenient_subject(changes: dict[str, str]) -> None:
    fields = {**event_tree_fields(), **changes}
    alert = normalize_alert_payload(event_tree_payload(fields))
    assert alert.entities.process.observations[0].subject_binding.status != "resolved"
    assert alert.entities.process.primary_ref is None
    assert alert.entities.process.process_id is None


@pytest.mark.parametrize(
    "event_content",
    [
        "日志片段：java进程发现异常执行行为，其进程树为：java(20)->telnet(50)",
        "java进程发现异常执行行为，其进程树为：java(20)->telnet(50); unrelated suffix",
        "java进程发现异常执行行为，其执行命令为：echo java进程发现异常执行行为，其进程树为：java(20)->telnet(50)",
        "java进程发现异常执行行为，其进程树为：java->telnet(50)",
    ],
    ids=["embedded-prose", "trailing-text", "template-inside-command", "missing-short-pid"],
)
def test_event_tree_requires_the_complete_subject_template(event_content: str) -> None:
    fields = {**event_tree_fields(), "event_content": event_content}
    alert = normalize_alert_payload(event_tree_payload(fields))
    assert alert.entities.process.observations[0].subject_binding.status != "resolved"
    assert alert.entities.process.primary_ref is None


@pytest.mark.parametrize("limit", ["node_count", "text_length"])
def test_event_tree_input_limits_cannot_be_bypassed_by_an_explicit_pid(limit: str) -> None:
    fields = event_tree_fields()
    fields["detail.pid"] = "20"
    if limit == "text_length":
        fields["event_content"] = "java进程发现异常执行行为，其进程树为：java(20)" + " " * 9000 + "->telnet(50)"
    else:
        # Every extra member exists in the full ancestry: only the bounded
        # event-summary size makes this otherwise consistent input inadmissible.
        for index in range(31):
            pid = 100 + index
            for key, value in {"proc_name": "worker", "proc_pid": str(pid), "proc_path": "/usr/bin/worker", "proc_cmd": "worker --check"}.items():
                fields[f"detail.proctree.{6 + index}.{key}"] = value
            fields["event_content"] += f"->worker({pid})"
            fields["detail.process_tree"] += f"->worker({pid})"
    alert = normalize_alert_payload(event_tree_payload(fields))
    assert alert.entities.process.observations[0].subject_binding.status != "resolved"
    assert alert.entities.process.observations[0].subject_binding.gaps
    assert alert.entities.process.primary_ref is None
    assert alert.entities.process.process_id is None
    assert not alert.entities.detections


def test_event_tree_binding_records_the_summary_full_ancestry_and_selected_node_sources() -> None:
    alert = normalize_alert_payload(event_tree_payload(event_tree_fields()))
    binding = alert.entities.process.observations[0].subject_binding
    assert binding.status == "resolved"
    assert {
        f"{SOURCE}#parsed.event_content",
        f"{SOURCE}#parsed.detail.process_tree",
        f"{SOURCE}#parsed.detail.proctree.2.proc_pid",
        f"{SOURCE}#parsed.detail.proctree.2.proc_cmd",
    } <= set(binding.evidence_paths)


def test_event_tree_cannot_borrow_a_full_tree_from_another_source() -> None:
    partial = {key: value for key, value in event_tree_fields().items() if key != "detail.process_tree" and not key.startswith("detail.proctree.2.")}
    raw = event_tree_payload(partial, event_tree_fields())
    alert = normalize_alert_payload(raw)
    first, second = alert.entities.process.observations
    assert first.subject_binding.status != "resolved"
    assert alert.entities.process.primary_ref is None
    assert second.subject_binding.status == "resolved"
    assert [item.subject_refs for item in alert.entities.detections] == [["entities.process.observations[1].nodes[2]"]]
    assert all(path.startswith("alert.hitLog[0].zeusRawLogs[1].message#") for path in second.subject_binding.evidence_paths)


def test_event_tree_without_indexed_evidence_retains_legacy_binding() -> None:
    fields = {key: value for key, value in event_tree_fields().items() if key != "detail.process_tree" and not key.startswith("detail.proctree.")}
    alert = normalize_alert_payload(event_tree_payload(fields))
    assert alert.entities.process.process_id == 20
    assert alert.entities.process.observations[0].subject_binding.reason == "legacy_unique_process_name"


def test_event_tree_new_binding_invalidates_old_review_snapshot_without_a_model_call() -> None:
    from soc_agent.contracts import AnalysisRun, NormalizationAssistResult
    from soc_agent.contracts.normalization import ProcessSubjectBinding
    from soc_agent.core.normalization_snapshot import find_snapshot
    from soc_agent.llm.normalization import JsonLLMNormalizationReviewer
    from soc_agent.utils.hashing import stable_hash

    class NoModelClient:
        def complete(self, *args, **kwargs):
            raise AssertionError("Snapshot preparation must not call a model")

    raw = event_tree_payload(event_tree_fields())
    current = normalize_alert_payload(raw)
    assert current.entities.process.observations[0].subject_binding.status == "resolved"
    previous_alert = current.model_copy(deep=True)
    process = previous_alert.entities.process
    for key in ("primary_ref", "process_name", "process_id", "process_path", "command_line", "parent_process_name", "parent_process_id", "parent_command_line"):
        setattr(process, key, None)
    process.observations[0].subject_binding = ProcessSubjectBinding(status="unresolved", reason="process_subject_not_identified", gaps=["process_subject_unresolved"])
    previous_alert.entities.detections = []
    reviewer = JsonLLMNormalizationReviewer(client=NoModelClient(), model_name="not-called")
    old_request = reviewer.prepare(previous_alert)
    new_request = reviewer.prepare(current)
    previous = AnalysisRun(
        run_id="old-event-tree-run",
        status="success",
        alert_id=current.alert_id,
        input_hash=stable_hash(raw),
        normalized_alert=previous_alert,
        normalization_assistance=NormalizationAssistResult(status="unchanged", mode="apply", request_hash=stable_hash(old_request.model_dump(mode="json")), model_name="not-called", prompt_version=reviewer.prompt_version),
    )
    run = AnalysisRun(run_id="new-event-tree-run", status="running", alert_id=current.alert_id, input_hash=previous.input_hash, normalized_alert=current)
    frozen = previous.model_dump(mode="json")

    assert find_snapshot(run, old_request, previous, None) is previous
    assert new_request.source_text == old_request.source_text
    assert new_request.configuration_hash == old_request.configuration_hash
    assert new_request.adapter_snapshot_hash != old_request.adapter_snapshot_hash
    assert find_snapshot(run, new_request, previous, None) is None
    assert previous.model_dump(mode="json") == frozen
