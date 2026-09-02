"""Internal DEV preflight and direct smoke helpers for PingAn providers."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from time import monotonic
from typing import Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

from deerflow.config.app_config import AppConfig
from soc_agent.integrations.pingan.asset_location import (
    PingAnAssetLocationAttempt,
    PingAnAssetLocationQuery,
    PingAnAssetLocationResult,
    PingAnAssetLocatorService,
    PingAnAssetProviderConfigurationError,
    PingAnAssetProviderUnavailableError,
    build_pingan_asset_locator_from_env,
)
from soc_agent.integrations.pingan.zeus_target import (
    PingAnZeusTargetConfigurationError,
    load_pingan_zeus_target,
)


class PingAnDevPreflightStatus(StrEnum):
    PASSED = "passed"
    WARNING = "warning"
    FAILED = "failed"


class PingAnDevPreflightCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    check_id: str = Field(min_length=1)
    status: PingAnDevPreflightStatus
    detail: str = Field(min_length=1)


class PingAnDevPreflightReport(BaseModel):
    """Secret-free validation of the local internal integration profile."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_dev_preflight.v1"] = "soc.pingan_dev_preflight.v1"
    environment: str
    provider_mode: str
    model_profile: str
    ready: bool
    checks: list[PingAnDevPreflightCheck]
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PingAnAssetDirectSmokeReport(BaseModel):
    """One direct Provider invocation with bounded, credential-free metadata."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_asset_direct_smoke.v1"] = "soc.pingan_asset_direct_smoke.v1"
    outcome: Literal[
        "found",
        "not_found",
        "ambiguous",
        "authentication_failed",
        "timeout",
        "provider_unavailable",
        "invalid_response",
        "preflight_failed",
        "invalid_configuration",
    ]
    query_hash: str = Field(min_length=64, max_length=64)
    asset_type: str
    role: str
    duration_ms: int = Field(ge=0)
    preflight: PingAnDevPreflightReport
    result: dict[str, Any] | None = None
    attempts: list[PingAnAssetLocationAttempt] = Field(default_factory=list)
    error_type: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def run_pingan_dev_preflight(
    environ: Mapping[str, str] | None = None,
    *,
    config_loader: Callable[[str], AppConfig] | None = None,
    locator_builder: Callable[[Mapping[str, str]], PingAnAssetLocatorService] = build_pingan_asset_locator_from_env,
) -> PingAnDevPreflightReport:
    """Validate DEV-only configuration and transports without issuing a request."""

    env = dict(os.environ if environ is None else environ)
    checks: list[PingAnDevPreflightCheck] = []
    environment = env.get("SOC_PINGAN_ENV", "").strip().lower()
    provider_mode = env.get("SOC_PINGAN_ASSET_PROVIDER_MODE", "").strip().lower()
    model_profile = env.get("SOC_LLM_MODEL", "deepseek-v4-flash").strip()

    _append_boolean_check(
        checks,
        check_id="environment.non_production",
        passed=environment in {"dev", "stg"},
        passed_detail=(f"SOC_PINGAN_ENV explicitly selects {environment.upper()}." if environment in {"dev", "stg"} else "SOC_PINGAN_ENV selects a supported non-production profile."),
        failed_detail="SOC_PINGAN_ENV must explicitly equal dev or stg for D12-B.",
    )
    _append_boolean_check(
        checks,
        check_id="provider.internal_mode",
        passed=provider_mode == "internal",
        passed_detail="Asset provider mode is internal and cannot use fake transports.",
        failed_detail="SOC_PINGAN_ASSET_PROVIDER_MODE must equal internal.",
    )

    required_names = (
        "SOC_PINGAN_ZEUS_ENV",
        "SOC_PINGAN_ZEUS_BASE_URL",
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_APP_ID",
        "SOC_PINGAN_ZEUS_APP_KEY",
    )
    workflow_required_names = (
        "SOC_PINGAN_WORKFLOW_ENV",
        "SOC_PINGAN_WORKFLOW_BASE_URL",
        "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS",
        "SOC_PINGAN_WORKFLOW_APP_ID",
        "SOC_PINGAN_WORKFLOW_APP_SECRET",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_USER_ID",
    )
    workflow_enabled = _env_bool(env, "SOC_PINGAN_ASSET_WORKFLOW_ENABLED", True)
    names = (*required_names, *(workflow_required_names if workflow_enabled else ()))
    missing = [name for name in names if not env.get(name, "").strip()]
    _append_boolean_check(
        checks,
        check_id="provider.required_configuration",
        passed=not missing,
        passed_detail="All required PingAn Provider values are configured.",
        failed_detail=("Missing required environment keys: " + ", ".join(missing)) if missing else "Required Provider configuration is invalid.",
    )

    _validate_zeus_target(env, checks)
    if workflow_enabled:
        _validate_workflow_host(env, checks)
    _validate_deerflow_model_profile(
        env,
        checks,
        model_profile=model_profile,
        config_loader=config_loader,
    )

    if not missing and provider_mode == "internal":
        try:
            locator_builder(env)
        except Exception as exc:  # noqa: BLE001 - report only a sanitized class/category
            checks.append(
                PingAnDevPreflightCheck(
                    check_id="provider.imports_and_construction",
                    status=PingAnDevPreflightStatus.FAILED,
                    detail=_provider_construction_error_detail(exc, env),
                )
            )
        else:
            checks.append(
                PingAnDevPreflightCheck(
                    check_id="provider.imports_and_construction",
                    status=PingAnDevPreflightStatus.PASSED,
                    detail="Tracked ZEUS signer and Agent Platform HTTP client construct without network I/O.",
                )
            )

    ready = bool(checks) and all(item.status is not PingAnDevPreflightStatus.FAILED for item in checks)
    return PingAnDevPreflightReport(
        environment=environment,
        provider_mode=provider_mode,
        model_profile=model_profile,
        ready=ready,
        checks=checks,
    )


def run_pingan_asset_direct_smoke(
    query: PingAnAssetLocationQuery | Mapping[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
    preflight_runner: Callable[[Mapping[str, str]], PingAnDevPreflightReport] = run_pingan_dev_preflight,
    locator_builder: Callable[[Mapping[str, str]], PingAnAssetLocatorService] = build_pingan_asset_locator_from_env,
) -> PingAnAssetDirectSmokeReport:
    """Invoke the internal Provider once after the no-network preflight passes."""

    env = dict(os.environ if environ is None else environ)
    request = query if isinstance(query, PingAnAssetLocationQuery) else PingAnAssetLocationQuery.model_validate(query)
    query_hash = hashlib.sha256(request.query.encode("utf-8")).hexdigest()
    preflight = preflight_runner(env)
    if not preflight.ready:
        return PingAnAssetDirectSmokeReport(
            outcome="preflight_failed",
            query_hash=query_hash,
            asset_type=request.asset_type.value,
            role=request.role or "",
            duration_ms=0,
            preflight=preflight,
            error_type="PingAnDevPreflightError",
            error_message="PingAn DEV preflight failed; no external request was issued.",
        )

    started = monotonic()
    try:
        locator = locator_builder(env)
        result = locator.locate(request)
    except PingAnAssetProviderUnavailableError as exc:
        attempts = list(exc.attempts)
        return _failed_smoke_report(
            request=request,
            query_hash=query_hash,
            preflight=preflight,
            attempts=attempts,
            started=started,
            outcome=_classify_provider_failure(attempts),
            error_type=exc.__class__.__name__,
            error_message=str(exc),
        )
    except PingAnAssetProviderConfigurationError as exc:
        return _failed_smoke_report(
            request=request,
            query_hash=query_hash,
            preflight=preflight,
            attempts=[],
            started=started,
            outcome="invalid_configuration",
            error_type=exc.__class__.__name__,
            error_message="PingAn asset Provider configuration is invalid.",
        )
    except Exception as exc:  # noqa: BLE001 - direct smoke must preserve a bounded failure report
        return _failed_smoke_report(
            request=request,
            query_hash=query_hash,
            preflight=preflight,
            attempts=[],
            started=started,
            outcome="provider_unavailable",
            error_type=exc.__class__.__name__,
            error_message="PingAn asset Provider invocation failed.",
        )

    if result.mocked or result.provider_mode != "internal":
        return _failed_smoke_report(
            request=request,
            query_hash=query_hash,
            preflight=preflight,
            attempts=result.attempts,
            started=started,
            outcome="invalid_configuration",
            error_type="UnexpectedMockProvider",
            error_message="D12-B direct smoke received mocked or non-internal output.",
        )
    outcome: Literal["found", "not_found", "ambiguous"]
    if result.ambiguous:
        outcome = "ambiguous"
    elif result.found:
        outcome = "found"
    else:
        outcome = "not_found"
    return PingAnAssetDirectSmokeReport(
        outcome=outcome,
        query_hash=query_hash,
        asset_type=request.asset_type.value,
        role=request.role or "",
        duration_ms=_elapsed_ms(started),
        preflight=preflight,
        result=_bounded_result(result),
        attempts=result.attempts,
    )


def write_validation_report(report: BaseModel, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def _validate_zeus_target(
    env: Mapping[str, str],
    checks: list[PingAnDevPreflightCheck],
) -> None:
    try:
        target = load_pingan_zeus_target(env)
    except PingAnZeusTargetConfigurationError:
        target = None
    _append_boolean_check(
        checks,
        check_id="provider.zeus_target_guard",
        passed=target is not None,
        passed_detail=(
            f"Local {target.runtime_environment.upper()} targets ZEUS {target.target_environment.upper()} through HTTPS, an explicit host allowlist, and the required environment guard." if target is not None else "ZEUS target is valid."
        ),
        failed_detail=("ZEUS target must declare dev/stg/prd, use an allowlisted HTTPS host, and PRD additionally requires SOC_PINGAN_ZEUS_PRD_CONFIRMATION=CALL_PINGAN_ZEUS_PRD."),
    )


def _validate_workflow_host(
    env: Mapping[str, str],
    checks: list[PingAnDevPreflightCheck],
) -> None:
    parsed = urlparse(env.get("SOC_PINGAN_WORKFLOW_BASE_URL", ""))
    allowed_hosts = {value.strip().lower() for value in env.get("SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS", "").split(",") if value.strip()}
    target = env.get("SOC_PINGAN_WORKFLOW_ENV", "").strip().lower()
    host_allowed = parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname.lower() in allowed_hosts
    production_confirmed = target != "prd" or env.get("SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION", "").strip() == "CALL_PINGAN_PRD"
    passed = target in {"dev", "stg", "prd"} and host_allowed and production_confirmed
    _append_boolean_check(
        checks,
        check_id="provider.workflow_host_allowlist",
        passed=passed,
        passed_detail=f"Agent Platform {target.upper()} target uses HTTPS, an explicit host allowlist, and the required environment guard.",
        failed_detail=("Agent Platform target must be dev/stg/prd, use an allowlisted HTTPS host, and PRD additionally requires SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION=CALL_PINGAN_PRD."),
    )


def _validate_deerflow_model_profile(
    env: Mapping[str, str],
    checks: list[PingAnDevPreflightCheck],
    *,
    model_profile: str,
    config_loader: Callable[[str], AppConfig] | None,
) -> None:
    config_path = env.get("DEER_FLOW_CONFIG_PATH", "").strip()
    if not config_path or not Path(config_path).is_file():
        checks.append(
            PingAnDevPreflightCheck(
                check_id="model.deerflow_profile",
                status=PingAnDevPreflightStatus.FAILED,
                detail="DEER_FLOW_CONFIG_PATH must reference an existing local DEV profile.",
            )
        )
        return
    try:
        config = (config_loader or AppConfig.from_file)(config_path)
        model = config.get_model_config(model_profile)
        if model is None:
            raise ValueError("configured SOC model profile is absent")
        api_base = str(getattr(model, "api_base", "") or getattr(model, "base_url", ""))
        parsed = urlparse(api_base)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("D12-B model endpoint is not loopback")
        if not str(getattr(model, "api_key", "") or "").strip():
            raise ValueError("model proxy key is missing")
    except Exception as exc:  # noqa: BLE001 - never project config values into the report
        checks.append(
            PingAnDevPreflightCheck(
                check_id="model.deerflow_profile",
                status=PingAnDevPreflightStatus.FAILED,
                detail=f"DeerFlow model profile validation failed ({exc.__class__.__name__}).",
            )
        )
        return
    checks.append(
        PingAnDevPreflightCheck(
            check_id="model.deerflow_profile",
            status=PingAnDevPreflightStatus.PASSED,
            detail="DeerFlow resolves the selected SOC model through a configured loopback OpenAI-compatible endpoint.",
        )
    )


def _provider_construction_error_detail(
    exc: Exception,
    _env: Mapping[str, str],
) -> str:
    return f"Provider construction failed ({exc.__class__.__name__}); check the typed ZEUS and Agent Platform HTTP configuration."


def _env_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _append_boolean_check(
    checks: list[PingAnDevPreflightCheck],
    *,
    check_id: str,
    passed: bool,
    passed_detail: str,
    failed_detail: str,
) -> None:
    checks.append(
        PingAnDevPreflightCheck(
            check_id=check_id,
            status=PingAnDevPreflightStatus.PASSED if passed else PingAnDevPreflightStatus.FAILED,
            detail=passed_detail if passed else failed_detail,
        )
    )


def _classify_provider_failure(
    attempts: list[PingAnAssetLocationAttempt],
) -> Literal["authentication_failed", "timeout", "provider_unavailable", "invalid_response"]:
    messages = " ".join(item.error_message or "" for item in attempts).lower()
    error_types = {item.error_type or "" for item in attempts}
    if "http 401" in messages or "http 403" in messages:
        return "authentication_failed"
    if error_types & {"TimeoutException", "PingAnAgentWorkflowTimeoutError"} or "timed out" in messages:
        return "timeout"
    if error_types & {"JSONDecodeError", "ValidationError", "TypeError"}:
        return "invalid_response"
    return "provider_unavailable"


def _failed_smoke_report(
    *,
    request: PingAnAssetLocationQuery,
    query_hash: str,
    preflight: PingAnDevPreflightReport,
    attempts: list[PingAnAssetLocationAttempt],
    started: float,
    outcome: Literal[
        "authentication_failed",
        "timeout",
        "provider_unavailable",
        "invalid_response",
        "invalid_configuration",
    ],
    error_type: str,
    error_message: str,
) -> PingAnAssetDirectSmokeReport:
    return PingAnAssetDirectSmokeReport(
        outcome=outcome,
        query_hash=query_hash,
        asset_type=request.asset_type.value,
        role=request.role or "",
        duration_ms=_elapsed_ms(started),
        preflight=preflight,
        attempts=attempts,
        error_type=error_type,
        error_message=error_message[:500],
    )


def _bounded_result(result: PingAnAssetLocationResult) -> dict[str, Any]:
    payload = result.model_dump(mode="json")
    payload["query"] = "<omitted; see query_hash>"
    return payload


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


__all__ = [
    "PingAnAssetDirectSmokeReport",
    "PingAnDevPreflightCheck",
    "PingAnDevPreflightReport",
    "PingAnDevPreflightStatus",
    "run_pingan_asset_direct_smoke",
    "run_pingan_dev_preflight",
    "write_validation_report",
]
