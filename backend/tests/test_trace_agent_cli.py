"""CLI probes stay configuration-only and never import the real engine."""

import json
import os
import subprocess
import sys
from pathlib import Path

PACKAGE_SRC = Path(__file__).resolve().parents[1] / "packages/trace-agent/src"


def run_cli(tmp_path, *args, extra_env=None):
    env = {key: value for key, value in os.environ.items() if not key.startswith(("TRACE_AGENT_", "SOC_", "DEER_FLOW_"))}
    env["PYTHONPATH"] = os.pathsep.join((str(PACKAGE_SRC), str(tmp_path)))
    env.update(extra_env or {})
    return subprocess.run([sys.executable, "-m", "trace_agent", *args], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=15)


def test_help_and_check_do_not_create_state_or_import_worker_dependencies(tmp_path):
    assert run_cli(tmp_path, "--help").returncode == 0
    result = run_cli(tmp_path, "check")
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "not_ready"
    assert list(tmp_path.iterdir()) == []
    probe = subprocess.run(
        [sys.executable, "-c", "import trace_agent; import trace_agent.contracts; import sys; assert not {'fastapi','uvicorn','celery','openai','redis','pydantic_ai'}.intersection(sys.modules)"],
        env={**os.environ, "PYTHONPATH": str(PACKAGE_SRC)},
        capture_output=True,
        text=True,
    )
    assert probe.returncode == 0, probe.stderr


def test_check_reports_configuration_without_importing_factory_or_revealing_key(tmp_path):
    sentinel = tmp_path / "factory-imported"
    (tmp_path / "danger.py").write_text(f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('side-effect')\n", encoding="utf-8")
    result = run_cli(tmp_path, "check", extra_env={"TRACE_AGENT_API_KEY": "private-test-key", "TRACE_AGENT_ENGINE_FACTORY": "danger:create_engine"})
    assert result.returncode == 0
    response = json.loads(result.stdout)
    assert response["status"] == "configured"
    assert response["worker_readiness"] == "not_checked"
    assert "private-test-key" not in result.stdout + result.stderr
    assert not sentinel.exists()


def test_serve_requires_api_key_and_invalid_configuration_is_sanitized(tmp_path):
    result = run_cli(tmp_path, "serve")
    assert result.returncode != 0
    assert "TRACE_AGENT_API_KEY" in result.stderr
    for extra in ({"TRACE_AGENT_PORT": "wrong"}, {"TRACE_AGENT_ENGINE_FACTORY": "not-a-factory"}):
        result = run_cli(tmp_path, "check", extra_env=extra)
        assert result.returncode != 0
        assert json.loads(result.stdout)["status"] == "not_ready"


def test_serve_builds_only_explicit_trusted_factory_with_loopback_defaults(tmp_path, monkeypatch):
    import types

    import uvicorn
    from trace_agent.__main__ import main
    from trace_agent.engine import UnavailableEngine

    calls = []
    module = types.ModuleType("test_trusted_trace_factory")
    module.create_engine = lambda: calls.append("factory") or UnavailableEngine()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv("TRACE_AGENT_API_KEY", "private-test-key")
    monkeypatch.setenv("TRACE_AGENT_ENGINE_FACTORY", "test_trusted_trace_factory:create_engine")
    monkeypatch.delenv("TRACE_AGENT_PORT", raising=False)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["serve"]) == 0
    assert calls == ["factory", {"host": "127.0.0.1", "port": 8300, "log_level": "info"}]
