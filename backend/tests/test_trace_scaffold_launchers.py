"""Trace's optional process must not change ordinary SOC startup."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
import soc_pingan_host_sidecars as sidecars


def test_trace_is_absent_from_default_host_startup(tmp_path: Path) -> None:
    specs = sidecars.build_pingan_sidecar_specs(root=tmp_path, environment={})
    assert "trace-agent" not in {spec.name for spec in specs}


def test_trace_opt_in_uses_shared_python_and_loopback_health(tmp_path: Path) -> None:
    specs = sidecars.build_pingan_sidecar_specs(
        root=tmp_path,
        environment={"SOC_TRACE_ENABLED": "true", "TRACE_AGENT_PORT": "8305"},
    )
    trace = next(spec for spec in specs if spec.name == "trace-agent")
    assert trace.command == (
        str(tmp_path / "backend/.venv/bin/python"),
        str(tmp_path / "backend/scripts/soc_trace_agent.py"),
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "8305",
    )
    assert trace.health_url == "http://127.0.0.1:8305/health"
    assert trace.expected_health_status == "ok"


def test_disabled_trace_remains_discoverable_for_stop(tmp_path: Path) -> None:
    specs = sidecars.build_pingan_sidecar_specs(root=tmp_path, environment={}, include_disabled=True)
    assert "trace-agent" in {spec.name for spec in specs}


@pytest.mark.parametrize("body", [b'{"status":"degraded"}', b"invalid-json", b"[]", b"{}"])
def test_trace_health_does_not_treat_http_200_as_ready(monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
    spec = sidecars.HostDevSidecarSpec(
        name="trace-agent",
        command=("python", "soc_trace_agent.py", "serve"),
        process_marker="soc_trace_agent.py",
        health_url="http://127.0.0.1:8300/health",
        expected_health_status="ok",
        startup_timeout_seconds=1,
    )
    ticks = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(sidecars.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(sidecars.time, "sleep", lambda _: None)
    response = io.BytesIO(body)
    response.status = 200
    monkeypatch.setattr(sidecars.urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    with pytest.raises(sidecars.HostDevSidecarError, match="did not become healthy"):
        sidecars._wait_until_ready(spec, process=SimpleNamespace(poll=lambda: None))


def test_trace_health_accepts_explicit_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = sidecars.HostDevSidecarSpec(
        name="trace-agent",
        command=("python", "soc_trace_agent.py", "serve"),
        process_marker="soc_trace_agent.py",
        health_url="http://127.0.0.1:8300/health",
        expected_health_status="ok",
    )
    response = io.BytesIO(b'{"status":"ok"}')
    response.status = 200
    monkeypatch.setattr(sidecars.urllib.request, "urlopen", lambda *_args, **_kwargs: response)
    sidecars._wait_until_ready(spec, process=SimpleNamespace(poll=lambda: None))
