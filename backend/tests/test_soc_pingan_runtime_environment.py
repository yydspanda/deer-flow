from __future__ import annotations

import stat
from pathlib import Path

import pytest

from soc_agent.integrations.pingan.runtime_environment import (
    PingAnRuntimeEnvironmentConfigurationError,
    set_pingan_runtime_environment,
)


def test_runtime_environment_switch_applies_stg_remote_targets_atomically(
    tmp_path: Path,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="dev")

    report = set_pingan_runtime_environment(env_path, environment="stg")

    rendered = env_path.read_text(encoding="utf-8")
    assert "export SOC_PINGAN_ENV=stg" in rendered
    assert "export SOC_PINGAN_ZEUS_ENV=stg" in rendered
    assert "export SOC_PINGAN_ZEUS_BASE_URL=https://zeus-stg.example" in rendered
    assert "export SOC_PINGAN_ZEUS_ALLOWED_HOSTS=zeus-stg.example" in rendered
    assert "export SOC_PINGAN_ZEUS_APP_ID=STG-APP" in rendered
    assert "export SOC_PINGAN_ZEUS_APP_KEY=stg-secret" in rendered
    assert "export SOC_PINGAN_ZEUS_PRD_CONFIRMATION=''" in rendered
    assert "export SOC_PINGAN_LEGACY_LIFECYCLE_MODE='internal'" in rendered
    assert "export SOC_PINGAN_LEGACY_CALLBACK_MODE='internal'" in rendered
    assert "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS='false'" in rendered
    assert "export SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL='https://model.example'" in rendered
    assert "export SOC_PINGAN_WORKFLOW_ENV=stg" in rendered
    assert "export SOC_PINGAN_WORKFLOW_BASE_URL=https://agent-stg.example" in rendered
    assert "export SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS=agent-stg.example" in rendered
    assert "export SOC_PINGAN_WORKFLOW_APP_ID=YHSYS-STG" in rendered
    assert "export SOC_PINGAN_WORKFLOW_APP_SECRET=workflow-stg-secret" in rendered
    assert "export SOC_PINGAN_WORKFLOW_TERMINAL_ID=2087710" in rendered
    assert "export SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION=''" in rendered
    assert "export SOC_PINGAN_ASSET_PROVIDER_MODE='internal'" in rendered
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert report.previous_environment == "dev"
    assert report.environment == "stg"
    assert report.database_filename == "soc_agent_stg.db"
    assert report.workbenches_enabled is False
    assert report.demo_no_auth_allowed is False
    assert report.previous_zeus_target_environment == "prd"
    assert report.zeus_target_environment == "stg"
    assert report.previous_agent_platform_target_environment == "prd"
    assert report.agent_platform_target_environment == "stg"
    assert report.runtime_target_mapping_applied is True
    assert report.provider_modes_unchanged is True
    assert report.external_action_setting_unchanged is True
    assert report.restart_required is True


def test_runtime_environment_switch_applies_prd_remote_targets_for_dev(
    tmp_path: Path,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="stg")

    report = set_pingan_runtime_environment(env_path, environment="dev")

    rendered = env_path.read_text(encoding="utf-8")
    assert "export SOC_PINGAN_ENV=dev" in rendered
    assert "export SOC_PINGAN_ZEUS_ENV=prd" in rendered
    assert "export SOC_PINGAN_ZEUS_BASE_URL=https://zeus-prd.example" in rendered
    assert "export SOC_PINGAN_ZEUS_ALLOWED_HOSTS=zeus-prd.example" in rendered
    assert "export SOC_PINGAN_ZEUS_APP_ID=PRD-APP" in rendered
    assert "export SOC_PINGAN_ZEUS_APP_KEY=prd-secret" in rendered
    assert "export SOC_PINGAN_ZEUS_PRD_CONFIRMATION=CALL_PINGAN_ZEUS_PRD" in rendered
    assert "export SOC_PINGAN_WORKFLOW_ENV=prd" in rendered
    assert "export SOC_PINGAN_WORKFLOW_BASE_URL=https://agent-prd.example" in rendered
    assert "export SOC_PINGAN_WORKFLOW_APP_ID=YHSYS" in rendered
    assert "export SOC_PINGAN_WORKFLOW_APP_SECRET=workflow-prd-secret" in rendered
    assert "export SOC_PINGAN_WORKFLOW_TERMINAL_ID=1087710" in rendered
    assert "export SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION=CALL_PINGAN_PRD" in rendered
    assert report.previous_zeus_target_environment == "stg"
    assert report.zeus_target_environment == "prd"
    assert report.previous_agent_platform_target_environment == "stg"
    assert report.agent_platform_target_environment == "prd"
    assert report.restart_required is True


