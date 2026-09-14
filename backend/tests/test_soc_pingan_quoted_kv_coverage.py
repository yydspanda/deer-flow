from __future__ import annotations

from copy import deepcopy

import pytest

from soc_agent.contracts import ParsedRawMessageEvidence
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.normalizers import normalize_alert_payload
from soc_agent.normalizers.pingan_messages import parse_pingan_raw_message


def parse(message: str) -> ParsedRawMessageEvidence:
    result = parse_pingan_raw_message(message, source_path="alert.hitLog[0].zeusRawLogs[0].message")
    assert result is not None
    assert result.parser_name == "pingan_quoted_kv"
    return result


def test_nested_command_quotes_preserve_full_value_and_following_fields() -> None:
    command = r'"C:\Program Files\Git\usr\bin\bash.exe" --login -i'
    message = f'hostname="example-host" cmdline="{command}" file_path="D:\\tools\\example.exe"'
    result = parse(message)

    assert result.fields["cmdline"] == command
    assert result.fields["file_path"] == r"D:\tools\example.exe"
    assert result.parser_version == "v4"
    coverage = result.syntax_coverage
    assert coverage is not None and coverage.complete
    assert coverage.recovered_fields == ["cmdline"]
    assert result.warnings == []
    span = next(span for span in coverage.field_spans if span.field_name == "cmdline")
    assert message[span.value_start : span.value_end] == command
    assert message[span.start : span.end] == f'cmdline="{command}"'


@pytest.mark.parametrize("value", ["", "0", r"C:\Program Files\tools\tool.exe", r"echo \"example\"", "a&gt;b", "中文值"])
def test_regular_values_are_preserved_with_complete_coverage(value: str) -> None:
    result = parse(f'a="{value}" b=""')
    assert result.fields == {"a": value.replace("&gt;", ">"), "b": ""}
    assert result.syntax_coverage.complete
    assert result.syntax_coverage.recovered_fields == []
    assert result.warnings == []


@pytest.mark.parametrize("prefix", ["2026-09-14T12:00:00+08:00 SENSOR qtAlert[99] ", "qtAlert ", "qtAlert[99]: "])
def test_recognized_log_prefix_is_not_an_unconsumed_field(prefix: str) -> None:
    message = prefix + 'host="example" pid="12"'
    result = parse(message)
    assert result.syntax_coverage.complete
    assert result.syntax_coverage.prefix_span is not None
    assert message[result.syntax_coverage.prefix_span.start : result.syntax_coverage.prefix_span.end].strip() == prefix.strip()
    assert result.warnings == []


def test_unconsumed_prefix_middle_and_truncated_tail_have_exact_offsets() -> None:
    message = 'unknown prefix host="示例" BROKEN pid="12" note="unterminated'
    result = parse(message)
    assert result.fields == {"host": "示例", "pid": "12"}
    assert not result.syntax_coverage.complete
    assert [message[s.start : s.end] for s in result.syntax_coverage.unconsumed_spans] == ["unknown prefix", "BROKEN", 'note="unterminated']
    assert result.syntax_coverage.offset_unit == "unicode_codepoint"
    assert any("unconsumed" in warning for warning in result.warnings)


def test_duplicate_conflicts_do_not_silently_pick_last_value() -> None:
    message = 'host="first" pid="12" host="second"'
    result = parse(message)
    assert result.fields == {"pid": "12"}
    assert result.syntax_coverage.duplicate_fields == ["host"]
    assert result.syntax_coverage.conflicting_fields == ["host"]
    assert len(result.syntax_coverage.field_spans) == 3
    assert not result.syntax_coverage.complete
    assert any("conflicting" in warning for warning in result.warnings)


def test_identical_duplicate_values_are_recorded_without_degrading() -> None:
    result = parse('host="same" pid="12" host="same"')
    assert result.fields == {"host": "same", "pid": "12"}
    assert result.syntax_coverage.duplicate_fields == ["host"]
    assert result.syntax_coverage.conflicting_fields == []
    assert result.syntax_coverage.complete
    assert result.warnings == []


@pytest.mark.parametrize("command", ['"unfinished path', '"path" --unfinished'])
def test_truncated_nested_quotes_cannot_swallow_the_next_field(command: str) -> None:
    message = f'host="example" cmdline="{command} pid="12" next="safe"'
    result = parse(message)
    assert result.fields["pid"] == "12"
    assert result.fields["next"] == "safe"
    assert "pid=" not in result.fields.get("cmdline", "")
    assert not result.syntax_coverage.complete


