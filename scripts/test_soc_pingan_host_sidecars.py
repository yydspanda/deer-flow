from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from scripts.soc_pingan_host_sidecars import (
    HostDevSidecarError,
    HostDevSidecarSpec,
    build_pingan_sidecar_specs,
    sidecar_status,
    start_sidecars,
    stop_sidecars,
)


def test_pingan_sidecar_specs_own_4001_8090_and_worker(tmp_path: Path) -> None:
    specs = build_pingan_sidecar_specs(
        root=tmp_path,
        environment={
            "SOC_PINGAN_MODEL_GATEWAY_PORT": "4001",
            "SOC_PINGAN_COMPAT_PORT": "8090",
        },
    )

    assert [item.name for item in specs] == [
        "model-gateway",
        "legacy-api",
        "legacy-worker",
    ]
    assert specs[0].health_url == "http://127.0.0.1:4001/health"
    assert specs[1].health_url == "http://127.0.0.1:8090/health"
    assert specs[2].health_url is None
    assert specs[2].readiness_env_var == "SOC_PINGAN_LEGACY_WORKER_READY_FILE"
    assert all(str(tmp_path) in item.command[1] for item in specs)


def test_sidecar_process_has_pid_status_and_bounded_stop(tmp_path: Path) -> None:
    spec = HostDevSidecarSpec(
        name="sleep-test",
        command=(
            sys.executable,
            "-c",
            "import time; time.sleep(30)",
        ),
        process_marker="time.sleep(30)",
        startup_timeout_seconds=1,
    )
    runtime_dir = tmp_path / "sidecars"
    reports = start_sidecars(
        (spec,),
        runtime_dir=runtime_dir,
        environment={},
    )
    try:
        assert reports[0]["status"] == "running"
        assert (
            sidecar_status((spec,), runtime_dir=runtime_dir)[0]["status"] == "running"
        )
    finally:
        stopped = stop_sidecars(
            (spec,),
            runtime_dir=runtime_dir,
            grace_seconds=1,
        )
    assert stopped[0]["status"] == "stopped"
    assert (
        sidecar_status((spec,), runtime_dir=runtime_dir)[0]["status"] == "not_running"
    )


def test_sidecar_waits_for_explicit_worker_readiness(tmp_path: Path) -> None:
    spec = HostDevSidecarSpec(
        name="ready-test",
        command=(
            sys.executable,
            "-c",
            (
                "import os,time,pathlib; "
                "path=pathlib.Path(os.environ['TEST_READY_FILE']); "
                "path.write_text(str(os.getpid()), encoding='utf-8'); "
                "time.sleep(30)"
            ),
        ),
        process_marker="TEST_READY_FILE",
        readiness_env_var="TEST_READY_FILE",
        startup_timeout_seconds=2,
    )
    runtime_dir = tmp_path / "sidecars"

    reports = start_sidecars(
        (spec,),
        runtime_dir=runtime_dir,
        environment=os.environ,
    )
    try:
        assert reports[0]["status"] == "running"
        assert (runtime_dir / "ready-test.ready").read_text(encoding="utf-8") == str(
            reports[0]["pid"]
        )
    finally:
        stop_sidecars((spec,), runtime_dir=runtime_dir, grace_seconds=1)


def test_sidecar_rejects_process_that_never_becomes_ready(tmp_path: Path) -> None:
    spec = HostDevSidecarSpec(
        name="not-ready-test",
        command=(sys.executable, "-c", "import time; time.sleep(30)"),
        process_marker="time.sleep(30)",
        readiness_env_var="TEST_READY_FILE",
        startup_timeout_seconds=0.2,
    )
    runtime_dir = tmp_path / "sidecars"

    with pytest.raises(HostDevSidecarError, match="did not become ready"):
        start_sidecars(
            (spec,),
            runtime_dir=runtime_dir,
            environment=os.environ,
        )

    assert (
        sidecar_status((spec,), runtime_dir=runtime_dir)[0]["status"] == "not_running"
    )
