"""Validated environment boundary for PingAn Agent Platform workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast
from urllib.parse import urlparse

PINGAN_AGENT_PLATFORM_PRD_CONFIRMATION = "CALL_PINGAN_PRD"
PINGAN_RUNTIME_AGENT_PLATFORM_TARGET_ENVIRONMENTS: Mapping[str, str] = MappingProxyType(
    {
        "dev": "prd",
        "stg": "stg",
    }
)


class PingAnAgentPlatformTargetConfigurationError(ValueError):
    """Raised when an Agent Platform workflow target is incomplete or unsafe."""


@dataclass(frozen=True)
class PingAnAgentPlatformTargetConfig:
    """One explicit Agent Platform target for the PingAn asset workflows."""

    runtime_environment: str
    target_environment: Literal["dev", "stg", "prd"]
    base_url: str
    allowed_hosts: tuple[str, ...]
    app_id: str
    app_secret: str = field(repr=False)
    terminal_workflow_id: int
    datacenter_workflow_id: int
    user_workflow_id: int


def load_pingan_agent_platform_target(
    environ: Mapping[str, str],
) -> PingAnAgentPlatformTargetConfig:
    """Load the active Agent Platform target without exposing its credential."""

    runtime_environment = _required(environ, "SOC_PINGAN_ENV").lower()
    if runtime_environment not in {"dev", "stg", "prd"}:
        raise PingAnAgentPlatformTargetConfigurationError("SOC_PINGAN_ENV must be dev, stg, or prd")

    target_environment = _required(environ, "SOC_PINGAN_WORKFLOW_ENV").lower()
    if target_environment not in {"dev", "stg", "prd"}:
        raise PingAnAgentPlatformTargetConfigurationError("SOC_PINGAN_WORKFLOW_ENV must be dev, stg, or prd")

    base_url = _required(environ, "SOC_PINGAN_WORKFLOW_BASE_URL").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise PingAnAgentPlatformTargetConfigurationError("SOC_PINGAN_WORKFLOW_BASE_URL must be an HTTPS URL without credentials, query, or fragment")

    allowed_hosts = tuple(
        dict.fromkeys(
            value.strip().lower()
            for value in _required(
                environ,
                "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
            ).split(",")
            if value.strip()
        )
    )
    if not allowed_hosts or parsed.hostname.lower() not in allowed_hosts:
        raise PingAnAgentPlatformTargetConfigurationError("SOC_PINGAN_WORKFLOW_BASE_URL host must match SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS allowlist")

    if target_environment == "prd" and environ.get("SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION", "").strip() != PINGAN_AGENT_PLATFORM_PRD_CONFIRMATION:
        raise PingAnAgentPlatformTargetConfigurationError(f"Agent Platform PRD requires SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION={PINGAN_AGENT_PLATFORM_PRD_CONFIRMATION}")

    return PingAnAgentPlatformTargetConfig(
        runtime_environment=runtime_environment,
        target_environment=cast(
            Literal["dev", "stg", "prd"],
            target_environment,
        ),
        base_url=base_url,
        allowed_hosts=allowed_hosts,
        app_id=_required(environ, "SOC_PINGAN_WORKFLOW_APP_ID"),
        app_secret=_required(environ, "SOC_PINGAN_WORKFLOW_APP_SECRET"),
        terminal_workflow_id=_positive_integer(
            environ,
            "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
        ),
        datacenter_workflow_id=_positive_integer(
            environ,
            "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
        ),
        user_workflow_id=_positive_integer(
            environ,
            "SOC_PINGAN_WORKFLOW_USER_ID",
        ),
    )


def enforce_pingan_runtime_agent_platform_mapping(
    target: PingAnAgentPlatformTargetConfig,
) -> PingAnAgentPlatformTargetConfig:
    """Enforce the approved Host deployment-to-Agent-Platform mapping."""

    expected = PINGAN_RUNTIME_AGENT_PLATFORM_TARGET_ENVIRONMENTS.get(target.runtime_environment)
    if expected is None:
        raise PingAnAgentPlatformTargetConfigurationError("governed PingAn Host Runtime must be DEV or STG")
    if target.target_environment != expected:
        raise PingAnAgentPlatformTargetConfigurationError(f"PingAn Runtime {target.runtime_environment.upper()} must target Agent Platform {expected.upper()}, not Agent Platform {target.target_environment.upper()}")
    return target


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise PingAnAgentPlatformTargetConfigurationError(f"{name} is required")
    return value


def _positive_integer(environ: Mapping[str, str], name: str) -> int:
    value = _required(environ, name)
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PingAnAgentPlatformTargetConfigurationError(f"{name} must be a positive integer") from exc
    if parsed <= 0:
        raise PingAnAgentPlatformTargetConfigurationError(f"{name} must be a positive integer")
    return parsed


__all__ = [
    "PINGAN_AGENT_PLATFORM_PRD_CONFIRMATION",
    "PINGAN_RUNTIME_AGENT_PLATFORM_TARGET_ENVIRONMENTS",
    "PingAnAgentPlatformTargetConfig",
    "PingAnAgentPlatformTargetConfigurationError",
    "enforce_pingan_runtime_agent_platform_mapping",
    "load_pingan_agent_platform_target",
]
