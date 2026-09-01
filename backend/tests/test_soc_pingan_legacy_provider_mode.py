from __future__ import annotations

import stat
from pathlib import Path

import pytest

from soc_agent.integrations.pingan.legacy_compat.provider_mode import (
    PingAnLegacyProviderModeConfigurationError,
    set_pingan_legacy_provider_mode,
)


def test_set_legacy_provider_mode_updates_both_values_atomically(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export KEEP_ME=unchanged\nexport SOC_PINGAN_LEGACY_LIFECYCLE_MODE=fake\nexport SOC_PINGAN_LEGACY_CALLBACK_MODE='fake'\n",
        encoding="utf-8",
    )

    report = set_pingan_legacy_provider_mode(env_path, mode="internal")

    assert env_path.read_text(encoding="utf-8") == ("export KEEP_ME=unchanged\nexport SOC_PINGAN_LEGACY_LIFECYCLE_MODE=internal\nexport SOC_PINGAN_LEGACY_CALLBACK_MODE=internal\n")
    assert report.mode == "internal"
    assert report.previous_modes == {
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "fake",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "fake",
    }
    assert report.restart_required is True
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600


def test_set_legacy_provider_mode_rejects_missing_or_duplicate_keys(
    tmp_path: Path,
) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_LEGACY_LIFECYCLE_MODE=fake\n",
        encoding="utf-8",
    )
    with pytest.raises(
        PingAnLegacyProviderModeConfigurationError,
        match="exactly once",
    ):
        set_pingan_legacy_provider_mode(env_path, mode="internal")

    env_path.write_text(
        "export SOC_PINGAN_LEGACY_LIFECYCLE_MODE=fake\nexport SOC_PINGAN_LEGACY_LIFECYCLE_MODE=fake\nexport SOC_PINGAN_LEGACY_CALLBACK_MODE=fake\n",
        encoding="utf-8",
    )
    with pytest.raises(
        PingAnLegacyProviderModeConfigurationError,
        match="exactly once",
    ):
        set_pingan_legacy_provider_mode(env_path, mode="internal")


def test_set_legacy_provider_mode_rejects_unparseable_value(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export SOC_PINGAN_LEGACY_LIFECYCLE_MODE=$(unsafe)\nexport SOC_PINGAN_LEGACY_CALLBACK_MODE=fake\n",
        encoding="utf-8",
    )

    with pytest.raises(
        PingAnLegacyProviderModeConfigurationError,
        match="unsupported current value",
    ):
        set_pingan_legacy_provider_mode(env_path, mode="internal")
