from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
OVERLAY = REPO_ROOT / "docker" / "docker-compose.soc-daemon.yaml"


def test_soc_daemon_compose_overlay_is_explicit_opt_in() -> None:
    overlay = OVERLAY.read_text(encoding="utf-8")
    docker_script = (REPO_ROOT / "scripts" / "docker.sh").read_text(encoding="utf-8")

    assert "soc-daemon:" in overlay
    assert "soc_daemon_entrypoint.sh" in overlay
    assert "soc_daemon_healthcheck.sh" in overlay
    assert "UV_EXTRAS: ${SOC_DAEMON_UV_EXTRAS:-postgres,kafka}" in overlay
    assert "SOC_DAEMON_METRIC_JSONL=${SOC_DAEMON_METRIC_JSONL:-stderr}" in overlay
    assert "SOC_ANALYZER_MODE=${SOC_ANALYZER_MODE:-stub}" in overlay
    assert "SOC_LLM_MODEL=${SOC_LLM_MODEL:-}" in overlay
    assert "SOC_LLM_MAX_CONCURRENCY=${SOC_LLM_MAX_CONCURRENCY:-1}" in overlay
    assert "SOC_LLM_REQUESTS_PER_MINUTE=${SOC_LLM_REQUESTS_PER_MINUTE:-0}" in overlay
    assert "SOC_LLM_ADMISSION_TIMEOUT_SECONDS=${SOC_LLM_ADMISSION_TIMEOUT_SECONDS:-5}" in overlay
    assert "docker-compose.soc-daemon.yaml" not in docker_script
