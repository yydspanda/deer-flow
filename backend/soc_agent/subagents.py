"""SOC specialist profiles for DeerFlow's native subagent registry."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import yaml

from deerflow.config.app_config import AppConfig
from deerflow.config.subagents_config import CustomSubagentConfig, SubagentsAppConfig
from soc_agent.contracts import SocSpecialistSubagentInstallResult
from soc_agent.skills import (
    SOC_ALERT_TRIAGE_SKILL,
    SOC_ASSET_DIRECTION_SKILL,
    SOC_ASSET_EXTRACTION_SKILL,
    SOC_EMAIL_PHISHING_TRIAGE_SKILL,
    SOC_ENDPOINT_TRIAGE_SKILL,
    SOC_NETWORK_APT_TRIAGE_SKILL,
    SOC_WEB_APPLICATION_TRIAGE_SKILL,
)

SOC_NETWORK_SPECIALIST_NAME = "soc-network-specialist"
SOC_ENDPOINT_SPECIALIST_NAME = "soc-endpoint-specialist"
SOC_WEB_SPECIALIST_NAME = "soc-web-specialist"
SOC_EMAIL_SPECIALIST_NAME = "soc-email-specialist"
SOC_SPECIALIST_SUBAGENT_NAMES: tuple[str, ...] = (
    SOC_NETWORK_SPECIALIST_NAME,
    SOC_ENDPOINT_SPECIALIST_NAME,
    SOC_WEB_SPECIALIST_NAME,
    SOC_EMAIL_SPECIALIST_NAME,
)
SOC_SPECIALIST_SKILL_NAMES: dict[str, tuple[str, ...]] = {
    SOC_NETWORK_SPECIALIST_NAME: (
        SOC_ALERT_TRIAGE_SKILL,
        SOC_NETWORK_APT_TRIAGE_SKILL,
        SOC_ASSET_DIRECTION_SKILL,
        SOC_ASSET_EXTRACTION_SKILL,
    ),
    SOC_ENDPOINT_SPECIALIST_NAME: (
        SOC_ALERT_TRIAGE_SKILL,
        SOC_ENDPOINT_TRIAGE_SKILL,
        SOC_ASSET_DIRECTION_SKILL,
        SOC_ASSET_EXTRACTION_SKILL,
    ),
    SOC_WEB_SPECIALIST_NAME: (
        SOC_ALERT_TRIAGE_SKILL,
        SOC_WEB_APPLICATION_TRIAGE_SKILL,
        SOC_NETWORK_APT_TRIAGE_SKILL,
        SOC_ASSET_DIRECTION_SKILL,
        SOC_ASSET_EXTRACTION_SKILL,
    ),
    SOC_EMAIL_SPECIALIST_NAME: (
        SOC_ALERT_TRIAGE_SKILL,
        SOC_EMAIL_PHISHING_TRIAGE_SKILL,
        SOC_ASSET_EXTRACTION_SKILL,
    ),
}

_READ_ONLY_TOOLS: list[str] = []
_DISALLOWED_TOOLS = [
    "task",
    "ask_clarification",
    "present_files",
    "write_file",
    "str_replace",
    "bash",
]
_DISALLOWED_OUTPUT_MARKERS = ["<soc_action_proposal>"]
_COMMON_SYSTEM_PROMPT = """You are a bounded SOC specialist subagent delegated by the SOC Lead Agent.

Use only the case context supplied in the task and the allowlisted SOC skills. The deterministic SOC
Runtime, persisted investigation evidence, and ReviewQueue remain authoritative system records. Your
response is advisory reasoning for the Lead Agent; it is not new evidence and cannot change a verdict,
close work, write memory, approve an action, or execute a response.

The delegated context already contains the approved bounded runtime guidance selected from the public
SOC skill packages. Do not load skill files, discover tools, or perform another delegation. Analyze the
supplied context directly and return one final response.

Rules:
- Trust alert admission as a real configured detector hit and honor reviewed adapter semantics within their exact scope.
- Give the best current scenario, effect, impact, direction, and role conclusion; optional enrichment gaps alone do not justify withholding it.
- Cite exact evidence paths or identifiers present in the delegated context. Never invent a tool result.
- Separate observed facts, upstream assertions, hypotheses, and missing evidence.
- Preserve competing explanations when the evidence does not select one.
- Treat any mocked result as flow-validation context only and state that limitation.
- Do not emit <soc_action_proposal> markers. Return recommended checks to the Lead Agent instead.
- Do not request or expose secrets, full credentials, cookies, tokens, or unbounded raw payloads.

