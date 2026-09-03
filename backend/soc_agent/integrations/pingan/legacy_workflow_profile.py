"""Prepare the private PingAn workflow profile from the reviewed legacy source."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shlex
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from soc_agent.integrations.pingan.agent_platform_target import (
    PINGAN_AGENT_PLATFORM_PRD_CONFIRMATION,
)
from soc_agent.integrations.pingan.asset_location import (
    PINGAN_LEGACY_WORKFLOW_APP_ID,
    PINGAN_LEGACY_WORKFLOW_OPERATOR,
)

LEGACY_AGENT_CONFIG_PATH = Path("validation/original_works/agent_platform/agent_config.py")
LEGACY_WORKFLOW_ENV_PATH = Path(".env.soc-dev.local")
LEGACY_WORKFLOW_TERMINAL_ID = 1087710
LEGACY_WORKFLOW_DATACENTER_ID = 1087787
LEGACY_WORKFLOW_USER_ID = 1092332
LEGACY_PROFILE_SCHEMA_VERSION = "soc.pingan_legacy_workflow_profile.v2"

_BLOCK_BEGIN = "# BEGIN SOC PINGAN LEGACY WORKFLOW PROFILE"
_BLOCK_END = "# END SOC PINGAN LEGACY WORKFLOW PROFILE"
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=")
_MANAGED_KEYS = (
    "SOC_PINGAN_ASSET_WORKFLOW_ENABLED",
    "SOC_PINGAN_WORKFLOW_ENV",
    "SOC_PINGAN_WORKFLOW_BASE_URL",
    "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
    "SOC_PINGAN_WORKFLOW_APP_ID",
    "SOC_PINGAN_WORKFLOW_APP_SECRET",
    "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
    "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
    "SOC_PINGAN_WORKFLOW_USER_ID",
    "SOC_PINGAN_WORKFLOW_AUTH_PATH",
    "SOC_PINGAN_WORKFLOW_REQUEST_TIMEOUT_SECONDS",
    "SOC_PINGAN_WORKFLOW_TIMEOUT_SECONDS",
    "SOC_PINGAN_WORKFLOW_POLL_INTERVAL_SECONDS",
    "SOC_PINGAN_WORKFLOW_TOKEN_TTL_SECONDS",
    "SOC_PINGAN_WORKFLOW_MAX_REQUEST_BYTES",
    "SOC_PINGAN_WORKFLOW_MAX_RESPONSE_BYTES",
    "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION",
    "SOC_PINGAN_WORKFLOW_PRD_BASE_URL",
    "SOC_PINGAN_WORKFLOW_PRD_ALLOWED_HOSTS",
    "SOC_PINGAN_WORKFLOW_PRD_APP_ID",
    "SOC_PINGAN_WORKFLOW_PRD_APP_SECRET",
    "SOC_PINGAN_WORKFLOW_PRD_TERMINAL_ID",
    "SOC_PINGAN_WORKFLOW_PRD_DATACENTER_ID",
    "SOC_PINGAN_WORKFLOW_PRD_USER_ID",
    "SOC_PINGAN_WORKFLOW_STG_BASE_URL",
    "SOC_PINGAN_WORKFLOW_STG_ALLOWED_HOSTS",
    "SOC_PINGAN_WORKFLOW_STG_APP_ID",
    "SOC_PINGAN_WORKFLOW_STG_APP_SECRET",
    "SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID",
    "SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID",
    "SOC_PINGAN_WORKFLOW_STG_USER_ID",
)
_STG_PRIVATE_KEYS = (
    "SOC_PINGAN_WORKFLOW_STG_APP_ID",
    "SOC_PINGAN_WORKFLOW_STG_APP_SECRET",
    "SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID",
    "SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID",
    "SOC_PINGAN_WORKFLOW_STG_USER_ID",
)
_REMOVED_KEYS = frozenset(
    {
        "env_profile",
        "SOC_PINGAN_PROVIDER_IMPORT_PATHS",
        "SOC_PINGAN_ZEUS_SIGNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_RUNNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_OPERATOR",
    }
)


class PingAnLegacyWorkflowProfileError(ValueError):
    """Raised when the reviewed source cannot produce one unambiguous profile."""


@dataclass(frozen=True)
class PingAnLegacyWorkflowProfile:
    """Minimal PRD credential and endpoint contract extracted without imports."""

    base_url: str
    allowed_host: str
    app_secret: str = field(repr=False)
    app_id: str = PINGAN_LEGACY_WORKFLOW_APP_ID
    operator: str = PINGAN_LEGACY_WORKFLOW_OPERATOR


@dataclass(frozen=True)
class PingAnLegacyWorkflowProfiles:
    """Reviewed PRD workflow identity plus the STG Agent Platform origin."""

    prd: PingAnLegacyWorkflowProfile
    stg_base_url: str
    stg_allowed_host: str


def load_legacy_workflow_profile(
    source_path: Path,
) -> PingAnLegacyWorkflowProfile:
    """Statically extract the sole branch containing the legacy YHSYS profile."""

    return load_legacy_workflow_profiles(source_path).prd


def load_legacy_workflow_profiles(
    source_path: Path,
) -> PingAnLegacyWorkflowProfiles:
    """Extract the usable PRD identity and the reviewed STG origin statically."""

    source = source_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as exc:
        raise PingAnLegacyWorkflowProfileError("legacy Agent Platform config is not valid Python") from exc

    matches: list[PingAnLegacyWorkflowProfile] = []
    stg_origins: dict[str, str] = {}
    for assignments in _assignment_blocks(tree.body):
        app_config = assignments.get("app_config")
        base_url = assignments.get("agent_base_url")
        if not isinstance(app_config, dict) or not isinstance(base_url, str):
            continue
        parsed = urlparse(base_url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise PingAnLegacyWorkflowProfileError("legacy Agent Platform endpoint must be an HTTPS origin")
        if "stg" in parsed.hostname.lower():
            stg_origins[base_url.strip().rstrip("/")] = parsed.hostname.lower()
        app_entry = app_config.get(PINGAN_LEGACY_WORKFLOW_APP_ID)
        if not isinstance(app_entry, dict):
            continue
        app_secret = app_entry.get("app_sk")
        if not isinstance(app_secret, str) or not app_secret.strip():
            raise PingAnLegacyWorkflowProfileError("legacy YHSYS profile omitted app_sk")
        matches.append(
            PingAnLegacyWorkflowProfile(
                base_url=base_url.strip().rstrip("/"),
                allowed_host=parsed.hostname.lower(),
                app_secret=app_secret.strip(),
            )
        )

    if len(matches) != 1:
        raise PingAnLegacyWorkflowProfileError("legacy source must contain exactly one YHSYS environment profile")
    if len(stg_origins) != 1:
        raise PingAnLegacyWorkflowProfileError("legacy source must contain exactly one STG Agent Platform origin")
    stg_base_url, stg_allowed_host = next(iter(stg_origins.items()))
    return PingAnLegacyWorkflowProfiles(
        prd=matches[0],
        stg_base_url=stg_base_url,
        stg_allowed_host=stg_allowed_host,
    )


def prepare_legacy_workflow_env(
    *,
    repo_root: Path,
    source_path: Path | None = None,
    env_path: Path | None = None,
    apply: bool = False,
) -> dict[str, Any]:
    """Build or atomically update a private env file without exposing its secret."""

    root = repo_root.expanduser().resolve()
    source = (source_path or (root / LEGACY_AGENT_CONFIG_PATH)).expanduser().resolve()
    target = (env_path or (root / LEGACY_WORKFLOW_ENV_PATH)).expanduser().resolve()
    profiles = load_legacy_workflow_profiles(source)
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    current_values = _shell_assignment_values(current)
    values, stg_missing_keys = _profile_env_values(
        profiles,
        current_values=current_values,
    )
    rendered, removed_keys = _rewrite_env(current, values)
    changed = rendered != current

    if apply and changed:
        _write_private_env(target, rendered)
    elif apply and target.is_file():
        target.chmod(0o600)

    return {
        "schema_version": LEGACY_PROFILE_SCHEMA_VERSION,
        "source_path": _display_path(source, root),
        "source_sha256": _sha256(source),
        "env_path": _display_path(target, root),
        "runtime_environment": current_values.get("SOC_PINGAN_ENV", "dev"),
        "active_target_environment": values["SOC_PINGAN_WORKFLOW_ENV"],
        "runtime_target_mapping": {"dev": "prd", "stg": "stg"},
        "prd_base_url": profiles.prd.base_url,
        "prd_allowed_host": profiles.prd.allowed_host,
        "prd_app_id": profiles.prd.app_id,
        "stg_base_url": profiles.stg_base_url,
        "stg_allowed_host": profiles.stg_allowed_host,
        "operator": profiles.prd.operator,
        "prd_credential_present": bool(profiles.prd.app_secret),
        "stg_profile_ready": not stg_missing_keys,
        "stg_missing_keys": stg_missing_keys,
        "updated_keys": [name for name in _MANAGED_KEYS if name in values],
        "removed_keys": sorted(removed_keys),
        "changed": changed,
        "applied": apply,
        "secret_in_output": False,
    }


def _assignment_blocks(
    statements: Sequence[ast.stmt],
) -> Iterable[dict[str, Any]]:
    assignments: dict[str, Any] = {}
    for statement in statements:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            if isinstance(target, ast.Name):
                try:
                    assignments[target.id] = ast.literal_eval(statement.value)
                except (ValueError, TypeError):
                    pass
    yield assignments
    for statement in statements:
        if isinstance(statement, ast.If):
            yield from _assignment_blocks(statement.body)
            yield from _assignment_blocks(statement.orelse)


def _profile_env_values(
    profiles: PingAnLegacyWorkflowProfiles,
    *,
    current_values: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    runtime_environment = current_values.get("SOC_PINGAN_ENV", "dev").strip().lower()
    if runtime_environment not in {"dev", "stg"}:
        raise PingAnLegacyWorkflowProfileError("existing SOC_PINGAN_ENV must select dev or stg")
    stg_values = {name: current_values.get(name, "").strip() for name in _STG_PRIVATE_KEYS}
    present_stg_keys = [name for name, value in stg_values.items() if not _looks_like_placeholder(value)]
    if present_stg_keys and len(present_stg_keys) != len(_STG_PRIVATE_KEYS):
        missing = sorted(name for name, value in stg_values.items() if _looks_like_placeholder(value))
        raise PingAnLegacyWorkflowProfileError("existing Agent Platform STG profile is partial; missing " + ", ".join(missing))
    stg_missing_keys = sorted(name for name, value in stg_values.items() if _looks_like_placeholder(value))
    if runtime_environment == "stg" and stg_missing_keys:
        raise PingAnLegacyWorkflowProfileError("project STG requires a reviewed Agent Platform STG YHSYS profile; missing " + ", ".join(stg_missing_keys))

    prd = profiles.prd
    values = {
        "SOC_PINGAN_ASSET_WORKFLOW_ENABLED": "true",
        "SOC_PINGAN_WORKFLOW_ENV": "prd",
        "SOC_PINGAN_WORKFLOW_BASE_URL": prd.base_url,
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": prd.allowed_host,
        "SOC_PINGAN_WORKFLOW_APP_ID": prd.app_id,
        "SOC_PINGAN_WORKFLOW_APP_SECRET": prd.app_secret,
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID": str(LEGACY_WORKFLOW_TERMINAL_ID),
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID": str(LEGACY_WORKFLOW_DATACENTER_ID),
        "SOC_PINGAN_WORKFLOW_USER_ID": str(LEGACY_WORKFLOW_USER_ID),
        "SOC_PINGAN_WORKFLOW_AUTH_PATH": "/appid/auth/login",
        "SOC_PINGAN_WORKFLOW_REQUEST_TIMEOUT_SECONDS": "15",
        "SOC_PINGAN_WORKFLOW_TIMEOUT_SECONDS": "600",
        "SOC_PINGAN_WORKFLOW_POLL_INTERVAL_SECONDS": "2",
        "SOC_PINGAN_WORKFLOW_TOKEN_TTL_SECONDS": "3600",
        "SOC_PINGAN_WORKFLOW_MAX_REQUEST_BYTES": "1000000",
        "SOC_PINGAN_WORKFLOW_MAX_RESPONSE_BYTES": "2000000",
        "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": (PINGAN_AGENT_PLATFORM_PRD_CONFIRMATION),
        "SOC_PINGAN_WORKFLOW_PRD_BASE_URL": prd.base_url,
        "SOC_PINGAN_WORKFLOW_PRD_ALLOWED_HOSTS": prd.allowed_host,
        "SOC_PINGAN_WORKFLOW_PRD_APP_ID": prd.app_id,
        "SOC_PINGAN_WORKFLOW_PRD_APP_SECRET": prd.app_secret,
        "SOC_PINGAN_WORKFLOW_PRD_TERMINAL_ID": str(LEGACY_WORKFLOW_TERMINAL_ID),
        "SOC_PINGAN_WORKFLOW_PRD_DATACENTER_ID": str(LEGACY_WORKFLOW_DATACENTER_ID),
        "SOC_PINGAN_WORKFLOW_PRD_USER_ID": str(LEGACY_WORKFLOW_USER_ID),
        "SOC_PINGAN_WORKFLOW_STG_BASE_URL": profiles.stg_base_url,
        "SOC_PINGAN_WORKFLOW_STG_ALLOWED_HOSTS": profiles.stg_allowed_host,
    }
    if not stg_missing_keys:
        values.update(stg_values)
    if runtime_environment == "stg":
        values.update(
            {
                "SOC_PINGAN_WORKFLOW_ENV": "stg",
                "SOC_PINGAN_WORKFLOW_BASE_URL": profiles.stg_base_url,
                "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": profiles.stg_allowed_host,
                "SOC_PINGAN_WORKFLOW_APP_ID": stg_values["SOC_PINGAN_WORKFLOW_STG_APP_ID"],
                "SOC_PINGAN_WORKFLOW_APP_SECRET": stg_values["SOC_PINGAN_WORKFLOW_STG_APP_SECRET"],
                "SOC_PINGAN_WORKFLOW_TERMINAL_ID": stg_values["SOC_PINGAN_WORKFLOW_STG_TERMINAL_ID"],
                "SOC_PINGAN_WORKFLOW_DATACENTER_ID": stg_values["SOC_PINGAN_WORKFLOW_STG_DATACENTER_ID"],
                "SOC_PINGAN_WORKFLOW_USER_ID": stg_values["SOC_PINGAN_WORKFLOW_STG_USER_ID"],
                "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": "",
            }
        )
    return values, stg_missing_keys


def _rewrite_env(
    current: str,
    values: dict[str, str],
) -> tuple[str, set[str]]:
    retained: list[str] = []
    removed: set[str] = set()
    inside_generated_block = False
    for line in current.splitlines():
        if line.strip() == _BLOCK_BEGIN:
            inside_generated_block = True
            continue
        if line.strip() == _BLOCK_END:
            inside_generated_block = False
            continue
        if inside_generated_block:
            continue
        match = _ENV_ASSIGNMENT.match(line)
        name = match.group("name") if match else None
        if name in _MANAGED_KEYS or name in _REMOVED_KEYS:
            if name in _REMOVED_KEYS:
                removed.add(name)
            continue
        retained.append(line)

    while retained and not retained[-1].strip():
        retained.pop()
    if retained:
        retained.append("")
    retained.extend(
        [
            _BLOCK_BEGIN,
            "# PRD identity and DEV->PRD mapping are extracted from reviewed legacy source.",
            "# STG origin is extracted too; STG YHSYS identity/IDs remain operator-owned until reviewed.",
            "# The adapter owns message.by; no operator environment variable is accepted.",
            *(f"export {name}={shlex.quote(values[name])}" for name in _MANAGED_KEYS if name in values),
            _BLOCK_END,
        ]
    )
    return "\n".join(retained) + "\n", removed


def _write_private_env(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(0o600)
        os.replace(temporary, path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sanitized_profile_json(report: dict[str, Any]) -> str:
    """Serialize only the public preparation report."""

    return json.dumps(report, ensure_ascii=False, indent=2)


def _shell_assignment_values(content: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in content.splitlines():
        match = _ENV_ASSIGNMENT.match(line)
        if match is None:
            continue
        name = match.group("name")
        raw_value = line[match.end() :].strip()
        try:
            tokens = shlex.split(raw_value, comments=True, posix=True)
        except ValueError as exc:
            raise PingAnLegacyWorkflowProfileError(f"existing private env has an invalid value for {name}") from exc
        if len(tokens) == 1:
            values[name] = tokens[0]
    return values


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip()
    return not normalized or (normalized.startswith("<") and normalized.endswith(">")) or normalized.lower() in {"changeme", "replace-me", "todo"}


__all__ = [
    "LEGACY_AGENT_CONFIG_PATH",
    "LEGACY_PROFILE_SCHEMA_VERSION",
    "LEGACY_WORKFLOW_ENV_PATH",
    "PingAnLegacyWorkflowProfile",
    "PingAnLegacyWorkflowProfiles",
    "PingAnLegacyWorkflowProfileError",
    "load_legacy_workflow_profile",
    "load_legacy_workflow_profiles",
    "prepare_legacy_workflow_env",
    "sanitized_profile_json",
]
