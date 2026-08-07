"""Install SOC Lead Agent profiles into DeerFlow custom-agent storage."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import yaml

from deerflow.config.agents_config import validate_agent_name
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from soc_agent.contracts import SocLeadAgentInstallResult, SocLeadAgentProfile
from soc_agent.lead_agent import build_soc_lead_agent_profile


class SocLeadAgentProfileInstaller:
    """Thin adapter over DeerFlow's per-user custom-agent profile storage."""

    def install(
        self,
        *,
        profile: SocLeadAgentProfile | None = None,
        user_id: str | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> SocLeadAgentInstallResult:
        resolved_profile = profile or build_soc_lead_agent_profile()
        agent_name = _normalize_agent_name(resolved_profile.name)
        effective_user_id = user_id or get_effective_user_id()
        paths = get_paths()
        agent_dir = paths.user_agent_dir(effective_user_id, agent_name)
        legacy_dir = paths.agent_dir(agent_name)
        config_path = agent_dir / "config.yaml"
        soul_path = agent_dir / "SOUL.md"

        legacy_exists = (legacy_dir / "config.yaml").exists()
        user_exists = config_path.exists()
        if legacy_exists and not user_exists:
            return _install_result(
                profile=resolved_profile,
                user_id=effective_user_id,
                agent_dir=agent_dir,
                config_path=config_path,
                soul_path=soul_path,
                status="skipped",
                dry_run=dry_run,
                overwrite=overwrite,
                message=(f"Agent '{agent_name}' exists in DeerFlow legacy shared storage at {legacy_dir}; not installing a user-scoped SOC profile that would shadow it."),
            )

        if dry_run:
            action = "update" if user_exists else "create"
            if user_exists and not overwrite:
                action = "skip"
            return _install_result(
                profile=resolved_profile,
                user_id=effective_user_id,
                agent_dir=agent_dir,
                config_path=config_path,
                soul_path=soul_path,
                status="dry_run",
                dry_run=True,
                overwrite=overwrite,
                message=f"Dry run: would {action} DeerFlow SOC custom agent '{agent_name}' for user '{effective_user_id}'.",
            )

        if user_exists and not overwrite:
            return _install_result(
                profile=resolved_profile,
                user_id=effective_user_id,
                agent_dir=agent_dir,
                config_path=config_path,
                soul_path=soul_path,
                status="skipped",
                dry_run=False,
                overwrite=False,
                message=f"Agent '{agent_name}' already exists for user '{effective_user_id}'; pass overwrite=True to update it.",
            )

        agent_dir.mkdir(parents=True, exist_ok=True)
        config_data = _profile_config(resolved_profile, agent_name)
        _write_text_atomic(config_path, yaml.dump(config_data, default_flow_style=False, allow_unicode=True, sort_keys=False))
        _write_text_atomic(soul_path, resolved_profile.soul)

        return _install_result(
            profile=resolved_profile,
            user_id=effective_user_id,
            agent_dir=agent_dir,
            config_path=config_path,
            soul_path=soul_path,
            status="updated" if user_exists else "created",
            dry_run=False,
            overwrite=overwrite,
            message=f"Installed DeerFlow SOC custom agent '{agent_name}' for user '{effective_user_id}'.",
        )


def _normalize_agent_name(name: str) -> str:
    validated = validate_agent_name(name)
    if validated is None:
        raise ValueError("agent name is required")
    return validated.lower()


def _profile_config(profile: SocLeadAgentProfile, agent_name: str) -> dict[str, object]:
    config: dict[str, object] = {
        "name": agent_name,
        "description": profile.description,
        "skills": profile.skills,
    }
    if profile.tool_groups is not None:
        config["tool_groups"] = profile.tool_groups
    if profile.middlewares:
        config["middlewares"] = profile.middlewares
    return config


def _write_text_atomic(path: Path, content: str) -> None:
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _install_result(
    *,
    profile: SocLeadAgentProfile,
    user_id: str,
    agent_dir: Path,
    config_path: Path,
    soul_path: Path,
    status: str,
    dry_run: bool,
    overwrite: bool,
    message: str,
) -> SocLeadAgentInstallResult:
    return SocLeadAgentInstallResult(
        agent_name=profile.name.lower(),
        user_id=user_id,
        agent_dir=str(agent_dir),
        config_path=str(config_path),
        soul_path=str(soul_path),
        status=status,
        dry_run=dry_run,
        overwrite=overwrite,
        message=message,
    )
