#!/usr/bin/env python3
"""Prepare and run PingAn SOC DEV directly on an Apple Silicon Mac."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

if __package__:
    from .soc_pingan_host_sidecars import (  # noqa: E402
        HostDevSidecarError,
        build_pingan_sidecar_specs,
        sidecar_status,
        start_sidecars,
        stop_sidecars,
    )
else:
    sys.path.insert(0, str(SCRIPT_DIR))
    from soc_pingan_host_sidecars import (  # noqa: E402
        HostDevSidecarError,
        build_pingan_sidecar_specs,
        sidecar_status,
        start_sidecars,
        stop_sidecars,
    )

BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
RUNTIME_DIR = BACKEND / ".deer-flow" / "internal-host-dev"
SIDECAR_RUNTIME_DIR = RUNTIME_DIR / "sidecars"
LOCKED_REQUIREMENTS = RUNTIME_DIR / "backend-requirements.lock.txt"
UV_INDEX_PROFILE = BACKEND / "samples" / "pingan_dev" / "uv-index.env.example"
LOCAL_ENV = ROOT / ".env.soc-dev.local"
LOCAL_CONFIG = ROOT / "config.pingan-dev.local"
LOCAL_NGINX_CONFIG = ROOT / "docker" / "nginx" / "nginx.local.conf"
DEV_MEMORY_CORPUS = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
)
DEV_CORPUS = (
    ROOT / "validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl"
)
DEV_CORPUS_INDEX = DEV_CORPUS.with_suffix(".workbench-index.json")
DEV_CORPUS_PAYLOAD_STORE = DEV_CORPUS.with_suffix(".workbench-payloads.sqlite")

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
    backend = root / "backend"
    runtime_dir = backend / ".deer-flow" / "internal-host-dev"
    locked_requirements = runtime_dir / "backend-requirements.lock.txt"
    virtualenv = backend / ".venv"
    virtualenv_python = virtualenv / "bin" / "python"
    return (
        (
            backend,
            [
                "uv",
                "export",
                "--frozen",
                "--all-packages",
                "--extra",
                "pingan-dev",
                "--no-emit-workspace",
                "--no-header",
                "--format",
                "requirements.txt",
                "--output-file",
                str(locked_requirements),
            ],
        ),
        (
            backend,
            [
                "uv",
                "venv",
                "--clear",
                "--python",
                python_executable,
                str(virtualenv),
            ],
        ),
        (
            backend,
            [
                "uv",
                "pip",
                "sync",
                "--python",
                str(virtualenv_python),
                "--require-hashes",
                str(locked_requirements),
            ],
        ),
        (
            backend,
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(virtualenv_python),
                "--no-deps",
                "--editable",
                str(backend / "packages" / "extension-api"),
                "--editable",
                str(backend / "packages" / "harness"),
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

    RUNTIME_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    RUNTIME_DIR.chmod(0o700)
    lock_hash_before = _sha256_file(BACKEND / "uv.lock")
    for cwd, command in build_install_commands(
        python_executable=report["paths"]["python"]
    ):
        subprocess.run(command, cwd=cwd, env=env, check=True)
        if command[:2] == ["uv", "export"]:
            _validate_locked_requirements(LOCKED_REQUIREMENTS)

    lock_hash_after = _sha256_file(BACKEND / "uv.lock")
    if lock_hash_after != lock_hash_before:
        raise HostDevError("native Host DEV install changed backend/uv.lock")

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
        "backend_lock_mode": "frozen_export_hash_sync",
        "backend_lock_sha256": lock_hash_after,
        "backend_requirements_sha256": _sha256_file(LOCKED_REQUIREMENTS),
        "backend_lock_changed": False,
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
        for path in (
            LOCAL_ENV,
            LOCAL_CONFIG,
            LOCAL_NGINX_CONFIG,
            DEV_MEMORY_CORPUS,
            DEV_CORPUS,
            DEV_CORPUS_INDEX,
            DEV_CORPUS_PAYLOAD_STORE,
        )
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
    subprocess.run(build_nginx_test_command(), check=True)


def build_nginx_test_command(*, root: Path = ROOT) -> list[str]:
    """Build a non-root nginx config check independent of compiled paths."""
    return [
        "nginx",
        "-t",
        "-e",
        str(root / "logs" / "nginx-error.log"),
        "-c",
        str(root / "docker" / "nginx" / "nginx.local.conf"),
        "-p",
        str(root),
    ]


def start_runtime(
    *,
    python_executable: str,
    daemon: bool,
    allowed_origins: tuple[str, ...] = (),
    local_only: bool = False,
    demo_no_auth: bool = False,
) -> None:
    inspect_host(python_executable=python_executable)
    validate_runtime_files()
    resolved_origins = resolve_start_allowed_origins(
        allowed_origins,
        local_only=local_only,
    )
    if resolved_origins:
        print("PingAn SOC LAN DEV access enabled:", flush=True)
        for origin in resolved_origins:
            print(f"  http://{_origin_host(origin)}:2026", flush=True)
    if demo_no_auth:
        print(
            "WARNING: demo authentication is disabled; every visitor shares one synthetic administrator identity.",
            flush=True,
        )
        print(
            "Use only on a trusted DEV network. Real external action execution remains disabled.",
            flush=True,
        )
    elif resolved_origins:
        print(
            "Authentication remains enabled; allow nginx through the macOS firewall.",
            flush=True,
        )
    start_environment = build_start_environment(
        allowed_origins=resolved_origins,
        force_allowed_origins_override=True,
    )
    runtime_environment = constrain_sidecar_bindings(
        load_local_runtime_environment(start_environment),
        local_only=local_only,
    )
    if (
        resolved_origins
        and runtime_environment.get("SOC_PINGAN_COMPAT_ENABLED", "true").strip().lower()
        in {"1", "true", "yes", "on"}
        and runtime_environment.get("SOC_PINGAN_COMPAT_HOST", "127.0.0.1").strip()
        in {"0.0.0.0", "::"}
    ):
        compat_port = runtime_environment.get("SOC_PINGAN_COMPAT_PORT", "8090")
        print("Legacy ZEUS compatibility ingress:", flush=True)
        for origin in resolved_origins:
            print(
                f"  http://{_origin_host(origin)}:{compat_port}/workflow/task",
                flush=True,
            )
    specs = build_pingan_sidecar_specs(
        root=ROOT,
        environment=runtime_environment,
    )
    start_sidecars(
        specs,
        runtime_dir=SIDECAR_RUNTIME_DIR,
        environment=runtime_environment,
    )
    command = build_start_command(daemon=daemon, demo_no_auth=demo_no_auth)
    try:
        subprocess.run(
            command,
            cwd=ROOT,
            env=start_environment,
            check=True,
        )
    except BaseException:
        stop_sidecars(specs, runtime_dir=SIDECAR_RUNTIME_DIR)
        raise
    if not daemon:
        stop_sidecars(specs, runtime_dir=SIDECAR_RUNTIME_DIR)


def build_start_command(*, daemon: bool, demo_no_auth: bool = False) -> list[str]:
    auth_setup = "export DEER_FLOW_AUTH_DISABLED=1; " if demo_no_auth else ""
    shell_command = (
        'set -a; source "$SOC_HOST_DEV_ROOT/.env.soc-dev.local"; set +a; '
        + auth_setup
        + "export SOC_DEV_MEMORY_WORKBENCH_ENABLED=true; "
        "export SOC_DEV_CORPUS_WORKBENCH_ENABLED=true; "
        'export SOC_LLM_MAX_CONCURRENCY="${SOC_LLM_MAX_CONCURRENCY:-3}"; '
        'export SOC_LLM_ADMISSION_TIMEOUT_SECONDS="${SOC_LLM_ADMISSION_TIMEOUT_SECONDS:-180}"; '
        'export SOC_DEV_MEMORY_CORPUS_PATH="$SOC_HOST_DEV_ROOT/validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"; '
        'export SOC_DEV_CORPUS_WORKBENCH_PATH="$SOC_HOST_DEV_ROOT/validation/compact_zeus/data/corpus/full_alert_dams_labeled_merged.pkl"; '
        "export SOC_MEMORY_ENVIRONMENT=dev; "
        "export SOC_AUTOMATION_ENVIRONMENT=dev; "
        "export SOC_DEV_WORKBENCH_ALLOW_TENANT_POLICY=true; "
        "export SOC_TENANT_POLICY_ENABLED=true; "
        'export SOC_TENANT_DISPOSITION_POLICY_PATH="$SOC_HOST_DEV_ROOT/backend/soc_agent/integrations/pingan/policies/tenant-disposition-v2.json"; '
        "export SOC_TENANT_POLICY_ENVIRONMENT=dev; "
        "export SOC_TENANT_POLICY_EVENT_TIMEZONE=Asia/Shanghai; "
        "export SOC_TENANT_POLICY_ADVISOR_MODE=llm; "
        'export SOC_TENANT_POLICY_SKILL_PATH="$SOC_HOST_DEV_ROOT/backend/soc_agent/integrations/pingan/policy_skills/disposition/SKILL.md"; '
        "export SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true; "
        'export SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH="$SOC_HOST_DEV_ROOT/backend/.deer-flow/pingan-context/software-path-catalog.sqlite"; '
        "export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false; "
        'if [[ "${SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE+x}" == x ]]; then '
        'export DEER_FLOW_DEV_ALLOWED_ORIGINS="$SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE"; '
        "fi; "
        'exec "$SOC_HOST_DEV_ROOT/scripts/serve.sh" --dev --skip-install "$@"'
    )
    command = [
        "/bin/bash",
        "-c",
        shell_command,
        "soc-pingan-macos-host-dev",
    ]
    if daemon:
        command.append("--daemon")
    return command


def build_start_environment(
    base: dict[str, str] | None = None,
    *,
    allowed_origins: tuple[str, ...] = (),
    force_allowed_origins_override: bool = False,
) -> dict[str, str]:
    env = dict(os.environ if base is None else base)
    env.update(
        {
            "SOC_HOST_DEV_ROOT": str(ROOT),
            "SOC_REPO_ROOT": str(ROOT),
            "DEER_FLOW_CONFIG_PATH": str(LOCAL_CONFIG),
            "NEXT_TELEMETRY_DISABLED": "1",
            "DO_NOT_TRACK": "1",
            "UV_OFFLINE": "1",
        }
    )
    normalized_origins = normalize_allowed_origins(allowed_origins)
    if normalized_origins or force_allowed_origins_override:
        env["SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE"] = ",".join(normalized_origins)
    else:
        env.pop("SOC_HOST_DEV_ALLOWED_ORIGINS_OVERRIDE", None)
    return env


def constrain_sidecar_bindings(
    environment: dict[str, str],
    *,
    local_only: bool,
) -> dict[str, str]:
    """Make --local-only cover the PingAn ingress sidecar as well as Next.js."""

    resolved = dict(environment)
    if local_only:
        resolved["SOC_PINGAN_COMPAT_HOST"] = "127.0.0.1"
    return resolved


def resolve_start_allowed_origins(
    allowed_origins: tuple[str, ...],
    *,
    local_only: bool,
    discovered_lan_origins: tuple[str, ...] | None = None,
) -> tuple[str, ...]:
    """Resolve the exact DEV hosts Next.js may serve on the internal LAN."""

    if local_only:
        if normalize_allowed_origins(allowed_origins):
            raise HostDevError("--local-only cannot be combined with --allowed-origin")
        return ()
    discovered = (
        discover_private_lan_ipv4s()
        if discovered_lan_origins is None
        else discovered_lan_origins
    )
    resolved = normalize_allowed_origins((*discovered, *allowed_origins))
    if not resolved:
        raise HostDevError(
            "cannot detect a private LAN IPv4; use --allowed-origin HOST or "
            "--local-only"
        )
    return resolved


def discover_private_lan_ipv4s(
    command_output: Callable[[tuple[str, ...]], str | None] | None = None,
) -> tuple[str, ...]:
    """Discover private IPv4 addresses on the macOS default/en* interfaces."""

    output = command_output or _optional_command_output
    route_output = output(("route", "-n", "get", "default")) or ""
    route_match = re.search(r"^\s*interface:\s*(\S+)\s*$", route_output, re.MULTILINE)
    interfaces = [route_match.group(1)] if route_match is not None else []
    interfaces.extend(("en0", "en1"))

    addresses: list[str] = []
    for interface in dict.fromkeys(interfaces):
        direct = output(("ipconfig", "getifaddr", interface))
        candidates = [direct.strip()] if direct and direct.strip() else []
        interface_output = output(("ifconfig", interface)) or ""
        candidates.extend(
            re.findall(
                r"^\s*inet\s+(\d+(?:\.\d+){3})\b", interface_output, re.MULTILINE
            )
        )
        for candidate in candidates:
            if _is_private_lan_ipv4(candidate) and candidate not in addresses:
                addresses.append(candidate)
    return tuple(addresses)


def _optional_command_output(command: tuple[str, ...]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    return completed.stdout if completed.returncode == 0 else None


def _is_private_lan_ipv4(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        address.version == 4
        and address.is_private
        and not address.is_loopback
        and not address.is_link_local
        and not address.is_unspecified
    )


def _origin_host(value: str) -> str:
    parsed = urlparse(value if "://" in value else f"http://{value}")
    return parsed.hostname or value


def normalize_allowed_origins(values: tuple[str, ...]) -> tuple[str, ...]:
    """Validate explicit LAN DEV origins before forwarding them to Next.js."""
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_value in values:
        for raw_entry in raw_value.split(","):
            entry = raw_entry.strip()
            if not entry:
                continue
            if any(character in entry for character in ("\r", "\n", "\x00")):
                raise HostDevError("allowed DEV origin contains a control character")
            if entry in seen:
                continue
            seen.add(entry)
            normalized.append(entry)
    return tuple(normalized)


def stop_runtime() -> None:
    environment = _best_effort_runtime_environment()
    specs = build_pingan_sidecar_specs(
        root=ROOT,
        environment=environment,
        include_disabled=True,
    )
    try:
        subprocess.run([str(ROOT / "scripts" / "serve.sh"), "--stop"], check=True)
    finally:
        stop_sidecars(specs, runtime_dir=SIDECAR_RUNTIME_DIR)


def runtime_status() -> dict[str, Any]:
    environment = _best_effort_runtime_environment()
    specs = build_pingan_sidecar_specs(
        root=ROOT,
        environment=environment,
        include_disabled=True,
    )
    return {
        "schema_version": "soc.pingan_macos_host_dev_status.v1",
        "core": {
            "gateway_8001": _tcp_port_open(8001),
            "frontend_3000": _tcp_port_open(3000),
            "nginx_2026": _tcp_port_open(2026),
        },
        "sidecars": sidecar_status(specs, runtime_dir=SIDECAR_RUNTIME_DIR),
    }


def load_local_runtime_environment(
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Source the operator-owned shell overlay without logging its secrets."""

    environment = build_start_environment(base)
    completed = subprocess.run(
        [
            "/bin/bash",
            "-c",
            'set -a; source "$1"; set +a; env -0',
            "soc-pingan-host-env",
            str(LOCAL_ENV),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    resolved = dict(environment)
    for item in completed.stdout.split(b"\0"):
        if b"=" not in item:
            continue
        raw_name, raw_value = item.split(b"=", 1)
        resolved[raw_name.decode()] = raw_value.decode()
    return resolved


def _best_effort_runtime_environment() -> dict[str, str]:
    if LOCAL_ENV.is_file():
        try:
            return load_local_runtime_environment()
        except (OSError, subprocess.CalledProcessError, UnicodeDecodeError):
            pass
    return build_start_environment()


def _tcp_port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
            return True
    except OSError:
        return False


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


def _validate_locked_requirements(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise HostDevError("locked requirements export is empty")
    if re.search(r"(?:https?|git\+https?)://", content, flags=re.IGNORECASE):
        raise HostDevError(
            "locked requirements contains a direct network URL; internal mirror "
            "installation cannot safely rewrite that source"
        )
    if re.search(r"(?m)^\s*-e\s+", content):
        raise HostDevError(
            "locked requirements unexpectedly contains a local editable package"
        )
    path.chmod(0o600)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    start.add_argument(
        "--allowed-origin",
        action="append",
        default=[],
        metavar="HOST_OR_URL",
        help=(
            "allow one LAN host or URL to load Next.js DEV assets/HMR; repeat for "
            "multiple hosts"
        ),
    )
    start.add_argument(
        "--local-only",
        action="store_true",
        help="disable automatic trusted-LAN access and bind browser use to localhost",
    )
    start.add_argument(
        "--demo-no-auth",
        action="store_true",
        help=(
            "disable registration/login and map trusted demo visitors to one "
            "synthetic administrator; rejected in production environments"
        ),
    )
    subparsers.add_parser("stop", help="stop native DEV services")
    subparsers.add_parser("status", help="show core and SOC sidecar process status")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.action == "check":
            result = inspect_host(python_executable=args.python)
        elif args.action == "install":
            result = install_dependencies(python_executable=args.python)
        elif args.action == "start":
            start_runtime(
                python_executable=args.python,
                daemon=args.daemon,
                allowed_origins=tuple(args.allowed_origin),
                local_only=args.local_only,
                demo_no_auth=args.demo_no_auth,
            )
            return 0
        elif args.action == "stop":
            stop_runtime()
            result = {"status": "stopped"}
        else:
            result = runtime_status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (
        HostDevError,
        HostDevSidecarError,
        OSError,
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:
        print(f"error: {str(exc)[:1000] or type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