def test_runtime_environment_switch_repairs_target_drift(
    tmp_path: Path,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="stg")
    content = env_path.read_text(encoding="utf-8")
    env_path.write_text(
        content.replace(
            "export SOC_PINGAN_ZEUS_ENV='stg'",
            "export SOC_PINGAN_ZEUS_ENV='prd'",
        ),
        encoding="utf-8",
    )

    report = set_pingan_runtime_environment(env_path, environment="stg")

    assert report.previous_environment == "stg"
    assert report.previous_zeus_target_environment == "prd"
    assert report.zeus_target_environment == "stg"
    assert report.restart_required is True


def test_runtime_environment_switch_is_idempotent(tmp_path: Path) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="stg")

    report = set_pingan_runtime_environment(env_path, environment="stg")

    assert report.restart_required is False
    rendered = env_path.read_text(encoding="utf-8")
    assert rendered.count("SOC_PINGAN_ENV=") == 1
    assert rendered.count("SOC_PINGAN_ZEUS_ENV=") == 1
    assert rendered.count("SOC_PINGAN_WORKFLOW_ENV=") == 1


@pytest.mark.parametrize("environment", ["prd", "test", ""])
def test_runtime_environment_switch_rejects_unsupported_profiles(
    tmp_path: Path,
    environment: str,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="dev")

    with pytest.raises(
        PingAnRuntimeEnvironmentConfigurationError,
        match="dev or stg",
    ):
        set_pingan_runtime_environment(env_path, environment=environment)


