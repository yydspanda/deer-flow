from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.soc_pingan_macos_host_dev as host_dev
from scripts.soc_pingan_macos_host_dev import (
    HostDevError,
    _validate_locked_requirements,
    apply_runtime_profile,
    build_install_commands,
    build_nginx_test_command,
    build_start_command,
    build_start_environment,
    constrain_sidecar_bindings,
    discover_private_lan_ipv4s,
    expected_pnpm_version,
    is_public_npm_registry,
    normalize_allowed_origins,
    normalize_internal_npm_registry,
    normalize_runtime_environment,
    parse_args,
    parse_version,
    prepare_soc_database,
    resolve_start_allowed_origins,
    soc_database_status,
    validate_local_config_profile,
)


def test_direct_script_entry_ignores_unrelated_installed_scripts_package(
    tmp_path: Path,
) -> None:
    fake_package = tmp_path / "site-packages" / "scripts"
    fake_package.mkdir(parents=True)
    (fake_package / "__init__.py").write_text("", encoding="utf-8")
    script = Path(__file__).with_name("soc_pingan_macos_host_dev.py")
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(fake_package.parent)

    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=tmp_path,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Prepare and run PingAn SOC DEV" in completed.stdout


def test_local_config_profile_requires_project_gateway_and_sqlite(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.pingan-dev.local"
    config.write_text(
        """models:
  - model: deepseek-v4-flash
    api_base: $PINGAN_MODEL_GATEWAY_BASE_URL
    api_key: $PINGAN_MODEL_GATEWAY_API_KEY
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data
""",
        encoding="utf-8",
    )

    validate_local_config_profile(config)


def test_local_config_profile_rejects_obsolete_litellm_reference(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.pingan-dev.local"
    config.write_text(
        """models:
  - model: deepseek-v4-flash
    api_base: $PINGAN_LITELLM_BASE_URL
database:
  backend: sqlite
  sqlite_dir: .deer-flow/data
""",
        encoding="utf-8",
    )

    with pytest.raises(HostDevError, match="obsolete LiteLLM"):
        validate_local_config_profile(config)


def test_parse_version_accepts_tool_version_shapes() -> None:
    assert parse_version("Python 3.12.7", label="Python") == (3, 12, 7)
    assert parse_version("v24.1.0", label="Node") == (24, 1, 0)
    assert parse_version("nginx version: nginx/1.23.4", label="nginx") == (1, 23, 4)


def test_parse_version_rejects_unknown_output() -> None:
    with pytest.raises(HostDevError, match="cannot parse uv version"):
        parse_version("development build", label="uv")


@pytest.mark.parametrize(
    ("registry", "expected"),
    [
        ("https://registry.npmjs.org/", True),
        ("https://registry.yarnpkg.com", True),
        ("https://registry.npmmirror.com/", True),
        ("http://maven.paic.com.cn/repository/npm-group/", False),
    ],
)
def test_public_npm_registry_detection(registry: str, expected: bool) -> None:
    assert is_public_npm_registry(registry) is expected


def test_internal_registry_normalization_uses_last_url_line() -> None:
    assert (
        normalize_internal_npm_registry(
            "configuration notice\nhttp://maven.paic.com.cn/repository/npm-group/\n"
        )
        == "http://maven.paic.com.cn/repository/npm-group/"
    )


@pytest.mark.parametrize(
    "registry",
    ["undefined", "https://registry.npmjs.org/"],
)
def test_internal_registry_normalization_rejects_unsafe_values(registry: str) -> None:
    with pytest.raises(HostDevError):
        normalize_internal_npm_registry(registry)


def test_expected_pnpm_version_reads_package_manager_pin(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "package.json").write_text(
        json.dumps({"packageManager": "pnpm@10.26.2"}), encoding="utf-8"
    )

    assert expected_pnpm_version(tmp_path) == "10.26.2"


def test_install_plan_uses_frozen_hash_export_and_internal_mirror(
    tmp_path: Path,
) -> None:
    commands = build_install_commands(
        root=tmp_path,
        python_executable="/opt/python3.12",
    )

    flattened = [part for _, command in commands for part in command]
    assert "export" in flattened
    assert "--frozen" in flattened
    assert "--no-emit-workspace" in flattened
    assert "pip" in flattened
    assert "sync" in flattened
    assert "--require-hashes" in flattened
    assert "--no-deps" in flattened
    assert "--editable" in flattened
    assert "--frozen-lockfile" in flattened
    assert "pingan-dev" in flattened
    assert "/opt/python3.12" in flattened
    assert "--locked" not in flattened
    assert "--offline" not in flattened
    assert all(command[:2] != ["uv", "lock"] for _, command in commands)
    assert all(command[:2] != ["uv", "sync"] for _, command in commands)
    assert all("docker" not in part.lower() for part in flattened)


def test_locked_requirements_accepts_pinned_hashes(tmp_path: Path) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(
        "example==1.2.3 --hash=sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )

    _validate_locked_requirements(requirements)

    assert requirements.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    "content",
    [
        "example @ https://packages.example.invalid/example.whl\n",
        "-e ./packages/harness\n",
        "\n",
    ],
)
def test_locked_requirements_rejects_non_mirror_inputs(
    tmp_path: Path, content: str
) -> None:
    requirements = tmp_path / "requirements.txt"
    requirements.write_text(content, encoding="utf-8")

    with pytest.raises(HostDevError):
        _validate_locked_requirements(requirements)


def test_start_plan_skips_install_and_enables_governed_policy_without_network_side_effects() -> (
    None
):
    command = build_start_command(daemon=True, runtime_environment="dev")
    environment = build_start_environment({"PATH": "/usr/bin"})

    assert "--skip-install" in command[2]
    assert 'scripts/serve.sh" --dev' in command[2]
    assert command[-1] == "--daemon"
    assert all("docker" not in part.lower() for part in command)
    assert environment["NEXT_TELEMETRY_DISABLED"] == "1"
    assert environment["DO_NOT_TRACK"] == "1"
    assert environment["UV_OFFLINE"] == "1"
    assert environment["SOC_REPO_ROOT"] == str(Path(__file__).resolve().parents[1])
    assert "export SOC_DEV_MEMORY_WORKBENCH_ENABLED=true" in command[2]
    assert "export SOC_DEV_CORPUS_WORKBENCH_ENABLED=true" in command[2]
    assert (
        'export SOC_LLM_MAX_CONCURRENCY="${SOC_LLM_MAX_CONCURRENCY:-3}"' in command[2]
    )
    assert (
        'export SOC_LLM_ADMISSION_TIMEOUT_SECONDS="${SOC_LLM_ADMISSION_TIMEOUT_SECONDS:-180}"'
        in command[2]
    )
    assert "full_alert_validation_corpus.pkl" in command[2]
    assert "full_alert_dams_labeled_merged.pkl" in command[2]
    assert "export SOC_MEMORY_ENVIRONMENT=dev" in command[2]
    assert "export SOC_AUTOMATION_ENVIRONMENT=dev" in command[2]
    assert "export SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY=true" in command[2]
    assert "export SOC_TENANT_POLICY_ENABLED=true" in command[2]
    assert "SOC_TENANT_DISPOSITION_POLICY_PATH" in command[2]
    assert "export SOC_TENANT_POLICY_ADVISOR_MODE=llm" in command[2]
    assert "SOC_TENANT_POLICY_SKILL_PATH" in command[2]
    assert "export SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true" in command[2]
    assert "SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH" in command[2]
    assert "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false" in command[2]
    assert 'export SOC_DATABASE_URL="$SOC_HOST_DATABASE_URL"' in command[2]
    assert "export DEER_FLOW_AUTH_DISABLED=1" not in command[2]


def test_demo_no_auth_start_is_explicit_and_does_not_change_secure_default() -> None:
    command = build_start_command(
        daemon=True,
        demo_no_auth=True,
        runtime_environment="dev",
    )

    assert "export DEER_FLOW_AUTH_DISABLED=1" in command[2]

    secure_command = build_start_command(daemon=True, runtime_environment="dev")
    assert "export DEER_FLOW_AUTH_DISABLED=1" not in secure_command[2]


def test_stg_start_is_isolated_and_rejects_dev_only_auth_bypass() -> None:
    command = build_start_command(daemon=True, runtime_environment="stg")

    assert 'scripts/serve.sh" --prod' in command[2]
    assert 'scripts/serve.sh" --dev' not in command[2]
    assert "export SOC_DEV_MEMORY_WORKBENCH_ENABLED=false" in command[2]
    assert "export SOC_DEV_CORPUS_WORKBENCH_ENABLED=false" in command[2]
    assert "export SOC_MEMORY_ENVIRONMENT=stg" in command[2]
    assert "export SOC_AUTOMATION_ENVIRONMENT=stg" in command[2]
    assert "export SOC_TENANT_POLICY_ENVIRONMENT=stg" in command[2]
    assert "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false" in command[2]
    assert "export DEER_FLOW_AUTH_DISABLED=1" not in command[2]

    with pytest.raises(HostDevError, match="only available in DEV"):
        build_start_command(
            daemon=True,
            demo_no_auth=True,
            runtime_environment="stg",
        )


def test_runtime_environment_accepts_only_dev_and_stg() -> None:
    assert normalize_runtime_environment(" DEV ") == "dev"
    assert normalize_runtime_environment("stg") == "stg"
    with pytest.raises(HostDevError, match="dev or stg"):
        normalize_runtime_environment("prd")


@pytest.mark.parametrize(
    ("runtime_environment", "zeus_environment", "workflow_environment"),
    (("dev", "prd", "prd"), ("stg", "stg", "stg")),
)
def test_apply_runtime_profile_accepts_governed_remote_mappings(
    runtime_environment: str,
    zeus_environment: str,
    workflow_environment: str,
) -> None:
    target_prefix = f"SOC_PINGAN_ZEUS_{zeus_environment.upper()}_"
    profile = {
        target_prefix + "BASE_URL": f"https://zeus-{zeus_environment}.example",
        target_prefix + "ALLOWED_HOSTS": f"zeus-{zeus_environment}.example",
        target_prefix + "APP_ID": f"{zeus_environment}-app",
        target_prefix + "APP_KEY": f"{zeus_environment}-secret",
    }
    workflow_prefix = f"SOC_PINGAN_WORKFLOW_{workflow_environment.upper()}_"
    workflow_profile = {
        workflow_prefix + "BASE_URL": (f"https://agent-{workflow_environment}.example"),
        workflow_prefix + "ALLOWED_HOSTS": (f"agent-{workflow_environment}.example"),
        workflow_prefix + "APP_ID": f"{workflow_environment}-workflow-app",
        workflow_prefix + "APP_SECRET": f"{workflow_environment}-workflow-secret",
        workflow_prefix + "TERMINAL_ID": "1087710",
        workflow_prefix + "DATACENTER_ID": "1087787",
        workflow_prefix + "USER_ID": "1092332",
    }
    resolved = apply_runtime_profile(
        {
            **profile,
            **workflow_profile,
            "SOC_PINGAN_ZEUS_ENV": zeus_environment,
            "SOC_PINGAN_ZEUS_BASE_URL": profile[target_prefix + "BASE_URL"],
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": profile[target_prefix + "ALLOWED_HOSTS"],
            "SOC_PINGAN_ZEUS_APP_ID": profile[target_prefix + "APP_ID"],
            "SOC_PINGAN_ZEUS_APP_KEY": profile[target_prefix + "APP_KEY"],
            "SOC_PINGAN_WORKFLOW_ENV": workflow_environment,
            "SOC_PINGAN_WORKFLOW_BASE_URL": workflow_profile[
                workflow_prefix + "BASE_URL"
            ],
            "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": workflow_profile[
                workflow_prefix + "ALLOWED_HOSTS"
            ],
            "SOC_PINGAN_WORKFLOW_APP_ID": workflow_profile[workflow_prefix + "APP_ID"],
            "SOC_PINGAN_WORKFLOW_APP_SECRET": workflow_profile[
                workflow_prefix + "APP_SECRET"
            ],
            "SOC_PINGAN_WORKFLOW_TERMINAL_ID": workflow_profile[
                workflow_prefix + "TERMINAL_ID"
            ],
            "SOC_PINGAN_WORKFLOW_DATACENTER_ID": workflow_profile[
                workflow_prefix + "DATACENTER_ID"
            ],
            "SOC_PINGAN_WORKFLOW_USER_ID": workflow_profile[
                workflow_prefix + "USER_ID"
            ],
        },
        runtime_environment=runtime_environment,
    )

    assert resolved["SOC_PINGAN_ENV"] == runtime_environment
    assert resolved["SOC_PINGAN_ZEUS_ENV"] == zeus_environment
    assert resolved["SOC_PINGAN_WORKFLOW_ENV"] == workflow_environment


def test_apply_runtime_profile_rejects_stg_to_prd_target_drift() -> None:
    with pytest.raises(HostDevError, match="STG must target ZEUS STG"):
        apply_runtime_profile(
            {"SOC_PINGAN_ZEUS_ENV": "prd"},
            runtime_environment="stg",
        )


def test_apply_runtime_profile_rejects_active_target_profile_drift() -> None:
    with pytest.raises(HostDevError, match="active ZEUS profile does not match"):
        apply_runtime_profile(
            {
                "SOC_PINGAN_ZEUS_ENV": "stg",
                "SOC_PINGAN_ZEUS_BASE_URL": "https://wrong.example",
                "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "zeus-stg.example",
                "SOC_PINGAN_ZEUS_APP_ID": "stg-app",
                "SOC_PINGAN_ZEUS_APP_KEY": "stg-secret",
                "SOC_PINGAN_ZEUS_STG_BASE_URL": "https://zeus-stg.example",
                "SOC_PINGAN_ZEUS_STG_ALLOWED_HOSTS": "zeus-stg.example",
                "SOC_PINGAN_ZEUS_STG_APP_ID": "stg-app",
                "SOC_PINGAN_ZEUS_STG_APP_KEY": "stg-secret",
            },
            runtime_environment="stg",
        )


def test_apply_runtime_profile_rejects_agent_platform_target_drift() -> None:
    with pytest.raises(
        HostDevError,
        match="DEV must target Agent Platform PRD",
    ):
        apply_runtime_profile(
            {
                "SOC_PINGAN_ZEUS_ENV": "prd",
                "SOC_PINGAN_ZEUS_BASE_URL": "https://zeus-prd.example",
                "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "zeus-prd.example",
                "SOC_PINGAN_ZEUS_APP_ID": "prd-app",
                "SOC_PINGAN_ZEUS_APP_KEY": "prd-secret",
                "SOC_PINGAN_ZEUS_PRD_BASE_URL": "https://zeus-prd.example",
                "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS": "zeus-prd.example",
                "SOC_PINGAN_ZEUS_PRD_APP_ID": "prd-app",
                "SOC_PINGAN_ZEUS_PRD_APP_KEY": "prd-secret",
                "SOC_PINGAN_WORKFLOW_ENV": "stg",
            },
            runtime_environment="dev",
        )


def test_prepare_soc_database_uses_absolute_path_and_disables_sidecar_migrations(
    tmp_path: Path,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):  # noqa: ANN001
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    resolved = prepare_soc_database(
        {"EXISTING": "value", "SOC_PINGAN_ENV": "dev"},
        root=tmp_path,
        run=fake_run,
    )
    database_path = tmp_path / "backend/.deer-flow/data/soc_agent_dev.db"
    database_url = f"sqlite+pysqlite:///{database_path}"

    assert resolved["SOC_DATABASE_URL"] == database_url
    assert resolved["SOC_HOST_DATABASE_URL"] == database_url
    assert resolved["SOC_PINGAN_COMPAT_AUTO_MIGRATE"] == "false"
    assert resolved["SOC_PINGAN_LEGACY_WORKER_AUTO_MIGRATE"] == "false"
    assert calls == [
        (
            [
                str(tmp_path / "backend/.venv/bin/python"),
                "-m",
                "soc_agent.cli",
                "db",
                "upgrade",
                "--database-url",
                database_url,
            ],
            {
                "cwd": tmp_path / "backend",
                "env": resolved,
                "check": True,
            },
        )
    ]


def test_prepare_soc_database_uses_isolated_stg_path(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run(command, **_kwargs):  # noqa: ANN001
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    resolved = prepare_soc_database(
        {"SOC_PINGAN_ENV": "stg"},
        root=tmp_path,
        run=fake_run,
    )

    assert resolved["SOC_DATABASE_URL"].endswith("/soc_agent_stg.db")
    assert resolved["SOC_HOST_DATABASE_URL"] == resolved["SOC_DATABASE_URL"]
    assert calls[0][-1].endswith("/soc_agent_stg.db")


def test_soc_database_status_reads_schema_without_creating_database(
    tmp_path: Path,
) -> None:
    missing = soc_database_status(root=tmp_path, runtime_environment="dev")

    assert missing["status"] == "missing"
    database_path = tmp_path / "backend/.deer-flow/data/soc_agent_dev.db"
    assert not database_path.exists()

    database_path.parent.mkdir(parents=True)
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "CREATE TABLE soc_alembic_version(version_num VARCHAR(64) NOT NULL)"
        )
        connection.execute(
            "INSERT INTO soc_alembic_version(version_num) VALUES (?)",
            ("0027_processing_jobs",),
        )

    ready = soc_database_status(root=tmp_path, runtime_environment="dev")

    assert ready == {
        "status": "ready",
        "path": str(database_path),
        "schema_revision": "0027_processing_jobs",
    }


def test_start_runtime_prepares_database_before_sidecars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    monkeypatch.setattr(host_dev, "inspect_host", lambda **kwargs: {})
    monkeypatch.setattr(host_dev, "validate_runtime_files", lambda **kwargs: None)
    monkeypatch.setattr(
        host_dev,
        "resolve_start_allowed_origins",
        lambda *args, **kwargs: (),
    )
    monkeypatch.setattr(
        host_dev,
        "build_start_environment",
        lambda *args, **kwargs: dict(args[0]) if args else {},
    )
    monkeypatch.setattr(
        host_dev,
        "load_local_runtime_environment",
        lambda environment: {
            "LOCAL": "value",
            "SOC_PINGAN_ENV": "dev",
            "SOC_PINGAN_ZEUS_ENV": "prd",
            "SOC_PINGAN_ZEUS_BASE_URL": "https://zeus-prd.example",
            "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "zeus-prd.example",
            "SOC_PINGAN_ZEUS_APP_ID": "prd-app",
            "SOC_PINGAN_ZEUS_APP_KEY": "prd-secret",
            "SOC_PINGAN_ZEUS_PRD_BASE_URL": "https://zeus-prd.example",
            "SOC_PINGAN_ZEUS_PRD_ALLOWED_HOSTS": "zeus-prd.example",
            "SOC_PINGAN_ZEUS_PRD_APP_ID": "prd-app",
            "SOC_PINGAN_ZEUS_PRD_APP_KEY": "prd-secret",
            "SOC_PINGAN_WORKFLOW_ENV": "prd",
            "SOC_PINGAN_WORKFLOW_BASE_URL": "https://agent-prd.example",
            "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": "agent-prd.example",
            "SOC_PINGAN_WORKFLOW_APP_ID": "YHSYS",
            "SOC_PINGAN_WORKFLOW_APP_SECRET": "workflow-prd-secret",
            "SOC_PINGAN_WORKFLOW_TERMINAL_ID": "1087710",
            "SOC_PINGAN_WORKFLOW_DATACENTER_ID": "1087787",
            "SOC_PINGAN_WORKFLOW_USER_ID": "1092332",
            "SOC_PINGAN_WORKFLOW_PRD_BASE_URL": "https://agent-prd.example",
            "SOC_PINGAN_WORKFLOW_PRD_ALLOWED_HOSTS": "agent-prd.example",
            "SOC_PINGAN_WORKFLOW_PRD_APP_ID": "YHSYS",
            "SOC_PINGAN_WORKFLOW_PRD_APP_SECRET": "workflow-prd-secret",
            "SOC_PINGAN_WORKFLOW_PRD_TERMINAL_ID": "1087710",
            "SOC_PINGAN_WORKFLOW_PRD_DATACENTER_ID": "1087787",
            "SOC_PINGAN_WORKFLOW_PRD_USER_ID": "1092332",
        },
    )
    monkeypatch.setattr(
        host_dev,
        "constrain_sidecar_bindings",
        lambda environment, **kwargs: environment,
    )

    def fake_prepare(environment):  # noqa: ANN001
        events.append("database")
        return {**environment, "SOC_DATABASE_URL": "sqlite:///prepared.db"}

    monkeypatch.setattr(host_dev, "prepare_soc_database", fake_prepare)
    monkeypatch.setattr(
        host_dev,
        "build_pingan_sidecar_specs",
        lambda **kwargs: (),
    )
    monkeypatch.setattr(
        host_dev,
        "start_sidecars",
        lambda *args, **kwargs: events.append("sidecars"),
    )
    monkeypatch.setattr(host_dev, "build_start_command", lambda **kwargs: ["true"])
    monkeypatch.setattr(
        host_dev.subprocess,
        "run",
        lambda *args, **kwargs: events.append("core"),
    )

    host_dev.start_runtime(
        python_executable=sys.executable,
        daemon=True,
        local_only=True,
    )

    assert events == ["database", "sidecars", "core"]


def test_start_plan_applies_explicit_lan_origin_after_private_env() -> None:
    command = build_start_command(daemon=True)
    environment = build_start_environment(
        {"PATH": "/usr/bin"},
        allowed_origins=("10.19.68.62", "http://soc-dev.internal:2026"),
    )

    assert "source" in command[2]
    assert "SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE" in command[2]
    assert command[2].index("source") < command[2].index(
        "export DEER_FLOW_DEV_ALLOWED_ORIGINS"
    )
    assert environment["SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE"] == (
        "10.19.68.62,http://soc-dev.internal:2026"
    )


def test_start_plan_without_lan_origin_does_not_override_private_env() -> None:
    environment = build_start_environment(
        {
            "PATH": "/usr/bin",
            "SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE": "stale.example",
        }
    )

    assert "SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE" not in environment


def test_local_only_forces_private_env_lan_origins_off() -> None:
    environment = build_start_environment(
        {"PATH": "/usr/bin"},
        force_allowed_origins_override=True,
    )

    assert environment["SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE"] == ""


def test_local_only_also_forces_legacy_compat_ingress_to_loopback() -> None:
    environment = {
        "SOC_PINGAN_COMPAT_HOST": "0.0.0.0",
        "SOC_PINGAN_COMPAT_PORT": "8090",
    }

    local = constrain_sidecar_bindings(environment, local_only=True)
    lan = constrain_sidecar_bindings(environment, local_only=False)

    assert local["SOC_PINGAN_COMPAT_HOST"] == "127.0.0.1"
    assert lan["SOC_PINGAN_COMPAT_HOST"] == "0.0.0.0"
    assert environment["SOC_PINGAN_COMPAT_HOST"] == "0.0.0.0"


def test_default_lan_origins_use_private_default_interface_address() -> None:
    outputs = {
        ("route", "-n", "get", "default"): "interface: en0\n",
        ("ipconfig", "getifaddr", "en0"): "10.19.68.62\n",
        ("ifconfig", "en0"): "inet 10.19.68.62 netmask 0xffffff00\n",
        ("ipconfig", "getifaddr", "en1"): "8.8.8.8\n",
        ("ifconfig", "en1"): "inet 127.0.0.1 netmask 0xff000000\n",
    }

    discovered = discover_private_lan_ipv4s(lambda command: outputs.get(command))
    resolved = resolve_start_allowed_origins(
        ("soc-dev.internal",),
        local_only=False,
        discovered_lan_origins=discovered,
    )

    assert discovered == ("10.19.68.62",)
    assert resolved == ("10.19.68.62", "soc-dev.internal")


def test_default_lan_start_fails_closed_when_no_private_address_exists() -> None:
    with pytest.raises(HostDevError, match="cannot detect a private LAN IPv4"):
        resolve_start_allowed_origins(
            (),
            local_only=False,
            discovered_lan_origins=(),
        )


def test_local_only_rejects_explicit_lan_origin() -> None:
    with pytest.raises(HostDevError, match="cannot be combined"):
        resolve_start_allowed_origins(
            ("10.19.68.62",),
            local_only=True,
            discovered_lan_origins=(),
        )


def test_allowed_origins_are_trimmed_deduplicated_and_accept_comma_lists() -> None:
    assert normalize_allowed_origins(
        (" 10.19.68.62 ", "10.19.68.62,http://soc-dev.internal:2026")
    ) == ("10.19.68.62", "http://soc-dev.internal:2026")


def test_allowed_origins_reject_control_characters() -> None:
    with pytest.raises(HostDevError, match="control character"):
        normalize_allowed_origins(("10.19.68.62\nMALICIOUS=1",))


def test_start_cli_accepts_repeated_allowed_origins() -> None:
    args = parse_args(
        [
            "start",
            "--daemon",
            "--allowed-origin",
            "10.19.68.62",
            "--allowed-origin",
            "soc-dev.internal",
        ]
    )

    assert args.daemon is True
    assert args.allowed_origin == ["10.19.68.62", "soc-dev.internal"]
    assert args.local_only is False


def test_start_cli_accepts_local_only() -> None:
    args = parse_args(["start", "--daemon", "--local-only"])

    assert args.daemon is True
    assert args.local_only is True


def test_start_cli_accepts_demo_no_auth() -> None:
    args = parse_args(["start", "--daemon", "--demo-no-auth"])

    assert args.daemon is True
    assert args.demo_no_auth is True


def test_status_cli_is_available() -> None:
    args = parse_args(["status"])

    assert args.action == "status"


def test_docker_soc_demo_start_is_explicit() -> None:
    script = (Path(__file__).resolve().parent / "soc-memory-dev.sh").read_text(
        encoding="utf-8"
    )

    assert "demo-start) demo_start ;;" in script
    assert "export SOC_DEMO_AUTH_DISABLED=1" in script
    assert "start) start ;;" in script


def test_nginx_check_overrides_homebrew_compiled_error_log(tmp_path: Path) -> None:
    command = build_nginx_test_command(root=tmp_path)

    assert command == [
        "nginx",
        "-t",
        "-e",
        str(tmp_path / "logs" / "nginx-error.log"),
        "-c",
        str(tmp_path / "docker" / "nginx" / "nginx.local.conf"),
        "-p",
        str(tmp_path),
    ]
