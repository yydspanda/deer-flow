"""Safely switch the two old-ZEUS provider modes in the private DEV env."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

PingAnLegacyProviderModeValue = Literal["fake", "internal"]

_MODE_KEYS = (
    "SOC_PINGAN_LEGACY_LIFECYCLE_MODE",
    "SOC_PINGAN_LEGACY_CALLBACK_MODE",
)
_ASSIGNMENT = re.compile(
    r"^(?P<prefix>\s*export\s+)"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*=\s*(?P<value>.*?)\s*$"
)


class PingAnLegacyProviderModeConfigurationError(ValueError):
    """Raised when the private environment cannot be updated safely."""


class PingAnLegacyProviderModeReport(BaseModel):
    """Secret-free evidence for an explicit provider-mode change."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_legacy_provider_mode.v1"] = "soc.pingan_legacy_provider_mode.v1"
    env_path: str
    mode: PingAnLegacyProviderModeValue
    previous_modes: dict[str, PingAnLegacyProviderModeValue]
    restart_required: bool


def set_pingan_legacy_provider_mode(
    env_path: Path,
    *,
    mode: PingAnLegacyProviderModeValue,
) -> PingAnLegacyProviderModeReport:
    """Update lifecycle and callback modes together without exposing secrets."""

    if mode not in {"fake", "internal"}:
        raise PingAnLegacyProviderModeConfigurationError("provider mode must be fake or internal")
    candidate = env_path.expanduser()
    if candidate.is_symlink():
        raise PingAnLegacyProviderModeConfigurationError("private environment file must not be a symbolic link")
    path = candidate.resolve()
    if not path.is_file():
        raise PingAnLegacyProviderModeConfigurationError(f"private environment file is missing: {path}")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PingAnLegacyProviderModeConfigurationError(f"cannot read private environment file: {exc}") from exc

    lines = content.splitlines()
    locations: dict[str, list[tuple[int, str]]] = {key: [] for key in _MODE_KEYS}
    for index, line in enumerate(lines):
        match = _ASSIGNMENT.fullmatch(line)
        if match is None:
            continue
        name = match.group("name")
        if name not in locations:
            continue
        locations[name].append((index, _parse_mode(match.group("value"), name=name)))

    invalid_counts = [key for key, values in locations.items() if len(values) != 1]
    if invalid_counts:
        rendered = ", ".join(invalid_counts)
        raise PingAnLegacyProviderModeConfigurationError(f"provider mode keys must each occur exactly once: {rendered}")

    previous_modes: dict[str, PingAnLegacyProviderModeValue] = {}
    for key in _MODE_KEYS:
        line_index, previous = locations[key][0]
        previous_modes[key] = previous
        lines[line_index] = f"export {key}={mode}"

    rendered = "\n".join(lines) + ("\n" if content.endswith("\n") else "")
    _write_private_file(path, rendered.encode("utf-8"))
    return PingAnLegacyProviderModeReport(
        env_path=str(path),
        mode=mode,
        previous_modes=previous_modes,
        restart_required=any(value != mode for value in previous_modes.values()),
    )


def _parse_mode(value: str, *, name: str) -> PingAnLegacyProviderModeValue:
    try:
        tokens = shlex.split(value, comments=True, posix=True)
    except ValueError as exc:
        raise PingAnLegacyProviderModeConfigurationError(f"{name} has an unsupported current value") from exc
    if len(tokens) != 1 or tokens[0] not in {"fake", "internal"}:
        raise PingAnLegacyProviderModeConfigurationError(f"{name} has an unsupported current value")
    return cast(PingAnLegacyProviderModeValue, tokens[0])


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
        raise PingAnLegacyProviderModeConfigurationError(f"cannot update private environment file: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


__all__ = [
    "PingAnLegacyProviderModeValue",
    "PingAnLegacyProviderModeConfigurationError",
    "PingAnLegacyProviderModeReport",
    "set_pingan_legacy_provider_mode",
]
