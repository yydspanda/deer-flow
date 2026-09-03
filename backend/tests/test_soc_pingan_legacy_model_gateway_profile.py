from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from soc_agent.integrations.pingan.legacy_model_gateway_profile import (
    PingAnLegacyModelGatewayProfileError,
    load_legacy_model_gateway_profile,
    prepare_legacy_model_gateway_env,
)


def test_load_legacy_model_gateway_profile_resolves_named_literals(tmp_path: Path) -> None:
    model_source, root_source, zeus_source, _key_hex = _legacy_sources(tmp_path)

    profile = load_legacy_model_gateway_profile(
        model_source,
        root_config_path=root_source,
        zeus_credential_source_path=zeus_source,
    )

    assert profile.environment == "stg"
    assert profile.model_config_name == "DeepSeek_V4_Flash"
    assert profile.base_url == "http://eagw.example.internal:10086/pingan/bigModel/api/v1"
    assert profile.allowed_host == "eagw.example.internal"
    assert profile.scene_id == "1737"
    assert profile.openapi_code == "API035059"
    assert profile.openapi_credential == "CRE-TEST"
    assert profile.compat_app_keys == {"common": "compat-secret"}
    assert profile.zeus_environment == "prd"
    assert profile.zeus_base_url == "https://isec-gw.example.internal"
    assert profile.zeus_allowed_host == "isec-gw.example.internal"
    assert profile.zeus_app_id == "SEC-MODEL"
    assert profile.zeus_stg_base_url == "https://isec-gw-stg.example.internal"
    assert profile.zeus_stg_allowed_host == "isec-gw-stg.example.internal"
    assert profile.zeus_stg_app_id == "SEC-MODEL"


