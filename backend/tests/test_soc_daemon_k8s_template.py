from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
K8S_TEMPLATE = REPO_ROOT / "docker" / "k8s" / "soc-daemon.yaml"


def test_soc_daemon_k8s_template_is_opt_in_and_uses_stable_entrypoints() -> None:
    manifest = K8S_TEMPLATE.read_text(encoding="utf-8")
    docker_script = (REPO_ROOT / "scripts" / "docker.sh").read_text(encoding="utf-8")

    assert "kind: ConfigMap" in manifest
    assert "kind: Secret" in manifest
    assert "kind: Deployment" in manifest
    assert "kind: Service" not in manifest
    assert "backend/scripts/soc_daemon_entrypoint.sh" in manifest
    assert "backend/scripts/soc_daemon_healthcheck.sh" in manifest
    assert "SOC_DAEMON_METRIC_JSONL: stderr" in manifest
    assert "SOC_KAFKA_SASL_PASSWORD_ENV: SOC_KAFKA_PASSWORD" in manifest
    assert "SOC_ANALYZER_MODE: llm" in manifest
    assert "SOC_LLM_MODEL: deepseek-v4-pro" in manifest
    assert 'SOC_LLM_MAX_CONCURRENCY: "1"' in manifest
    assert 'SOC_LLM_REQUESTS_PER_MINUTE: "0"' in manifest
    assert 'SOC_LLM_ADMISSION_TIMEOUT_SECONDS: "5"' in manifest
    assert 'SOC_LLM_CALL_TIMEOUT_SECONDS: "180"' in manifest
    assert "SOC_LLM_SENSITIVE_EVIDENCE_MODE: redact" in manifest
    assert "soc-daemon.yaml" not in docker_script


def test_soc_daemon_k8s_template_separates_config_from_secrets() -> None:
    manifest = K8S_TEMPLATE.read_text(encoding="utf-8")

    assert "SOC_KAFKA_BOOTSTRAP_SERVERS:" in manifest
    assert "SOC_KAFKA_ALERT_TOPICS:" in manifest
    assert "SOC_KAFKA_DEAD_LETTER_TOPIC:" in manifest
    assert "stringData:" in manifest
    assert "SOC_DATABASE_URL:" in manifest
    assert "SOC_KAFKA_PASSWORD:" in manifest
    assert "DEEPSEEK_API_KEY:" in manifest
    assert "resources:" in manifest
    assert "requests:" in manifest
    assert "limits:" in manifest
