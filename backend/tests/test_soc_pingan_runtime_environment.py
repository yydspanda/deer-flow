from __future__ import annotations

import stat
from pathlib import Path

import pytest

from soc_agent.integrations.pingan.runtime_environment import (
    PingAnRuntimeEnvironmentConfigurationError,
    set_pingan_runtime_environment,
)


def test_runtime_environment_switch_changes_only_runtime_selector(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    original = "\n".join(
        (
            "export SOC_PINGAN_ENV=dev",
            "export SOC_PINGAN_ZEUS_ENV=prd",
            "export SOC_PINGAN_ZEUS_APP_KEY=secret-value",
            "export SOC_PINGAN_LEGACY_LIFECYCLE_MODE=internal",
            "export SOC_PINGAN_LEGACY_CALLBACK_MODE=internal",
            "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false",
            "",
        )
    )
    env_path.write_text(original, encoding="utf-8")
    env_path.chmod(0o600)

    report = set_pingan_runtime_environment(env_path, environment="stg")

    rendered = env_path.read_text(encoding="utf-8")
    assert "export SOC_PINGAN_ENV=stg" in rendered
    assert "export SOC_PINGAN_ZEUS_ENV=prd" in rendered
    assert "export SOC_PINGAN_ZEUS_APP_KEY=secret-value" in rendered
    assert "export SOC_PINGAN_LEGACY_LIFECYCLE_MODE=internal" in rendered
    assert "export SOC_PINGAN_LEGACY_CALLBACK_MODE=internal" in rendered
    assert "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false" in rendered
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert report.previous_environment == "dev"
    assert report.environment == "stg"
    assert report.database_filename == "soc_agent_stg.db"
    assert report.workbenches_enabled is False
    assert report.demo_no_auth_allowed is False
    assert report.zeus_target_environment == "prd"
    assert report.zeus_target_unchanged is True
    assert report.provider_modes_unchanged is True
    assert report.external_action_setting_unchanged is True
    assert report.restart_required is True


def test_runtime_environment_switch_is_idempotent(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_ENV=stg\nexport SOC_PINGAN_ZEUS_ENV=prd\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    report = set_pingan_runtime_environment(env_path, environment="stg")

    assert report.restart_required is False
    assert env_path.read_text(encoding="utf-8").count("SOC_PINGAN_ENV=") == 1


@pytest.mark.parametrize("environment", ["prd", "test", ""])
def test_runtime_environment_switch_rejects_unsupported_profiles(
    tmp_path: Path,
    environment: str,
) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_ENV=dev\nexport SOC_PINGAN_ZEUS_ENV=prd\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    with pytest.raises(
        PingAnRuntimeEnvironmentConfigurationError,
        match="dev or stg",
    ):
        set_pingan_runtime_environment(env_path, environment=environment)


def test_runtime_environment_switch_rejects_ambiguous_or_unsafe_env_file(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_ENV=dev\nexport SOC_PINGAN_ENV=stg\n",
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