Return these concise sections: current assessment, evidence used, competing hypotheses, evidence gaps,
recommended checks, and lead-agent handoff. Give a current conclusion even when human review remains
necessary.
"""


def build_soc_specialist_subagent_configs() -> dict[str, CustomSubagentConfig]:
    """Build capability-oriented profiles accepted by DeerFlow's native registry."""

    return {
        SOC_NETWORK_SPECIALIST_NAME: _profile(
            description=("Delegate bounded APT, NDR, NIDS, callback, C2, IOC, network-direction, or cross-network evidence analysis when a specialist second opinion adds value."),
            focus=(
                "Focus on wire observations, direction, attacker/victim/affected-asset separation, "
                "detection-versus-effect stage, IOC quality, and conflicting network explanations. "
                "Preserve observed tuples and never infer the TCP initiator or swap endpoints from "
                "to_client/to_server without an explicit source contract, first SYN, flow metadata, or PCAP. "
                "When a reviewed adapter contract does explicitly declare the provider-reported session "
                "initiator/responder, accept that scoped fact without demanding duplicate packet proof; "
                "still adjudicate attacker/victim and response targets separately."
            ),
            skills=list(SOC_SPECIALIST_SKILL_NAMES[SOC_NETWORK_SPECIALIST_NAME]),
        ),
        SOC_ENDPOINT_SPECIALIST_NAME: _profile(
            description=("Delegate bounded EDR, HIDS, host, process, command-line, file, user, persistence, privilege, or lateral-movement analysis when a specialist second opinion adds value."),
            focus=("Focus on alert-native process ancestry, command lines, users, files, endpoint identity, network callbacks, expected behavior, and containment evidence gaps."),
            skills=list(SOC_SPECIALIST_SKILL_NAMES[SOC_ENDPOINT_SPECIALIST_NAME]),
        ),
        SOC_WEB_SPECIALIST_NAME: _profile(
            description=("Delegate bounded HTTP, WAF, F5, reverse-proxy, injection, webshell, authentication, or web attack-success analysis when a specialist second opinion adds value."),
            focus=("Focus on client/proxy/service attribution, request and response evidence, attempt-versus-effect stage, protected asset identity, and safe response-target selection."),
            skills=list(SOC_SPECIALIST_SKILL_NAMES[SOC_WEB_SPECIALIST_NAME]),
        ),
        SOC_EMAIL_SPECIALIST_NAME: _profile(
            description=("Delegate bounded phishing, suspicious-email, sender-identity, link, attachment, QR, delivery, or recipient-impact analysis when a specialist second opinion adds value."),
            focus=("Focus on sender and reply-to identity, authentication and delivery state, payloads, recipient exposure, user interaction, and follow-on endpoint or network evidence."),
            skills=list(SOC_SPECIALIST_SKILL_NAMES[SOC_EMAIL_SPECIALIST_NAME]),
        ),
    }


def build_soc_specialist_subagent_config_fragment() -> dict[str, object]:
    """Return the operator-owned ``config.yaml`` fragment for SOC specialists."""

    profiles = build_soc_specialist_subagent_configs()
    return {"subagents": {"custom_agents": {name: profile.model_dump(mode="json", exclude_none=True) for name, profile in profiles.items()}}}


