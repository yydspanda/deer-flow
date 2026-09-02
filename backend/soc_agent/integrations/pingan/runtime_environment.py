"""Governed PingAn Host Runtime and ZEUS target profile switch."""

from __future__ import annotations

import os
import re
import shlex
import stat
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from .zeus_target import (
    PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS,
    PINGAN_ZEUS_PRD_CONFIRMATION,
    PingAnZeusTargetConfigurationError,
    enforce_pingan_runtime_zeus_mapping,
    load_pingan_zeus_target,
)

PingAnRuntimeEnvironmentValue = Literal["dev", "stg"]

_RUNTIME_KEY = "SOC_PINGAN_ENV"
_ACTIVE_TARGET_KEYS = (
    "SOC_PINGAN_ZEUS_ENV",
    "SOC_PINGAN_ZEUS_BASE_URL",
    "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
    "SOC_PINGAN_ZEUS_APP_ID",
    "SOC_PINGAN_ZEUS_APP_KEY",
    "SOC_PINGAN_ZEUS_PRD_CONFIRMATION",
)
_TARGET_PROFILE_SUFFIXES = (
    "BASE_URL",
    "ALLOWED_HOSTS",
    "APP_ID",
    "APP_KEY",
)
_ASSIGNMENT = re.compile(
    r"^(?P<prefix>\s*export\s+)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*=\s*(?P<value>.*?)\s*$"
)


class PingAnRuntimeEnvironmentConfigurationError(ValueError):
    """Raised when the private deployment profile cannot be switched safely."""


