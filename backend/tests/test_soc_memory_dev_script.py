from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "soc-memory-dev.sh"
COMPOSE_PATH = REPO_ROOT / "docker" / "docker-compose.soc-memory-dev.yaml"


def test_memory_dev_start_prewarms_each_operational_soc_route() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert '"${COMPOSE[@]}" up --no-build' in script
    assert '"${COMPOSE[@]}" up --build' in script
    assert script.count("    warm_soc_routes\n") == 2
    for path in (
        "/workspace/soc/operations",
        "/workspace/soc/review/alerts",
        "/workspace/soc/review/memory-candidates",
        "/workspace/soc/review/memory-candidates/__warmup__",
        "/workspace/soc/review/samples",
        "/workspace/soc/memory",
        "/workspace/soc/memory/patterns/__warmup__",
        "/workspace/soc/normalization",
        "/workspace/soc/corpus-validation",
        "/workspace/soc/dev/memory-validation/galaxylab",
    ):
        assert f'    "{path}"' in script


def test_memory_dev_warmup_is_bounded_and_reports_route_latency() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "--max-time 120" in script
    assert 'FRONTEND_HEALTH_URL="http://localhost:2026/"' in script
    assert "    wait_for_frontend\n" in script
    assert "%{time_starttransfer}" in script
    assert "%{time_total}" in script
    assert "warm) warm_soc_routes ;;" in script


def test_memory_dev_overlay_avoids_forced_idle_polling_by_default() -> None:
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    assert "WATCHPACK_POLLING: ${SOC_MEMORY_DEV_WATCHPACK_POLLING:-15000}" in compose
    assert "WATCHFILES_FORCE_POLLING: ${SOC_MEMORY_DEV_WATCHFILES_FORCE_POLLING:-false}" in compose


def test_memory_dev_enables_full_pingan_policy_without_external_actions() -> None:
    script = SCRIPT_PATH.read_text(encoding="utf-8")
    compose = COMPOSE_PATH.read_text(encoding="utf-8")

    for content in (script, compose):
        assert "SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY" in content
        assert "SOC_TENANT_POLICY_ENABLED" in content
        assert "SOC_TENANT_DISPOSITION_POLICY_PATH" in content
        assert "SOC_TENANT_POLICY_ADVISOR_MODE" in content
        assert "SOC_TENANT_POLICY_SKILL_PATH" in content
        assert "SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED" in content
        assert "SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH" in content
        assert "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS" in content

    assert "export SOC_TENANT_POLICY_ENABLED=true" in script
    assert "export SOC_TENANT_POLICY_ADVISOR_MODE=llm" in script
    assert "export SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true" in script
    assert "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false" in script
    assert 'SOC_TENANT_POLICY_ENABLED: "true"' in compose
    assert 'SOC_TENANT_POLICY_ADVISOR_MODE: "llm"' in compose
    assert 'SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED: "true"' in compose
    assert 'SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS: "false"' in compose
