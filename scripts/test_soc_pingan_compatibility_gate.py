from __future__ import annotations

import hashlib
import json
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from scripts import soc_pingan_compatibility_gate as gate


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    python = tmp_path / "backend/.venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("test interpreter", encoding="utf-8")
    for relative in (*gate.REQUIRED_TEST_FILES, gate.FIXTURE_PATH):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (
            json.dumps(
                {
                    "fixture_id": "synthetic-frozen-fixture",
                    "source_commit": "c" * 40,
                    "payload_sha256": "d" * 64,
                }
            )
            if target.suffix == ".json"
            else "# test"
        )
        target.write_text(content, encoding="utf-8")
    return tmp_path


def _report(
    command,
    *,
    omitted=None,
    outcome=None,
    empty=False,
    wrong_count=False,
    omitted_name=None,
):
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", errors="0", failures="0", skipped="0")
    for relative in gate.REQUIRED_TEST_FILES:
        if relative == omitted or empty:
            continue
        stem = Path(relative).stem
        names = gate.REQUIRED_TEST_NAMES.get(relative, ("test_compatibility",))
        for name in names:
            if name == omitted_name:
                continue
            case = ET.SubElement(
                suite, "testcase", classname=f"tests.{stem}", name=name
            )
            if outcome:
                ET.SubElement(case, outcome, message="synthetic blocked result")
    count = len(suite.findall("testcase"))
    suite.set("tests", str(count + int(wrong_count)))
    if outcome:
        suite.set(
            {"failure": "failures", "error": "errors", "skipped": "skipped"}[outcome],
            str(count),
        )
    target = next(
        value.split("=", 1)[1] for value in command if value.startswith("--junitxml=")
    )
    ET.ElementTree(root).write(target, encoding="utf-8")


def _mock_run(monkeypatch, *, report=True, returncode=0, **report_options):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        kwargs["stdout"].write("synthetic pytest output\n")
        if report:
            _report(command, **report_options)
        return subprocess.CompletedProcess(command, returncode)

    monkeypatch.setattr(gate.subprocess, "run", run)
    return calls


def _invoke(root):
    return gate.run_compatibility_gate(
        root, source_commit="a" * 40, source_fingerprint="b" * 64
    )


def test_gate_records_only_verified_execution_with_source_and_frozen_fixture(
    checkout, monkeypatch
):
    calls = _mock_run(monkeypatch)
    receipt = _invoke(checkout)
    assert receipt["status"] == "passed"
    assert receipt["source_commit"] == "a" * 40
    assert receipt["source_fingerprint"] == "b" * 64
    assert receipt["fixture"]["path"] == gate.FIXTURE_PATH
    assert (
        receipt["fixture"]["sha256"]
        == hashlib.sha256((checkout / gate.FIXTURE_PATH).read_bytes()).hexdigest()
    )
    assert receipt["passed"] >= len(gate.REQUIRED_TEST_FILES)
    assert set(receipt["tests_by_file"]) == set(gate.REQUIRED_TEST_FILES)
    assert receipt["duration_seconds"] >= 0
    command, options = calls[0]
    assert command[:3] == [str(checkout / "backend/.venv/bin/python"), "-m", "pytest"]
    assert options["cwd"] == checkout / "backend"
    assert options["timeout"] == 600
    assert all(
        relative.removeprefix("backend/") in command
        for relative in gate.REQUIRED_TEST_FILES
    )
    assert "-o" in command and "xfail_strict=true" in command
    json.dumps(receipt)


def test_gate_clears_test_selection_and_private_runtime_environment(
    checkout, monkeypatch
):
    for key, value in {
        "PYTEST_ADDOPTS": "-k missing",
        "PYTEST_PLUGINS": "fake_plugin",
        "PYTEST_CURRENT_TEST": "parent::test",
        "PYTHONPATH": "/private/injected",
        "PYTHONHOME": "/private/interpreter",
        "SOC_DATABASE_URL": "sqlite:////private/live.db",
        "SOC_NORMALIZATION_ASSIST_MODE": "apply",
        "PINGAN_MODEL_GATEWAY_API_KEY": "private-key",
        "DEER_FLOW_CONFIG_PATH": "/private/config.yaml",
    }.items():
        monkeypatch.setenv(key, value)
    calls = _mock_run(monkeypatch)
    _invoke(checkout)
    env = calls[0][1]["env"]
    assert "PYTEST_ADDOPTS" not in env
    assert "PYTEST_PLUGINS" not in env
    assert "PYTEST_CURRENT_TEST" not in env
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "PINGAN_MODEL_GATEWAY_API_KEY" not in env
    assert env["SOC_DATABASE_URL"] == "sqlite:///:memory:"
    assert env["SOC_NORMALIZATION_ASSIST_MODE"] == "off"
    assert env["PYTHON_DOTENV_DISABLED"] == "1"
    assert env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] == "1"
    assert env["DEER_FLOW_CONFIG_PATH"] != "/private/config.yaml"
    assert str(checkout) not in env["DEER_FLOW_HOME"]


