from __future__ import annotations

from pathlib import Path

import pytest

from soc_agent.integrations.pingan.legacy_compat.wiring import (
    PingAnLegacyApiSettings,
    PingAnLegacyProviderMode,
    PingAnLegacyWorkerSettings,
    build_pingan_callback_port,
    build_pingan_lifecycle_service,
)


def test_api_settings_preserve_old_port_and_parse_per_app_keys(
    tmp_path: Path,
) -> None:
    settings = PingAnLegacyApiSettings.from_env(
        {
            "SOC_DATABASE_URL": f"sqlite:///{tmp_path / 'soc.db'}",
            "SOC_PINGAN_COMPAT_APP_KEYS_JSON": ('{"common":"common-key","zeus":"zeus-key"}'),
        }
    )

    assert settings.bind_host == "127.0.0.1"
    assert settings.port == 8090
    assert settings.queue_ttl_seconds == 1_800
    assert settings.app_keys == {
        "common": "common-key",
        "zeus": "zeus-key",
    }
    assert "zeus-key" not in repr(settings)


def test_api_settings_allow_operator_owned_queue_deadline(tmp_path: Path) -> None:
    settings = PingAnLegacyApiSettings.from_env(
        {
            "SOC_DATABASE_URL": f"sqlite:///{tmp_path / 'soc.db'}",
            "SOC_PINGAN_COMPAT_APP_KEYS_JSON": '{"common":"common-key"}',
            "SOC_PINGAN_LEGACY_QUEUE_TTL_SECONDS": "172800",
        }
    )

    assert settings.queue_ttl_seconds == 172_800


def test_worker_settings_force_one_sqlite_worker(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SQLite.*one worker"):
        PingAnLegacyWorkerSettings.from_env(
            {
                "SOC_DATABASE_URL": f"sqlite:///{tmp_path / 'soc.db'}",
                "SOC_PINGAN_LEGACY_WORKER_CONCURRENCY": "2",
            }
        )


def test_fake_provider_mode_needs_no_internal_credentials(tmp_path: Path) -> None:
    values = {
        "SOC_DATABASE_URL": f"sqlite:///{tmp_path / 'soc.db'}",
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "fake",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "fake",
    }
    settings = PingAnLegacyWorkerSettings.from_env(values)
    lifecycle = build_pingan_lifecycle_service(settings, environ=values)
    callback = build_pingan_callback_port(settings, environ=values)

    assert settings.lifecycle_mode is PingAnLegacyProviderMode.FAKE
    assert settings.callback_mode is PingAnLegacyProviderMode.FAKE
    assert lifecycle.check("A-1").mocked is True
    callback_result = callback.send({"taskId": "JOB-1", "status": "SUCCESS"})
    assert callback_result["provider_code"] == "200"
    assert callback_result["mocked"] is True


def test_internal_provider_mode_fails_closed_without_zeus_credentials(
    tmp_path: Path,
) -> None:
    values = {
        "SOC_DATABASE_URL": f"sqlite:///{tmp_path / 'soc.db'}",
        "SOC_PINGAN_LEGACY_LIFECYCLE_MODE": "internal",
        "SOC_PINGAN_LEGACY_CALLBACK_MODE": "internal",
    }
    settings = PingAnLegacyWorkerSettings.from_env(values)

    with pytest.raises(ValueError, match="SOC_PINGAN_ZEUS_BASE_URL"):
        build_pingan_lifecycle_service(settings, environ=values)
    with pytest.raises(ValueError, match="SOC_PINGAN_ZEUS_BASE_URL"):
        build_pingan_callback_port(settings, environ=values)
