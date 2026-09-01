"""Process lifecycle for PingAn-only Host DEV sidecars."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


class HostDevSidecarError(RuntimeError):
    pass


@dataclass(frozen=True)
class HostDevSidecarSpec:
    name: str
    command: tuple[str, ...]
    process_marker: str
    health_url: str | None = None
    startup_timeout_seconds: float = 30.0


def build_pingan_sidecar_specs(
    *,
    root: Path,
    environment: Mapping[str, str],
    include_disabled: bool = False,
) -> tuple[HostDevSidecarSpec, ...]:
    python = root / "backend" / ".venv" / "bin" / "python"
    model_port = _env_port(environment, "SOC_PINGAN_MODEL_GATEWAY_PORT", 4001)
    compat_port = _env_port(environment, "SOC_PINGAN_COMPAT_PORT", 8090)
    specs: list[HostDevSidecarSpec] = []
    if include_disabled or _env_bool(
        environment,
        "SOC_PINGAN_MODEL_GATEWAY_ENABLED",
        True,
    ):
        specs.append(
            HostDevSidecarSpec(
                name="model-gateway",
                command=(
                    str(python),
                    str(root / "backend" / "scripts" / "soc_pingan_model_gateway.py"),
                ),
                process_marker="soc_pingan_model_gateway.py",
                health_url=f"http://127.0.0.1:{model_port}/health",
                startup_timeout_seconds=30.0,
            )
        )
    if include_disabled or _env_bool(
        environment,
        "SOC_PINGAN_COMPAT_ENABLED",
        True,
    ):
        specs.extend(
            (
                HostDevSidecarSpec(
                    name="legacy-api",
                    command=(
                        str(python),
                        str(root / "backend" / "scripts" / "soc_pingan_legacy_api.py"),
                    ),
                    process_marker="soc_pingan_legacy_api.py",
                    health_url=f"http://127.0.0.1:{compat_port}/health",
                    startup_timeout_seconds=30.0,
                ),
                HostDevSidecarSpec(
                    name="legacy-worker",
                    command=(
                        str(python),
                        str(
                            root / "backend" / "scripts" / "soc_pingan_legacy_worker.py"
                        ),
                    ),
                    process_marker="soc_pingan_legacy_worker.py",
                    startup_timeout_seconds=5.0,
                ),
            )
        )
    return tuple(specs)


def start_sidecars(
    specs: Sequence[HostDevSidecarSpec],
    *,
    runtime_dir: Path,
    environment: Mapping[str, str],
    popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
) -> list[dict[str, Any]]:
    runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    runtime_dir.chmod(0o700)
    for spec in specs:
        state = _read_state(runtime_dir, spec.name)
        if state is not None and _process_matches(
            int(state["pid"]),
            spec.process_marker,
        ):
            raise HostDevSidecarError(f"{spec.name} is already running")
        _state_path(runtime_dir, spec.name).unlink(missing_ok=True)

    started: list[HostDevSidecarSpec] = []
    reports: list[dict[str, Any]] = []
    try:
        for spec in specs:
            log_path = runtime_dir / f"{spec.name}.log"
            with log_path.open("ab") as log:
                process = popen(
                    list(spec.command),
                    cwd=str(Path(spec.command[1]).resolve().parents[1]),
                    env=dict(environment),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            state = {
                "schema_version": "soc.pingan_host_sidecar_state.v1",
                "name": spec.name,
                "pid": process.pid,
                "process_marker": spec.process_marker,
                "started_at": datetime.now(UTC).isoformat(),
                "log_path": str(log_path),
            }
            _write_state(runtime_dir, spec.name, state)
            started.append(spec)
            _wait_until_ready(spec, process=process)
            reports.append({**state, "status": "running"})
        return reports
    except Exception:
        stop_sidecars(
            tuple(reversed(started)),
            runtime_dir=runtime_dir,
            grace_seconds=2.0,
        )
        raise


def stop_sidecars(
    specs: Sequence[HostDevSidecarSpec],
    *,
    runtime_dir: Path,
    grace_seconds: float = 10.0,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for spec in reversed(tuple(specs)):
        state_path = _state_path(runtime_dir, spec.name)
        state = _read_state(runtime_dir, spec.name)
        if state is None:
            reports.append({"name": spec.name, "status": "not_running"})
            continue
        pid = int(state["pid"])
        if not _process_matches(pid, spec.process_marker):
            state_path.unlink(missing_ok=True)
            reports.append({"name": spec.name, "status": "stale_state_removed"})
            continue
        os.kill(pid, signal.SIGTERM)
        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline and _process_matches(
            pid,
            spec.process_marker,
        ):
            time.sleep(0.05)
        if _process_matches(pid, spec.process_marker):
            os.kill(pid, signal.SIGKILL)
        state_path.unlink(missing_ok=True)
        reports.append({"name": spec.name, "status": "stopped", "pid": pid})
    return reports


def sidecar_status(
    specs: Sequence[HostDevSidecarSpec],
    *,
    runtime_dir: Path,
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for spec in specs:
        state = _read_state(runtime_dir, spec.name)
        if state is None:
            reports.append({"name": spec.name, "status": "not_running"})
            continue
        pid = int(state["pid"])
        reports.append(
            {
                "name": spec.name,
                "pid": pid,
                "status": (
                    "running" if _process_matches(pid, spec.process_marker) else "stale"
                ),
                "health_url": spec.health_url,
            }
        )
    return reports


def _wait_until_ready(
    spec: HostDevSidecarSpec,
    *,
    process: subprocess.Popen[bytes],
) -> None:
    deadline = time.monotonic() + spec.startup_timeout_seconds
    if spec.health_url is None:
        stable_until = min(deadline, time.monotonic() + 0.5)
        while time.monotonic() < stable_until:
            if process.poll() is not None:
                raise HostDevSidecarError(
                    f"{spec.name} exited during startup; inspect its log"
                )
            time.sleep(0.05)
        return
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise HostDevSidecarError(
                f"{spec.name} exited during startup; inspect its log"
            )
        try:
            with urllib.request.urlopen(spec.health_url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise HostDevSidecarError(f"{spec.name} did not become healthy before timeout")


def _process_matches(pid: int, marker: str) -> bool:
    if pid < 1:
        return False
    try:
        completed = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return False
    return completed.returncode == 0 and marker in completed.stdout


def _state_path(runtime_dir: Path, name: str) -> Path:
    return runtime_dir / f"{name}.json"


def _read_state(runtime_dir: Path, name: str) -> dict[str, Any] | None:
    path = _state_path(runtime_dir, name)
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return (
        value if isinstance(value, dict) and isinstance(value.get("pid"), int) else None
    )


def _write_state(runtime_dir: Path, name: str, state: Mapping[str, Any]) -> None:
    path = _state_path(runtime_dir, name)
    path.write_text(
        json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _env_bool(
    environment: Mapping[str, str],
    name: str,
    default: bool,
) -> bool:
    value = environment.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HostDevSidecarError(f"{name} must be a boolean")


def _env_port(environment: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(environment.get(name, str(default)))
    except ValueError as exc:
        raise HostDevSidecarError(f"{name} must be an integer") from exc
    if not 1 <= value <= 65_535:
        raise HostDevSidecarError(f"{name} is outside the supported port range")
    return value


__all__ = [
    "HostDevSidecarError",
    "HostDevSidecarSpec",
    "build_pingan_sidecar_specs",
    "sidecar_status",
    "start_sidecars",
    "stop_sidecars",
]
