"""Validated environment boundary shared by PingAn ZEUS providers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, cast
from urllib.parse import urlparse

PINGAN_ZEUS_PRD_CONFIRMATION = "CALL_PINGAN_ZEUS_PRD"
PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS: Mapping[str, str] = MappingProxyType(
    {
        "dev": "prd",
        "stg": "stg",
    }
)


class PingAnZeusTargetConfigurationError(ValueError):
    """Raised when a ZEUS provider target is incomplete or unsafe."""


@dataclass(frozen=True)
class PingAnZeusTargetConfig:
    """One explicit ZEUS target, independent from the local runtime environment."""

    runtime_environment: str
    target_environment: Literal["dev", "stg", "prd"]
    base_url: str
    allowed_hosts: tuple[str, ...]
    app_id: str
    app_key: str = field(repr=False)


def load_pingan_zeus_target(
    environ: Mapping[str, str],
) -> PingAnZeusTargetConfig:
    """Load and validate the shared target used by every internal ZEUS adapter."""

    runtime_environment = _required(environ, "SOC_PINGAN_ENV").lower()
    if runtime_environment not in {"dev", "stg", "prd"}:
        raise PingAnZeusTargetConfigurationError("SOC_PINGAN_ENV must be dev, stg, or prd")

    target_environment = _required(environ, "SOC_PINGAN_ZEUS_ENV").lower()
    if target_environment not in {"dev", "stg", "prd"}:
        raise PingAnZeusTargetConfigurationError("SOC_PINGAN_ZEUS_ENV must be dev, stg, or prd")

    base_url = _required(environ, "SOC_PINGAN_ZEUS_BASE_URL").rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise PingAnZeusTargetConfigurationError("SOC_PINGAN_ZEUS_BASE_URL must be an HTTPS URL without credentials, query, or fragment")

    allowed_hosts = tuple(
        dict.fromkeys(
            value.strip().lower()
            for value in _required(
                environ,
                "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
            ).split(",")
            if value.strip()
        )
    )
    if not allowed_hosts or parsed.hostname.lower() not in allowed_hosts:
        raise PingAnZeusTargetConfigurationError("SOC_PINGAN_ZEUS_BASE_URL host must match SOC_PINGAN_ZEUS_ALLOWED_HOSTS allowlist")

    if target_environment == "prd" and environ.get("SOC_PINGAN_ZEUS_PRD_CONFIRMATION", "").strip() != PINGAN_ZEUS_PRD_CONFIRMATION:
        raise PingAnZeusTargetConfigurationError(f"ZEUS PRD requires SOC_PINGAN_ZEUS_PRD_CONFIRMATION={PINGAN_ZEUS_PRD_CONFIRMATION}")

    return PingAnZeusTargetConfig(
        runtime_environment=runtime_environment,
        target_environment=cast(Literal["dev", "stg", "prd"], target_environment),
        base_url=base_url,
        allowed_hosts=allowed_hosts,
        app_id=_required(environ, "SOC_PINGAN_ZEUS_APP_ID"),
        app_key=_required(environ, "SOC_PINGAN_ZEUS_APP_KEY"),
    )


def enforce_pingan_runtime_zeus_mapping(
    target: PingAnZeusTargetConfig,
) -> PingAnZeusTargetConfig:
    """Enforce the operator-approved Host deployment-to-ZEUS target mapping."""

    expected = PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS.get(target.runtime_environment)
    if expected is None:
        raise PingAnZeusTargetConfigurationError("governed PingAn Host Runtime must be DEV or STG")
    if target.target_environment != expected:
        raise PingAnZeusTargetConfigurationError(f"PingAn Runtime {target.runtime_environment.upper()} must target ZEUS {expected.upper()}, not ZEUS {target.target_environment.upper()}")
    return target


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise PingAnZeusTargetConfigurationError(f"{name} is required")
    return value


__all__ = [
    "PINGAN_RUNTIME_ZEUS_TARGET_ENVIRONMENTS",
    "PINGAN_ZEUS_PRD_CONFIRMATION",
    "PingAnZeusTargetConfig",
    "PingAnZeusTargetConfigurationError",
    "enforce_pingan_runtime_zeus_mapping",
    "load_pingan_zeus_target",
]