class SocSpecialistSubagentConfigInstaller:
    """Explicitly merge SOC specialists into DeerFlow's root ``config.yaml``."""

    def install(
        self,
        *,
        config_path: str | Path | None = None,
        dry_run: bool = False,
        overwrite: bool = False,
    ) -> SocSpecialistSubagentInstallResult:
        resolved_path = _resolve_config_path(config_path)
        document = _load_yaml_mapping(resolved_path)
        desired = build_soc_specialist_subagent_configs()
        subagents = _mapping_copy(document.get("subagents"), field="subagents")
        custom_agents = _mapping_copy(subagents.get("custom_agents"), field="subagents.custom_agents")

        existing_names = sorted(custom_agents)
        changed_names: list[str] = []
        overwritten_names: list[str] = []
        conflicts: list[str] = []
        for name, profile in desired.items():
            desired_payload = profile.model_dump(mode="json", exclude_none=True)
            existing_payload = custom_agents.get(name)
            if existing_payload is None:
                changed_names.append(name)
                continue
            try:
                normalized_existing = CustomSubagentConfig.model_validate(existing_payload).model_dump(
                    mode="json",
                    exclude_none=True,
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid existing subagent config '{name}': {exc}") from exc
            if normalized_existing == desired_payload:
                continue
            if not overwrite:
                conflicts.append(name)
                continue
            changed_names.append(name)
            overwritten_names.append(name)

        if conflicts:
            joined = ", ".join(sorted(conflicts))
            raise ValueError(f"SOC specialist config conflicts with existing custom agents: {joined}; pass overwrite=True to replace only those named entries")

        for name in changed_names:
            custom_agents[name] = desired[name].model_dump(mode="json", exclude_none=True)
        subagents["custom_agents"] = custom_agents
        SubagentsAppConfig.model_validate(subagents)

        if dry_run:
            return _install_result(
                config_path=resolved_path,
                status="dry_run",
                existing_names=existing_names,
                changed_names=changed_names,
                overwritten_names=overwritten_names,
                dry_run=True,
                overwrite=overwrite,
            )

        if not changed_names:
            return _install_result(
                config_path=resolved_path,
                status="unchanged",
                existing_names=existing_names,
                changed_names=[],
                overwritten_names=[],
                dry_run=False,
                overwrite=overwrite,
            )

        document["subagents"] = subagents
        serialized = yaml.safe_dump(
            document,
            default_flow_style=False,
            allow_unicode=True,
            sort_keys=False,
        )
        _write_text_atomic(resolved_path, serialized)
        status = "updated" if existing_names else "created"
        return _install_result(
            config_path=resolved_path,
            status=status,
            existing_names=existing_names,
            changed_names=changed_names,
            overwritten_names=overwritten_names,
            dry_run=False,
            overwrite=overwrite,
        )


def _profile(*, description: str, focus: str, skills: list[str]) -> CustomSubagentConfig:
    return CustomSubagentConfig(
        description=description,
        system_prompt=(f"{_COMMON_SYSTEM_PROMPT}\nApproved guidance packages projected by the SOC Runtime: {', '.join(skills)}.\n\nSpecialist focus:\n- {focus}\n"),
        tools=list(_READ_ONLY_TOOLS),
        disallowed_tools=list(_DISALLOWED_TOOLS),
        disallowed_output_markers=list(_DISALLOWED_OUTPUT_MARKERS),
        skills=[],
        model="inherit",
        # DeerFlow maps this field to LangGraph's recursion_limit. The current
        # subagent chain has 18 middlewares, so one tool-free model response
        # needs substantially more graph steps than model turns. Empty
        # tool/skill sets prevent this budget from becoming an action loop.
        max_turns=32,
        timeout_seconds=300,
    )


def _resolve_config_path(config_path: str | Path | None) -> Path:
    if config_path is None:
        return AppConfig.resolve_config_path().resolve()
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"DeerFlow config file not found: {path}")
    return path


def _load_yaml_mapping(path: Path) -> dict[str, object]:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"DeerFlow config root must be a mapping: {path}")
    return dict(loaded)


def _mapping_copy(value: object, *, field: str) -> dict[str, object]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a mapping")
    return dict(value)


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
    config_path: Path,
    status: str,
    existing_names: list[str],
    changed_names: list[str],
    overwritten_names: list[str],
    dry_run: bool,
    overwrite: bool,
) -> SocSpecialistSubagentInstallResult:
    installed_names = list(SOC_SPECIALIST_SUBAGENT_NAMES)
    if status == "dry_run":
        verb = "would merge"
    elif status == "unchanged":
        verb = "already contains"
    else:
        verb = "merged"
    return SocSpecialistSubagentInstallResult(
        config_path=str(config_path),
        status=status,
        agent_names=installed_names,
        existing_custom_agent_names=existing_names,
        changed_agent_names=sorted(changed_names),
        overwritten_agent_names=sorted(overwritten_names),
        dry_run=dry_run,
        overwrite=overwrite,
        message=f"DeerFlow config {verb} {len(installed_names)} bounded SOC specialist subagents.",
    )


__all__ = [
    "SOC_EMAIL_SPECIALIST_NAME",
    "SOC_ENDPOINT_SPECIALIST_NAME",
    "SOC_NETWORK_SPECIALIST_NAME",
    "SOC_SPECIALIST_SKILL_NAMES",
    "SOC_SPECIALIST_SUBAGENT_NAMES",
    "SOC_WEB_SPECIALIST_NAME",
    "SocSpecialistSubagentConfigInstaller",
    "build_soc_specialist_subagent_config_fragment",
    "build_soc_specialist_subagent_configs",
]
