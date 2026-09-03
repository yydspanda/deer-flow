from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import yaml

from deerflow.config.app_config import AppConfig
from soc_agent.integrations.pingan.asset_location import (
    PingAnAssetLocationAttempt,
    PingAnAssetLocationResult,
    PingAnAssetProviderConfigurationError,
    PingAnAssetProviderUnavailableError,
)
from soc_agent.integrations.pingan.dev_validation import (
    PingAnDevPreflightCheck,
    PingAnDevPreflightReport,
    PingAnDevPreflightStatus,
    run_pingan_asset_direct_smoke,
    run_pingan_dev_preflight,
)


def test_pingan_dev_model_sample_loads_as_deerflow_profile(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config_path = repo_root / "backend" / "samples" / "pingan_dev" / "config.example.yaml"
    extensions_path = tmp_path / "extensions.json"
    extensions_path.write_text('{"mcpServers": {}, "skills": {}}', encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(extensions_path))
    monkeypatch.setenv("PINGAN_MODEL_GATEWAY_BASE_URL", "http://localhost:4001/v1")
    monkeypatch.setenv("PINGAN_MODEL_GATEWAY_API_KEY", "local-gateway-test-key")

    config = AppConfig.from_file(str(config_path))
    model = config.get_model_config("deepseek-v4-flash")

    assert model is not None
    assert model.model == "deepseek-v4-flash"
    assert model.api_base == "http://localhost:4001/v1"
    assert model.api_key == "local-gateway-test-key"
    assert config.database.backend == "sqlite"


def test_pingan_dev_sample_tracks_current_config_version() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    root_example = yaml.safe_load((repo_root / "config.example.yaml").read_text(encoding="utf-8"))
    pingan_example = yaml.safe_load((repo_root / "backend/samples/pingan_dev/config.example.yaml").read_text(encoding="utf-8"))

    assert pingan_example["config_version"] == root_example["config_version"]


def test_internal_asset_profiles_forward_self_contained_workflow_http_config() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    profile_paths = (
        repo_root / "backend/samples/pingan_dev/extensions.example.json",
        repo_root / "backend/samples/mcp/pingan_asset/extensions.internal.example.json",
        repo_root / "backend/samples/mcp/pingan_shadow/extensions.internal.json",
    )
    required = {
        "SOC_PINGAN_ENV",
        "SOC_PINGAN_ZEUS_ENV",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION",
        "SOC_PINGAN_WORKFLOW_ENV",
        "SOC_PINGAN_WORKFLOW_BASE_URL",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
        "SOC_PINGAN_WORKFLOW_APP_ID",
        "SOC_PINGAN_WORKFLOW_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION",
    }
    obsolete = {
        "SOC_PINGAN_PROVIDER_IMPORT_PATHS",
        "SOC_PINGAN_ZEUS_SIGNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_RUNNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_OPERATOR",
    }

    for profile_path in profile_paths:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        asset_env = payload["mcpServers"]["pingan_asset"]["env"]
        assert required <= set(asset_env), profile_path
        assert obsolete.isdisjoint(asset_env), profile_path


def test_pingan_uv_index_is_opt_in_and_does_not_pollute_project_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    profile = (repo_root / "backend/samples/pingan_dev/uv-index.env.example").read_text(encoding="utf-8")
    project = (repo_root / "backend/pyproject.toml").read_text(encoding="utf-8")

    assert 'UV_DEFAULT_INDEX="http://maven.paic.com.cn:8445/repository/pypi/simple/"' in profile
    assert 'UV_INSECURE_HOST="maven.paic.com.cn:8445"' in profile
    assert "maven.paic.com.cn" not in project
    assert "tool.poetry.source" not in project


def test_pingan_dev_preflight_validates_profile_without_network_or_secret_output(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.local"
    config_path.touch()
    env = _valid_env(config_path)
    constructed: list[dict[str, str]] = []

    def config_loader(path: str):
        assert path == str(config_path)
        return SimpleNamespace(
            get_model_config=lambda name: (
                SimpleNamespace(
                    api_base="http://localhost:4001/v1/",
                    api_key="local-proxy-secret",
                    model="deepseek-v4-flash",
                )
                if name == "deepseek-v4-flash"
                else None
            )
        )

    def locator_builder(values):
        constructed.append(dict(values))
        return object()

    report = run_pingan_dev_preflight(
        env,
        config_loader=config_loader,
        locator_builder=locator_builder,
    )

    assert report.ready is True
    assert all(item.status is PingAnDevPreflightStatus.PASSED for item in report.checks)
    assert constructed
    encoded = json.dumps(report.model_dump(mode="json"))
    assert "zeus-secret" not in encoded
    assert "local-proxy-secret" not in encoded
    assert "zeus.dev.example" not in encoded


def test_pingan_preflight_accepts_stg_runtime_with_mapped_stg_zeus_target(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.local"
    config_path.touch()
    env = _valid_env(config_path)
    env["SOC_PINGAN_ENV"] = "stg"
    env["SOC_PINGAN_ZEUS_ENV"] = "stg"
    env["SOC_PINGAN_ZEUS_BASE_URL"] = "https://zeus.stg.example"
    env["SOC_PINGAN_ZEUS_ALLOWED_HOSTS"] = "zeus.stg.example"
    env.pop("SOC_PINGAN_ZEUS_PRD_CONFIRMATION")
    _use_stg_agent_platform(env)

    report = run_pingan_dev_preflight(
        env,
        config_loader=lambda _: SimpleNamespace(
            get_model_config=lambda __: SimpleNamespace(
                api_base="http://localhost:4001/v1/",
                api_key="key",
            )
        ),
        locator_builder=lambda _: object(),
    )

    assert report.ready is True
    assert report.environment == "stg"
    environment_check = next(item for item in report.checks if item.check_id == "environment.non_production")
    assert environment_check.status is PingAnDevPreflightStatus.PASSED


def test_pingan_preflight_rejects_stg_runtime_with_prd_zeus_target(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.local"
    config_path.touch()
    env = _valid_env(config_path)
    env["SOC_PINGAN_ENV"] = "stg"
    _use_stg_agent_platform(env)

    report = run_pingan_dev_preflight(
        env,
        config_loader=lambda _: SimpleNamespace(
            get_model_config=lambda __: SimpleNamespace(
                api_base="http://localhost:4001/v1/",
                api_key="key",
            )
        ),
        locator_builder=lambda _: object(),
    )

    failed_ids = {item.check_id for item in report.checks if item.status is PingAnDevPreflightStatus.FAILED}
    assert failed_ids == {"provider.zeus_target_guard"}


def test_pingan_dev_preflight_rejects_fake_mode_and_unapproved_host(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.local"
    config_path.touch()
    env = _valid_env(config_path)
    env["SOC_PINGAN_ASSET_PROVIDER_MODE"] = "fake"
    env["SOC_PINGAN_ZEUS_BASE_URL"] = "https://production.example"
    called = False

    def locator_builder(values):
        nonlocal called
        called = True
        return object()

    report = run_pingan_dev_preflight(
        env,
        config_loader=lambda _: SimpleNamespace(
            get_model_config=lambda __: SimpleNamespace(
                api_base="http://localhost:4001/v1/",
                api_key="key",
            )
        ),
        locator_builder=locator_builder,
    )

    assert report.ready is False
    assert called is False
    failed_ids = {item.check_id for item in report.checks if item.status is PingAnDevPreflightStatus.FAILED}
    assert failed_ids == {
        "provider.internal_mode",
        "provider.zeus_target_guard",
    }


def test_pingan_dev_preflight_rejects_unconfirmed_zeus_prd(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.local"
    config_path.touch()
    env = _valid_env(config_path)
    env.pop("SOC_PINGAN_ZEUS_PRD_CONFIRMATION")

    report = run_pingan_dev_preflight(
        env,
        config_loader=lambda _: SimpleNamespace(
            get_model_config=lambda __: SimpleNamespace(
                api_base="http://localhost:4001/v1/",
                api_key="key",
            )
        ),
        locator_builder=lambda _: object(),
    )

    failed_ids = {item.check_id for item in report.checks if item.status is PingAnDevPreflightStatus.FAILED}
    assert failed_ids == {"provider.zeus_target_guard"}


def test_pingan_dev_preflight_sanitizes_transport_construction_failure(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.local"
    config_path.touch()
    env = _valid_env(config_path)

    def locator_builder(_values):
        raise PingAnAssetProviderConfigurationError("workflow configuration contains secret-value")

    report = run_pingan_dev_preflight(
        env,
        config_loader=lambda _: SimpleNamespace(
            get_model_config=lambda __: SimpleNamespace(
                api_base="http://localhost:4001/v1/",
                api_key="key",
            )
        ),
        locator_builder=locator_builder,
    )

    failed = next(item for item in report.checks if item.check_id == "provider.imports_and_construction")
    assert failed.status is PingAnDevPreflightStatus.FAILED
    assert "typed ZEUS and Agent Platform HTTP configuration" in failed.detail
    assert "secret-value" not in failed.detail


def test_direct_smoke_reports_internal_found_result_without_raw_query() -> None:
    preflight = _ready_preflight()

    class Locator:
        def locate(self, _query):
            return PingAnAssetLocationResult(
                query="10.0.0.8",
                asset_type="IP",
                role="victim",
                found=True,
                resolved=True,
                ambiguous=False,
                company_code="PA011",
                company_name="Example",
                source="zeus_search_asset_info",
                candidates=[
                    {
                        "company_code": "PA011",
                        "company_name": "Example",
                        "source": "zeus_search_asset_info",
                        "matched_asset_type": "IP",
                    }
                ],
                attempts=[
                    {
                        "stage": "search_asset_info",
                        "lookup_kind": "IP",
                        "status": "found",
                        "candidate_count": 1,
                        "response_code": 200,
                        "mocked": False,
                    }
                ],
                mocked=False,
                provider_mode="internal",
            )

    report = run_pingan_asset_direct_smoke(
        {"query": "10.0.0.8", "asset_type": "IP", "role": "victim"},
        environ={},
        preflight_runner=lambda _: preflight,
        locator_builder=lambda _: Locator(),
    )

    assert report.outcome == "found"
    assert report.result is not None
    assert report.result["query"] == "<omitted; see query_hash>"
    assert report.result["mocked"] is False
    assert report.result["decision_impact"] == "none"


def test_direct_smoke_classifies_authentication_failure_from_sanitized_attempt() -> None:
    preflight = _ready_preflight()
    attempt = PingAnAssetLocationAttempt(
        stage="search_asset_info",
        lookup_kind="IP",
        status="failed",
        mocked=False,
        error_type="HTTPStatusError",
        error_message="provider returned HTTP 401",
    )

    class Locator:
        def locate(self, _query):
            raise PingAnAssetProviderUnavailableError(
                "PingAn asset location provider failed at search_asset_info",
                attempts=[attempt],
            )

    report = run_pingan_asset_direct_smoke(
        {"query": "10.0.0.8", "asset_type": "IP"},
        environ={},
        preflight_runner=lambda _: preflight,
        locator_builder=lambda _: Locator(),
    )

    assert report.outcome == "authentication_failed"
    assert report.result is None
    assert report.attempts == [attempt]


def _valid_env(config_path: Path) -> dict[str, str]:
    return {
        "SOC_PINGAN_ENV": "dev",
        "SOC_PINGAN_ASSET_PROVIDER_MODE": "internal",
        "SOC_PINGAN_ZEUS_ENV": "prd",
        "SOC_PINGAN_ZEUS_BASE_URL": "https://zeus.prd.example",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "zeus.prd.example",
        "SOC_PINGAN_ZEUS_APP_ID": "app-id",
        "SOC_PINGAN_ZEUS_APP_KEY": "zeus-secret",
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": "CALL_PINGAN_ZEUS_PRD",
        "SOC_PINGAN_WORKFLOW_ENV": "prd",
        "SOC_PINGAN_WORKFLOW_BASE_URL": "https://agent-prd.example",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": "agent-prd.example",
        "SOC_PINGAN_WORKFLOW_APP_ID": "YHSYS",
        "SOC_PINGAN_WORKFLOW_APP_SECRET": "workflow-secret",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID": "1087710",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID": "1087787",
        "SOC_PINGAN_WORKFLOW_USER_ID": "1092332",
        "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": "CALL_PINGAN_PRD",
        "DEER_FLOW_CONFIG_PATH": str(config_path),
        "SOC_LLM_MODEL": "deepseek-v4-flash",
    }


def _use_stg_agent_platform(env: dict[str, str]) -> None:
    env.update(
        {
            "SOC_PINGAN_WORKFLOW_ENV": "stg",
            "SOC_PINGAN_WORKFLOW_BASE_URL": "https://agent-stg.example",
            "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": "agent-stg.example",
            "SOC_PINGAN_WORKFLOW_APP_ID": "YHSYS-STG",
            "SOC_PINGAN_WORKFLOW_APP_SECRET": "workflow-stg-secret",
            "SOC_PINGAN_WORKFLOW_TERMINAL_ID": "2087710",
            "SOC_PINGAN_WORKFLOW_DATACENTER_ID": "2087787",
            "SOC_PINGAN_WORKFLOW_USER_ID": "2092332",
            "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": "",
        }
    )


def _ready_preflight() -> PingAnDevPreflightReport:
    return PingAnDevPreflightReport(
        environment="dev",
        provider_mode="internal",
        model_profile="deepseek-v4-flash",
        ready=True,
        checks=[
            PingAnDevPreflightCheck(
                check_id="test",
                status="passed",
                detail="ready",
            )
        ],
    )
