"""Governed switch for the PingAn host runtime environment."""

from __future__ import annotations

import os
import re
import shlex
import stat
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

PingAnRuntimeEnvironmentValue = Literal["dev", "stg"]

_RUNTIME_KEY = "SOC_PINGAN_ENV"
_ZEUS_TARGET_KEY = "SOC_PINGAN_ZEUS_ENV"
_ASSIGNMENT = re.compile(
    r"^(?P<prefix>\s*export\s+)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*=\s*(?P<value>.*?)\s*$"
)


class PingAnRuntimeEnvironmentConfigurationError(ValueError):
    """Raised when the private runtime profile cannot be switched safely."""


class PingAnRuntimeEnvironmentReport(BaseModel):
    """Secret-free evidence for one runtime-environment change."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_runtime_environment.v1"] = "soc.pingan_runtime_environment.v1"
    env_path: str
    previous_environment: PingAnRuntimeEnvironmentValue
    environment: PingAnRuntimeEnvironmentValue
    database_filename: str
    workbenches_enabled: bool
    demo_no_auth_allowed: bool
    zeus_target_environment: str
    zeus_target_unchanged: bool = True
    provider_modes_unchanged: bool = True
    external_action_setting_unchanged: bool = True
    restart_required: bool


def set_pingan_runtime_environment(
    env_path: Path,
    *,
    environment: str,
) -> PingAnRuntimeEnvironmentReport:
    """Switch DEV/STG scope without changing providers, targets, or authority."""

    selected = _parse_runtime_environment(environment)
    candidate = env_path.expanduser()
    if candidate.is_symlink():
        raise PingAnRuntimeEnvironmentConfigurationError("private environment file must not be a symbolic link")
    path = candidate.resolve()
    if not path.is_file():
        raise PingAnRuntimeEnvironmentConfigurationError(f"private environment file is missing: {path}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise PingAnRuntimeEnvironmentConfigurationError("private environment file must be mode 0600")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PingAnRuntimeEnvironmentConfigurationError(f"cannot read private environment file: {exc}") from exc

    lines = content.splitlines()
    runtime_locations: list[tuple[int, PingAnRuntimeEnvironmentValue]] = []
    zeus_targets: list[str] = []
    for index, line in enumerate(lines):
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            continue
        name = match.group("name")
        if name == _RUNTIME_KEY:
            runtime_locations.append((index, _parse_runtime_environment(match.group("value"))))
        elif name == _ZEUS_TARGET_KEY:
            zeus_targets.append(_parse_scalar(match.group("value"), name=name))

    if len(runtime_locations) != 1:
        raise PingAnRuntimeEnvironmentConfigurationError(f"{_RUNTIME_KEY} must occur exactly once")
    if len(zeus_targets) != 1 or zeus_targets[0] not in {"dev", "stg", "prd"}:
        raise PingAnRuntimeEnvironmentConfigurationError(f"{_ZEUS_TARGET_KEY} must occur exactly once and select dev, stg, or prd")

    line_index, previous = runtime_locations[0]
    lines[line_index] = f"export {_RUNTIME_KEY}={selected}"
    rendered = "\n".join(lines) + ("\n" if content.endswith("\n") else "")
    if rendered != content:
        _write_private_file(path, rendered.encode("utf-8"))

    return PingAnRuntimeEnvironmentReport(
        env_path=str(path),
        previous_environment=previous,
        environment=selected,
        database_filename=f"soc_agent_{selected}.db",
        workbenches_enabled=selected == "dev",
        demo_no_auth_allowed=selected == "dev",
        zeus_target_environment=zeus_targets[0],
        restart_required=previous != selected,
    )


def _parse_runtime_environment(value: str) -> PingAnRuntimeEnvironmentValue:
    if not value.strip():
        raise PingAnRuntimeEnvironmentConfigurationError("PingAn host runtime environment must be dev or stg")
    parsed = _parse_scalar(value, name=_RUNTIME_KEY).lower()
    if parsed not in {"dev", "stg"}:
        raise PingAnRuntimeEnvironmentConfigurationError("PingAn host runtime environment must be dev or stg")
    return cast(PingAnRuntimeEnvironmentValue, parsed)


def _parse_scalar(value: str, *, name: str) -> str:
    try:
        tokens = shlex.split(value, comments=True, posix=True)
    except ValueError as exc:
        raise PingAnRuntimeEnvironmentConfigurationError(f"{name} has an unsupported current value") from exc
    if len(tokens) != 1 or not tokens[0].strip():
        raise PingAnRuntimeEnvironmentConfigurationError(f"{name} has an unsupported current value")
    return tokens[0].strip()


def _write_private_file(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        path.chmod(0o600)
    except OSError as exc:
        raise PingAnRuntimeEnvironmentConfigurationError(f"cannot update private environment file: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


__all__ = [
    "PingAnRuntimeEnvironmentConfigurationError",
    "PingAnRuntimeEnvironmentReport",
    "PingAnRuntimeEnvironmentValue",
    "set_pingan_runtime_environment",
]
