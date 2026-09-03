from __future__ import annotations

import os
import runpy
from pathlib import Path

import pytest


def _worker_globals() -> dict[str, object]:
    script = Path(__file__).resolve().parents[1] / "scripts/soc_pingan_legacy_worker.py"
    return runpy.run_path(str(script), run_name="soc_pingan_legacy_worker_test")


def test_worker_readiness_file_is_atomic_pid_bound_and_private(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = _worker_globals()["_publish_readiness"]
    readiness_path = tmp_path / "worker.ready"
    monkeypatch.setenv(
        "SOC_PINGAN_LEGACY_WORKER_READY_FILE",
        str(readiness_path),
    )

    result = publish()

    assert result == readiness_path
    assert readiness_path.read_text(encoding="utf-8") == str(os.getpid())
    assert readiness_path.stat().st_mode & 0o777 == 0o600


def test_worker_readiness_file_must_be_absolute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publish = _worker_globals()["_publish_readiness"]
    monkeypatch.setenv(
        "SOC_PINGAN_LEGACY_WORKER_READY_FILE",
        "relative.ready",
    )

    with pytest.raises(ValueError, match="must be absolute"):
        publish()
