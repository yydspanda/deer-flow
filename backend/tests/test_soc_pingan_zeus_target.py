from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.integrations.pingan.zeus_target import (
    PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS,
    PINGAN_ZEUS_PRD_CONFIRMATION,
    PingAnZeusTargetConfigurationError,
    enforce_pingan_runtime_zeus_mapping,
    load_pingan_zeus_target,
)


def test_load_pingan_zeus_target_allows_explicit_prd_from_dev_runtime() -> None:
    target = load_pingan_zeus_target(
        {
            "SOC_PINGAN_ENV": "dev",
            "SOC_PINGAN_ZEUS_ENV": "prd",
            "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
            "SOC_PINGAN_ZEUS_APP_KEY": "private-key",
            "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": PINGAN_ZEUS_PRD_CONFIRMATION,
        }
    )

    assert target.runtime_environment == "dev"
    assert target.target_environment == "prd"
    assert target.base_url == "https://isec-gw.paic.com.cn"
    assert target.allowed_hosts == ("isec-gw.paic.com.cn",)
    assert target.app_id == "SEC-MODEL"
    assert target.app_key == "private-key"


def test_governed_runtime_target_mapping_is_dev_to_prd_and_stg_to_stg() -> None:
    assert PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS == {
        "dev": "prd",
        "stg": "stg",
    }


def test_governed_runtime_target_mapping_rejects_stg_to_prd() -> None:
    target = load_pingan_zeus_target(
        {
            "SOC_PINGAN_ENV": "stg",
            "SOC_PINGAN_ZEUS_ENV": "prd",
            "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
            "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
            "SOC_PINGAN_ZEUS_APP_KEY": "private-key",
            "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": PINGAN_ZEUS_PRD_CONFIRMATION,
        }
    )

    with pytest.raises(
        PingAnZeusTargetConfigurationError,
        match="STG must target ZEUS STG",
    ):
        enforce_pingan_runtime_zeus_mapping(target)


def test_governed_runtime_target_mapping_accepts_stg_to_stg() -> None:
    target = load_pingan_zeus_target(
        {
            "SOC_PINGAN_ENV": "stg",
            "SOC_PINGAN_ZEUS_ENV": "stg",
            "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw-stg.paic.com.cn",
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw-stg.paic.com.cn",
            "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
            "SOC_PINGAN_ZEUS_APP_KEY": "private-key",
        }
    )

    enforce_pingan_runtime_zeus_mapping(target)


def test_load_pingan_zeus_target_rejects_prd_without_explicit_confirmation() -> None:
    with pytest.raises(
        PingAnZeusTargetConfigurationError,
        match="SOC_PINGAN_ZEUS_PRD_CONFIRMATION",
    ):
        load_pingan_zeus_target(
            {
                "SOC_PINGAN_ENV": "dev",
                "SOC_PINGAN_ZEUS_ENV": "prd",
                "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
                "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
                "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
                "SOC_PINGAN_ZEUS_APP_KEY": "private-key",
            }
        )


def test_load_pingan_zeus_target_rejects_host_outside_allowlist() -> None:
    with pytest.raises(
        PingAnZeusTargetConfigurationError,
        match="allowlist",
    ):
        load_pingan_zeus_target(
            {
                "SOC_PINGAN_ENV": "dev",
                "SOC_PINGAN_ZEUS_ENV": "prd",
                "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
                "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw-stg.paic.com.cn",
                "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
                "SOC_PINGAN_ZEUS_APP_KEY": "private-key",
                "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": PINGAN_ZEUS_PRD_CONFIRMATION,
            }
        )


def test_load_pingan_zeus_target_rejects_unknown_target_environment() -> None:
    with pytest.raises(
        PingAnZeusTargetConfigurationError,
        match="SOC_PINGAN_ZEUS_ENV",
    ):
        load_pingan_zeus_target(
            {
                "SOC_PINGAN_ENV": "dev",
                "SOC_PINGAN_ZEUS_ENV": "production",
                "SOC_PINGAN_ZEUS_BASE_URL": "https://isec-gw.paic.com.cn",
                "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "isec-gw.paic.com.cn",
                "SOC_PINGAN_ZEUS_APP_ID": "SEC-MODEL",
                "SOC_PINGAN_ZEUS_APP_KEY": "private-key",
            }
        )


def test_internal_mcp_profiles_forward_the_complete_shared_zeus_target() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    profiles = (
        ("samples/pingan_dev/extensions.example.json", "pingan_asset"),
        ("samples/pingan_dev/extensions.example.json", "pingan_threat_intel"),
        ("samples/pingan_dev/extensions.example.json", "pingan_security_tag"),
        ("samples/mcp/pingan_asset/extensions.internal.example.json", "pingan_asset"),
        (
            "samples/mcp/pingan_threat_intel/extensions.internal.example.json",
            "pingan_threat_intel",
        ),
        (
            "samples/mcp/pingan_security_tag/extensions.internal.example.json",
            "pingan_security_tag",
        ),
        ("samples/mcp/pingan_shadow/extensions.internal.json", "pingan_asset"),
        (
            "samples/mcp/pingan_shadow/extensions.internal.json",
            "pingan_security_tag",
        ),
    )
    required = {
        "SOC_PINGAN_ENV",
        "SOC_PINGAN_ZEUS_ENV",
        "SOC_PINGAN_ZEUS_BASE_URL",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_APP_ID",
        "SOC_PINGAN_ZEUS_APP_KEY",
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION",
    }

    for relative_path, server_name in profiles:
        payload = json.loads((backend_root / relative_path).read_text(encoding="utf-8"))
        environment = payload["mcpServers"][server_name]["env"]
        assert required <= environment.keys(), (relative_path, server_name)
