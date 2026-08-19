#!/usr/bin/env python3
"""Prepare and run PingAn SOC DEV directly on an Apple Silicon Mac."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
RUNTIME_DIR = BACKEND / ".deer-flow" / "internal-host-dev"
UV_INDEX_PROFILE = BACKEND / "samples" / "pingan_dev" / "uv-index.env.example"
LOCAL_ENV = ROOT / ".env.soc-dev.local"
LOCAL_CONFIG = ROOT / "config.pingan-dev.local"
LOCAL_NGINX_CONFIG = ROOT / "docker" / "nginx" / "nginx.local.conf"

MIN_PYTHON = (3, 12)
MIN_NODE_MAJOR = 22
MIN_NGINX = (1, 23)
REQUIRED_COMMANDS = (
    "uv",
    "node",
    "pnpm",
    "nginx",
    "git",
    "make",
    "curl",
    "tar",
    "shasum",
    "lsof",
)
PUBLIC_NPM_REGISTRY_HOSTS = frozenset(
    {"registry.npmjs.org", "registry.yarnpkg.com", "registry.npmmirror.com"}
)


class HostDevError(RuntimeError):
    """Raised when the internal host cannot satisfy the frozen DEV contract."""


def parse_version(value: str, *, label: str) -> tuple[int, ...]:
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", value)
    if match is None:
        raise HostDevError(f"cannot parse {label} version from: {value!r}")
    return tuple(int(part) for part in match.groups(default="0"))


def expected_pnpm_version(root: Path = ROOT) -> str:
    package = json.loads((root / "frontend" / "package.json").read_text())
    manager = package.get("packageManager", "")
    name, separator, version = manager.partition("@")
    if name != "pnpm" or not separator or not version:
        raise HostDevError("frontend/package.json must pin packageManager=pnpm@VERSION")
    return version


def is_public_npm_registry(value: str) -> bool:
    host = (urlparse(value.strip()).hostname or "").lower()
    return host in PUBLIC_NPM_REGISTRY_HOSTS


def normalize_internal_npm_registry(value: str) -> str:
    for raw_line in reversed(value.splitlines()):
        candidate = raw_line.strip()
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        if is_public_npm_registry(candidate):
            raise HostDevError(
                "pnpm registry still points to a public service; configure the "
                "approved PingAn internal registry before installing dependencies"
            )
        return candidate
    raise HostDevError("pnpm registry did not return a valid internal HTTP(S) URL")


def build_install_commands(
    *, root: Path = ROOT, python_executable: str
) -> tuple[tuple[Path, list[str]], ...]:
    return (
        (
            root / "backend",
            [
                "uv",
                "sync",
                "--locked",
                "--all-packages",
                "--extra",
                "pingan-dev",
                "--python",
                python_executable,
            ],
        ),
        (
            root / "frontend",
            [
                python_executable,
                str(root / "scripts" / "pnpm.py"),
                "install",
                "--frozen-lockfile",
            ],
        ),
    )


def inspect_host(
    *,
    root: Path = ROOT,
    python_executable: str = sys.executable,
    enforce_platform: bool = True,
) -> dict[str, Any]:
    system = platform.system()
    machine = platform.machine()
    if enforce_platform and (system != "Darwin" or machine != "arm64"):
        raise HostDevError(
            f"PingAn Host DEV requires Apple Silicon macOS; found {system} {machine}"
        )

    command_paths: dict[str, str] = {}
    missing: list[str] = []
    for command in REQUIRED_COMMANDS:
        resolved = shutil.which(command)
        if resolved is None:
            missing.append(command)
        else:
            command_paths[command] = resolved
    if missing:
        raise HostDevError("required commands are missing: " + ", ".join(missing))

    python_path = _resolve_executable(python_executable)
    python_version_text = _capture(
        [python_path, "-c", "import platform; print(platform.python_version())"]
    )
    python_version = parse_version(python_version_text, label="Python")
    if python_version[:2] < MIN_PYTHON:
        raise HostDevError(
            f"Python {python_version_text} is unsupported; require >=3.12"
        )

    uv_version = _capture([command_paths["uv"], "--version"])
    node_version = _capture([command_paths["node"], "--version"])
    if parse_version(node_version, label="Node")[0] < MIN_NODE_MAJOR:
        raise HostDevError(f"Node {node_version} is unsupported; require >=22")

    pnpm_version = _capture([command_paths["pnpm"], "--version"])
    pinned_pnpm = expected_pnpm_version(root)
    if pnpm_version != pinned_pnpm:
        raise HostDevError(
            f"pnpm {pnpm_version} does not match the project pin {pinned_pnpm}"
        )
    npm_registry = normalize_internal_npm_registry(
        _capture([command_paths["pnpm"], "config", "get", "registry"])
    )

    nginx_version_text = _capture([command_paths["nginx"], "-v"])
    nginx_version = parse_version(nginx_version_text, label="nginx")
    if nginx_version[:2] < MIN_NGINX:
        raise HostDevError(
            f"nginx {nginx_version_text} is unsupported for this handoff; require >=1.23"
        )

    return {
        "schema_version": "soc.pingan_macos_host_dev_check.v1",
        "checked_at": datetime.now(UTC).isoformat(),
        "platform": {"system": system, "machine": machine},
        "versions": {
            "python": python_version_text,
            "uv": uv_version,
            "node": node_version,
            "pnpm": pnpm_version,
            "nginx": nginx_version_text,
        },
        "paths": {**command_paths, "python": python_path},
        "registries": {
            "pnpm": npm_registry,
            "uv_profile": str(UV_INDEX_PROFILE.relative_to(root)),
        },
        "runtime": {
            "docker_required": False,
            "sandbox_provider": "deerflow.sandbox.local:LocalSandboxProvider",
            "database_backend": "sqlite",
            "dependency_sync_on_start": False,
            "next_telemetry_disabled": True,
        },
        "status": "ready",
    }


def install_dependencies(*, python_executable: str) -> dict[str, Any]:
    report = inspect_host(python_executable=python_executable)
    env = os.environ.copy()
    env.update(_read_export_profile(UV_INDEX_PROFILE))
    env["NEXT_TELEMETRY_DISABLED"] = "1"
    env["DO_NOT_TRACK"] = "1"

    for cwd, command in build_install_commands(
        python_executable=report["paths"]["python"]
    ):
        subprocess.run(command, cwd=cwd, env=env, check=True)

    _capture(
        [
            str(BACKEND / ".venv" / "bin" / "python"),
            "-c",
            "import httpx,pandas,pydantic; import soc_agent; print('host backend imports: OK')",
        ],
        cwd=BACKEND,
        env=env,
    )
    _capture(
        [
            report["paths"]["python"],
            str(ROOT / "scripts" / "pnpm.py"),
            "exec",
            "next",
            "--version",
        ],
        cwd=FRONTEND,
        env=env,
    )
    report["install"] = {
        "backend_lock_mode": "locked",
        "backend_dependency_source": "approved_internal_registry",
        "backend_cache_prerequisite": False,
        "backend_extra": "pingan-dev",
        "frontend_lock_mode": "frozen-lockfile",
        "public_network_required": False,
        "completed": True,
    }
    _write_report(report)
    return report


def validate_runtime_files() -> None:
    missing = [
        str(path.relative_to(ROOT))
        for path in (LOCAL_ENV, LOCAL_CONFIG, LOCAL_NGINX_CONFIG)
        if not path.is_file()
    ]
    if missing:
        raise HostDevError(
            "required local runtime files are missing: " + ", ".join(missing)
        )
    for path in (LOCAL_ENV, LOCAL_CONFIG):
        if path.stat().st_mode & 0o077:
            raise HostDevError(f"private local file must be mode 0600: {path.name}")

    for path in (
        ROOT / "logs",
        ROOT / "temp" / "client_body_temp",
        ROOT / "temp" / "proxy_temp",
        ROOT / "temp" / "fastcgi_temp",
        ROOT / "temp" / "uwsgi_temp",
        ROOT / "temp" / "scgi_temp",
    ):
        path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "nginx",
            "-t",
            "-c",
            str(LOCAL_NGINX_CONFIG),
            "-p",
            str(ROOT),
        ],
        check=True,
    )


def start_runtime(*, python_executable: str, daemon: bool) -> None:
    inspect_host(python_executable=python_executable)
    validate_runtime_files()
    command = build_start_command(daemon=daemon)
    os.execve(command[0], command, build_start_environment())


def build_start_command(*, daemon: bool) -> list[str]:
    command = [
        "/bin/bash",
        "-c",
        'set -a; source "$SOC_HOST_DEV_ROOT/.env.soc-dev.local"; set +a; '
        'exec "$SOC_HOST_DEV_ROOT/scripts/serve.sh" --dev --skip-install "$@"',
        "soc-pingan-macos-host-dev",
    ]
    if daemon:
        command.append("--daemon")
    return command


def build_start_environment(
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(
        {
            "SOC_HOST_DEV_ROOT": str(ROOT),
            "DEER_FLOW_CONFIG_PATH": str(LOCAL_CONFIG),
            "NEXT_TELEMETRY_DISABLED": "1",
            "DO_NOT_TRACK": "1",
            "UV_OFFLINE": "1",
        }
    )
    return env


def stop_runtime() -> None:
    subprocess.run([str(ROOT / "scripts" / "serve.sh"), "--stop"], check=True)


def _resolve_executable(value: str) -> str:
    candidate = Path(value).expanduser()
    if candidate.parent != Path(".") or "/" in value:
        if not candidate.is_file():
            raise HostDevError(f"Python executable does not exist: {candidate}")
        return str(candidate.resolve())
    resolved = shutil.which(value)
    if resolved is None:
        raise HostDevError(f"Python executable is unavailable: {value}")
    return resolved


def _capture(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        stderr=subprocess.STDOUT,
    )
    return completed.stdout.strip()


def _read_export_profile(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line.startswith("export "):
            continue
        name, separator, raw_value = line.removeprefix("export ").partition("=")
        if not separator:
            continue
        value = raw_value.strip().strip('"').strip("'")
        values[name.strip()] = value
    return values


def _write_report(report: dict[str, Any]) -> Path:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RUNTIME_DIR.chmod(0o700)
    path = RUNTIME_DIR / "install-report.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)
    return path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python 3.12+ executable used for the project environment",
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("check", help="validate native macOS DEV prerequisites")
    subparsers.add_parser(
        "install", help="install locked backend/frontend dependencies"
    )
    start = subparsers.add_parser(
        "start", help="start native DEV without dependency sync"
    )
    start.add_argument("--daemon", action="store_true")
    subparsers.add_parser("stop", help="stop native DEV services")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.action == "check":
            result = inspect_host(python_executable=args.python)
        elif args.action == "install":
            result = install_dependencies(python_executable=args.python)
        elif args.action == "start":
            start_runtime(python_executable=args.python, daemon=args.daemon)
            return 0
        else:
            stop_runtime()
            result = {"status": "stopped"}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (HostDevError, OSError, subprocess.CalledProcessError, ValueError) as exc:
        print(f"error: {str(exc)[:1000] or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