def test_runtime_environment_switch_rejects_ambiguous_or_unsafe_env_file(
    tmp_path: Path,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="dev")
    env_path.write_text(
        env_path.read_text(encoding="utf-8") + "export SOC_PINGAN_ENV=stg\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)

    with pytest.raises(
        PingAnRuntimeEnvironmentConfigurationError,
        match="mode 0600",
    ):
        set_pingan_runtime_environment(env_path, environment="stg")

    env_path.chmod(0o600)
    with pytest.raises(
        PingAnRuntimeEnvironmentConfigurationError,
        match="exactly once",
    ):
        set_pingan_runtime_environment(env_path, environment="stg")


def test_runtime_environment_switch_rejects_missing_target_profile(
    tmp_path: Path,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="dev")
    env_path.write_text(
        "\n".join(line for line in env_path.read_text(encoding="utf-8").splitlines() if not line.startswith("export SOC_PINGAN_ZEUS_STG_APP_KEY=")) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        PingAnRuntimeEnvironmentConfigurationError,
        match="SOC_PINGAN_ZEUS_STG_APP_KEY must occur exactly once",
    ):
        set_pingan_runtime_environment(env_path, environment="stg")


def test_runtime_environment_switch_rejects_incomplete_agent_platform_profile(
    tmp_path: Path,
) -> None:
    env_path = _write_runtime_env(tmp_path, runtime_environment="dev")
    env_path.write_text(
        "\n".join(line for line in env_path.read_text(encoding="utf-8").splitlines() if not line.startswith("export SOC_PINGAN_WORKFLOW_STG_APP_SECRET=")) + "\n",
        encoding="utf-8",
    )
    original = env_path.read_bytes()

    with pytest.raises(
        PingAnRuntimeEnvironmentConfigurationError,
        match="SOC_PINGAN_WORKFLOW_STG_APP_SECRET must occur exactly once",
    ):
        set_pingan_runtime_environment(env_path, environment="stg")
    assert env_path.read_bytes() == original


def _write_runtime_env(
    tmp_path: Path,
    *,
    runtime_environment: str,
) -> Path:
    active_target = "prd" if runtime_environment == "dev" else "stg"
    active_prefix = active_target.upper()
    confirmation = "CALL_PINGAN_ZEUS_PRD" if active_target == "prd" else ""
    workflow_confirmation = "CALL_PINGAN_PRD" if active_target == "prd" else ""
    workflow_profile = {
        "prd": {
            "BASE_URL": "https://agent-prd.example",
            "ALLOWED_HOSTS": "agent-prd.example",
            "APP_ID": "YHSYS",
            "APP_SECRET": "workflow-prd-secret",
            "TERMINAL_ID": "1087710",
            "DATACENTER_ID": "1087787",
            "USER_ID": "1092332",
        },
        "stg": {
            "BASE_URL": "https://agent-stg.example",
            "ALLOWED_HOSTS": "agent-stg.example",
            "APP_ID": "YHSYS-STG",
            "APP_SECRET": "workflow-stg-secret",
            "TERMINAL_ID": "2087710",
            "DATACENTER_ID": "2087787",
            "USER_ID": "2092332",
        },
    }
    active_workflow = workflow_profile[active_target]
    values = {
        "SOC_PINGAN_ENV": runtime_environment,
        "SOC_PINGAN_ZEUS_ENV": active_target,
        "SOC_PINGAN_ZEUS_BASE_URL": f"https://zeus-{active_target}.example",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": f"zeus-{active_target}.example",
        "SOC_PINGAN_ZEUS_APP_ID": f"{active_prefix}-APP",
        "SOC_PINGAN_ZEUS_APP_KEY": f"{active_target}-secret",
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": confirmation,
        "SOC_PINGAN_ZEUS_PRD_BASE_URL": "https://zeus-prd.example",
        "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS": "zeus-prd.example",
        "SOC_PINGAN_ZEUS_PRD_APP_ID": "PRD-APP",
        "SOC_PINGAN_ZEUS_PRD_APP_KEY": "prd-secret",
        "SOC_PINGAN_ZEUS_STG_BASE_URL": "https://zeus-stg.example",
        "SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS": "zeus-stg.example",
        "SOC_PINGAN_ZEUS_STG_APP_ID": "STG-APP",
        "SOC_PINGAN_ZEUS_STG_APP_KEY": "stg-secret",
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "internal",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "internal",
        "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS": "false",
        "SOC_PINGAN_MODEL_GATEWAY_UPSTREAM_BASE_URL": "https://model.example",
        "SOC_PINGAN_WORKFLOW_ENV": active_target,
        "SOC_PINGAN_WORKFLOW_BASE_URL": active_workflow["BASE_URL"],
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": active_workflow["ALLOWED_HOSTS"],
        "SOC_PINGAN_WORKFLOW_APP_ID": active_workflow["APP_ID"],
        "SOC_PINGAN_WORKFLOW_APP_SECRET": active_workflow["APP_SECRET"],
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID": active_workflow["TERMINAL_ID"],
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID": active_workflow["DATACENTER_ID"],
        "SOC_PINGAN_WORKFLOW_USER_ID": active_workflow["USER_ID"],
        "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": workflow_confirmation,
        "SOC_PINGAN_ASSET_PROVIDER_MODE": "internal",
    }
    for target, profile in workflow_profile.items():
        prefix = f"SOC_PINGAN_WORKFLOW_{target.upper()}_"
        values.update({prefix + suffix: value for suffix, value in profile.items()})
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "".join(f"export {name}={value!r}\n" for name, value in values.items()),
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    return env_path