def test_regular_embedded_assignments_are_one_value_not_new_fields() -> None:
    result = parse(r'host="example" cmd="echo name=\"quoted\"" pid="12"')
    assert set(result.fields) == {"host", "cmd", "pid"}
    assert result.fields["cmd"] == r"echo name=\"quoted\""
    assert result.syntax_coverage.complete


def test_comma_separators_remain_supported() -> None:
    result = parse('host="example",pid="12",cmd="echo ok"')
    assert result.fields == {"host": "example", "pid": "12", "cmd": "echo ok"}
    assert result.syntax_coverage.complete


def test_dotted_field_names_preserve_namespace_and_tree():
    result = parse('host="example" detail.process_tree="sh(1) -> kubectl(2)" detail.cmd="kubectl get secrets"')
    assert result.fields["detail.process_tree"] == "sh(1) -> kubectl(2)"
    assert result.fields["detail.cmd"] == "kubectl get secrets"
    assert result.syntax_coverage.complete


def test_comma_kv_is_not_misidentified_from_two_quoted_commands():
    message = 'str_id=1,str_process_short=powershell.exe,str_cmd="powershell.exe" -NoExit,str_parent_cmd="wscript.exe" "C:\\scripts\\notify.vbs",str_user_agent=user'
    result = parse_pingan_raw_message(message, source_path="message")
    assert result.parser_name == "pingan_comma_kv"
    assert result.fields["str_cmd"] == '"powershell.exe" -NoExit'
    assert result.fields["str_process_short"] == "powershell.exe"


def test_escaped_backslashes_and_quotes_are_not_decoded_as_json() -> None:
    value = r"C:\\tools\\example.exe --value=\"test\""
    result = parse(f'cmd="{value}" host="example"')
    assert result.fields["cmd"] == value
    assert result.syntax_coverage.complete


def test_multiple_balanced_quoted_command_arguments_are_preserved() -> None:
    result = parse('host="example" cmd=""tool.exe" --name "several quotes"" pid="12"')
    assert result.fields["cmd"] == '"tool.exe" --name "several quotes"'
    assert result.fields["pid"] == "12"
    assert result.syntax_coverage.complete
    assert result.syntax_coverage.recovered_fields == ["cmd"]


def test_unbalanced_nested_quotes_are_reported_not_guessed() -> None:
    result = parse('host="example" cmd=""tool.exe" --name "several quotes" pid="12"')
    assert "cmd" not in result.fields
    assert result.fields["pid"] == "12"
    assert not result.syntax_coverage.complete
    assert result.syntax_coverage.recovered_fields == []


def test_diagnostics_survive_serialization_without_rewriting_old_records() -> None:
    result = parse('host="example" pid="12"')
    encoded = result.model_dump(mode="json")
    assert ParsedRawMessageEvidence.model_validate(encoded).syntax_coverage == result.syntax_coverage
    encoded.pop("syntax_coverage")
    encoded["parser_version"] = "v2"
    assert ParsedRawMessageEvidence.model_validate(encoded).syntax_coverage is None


def test_adapter_persists_diagnostics_but_does_not_feed_offsets_to_llm() -> None:
    command = r'"C:\Program Files\tools\tool.exe" --check'
    message = f'host_name="endpoint" cmd="{command}" datatype="web_command_win"'
    payload = {
        "alert": {"alertId": "SYNTHETIC-KV", "hitLog": [{"topic": "security_qthids", "zeusRawLogs": [{"message": message, "cmd": "excluded outer command"}]}]},
    }
    original = deepcopy(payload)
    alert = normalize_alert_payload(payload)
    assert payload == original == alert.raw
    parsed = alert.extensions["parsed_raw_messages"][0]
    assert parsed["fields"]["cmd"] == command
    assert parsed["syntax_coverage"]["complete"]
    request = build_analysis_request_for_payload(payload)
    assert request.primary_evidence is not None
    assert "--check" in request.primary_evidence.content
    assert "excluded outer command" not in request.primary_evidence.content
    assert "syntax_coverage" not in request.primary_evidence.content
    assert "field_spans" not in request.primary_evidence.content
