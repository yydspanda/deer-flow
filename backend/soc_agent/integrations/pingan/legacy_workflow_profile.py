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

from soc_agent.integrations.pingan.asset_location import (
    PINGAN_LEGACY_WORKFLOW_APP_ID,
    PINGAN_LEGACY_WORKFLOW_OPERATOR,
)

LEGACY_AGENT_CONFIG_PATH = Path("validation/original_works/agent_platform/agent_config.py")
LEGACY_WORKFLOW_ENV_PATH = Path(".env.soc-dev.local")
LEGACY_WORKFLOW_TERMINAL_ID = 1087710
LEGACY_WORKFLOW_DATACENTER_ID = 1087787
LEGACY_WORKFLOW_USER_ID = 1092332
LEGACY_PROFILE_SCHEMA_VERSION = "soc.pingan_legacy_workflow_profile.v1"

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


def load_legacy_workflow_profile(
    source_path: Path,
) -> PingAnLegacyWorkflowProfile:
    """Statically extract the sole branch containing the legacy YHSYS profile."""

    source = source_path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(source_path))
    except SyntaxError as exc:
        raise PingAnLegacyWorkflowProfileError("legacy Agent Platform config is not valid Python") from exc

    matches: list[PingAnLegacyWorkflowProfile] = []
    for assignments in _assignment_blocks(tree.body):
        app_config = assignments.get("app_config")
        base_url = assignments.get("agent_base_url")
        if not isinstance(app_config, dict) or not isinstance(base_url, str):
            continue
        app_entry = app_config.get(PINGAN_LEGACY_WORKFLOW_APP_ID)
        if not isinstance(app_entry, dict):
            continue
        app_secret = app_entry.get("app_sk")
        if not isinstance(app_secret, str) or not app_secret.strip():
            raise PingAnLegacyWorkflowProfileError("legacy YHSYS profile omitted app_sk")
        parsed = urlparse(base_url.strip())
        if parsed.scheme != "https" or not parsed.hostname:
            raise PingAnLegacyWorkflowProfileError("legacy YHSYS endpoint must be an HTTPS origin")
        matches.append(
            PingAnLegacyWorkflowProfile(
                base_url=base_url.strip().rstrip("/"),
                allowed_host=parsed.hostname.lower(),
                app_secret=app_secret.strip(),
            )
        )

    if len(matches) != 1:
        raise PingAnLegacyWorkflowProfileError("legacy source must contain exactly one YHSYS environment profile")
    return matches[0]


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
    profile = load_legacy_workflow_profile(source)
    current = target.read_text(encoding="utf-8") if target.is_file() else ""
    values = _profile_env_values(profile)
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
        "environment": "prd",
        "base_url": profile.base_url,
        "allowed_host": profile.allowed_host,
        "app_id": profile.app_id,
        "operator": profile.operator,
        "credential_present": bool(profile.app_secret),
        "updated_keys": list(_MANAGED_KEYS),
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
    profile: PingAnLegacyWorkflowProfile,
) -> dict[str, str]:
    return {
        "SOC_PINGAN_ASSET_WORKFLOW_ENABLED": "true",
        "SOC_PINGAN_WORKFLOW_ENV": "prd",
        "SOC_PINGAN_WORKFLOW_BASE_URL": profile.base_url,
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS": profile.allowed_host,
        "SOC_PINGAN_WORKFLOW_APP_ID": profile.app_id,
        "SOC_PINGAN_WORKFLOW_APP_SECRET": profile.app_secret,
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
        "SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION": "CALL_PINGAN_PRD",
    }


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
        if name in values or name in _REMOVED_KEYS:
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
            "# Extracted statically from the reviewed legacy PRD Agent Platform profile.",
            "# The adapter owns message.by; no operator environment variable is accepted.",
            *(f"export {name}={shlex.quote(values[name])}" for name in _MANAGED_KEYS),
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


__all__ = [
    "LEGACY_AGENT_CONFIG_PATH",
    "LEGACY_PROFILE_SCHEMA_VERSION",
    "LEGACY_WORKFLOW_ENV_PATH",
    "PingAnLegacyWorkflowProfile",
    "PingAnLegacyWorkflowProfileError",
    "load_legacy_workflow_profile",
    "prepare_legacy_workflow_env",
    "sanitized_profile_json",
]