def test_prepare_legacy_model_gateway_env_writes_private_files_without_secret_report(
    tmp_path: Path,
) -> None:
    model_source, root_source, zeus_source, key_hex = _legacy_sources(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "\n".join(
            [
                'export PINGAN_LITELLM_BASE_URL="http://localhost:4001/v1/"',
                'export PINGAN_LITELLM_API_KEY="local-gateway-secret"',
                'export PINGAN_LITELLM_MODEL="deepseek-v4-flash"',
                'export D12B_TIMEOUT_ZEUS_BASE_URL="https://retired.example"',
                'export D12B_TIMEOUT_ZEUS_ALLOWED_HOSTS="retired.example"',
                'export SOC_PINGAN_ENV="dev"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    env_path.chmod(0o600)
    key_path = tmp_path / ".secrets/eagw-private-key.der"

    report = prepare_legacy_model_gateway_env(
        repo_root=tmp_path,
        model_source_path=model_source,
        root_config_path=root_source,
        zeus_credential_source_path=zeus_source,
        env_path=env_path,
        key_path=key_path,
        apply=True,
    )

    rendered = env_path.read_text(encoding="utf-8")
    assert "PINGAN_LITELLM_" not in rendered
    assert "D12B_TIMEOUT_ZEUS_BASE_URL" not in rendered
    assert "D12B_TIMEOUT_ZEUS_ALLOWED_HOSTS" not in rendered
    assert "D12B_TIMEOUT_SECONDS=0.001" in rendered
    assert "PINGAN_MODEL_GATEWAY_BASE_URL=http://127.0.0.1:4001/v1" in rendered
    assert "PINGAN_MODEL_GATEWAY_API_KEY=local-gateway-secret" in rendered
    assert "PINGAN_MODEL_GATEWAY_SMOKE_THINKING_ENABLED=false" in rendered
    assert "PINGAN_MODEL_GATEWAY_SMOKE_MAX_TOKENS=128" in rendered
    assert "SOC_LLM_THINKING_ENABLED=false" in rendered
    assert "SOC_PINGAN_MODEL_GATEWAY_PROVIDER=eagw" in rendered
    assert "SOC_PINGAN_MODEL_GATEWAY_SCENE_ID=1737" in rendered
    assert "SOC_PINGAN_MODEL_GATEWAY_MAX_CONCURRENCY=3" in rendered
    assert "SOC_LLM_MAX_CONCURRENCY=3" in rendered
    assert "SOC_PINGAN_LEGACY_LIFECYCLE_MODE=fake" in rendered
    assert "SOC_PINGAN_LEGACY_CALLBACK_MODE=fake" in rendered
    assert "SOC_PINGAN_ZEUS_ENV=prd" in rendered
    assert "SOC_PINGAN_ZEUS_BASE_URL=https://isec-gw.example.internal" in rendered
    assert "SOC_PINGAN_ZEUS_ALLOWED_HOSTS=isec-gw.example.internal" in rendered
    assert "SOC_PINGAN_ZEUS_APP_ID=SEC-MODEL" in rendered
    assert "SOC_PINGAN_ZEUS_APP_KEY=zeus-prd-secret" in rendered
    assert "SOC_PINGAN_ZEUS_PRD_CONFIRMATION=CALL_PINGAN_ZEUS_PRD" in rendered
    assert "SOC_PINGAN_ZEUS_PRD_BASE_URL=https://isec-gw.example.internal" in rendered
    assert "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS=isec-gw.example.internal" in rendered
    assert "SOC_PINGAN_ZEUS_PRD_APP_ID=SEC-MODEL" in rendered
    assert "SOC_PINGAN_ZEUS_PRD_APP_KEY=zeus-prd-secret" in rendered
    assert "SOC_PINGAN_ZEUS_STG_BASE_URL=https://isec-gw-stg.example.internal" in rendered
    assert "SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS=isec-gw-stg.example.internal" in rendered
    assert "SOC_PINGAN_ZEUS_STG_APP_ID=SEC-MODEL" in rendered
    assert "SOC_PINGAN_ZEUS_STG_APP_KEY=zeus-stg-secret" in rendered
    assert "SOC_PINGAN_COMPAT_APP_KEYS_JSON=" in rendered
    assert "${SOC_REPO_ROOT}/.secrets/eagw-private-key.der" in rendered
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    loaded_key = serialization.load_der_private_key(key_path.read_bytes(), password=None)
    assert isinstance(loaded_key, rsa.RSAPrivateKey)

    serialized = json.dumps(report, ensure_ascii=False)
    assert "model-app-secret" not in serialized
    assert "compat-secret" not in serialized
    assert "local-gateway-secret" not in serialized
    assert "zeus-prd-secret" not in serialized
    assert "zeus-stg-secret" not in serialized
    assert key_hex not in serialized
    assert report["credential_present"] is True
    assert report["compatibility_key_present"] is True
    assert report["runtime_environment"] == "dev"
    assert report["active_zeus_environment"] == "prd"
    assert report["runtime_target_mapping"] == {"dev": "prd", "stg": "stg"}
    assert report["secret_in_output"] is False
    assert report["applied"] is True

    replay = prepare_legacy_model_gateway_env(
        repo_root=tmp_path,
        model_source_path=model_source,
        root_config_path=root_source,
        zeus_credential_source_path=zeus_source,
        env_path=env_path,
        key_path=key_path,
        apply=True,
    )
    assert replay["env_changed"] is False
    assert replay["key_changed"] is False


def test_prepare_legacy_model_gateway_env_requires_private_loopback_key(
    tmp_path: Path,
) -> None:
    model_source, root_source, zeus_source, _key_hex = _legacy_sources(tmp_path)

    with pytest.raises(
        PingAnLegacyModelGatewayProfileError,
        match="private env must provide",
    ):
        prepare_legacy_model_gateway_env(
            repo_root=tmp_path,
            model_source_path=model_source,
            root_config_path=root_source,
            zeus_credential_source_path=zeus_source,
            env_path=tmp_path / ".env.soc-dev.local",
            key_path=tmp_path / ".secrets/eagw-private-key.der",
            apply=True,
        )


def test_prepare_legacy_model_gateway_env_preserves_selected_stg_runtime(
    tmp_path: Path,
) -> None:
    model_source, root_source, zeus_source, _key_hex = _legacy_sources(tmp_path)
    env_path = tmp_path / ".env.soc-dev.local"
    env_path.write_text(
        "export PINGAN_MODEL_GATEWAY_API_KEY=local-gateway-secret\nexport SOC_PINGAN_ENV=stg\n",
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    report = prepare_legacy_model_gateway_env(
        repo_root=tmp_path,
        model_source_path=model_source,
        root_config_path=root_source,
        zeus_credential_source_path=zeus_source,
        env_path=env_path,
        key_path=tmp_path / ".secrets/eagw-private-key.der",
        apply=True,
    )

    rendered = env_path.read_text(encoding="utf-8")
    assert "export SOC_PINGAN_ENV=stg" in rendered
    assert "export SOC_PINGAN_ZEUS_ENV=stg" in rendered
    assert "export SOC_PINGAN_ZEUS_BASE_URL=https://isec-gw-stg.example.internal" in rendered
    assert "export SOC_PINGAN_ZEUS_APP_KEY=zeus-stg-secret" in rendered
    assert "export SOC_PINGAN_ZEUS_PRD_CONFIRMATION=''" in rendered
    assert report["runtime_environment"] == "stg"
    assert report["active_zeus_environment"] == "stg"


def _legacy_sources(tmp_path: Path) -> tuple[Path, Path, Path, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    key_hex = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    model_source = tmp_path / "openai_completion.py"
    model_source.write_text(
        "\n".join(
            [
                'OPENAPI_CHAT_CODE = "API035059"',
                'stg_base_url = "http://eagw.example.internal:10086/pingan/bigModel/api/v1/"',
                f'stg_rsa_private_key_hex = "{key_hex}"',
                'openapi_credential = "CRE-TEST"',
                "STG_MODEL_CONFIGS = {",
                '    "DeepSeek_V4_Flash": {',
                '        "type": "EAGW",',
                '        "app_key": "model-app-key",',
                '        "app_secret": "model-app-secret",',
                '        "scene_id": 1737,',
                '        "base_url": stg_base_url,',
                '        "rsa_private_key_hex": stg_rsa_private_key_hex,',
                '        "openapi_credential": openapi_credential,',
                "    }",
                "}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    root_source = tmp_path / "root_config.py"
    root_source.write_text(
        "\n".join(
            [
                'APP_KEY = {"common": "compat-secret"}',
                'ENV = "LOCAL"',
                'if ENV == "LOCAL":',
                '    ZEUS_SYSTEM_URL = "https://isec-gw-stg.example.internal"',
                '    ZEUS_APP_ID = "SEC-MODEL"',
                '    ZEUS_APP_KEY = "zeus-stg-secret"',
                'elif ENV == "STG":',
                '    ZEUS_SYSTEM_URL = "https://isec-gw-stg.example.internal"',
                '    ZEUS_APP_ID = "SEC-MODEL"',
                "else:",
                '    ZEUS_SYSTEM_URL = "https://isec-gw.example.internal"',
                '    ZEUS_APP_ID = "SEC-MODEL"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    zeus_source = tmp_path / "black_white_tag_client.py"
    zeus_source.write_text(
        "\n".join(
            [
                'ZEUS_APP_KEY = "imported-runtime-value"',
                'if __name__ == "__main__":',
                '    ZEUS_APP_ID = "SEC-MODEL"',
                '    ZEUS_APP_KEY = "zeus-prd-secret"',
                '    ZEUS_SYSTEM_URL = "https://isec-gw.example.internal"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    return model_source, root_source, zeus_source, key_hex
