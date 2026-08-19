from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.soc_pingan_macos_host_dev import (
    HostDevError,
    build_install_commands,
    build_start_command,
    build_start_environment,
    expected_pnpm_version,
    is_public_npm_registry,
    normalize_internal_npm_registry,
    parse_version,
)


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


def test_install_plan_is_locked_native_and_docker_free(tmp_path: Path) -> None:
    commands = build_install_commands(
        root=tmp_path,
        python_executable="/opt/python3.12",
    )

    flattened = [part for _, command in commands for part in command]
    assert "--locked" in flattened
    assert "--frozen-lockfile" in flattened
    assert "pingan-dev" in flattened
    assert "/opt/python3.12" in flattened
    assert all("docker" not in part.lower() for part in flattened)


def test_start_plan_skips_install_and_disables_network_side_effects() -> None:
    command = build_start_command(daemon=True)
    environment = build_start_environment({"PATH": "/usr/bin"})

    assert "--skip-install" in command[2]
    assert command[-1] == "--daemon"
    assert all("docker" not in part.lower() for part in command)
    assert environment["NEXT_TELEMETRY_DISABLED"] == "1"
    assert environment["DO_NOT_TRACK"] == "1"
    assert environment["UV_OFFLINE"] == "1"
