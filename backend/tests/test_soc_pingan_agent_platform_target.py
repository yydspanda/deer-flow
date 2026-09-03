from __future__ import annotations

import pytest

from soc_agent.integrations.pingan.agent_platform_target import (
    PingAnAgentPlatformTargetConfigurationError,
    enforce_pingan_runtime_agent_platform_mapping,
    load_pingan_agent_platform_target,
)


def test_agent_platform_target_loads_governed_dev_to_prd_profile() -> None:
    target = enforce_pingan_runtime_agent_platform_mapping(load_pingan_agent_platform_target(_target_env()))

    assert target.runtime_environment == "dev"
    assert target.target_environment == "prd"
    assert target.base_url == "https://agent-prd.example"
    assert target.allowed_hosts == ("agent-prd.example",)
    assert target.app_id == "YHSYS"
    assert target.terminal_workflow_id == 1087710
    assert "workflow-secret" not in repr(target)


def test_agent_platform_target_rejects_runtime_target_drift() -> None:
    env = _target_env()
    env["SOC_PINGAN_ENV"] = "stg"

    with pytest.raises(
        PingAnAgentPlatformTargetConfigurationError,
        match="STG must target Agent Platform STG",
    ):
        enforce_pingan_runtime_agent_platform_mapping(load_pingan_agent_platform_target(env))


def test_agent_platform_target_rejects_prd_without_confirmation() -> None:
    env = _target_env()
    env["SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION"] = ""

    with pytest.raises(
        PingAnAgentPlatformTargetConfigurationError,
        match="Agent Platform PRD requires",
    ):
        load_pingan_agent_platform_target(env)


def test_agent_platform_target_rejects_missing_workflow_identity() -> None:
    env = _target_env()
    env["SOC_PINGAN_WORKFLOW_USER_ID"] = ""

    with pytest.raises(
        PingAnAgentPlatformTargetConfigurationError,
        match="SOC_PINGAN_WORKFLOW_USER_ID is required",
    ):
        load_pingan_agent_platform_target(env)


def _target_env() -> dict[str, str]:
    return {
        "SOC_PINGAN_ENV": "dev",
        "SOC_PINGAN_WORKFLOW_ENV": "prd",
        "SOC_PINGAN_WORKFLOW_BASE_URL": "https://agent-prd.example/",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": "agent-prd.example",
        "SOC_PINGAN_WORKFLOW_APP_ID": "YHSYS",
        "SOC_PINGAN_WORKFLOW_APP_SECRET": "workflow-secret",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID": "1087710",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID": "1087787",
        "SOC_PINGAN_WORKFLOW_USER_ID": "1092332",
        "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": "CALL_PINGAN_PRD",
    }
