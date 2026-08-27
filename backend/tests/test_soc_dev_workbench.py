from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.gateway.soc_dev_workbench import resolve_soc_dev_policy_safety


def _clear_policy_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY",
        "SOC_TENANT_POLICY_ENABLED",
        "SOC_TENANT_POLICY_ENVIRONMENT",
        "SOC_TENANT_POLICY_ADVISOR_MODE",
        "SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


def test_dev_workbench_keeps_tenant_policy_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_policy_environment(monkeypatch)

    safety = resolve_soc_dev_policy_safety()

    assert safety.tenant_policy == "disabled"
    assert safety.software_path_fast_policy is False


def test_dev_workbench_requires_explicit_policy_allow_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_policy_environment(monkeypatch)
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "true")

    with pytest.raises(HTTPException, match="explicit DEV policy allow switch") as exc_info:
        resolve_soc_dev_policy_safety()

    assert exc_info.value.status_code == 503


def test_dev_workbench_reports_full_pingan_policy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_policy_environment(monkeypatch)
    monkeypatch.setenv("SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY", "true")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENVIRONMENT", "dev")
    monkeypatch.setenv("SOC_TENANT_POLICY_ADVISOR_MODE", "llm")
    monkeypatch.setenv("SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED", "true")

    safety = resolve_soc_dev_policy_safety()

    assert safety.tenant_policy == "deterministic_and_llm"
    assert safety.software_path_fast_policy is True


def test_dev_workbench_policy_requires_dev_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_policy_environment(monkeypatch)
    monkeypatch.setenv("SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY", "true")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENABLED", "true")
    monkeypatch.setenv("SOC_TENANT_POLICY_ENVIRONMENT", "prd")

    with pytest.raises(HTTPException, match="must be explicitly set to dev"):
        resolve_soc_dev_policy_safety()