class PingAnRuntimeEnvironmentReport(BaseModel):
    """Secret-free evidence for one governed Host profile change."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_runtime_environment.v2"] = "soc.pingan_runtime_environment.v2"
    env_path: str
    previous_environment: PingAnRuntimeEnvironmentValue
    environment: PingAnRuntimeEnvironmentValue
    database_filename: str
    workbenches_enabled: bool
    demo_no_auth_allowed: bool
    previous_zeus_target_environment: str
    zeus_target_environment: str
    runtime_target_mapping_applied: bool = True
    provider_modes_unchanged: bool = True
    external_action_setting_unchanged: bool = True
    restart_required: bool


def set_pingan_runtime_environment(
    env_path: Path,
    *,
    environment: str,
) -> PingAnRuntimeEnvironmentReport:
    """Atomically apply DEV->ZEUS PRD or STG->ZEUS STG to a private env."""

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
    assignments = _collect_assignments(lines)
    previous = _parse_runtime_environment(_exact_value(assignments, _RUNTIME_KEY))
    previous_target = _parse_target_environment(_exact_value(assignments, "SOC_PINGAN_ZEUS_ENV"))
    for name in _ACTIVE_TARGET_KEYS:
        _require_exact_assignment(assignments, name, allow_empty=True)

    selected_target = PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS[selected]
    profile = _load_target_profile(assignments, selected_target)
    replacements = {
        _RUNTIME_KEY: selected,
        "SOC_PINGAN_ZEUS_ENV": selected_target,
        "SOC_PINGAN_ZEUS_BASE_URL": profile["BASE_URL"],
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": profile["ALLOWED_HOSTS"],
        "SOC_PINGAN_ZEUS_APP_ID": profile["APP_ID"],
        "SOC_PINGAN_ZEUS_APP_KEY": profile["APP_KEY"],
        "SOC_PINGAN_ZEUS_PRD_CONFIRMATION": (PINGAN_ZEUS_PRD_CONFIRMATION if selected_target == "prd" else ""),
    }
    _validate_selected_target(replacements)

    rendered_lines: list[str] = []
    for line in lines:
        match = _ASSIGNMENT.fullmatch(line)
        name = match.group("name") if match is not None else None
        if name not in replacements or match is None:
            rendered_lines.append(line)
            continue
        current_value = _parse_scalar(
            match.group("value"),
            name=name,
            allow_empty=name == "SOC_PINGAN_ZEUS_PRD_CONFIRMATION",
        )
        rendered_lines.append(line if current_value == replacements[name] else _render_assignment(name, replacements[name]))

    rendered = "\n".join(rendered_lines) + ("\n" if content.endswith("\n") else "")
    changed = rendered != content
    if changed:
        _write_private_file(path, rendered.encode("utf-8"))

    return PingAnRuntimeEnvironmentReport(
        env_path=str(path),
        previous_environment=previous,
        environment=selected,
        database_filename=f"soc_agent_{selected}.db",
        workbenches_enabled=selected == "dev",
        demo_no_auth_allowed=selected == "dev",
        previous_zeus_target_environment=previous_target,
        zeus_target_environment=selected_target,
        restart_required=changed,
    )


def _collect_assignments(lines: list[str]) -> dict[str, list[str]]:
    assignments: dict[str, list[str]] = {}
    for line in lines:
        match = _ASSIGNMENT.fullmatch(line)
        if match is not None:
            assignments.setdefault(match.group("name"), []).append(match.group("value"))
    return assignments


def _load_target_profile(
    assignments: dict[str, list[str]],
    target_environment: str,
) -> dict[str, str]:
    prefix = f"SOC_PINGAN_ZEUS_{target_environment.upper()}_"
    values: dict[str, str] = {}
    for suffix in _TARGET_PROFILE_SUFFIXES:
        name = prefix + suffix
        value = _exact_value(assignments, name)
        if _looks_like_placeholder(value):
            raise PingAnRuntimeEnvironmentConfigurationError(f"{name} contains an unresolved value")
        values[suffix] = value
    return values


def _validate_selected_target(replacements: dict[str, str]) -> None:
    try:
        target = load_pingan_zeus_target(replacements)
        enforce_pingan_runtime_zeus_mapping(target)
    except PingAnZeusTargetConfigurationError as exc:
        raise PingAnRuntimeEnvironmentConfigurationError(str(exc)) from exc


def _exact_value(assignments: dict[str, list[str]], name: str) -> str:
    return _require_exact_assignment(assignments, name)


def _require_exact_assignment(
    assignments: dict[str, list[str]],
    name: str,
    *,
    allow_empty: bool = False,
) -> str:
    values = assignments.get(name, [])
    if len(values) != 1:
        raise PingAnRuntimeEnvironmentConfigurationError(f"{name} must occur exactly once")
    return _parse_scalar(values[0], name=name, allow_empty=allow_empty)


def _parse_runtime_environment(value: str) -> PingAnRuntimeEnvironmentValue:
    if not value.strip():
        raise PingAnRuntimeEnvironmentConfigurationError("PingAn host runtime environment must be dev or stg")
    parsed = _parse_scalar(value, name=_RUNTIME_KEY).lower()
    if parsed not in {"dev", "stg"}:
        raise PingAnRuntimeEnvironmentConfigurationError("PingAn host runtime environment must be dev or stg")
    return cast(PingAnRuntimeEnvironmentValue, parsed)


def _parse_target_environment(value: str) -> str:
    parsed = _parse_scalar(value, name="SOC_PINGAN_ZEUS_ENV").lower()
    if parsed not in {"dev", "stg", "prd"}:
        raise PingAnRuntimeEnvironmentConfigurationError("SOC_PINGAN_ZEUS_ENV must select dev, stg, or prd")
    return parsed


def _parse_scalar(value: str, *, name: str, allow_empty: bool = False) -> str:
    try:
        tokens = shlex.split(value, comments=True, posix=True)
    except ValueError as exc:
        raise PingAnRuntimeEnvironmentConfigurationError(f"{name} has an unsupported current value") from exc
    if len(tokens) != 1 or (not allow_empty and not tokens[0].strip()):
        raise PingAnRuntimeEnvironmentConfigurationError(f"{name} has an unsupported current value")
    return tokens[0].strip()


def _render_assignment(name: str, value: str) -> str:
    return f"export {name}={shlex.quote(value)}"


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip()
    return not normalized or (normalized.startswith("<") and normalized.endswith(">")) or normalized.lower() in {"changeme", "todo", "replace-me"}


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