@pytest.mark.parametrize(
    "missing",
    ["backend/.venv/bin/python", *gate.REQUIRED_TEST_FILES, gate.FIXTURE_PATH],
)
def test_gate_requires_interpreter_tests_and_frozen_data(
    checkout, monkeypatch, missing
):
    calls = _mock_run(monkeypatch)
    (checkout / missing).unlink()
    with pytest.raises(gate.CompatibilityGateError, match="missing"):
        _invoke(checkout)
    assert not calls


@pytest.mark.parametrize("returncode", [1, 2, 3, 4, 5, -9])
def test_gate_rejects_nonzero_exit_even_with_passing_report(
    checkout, monkeypatch, returncode
):
    _mock_run(monkeypatch, returncode=returncode)
    with pytest.raises(gate.CompatibilityGateError, match="pytest exited"):
        _invoke(checkout)


@pytest.mark.parametrize("outcome", ["skipped", "failure", "error"])
def test_gate_rejects_nonpassing_results_even_when_process_exits_zero(
    checkout, monkeypatch, outcome
):
    _mock_run(monkeypatch, outcome=outcome)
    with pytest.raises(gate.CompatibilityGateError, match="nonpassing"):
        _invoke(checkout)


@pytest.mark.parametrize(
    "options, reason",
    [
        ({"report": False}, "JUnit"),
        ({"empty": True}, "zero"),
        ({"wrong_count": True}, "count"),
        *[
            ({"omitted": relative}, "missing required")
            for relative in gate.REQUIRED_TEST_FILES
        ],
    ],
)
def test_gate_requires_complete_actual_execution(
    checkout, monkeypatch, options, reason
):
    _mock_run(monkeypatch, **options)
    with pytest.raises(gate.CompatibilityGateError, match=reason):
        _invoke(checkout)


def test_gate_rejects_timeout_and_keeps_bounded_failure_evidence(checkout, monkeypatch):
    def run(command, **kwargs):
        kwargs["stdout"].write("synthetic partial test output\n")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises(gate.CompatibilityGateError, match="timed out") as caught:
        _invoke(checkout)
    assert (
        Path(caught.value.log_path).read_text(encoding="utf-8")
        == "synthetic partial test output\n"
    )


def test_gate_rejects_missing_execution_dependency(checkout, monkeypatch):
    def run(*args, **kwargs):
        raise OSError("synthetic missing interpreter dependency")

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises(gate.CompatibilityGateError, match="could not start"):
        _invoke(checkout)


def test_gate_rejects_malformed_junit(checkout, monkeypatch):
    def run(command, **kwargs):
        target = next(
            value.split("=", 1)[1]
            for value in command
            if value.startswith("--junitxml=")
        )
        Path(target).write_text("not XML", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises(gate.CompatibilityGateError, match="JUnit"):
        _invoke(checkout)


def test_gate_rejects_missing_behavior_even_when_its_file_has_other_passing_tests(
    checkout, monkeypatch
):
    required = "test_frozen_reviewed_memory_still_reuses_in_validation"
    _mock_run(monkeypatch, omitted_name=required)
    with pytest.raises(gate.CompatibilityGateError, match=required):
        _invoke(checkout)


def test_gate_rejects_fixture_replacement_during_tests(checkout, monkeypatch):
    def run(command, **kwargs):
        _report(command)
        (checkout / gate.FIXTURE_PATH).write_text("{}", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(gate.subprocess, "run", run)
    with pytest.raises(gate.CompatibilityGateError, match="fixture changed"):
        _invoke(checkout)


@pytest.mark.parametrize("content", ["{}", "[]", "broken", '{"fixture_id": "test"}'])
def test_gate_rejects_missing_fixture_identity_before_execution(
    checkout, monkeypatch, content
):
    calls = _mock_run(monkeypatch)
    (checkout / gate.FIXTURE_PATH).write_text(content, encoding="utf-8")
    with pytest.raises(gate.CompatibilityGateError, match="fixture identity"):
        _invoke(checkout)
    assert not calls
