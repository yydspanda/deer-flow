from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = BACKEND_ROOT / "scripts" / "soc_daemon_entrypoint.sh"
HEALTHCHECK = BACKEND_ROOT / "scripts" / "soc_daemon_healthcheck.sh"


def test_soc_daemon_entrypoint_rejects_disabled_kafka_without_override() -> None:
    result = _run_script(
        ENTRYPOINT,
        {
            "SOC_KAFKA_ENABLED": "false",
        },
    )

    assert result.returncode == 2
    assert "SOC_KAFKA_ENABLED must be true" in result.stderr


def test_soc_daemon_entrypoint_supports_bounded_local_validation() -> None:
    result = _run_script(
        ENTRYPOINT,
        {
            "SOC_DAEMON_ALLOW_DISABLED": "true",
            "SOC_KAFKA_ENABLED": "false",
            "SOC_DAEMON_MAX_LOOPS": "1",
            "SOC_DAEMON_IDLE_SLEEP_MS": "0",
            "SOC_DAEMON_ERROR_BACKOFF_MS": "0",
        },
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "soc.kafka_daemon_run_result.v1"
    assert payload["settings"]["enabled"] is False
    assert payload["stop_reason"] == "max_loops_reached"
    assert payload["loop_count"] == 1


def test_soc_daemon_healthcheck_supports_config_only_local_validation() -> None:
    result = _run_script(
        HEALTHCHECK,
        {
            "SOC_KAFKA_ENABLED": "false",
            "SOC_DAEMON_HEALTHCHECK_BROKER": "false",
        },
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "soc.kafka_daemon_status.v1"
    assert payload["ready"] is True
    assert payload["database"]["configured"] is True
    assert payload["database"]["reachable"] is True
    assert payload["kafka"]["enabled"] is False


def _run_script(path: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    full_env = os.environ.copy()
    full_env.update(env)
    full_env["SOC_DAEMON_PYTHON"] = sys.executable
    full_env["SOC_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    return subprocess.run(
        ["sh", str(path)],
        cwd=BACKEND_ROOT,
        env=full_env,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )
