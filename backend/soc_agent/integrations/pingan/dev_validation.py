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
    """Validate DEV-only configuration and imports without issuing a request."""

    env = dict(os.environ if environ is None else environ)
    checks: list[PingAnDevPreflightCheck] = []
    environment = env.get("SOC_PINGAN_ENV", "").strip().lower()
    provider_mode = env.get("SOC_PINGAN_ASSET_PROVIDER_MODE", "").strip().lower()
    model_profile = env.get("SOC_LLM_MODEL", "deepseek-v4-flash").strip()

    _append_boolean_check(
        checks,
        check_id="environment.dev_explicit",
        passed=environment == "dev",
        passed_detail="SOC_PINGAN_ENV explicitly selects DEV.",
        failed_detail="SOC_PINGAN_ENV must explicitly equal dev for D12-B.",
    )
    _append_boolean_check(
        checks,
        check_id="environment.legacy_profile_local",
        passed=env.get("env_profile", "").strip().upper() == "LOCAL",
        passed_detail="Legacy modules explicitly select their LOCAL profile.",
        failed_detail="env_profile must explicitly equal LOCAL for the reviewed DEV stack.",
    )
    _append_boolean_check(
        checks,
        check_id="provider.internal_mode",
        passed=provider_mode == "internal",
        passed_detail="Asset provider mode is internal and cannot use fake transports.",
        failed_detail="SOC_PINGAN_ASSET_PROVIDER_MODE must equal internal.",
    )

    required_names = (
        "SOC_PINGAN_ZEUS_BASE_URL",
        "SOC_PINGAN_ZEUS_APP_ID",
        "SOC_PINGAN_ZEUS_APP_KEY",
        "SOC_PINGAN_ZEUS_SIGNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_RUNNER_IMPORT",
        "SOC_PINGAN_WORKFLOW_APP_ID",
        "SOC_PINGAN_WORKFLOW_OPERATOR",
        "SOC_PINGAN_WORKFLOW_TERMINAL_ID",
        "SOC_PINGAN_WORKFLOW_DATACENTER_ID",
        "SOC_PINGAN_WORKFLOW_USER_ID",
    )
    missing = [name for name in required_names if not env.get(name, "").strip()]
    _append_boolean_check(
        checks,
        check_id="provider.required_configuration",
        passed=not missing,
        passed_detail="All required PingAn Provider values are configured.",
        failed_detail=("Missing required environment keys: " + ", ".join(missing)) if missing else "Required Provider configuration is invalid.",
    )

    _validate_zeus_host(env, checks)
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
                    detail="Signer and workflow callables resolve and the internal Provider constructs without network I/O.",
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


def _validate_zeus_host(
    env: Mapping[str, str],
    checks: list[PingAnDevPreflightCheck],
) -> None:
    parsed = urlparse(env.get("SOC_PINGAN_ZEUS_BASE_URL", ""))
    allowed_hosts = {value.strip().lower() for value in env.get("SOC_PINGAN_ZEUS_ALLOWED_HOSTS", "").split(",") if value.strip()}
    passed = parsed.scheme == "https" and bool(parsed.hostname) and parsed.hostname.lower() in allowed_hosts
    _append_boolean_check(
        checks,
        check_id="provider.dev_host_allowlist",
        passed=passed,
        passed_detail="ZEUS uses HTTPS and its host is explicitly allowlisted for this DEV profile.",
        failed_detail="ZEUS DEV URL must use HTTPS and match SOC_PINGAN_ZEUS_ALLOWED_HOSTS.",
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
    env: Mapping[str, str],
) -> str:
    message = str(exc)
    signer_import = env.get("SOC_PINGAN_ZEUS_SIGNER_IMPORT", "")
    workflow_import = env.get("SOC_PINGAN_WORKFLOW_RUNNER_IMPORT", "")
    if signer_import and repr(signer_import) in message:
        return "Configured ZEUS signer callable cannot be imported; check the tracked signer module and backend import root."
    if workflow_import and repr(workflow_import) in message:
        return "Configured internal workflow runner cannot be imported; add the legacy Agent Platform package to the DEV environment."
    return f"Provider construction failed ({exc.__class__.__name__}); check configured import roots and internal dependencies."


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
    if "TimeoutException" in error_types or "timed out" in messages:
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
