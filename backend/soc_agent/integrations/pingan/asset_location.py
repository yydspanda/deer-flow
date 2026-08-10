"""PingAn asset ownership provider behind the generic ``asset.locate`` route.

The implementation preserves the useful behavior of the legacy ZEUS locator:
``searchAssetInfo`` first, asset-to-BU workflows second, and an optional UM
workflow last. Asset extraction, role reconstruction, disposition target
selection, and verdict changes deliberately stay outside this provider.
"""

from __future__ import annotations

import ipaddress
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from time import monotonic
from typing import Any, Literal, Protocol
from urllib.parse import urljoin, urlparse
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.integrations.pingan.agent_workflow import (
    HttpPingAnAgentWorkflowPort,
    PingAnAgentWorkflowHttpConfig,
)
from soc_agent.integrations.pingan.zeus_signing import isec_sign

PINGAN_LEGACY_WORKFLOW_APP_ID = "YHSYS"
PINGAN_LEGACY_WORKFLOW_OPERATOR = "WANGWENBIN520"


class PingAnAssetProviderError(RuntimeError):
    """Base error for the PingAn asset provider boundary."""


class PingAnAssetProviderConfigurationError(PingAnAssetProviderError, ValueError):
    """Raised when internal provider configuration is incomplete or invalid."""


class PingAnAssetProviderUnavailableError(PingAnAssetProviderError):
    """Raised when every configured external lookup failed rather than missed."""

    def __init__(
        self,
        message: str,
        *,
        attempts: Sequence[PingAnAssetLocationAttempt] = (),
    ) -> None:
        super().__init__(message)
        self.attempts = tuple(attempts)


class PingAnAssetType(StrEnum):
    IP = "IP"
    DOMAIN = "DOMAIN"
    WEB = "WEB"
    HOST = "HOST"
    USER = "USER"


class PingAnAssetLocationQuery(BaseModel):
    """One already-extracted asset candidate submitted to the provider."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2048)
    asset_type: PingAnAssetType | None = None
    role: str | None = Field(default=None, max_length=64)
    um: str | None = Field(default=None, max_length=256)

    @field_validator("query", "role", "um", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("asset_type", mode="before")
    @classmethod
    def _normalize_asset_type(cls, value: Any) -> Any:
        if value is None:
            return None
        normalized = str(value).strip().upper()
        aliases = {
            "HOSTNAME": "HOST",
            "URL": "WEB",
            "UM": "USER",
            "ACCOUNT": "USER",
        }
        return aliases.get(normalized, normalized)

    @model_validator(mode="after")
    def _infer_asset_type(self) -> PingAnAssetLocationQuery:
        if self.asset_type is None:
            self.asset_type = infer_pingan_asset_type(self.query)
        if self.asset_type is PingAnAssetType.USER and not self.um:
            self.um = self.query
        return self


class PingAnAssetLocationCandidate(BaseModel):
    """Bounded ownership candidate projected from one provider response."""

    model_config = ConfigDict(extra="forbid")

    company_code: str = ""
    company_name: str = ""
    biz_group: str = ""
    subsystem_code: str = ""
    source: Literal["zeus_search_asset_info", "agent_asset_to_bu", "agent_locate_user"]
    matched_asset_type: PingAnAssetType | None = None

    @field_validator("company_code", "company_name", "biz_group", "subsystem_code", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: Any) -> str:
        return str(value).strip() if value is not None else ""

    @model_validator(mode="after")
    def _require_ownership_value(self) -> PingAnAssetLocationCandidate:
        if not any((self.company_code, self.company_name, self.biz_group)):
            raise ValueError("asset location candidate requires ownership information")
        return self


class PingAnAssetLocationAttempt(BaseModel):
    """Sanitized trace of one external query attempt."""

    model_config = ConfigDict(extra="forbid")

    stage: Literal["search_asset_info", "asset_to_bu", "um"]
    lookup_kind: str
    status: Literal["found", "not_found", "failed"]
    candidate_count: int = Field(default=0, ge=0)
    response_code: int | str | None = None
    duration_ms: int = Field(default=0, ge=0)
    mocked: bool
    error_type: str | None = None
    error_message: str | None = None


class PingAnAssetLocationResult(BaseModel):
    """Typed result returned by the PingAn ``asset.locate`` MCP tool."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.pingan_asset_location_result.v1"
    query: str
    asset_type: PingAnAssetType
    role: str = ""
    found: bool
    resolved: bool
    ambiguous: bool
    company_code: str = ""
    company_name: str = ""
    biz_group: str = ""
    source: str = ""
    candidates: list[PingAnAssetLocationCandidate] = Field(default_factory=list)
    attempts: list[PingAnAssetLocationAttempt] = Field(default_factory=list)
    mocked: bool
    provider_mode: Literal["fake", "internal"]
    evidence_boundary: Literal["investigation_only"] = "investigation_only"
    decision_impact: Literal["none"] = "none"
    raw_response_included: Literal[False] = False

    @model_validator(mode="after")
    def _validate_resolution(self) -> PingAnAssetLocationResult:
        if self.resolved and (not self.found or self.ambiguous):
            raise ValueError("resolved asset location must be found and unambiguous")
        if self.ambiguous and len(self.candidates) < 2:
            raise ValueError("ambiguous asset location requires multiple candidates")
        if not self.found and self.candidates:
            raise ValueError("not-found asset location cannot contain candidates")
        return self


class PingAnAssetWorkflowConfig(BaseModel):
    """Tenant-owned identifiers for the legacy asset-to-BU workflow family."""

    model_config = ConfigDict(extra="forbid")

    app_id: str = Field(min_length=1)
    terminal_workflow_id: int = Field(gt=0)
    datacenter_workflow_id: int = Field(gt=0)
    user_workflow_id: int = Field(gt=0)


class PingAnAssetOwnershipOverride(BaseModel):
    """Tenant-owned correction for a reviewed legacy ownership alias."""

    model_config = ConfigDict(extra="forbid")

    match_biz_group: str = Field(min_length=1)
    company_code: str = ""
    company_name: str = ""
    biz_group: str | None = None

    @field_validator("match_biz_group", "company_code", "company_name", "biz_group", mode="before")
    @classmethod
    def _normalize_text(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _require_override_value(self) -> PingAnAssetOwnershipOverride:
        if not any((self.company_code, self.company_name, self.biz_group)):
            raise ValueError("ownership override requires at least one replacement value")
        return self


class PingAnAssetSearchPort(Protocol):
    """Port for the signed ZEUS ``searchAssetInfo`` request."""

    mocked: bool

    def search(self, *, keyword: str, asset_types: Sequence[PingAnAssetType]) -> Mapping[str, Any]: ...


class PingAnAssetWorkflowPort(Protocol):
    """Port for the internal Agent Platform ``run_workflow`` call."""

    mocked: bool

    def run(self, *, app_id: str, workflow_id: int, query_data: Mapping[str, Any]) -> Any: ...


class HttpPingAnZeusAssetSearchPort:
    """Signed HTTP transport matching the legacy ZEUS search request contract."""

    mocked = False

    def __init__(
        self,
        *,
        base_url: str,
        allowed_hosts: Sequence[str],
        app_id: str,
        app_key: str,
        signer: Callable[..., Mapping[str, Any]],
        timeout_seconds: float = 10.0,
        endpoint_path: str = "/public/searchAssetInfo",
        company_code_header: str = "all",
        query_type: int = 1,
        page_size: int = 10,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url.strip() or not app_id.strip() or not app_key:
            raise PingAnAssetProviderConfigurationError("ZEUS base URL, app ID, and app key are required")
        if timeout_seconds <= 0 or page_size <= 0:
            raise PingAnAssetProviderConfigurationError("ZEUS timeout and page size must be positive")
        normalized_hosts = {item.strip().lower() for item in allowed_hosts if item.strip()}
        parsed_base = urlparse(base_url)
        if parsed_base.scheme != "https" or not parsed_base.hostname or parsed_base.hostname.lower() not in normalized_hosts or parsed_base.username or parsed_base.password or parsed_base.query or parsed_base.fragment:
            raise PingAnAssetProviderConfigurationError("ZEUS base URL must use an explicitly allowlisted HTTPS host")
        self._base_url = base_url.rstrip("/") + "/"
        self._allowed_hosts = normalized_hosts
        self._app_id = app_id
        self._app_key = app_key
        self._signer = signer
        self._timeout_seconds = timeout_seconds
        self._endpoint_path = endpoint_path.lstrip("/")
        self._company_code_header = company_code_header
        self._query_type = query_type
        self._page_size = page_size
        self._client = client

    def search(self, *, keyword: str, asset_types: Sequence[PingAnAssetType]) -> Mapping[str, Any]:
        request_body = {
            "assetTypeList": [item.value for item in asset_types],
            "param": {"keyword": keyword, "queryType": self._query_type},
            "pageNum": 1,
            "pageSize": self._page_size,
        }
        headers = dict(
            self._signer(
                data=request_body,
                app_id=self._app_id,
                app_key=self._app_key,
            )
        )
        headers["companyCode"] = self._company_code_header
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            url = urljoin(self._base_url, self._endpoint_path)
            parsed_url = urlparse(url)
            if parsed_url.scheme != "https" or (parsed_url.hostname or "").lower() not in self._allowed_hosts:
                raise PingAnAssetProviderConfigurationError("resolved ZEUS asset URL left the configured host allowlist")
            response = client.post(
                url,
                json=request_body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            result = response.json()
        finally:
            if owns_client:
                client.close()
        if not isinstance(result, Mapping):
            raise PingAnAssetProviderError("ZEUS searchAssetInfo returned a non-object JSON response")
        return result


class StaticPingAnAssetSearchPort:
    """Deterministic fake search transport for external-network tests."""

    mocked = True

    def __init__(self, responses: Mapping[tuple[str, tuple[str, ...]], Any]) -> None:
        self._responses = dict(responses)
        self.calls: list[dict[str, Any]] = []

    def search(self, *, keyword: str, asset_types: Sequence[PingAnAssetType]) -> Mapping[str, Any]:
        key = (keyword, tuple(item.value for item in asset_types))
        self.calls.append({"keyword": keyword, "asset_types": list(key[1])})
        response = self._responses.get(key, {"code": 200, "data": []})
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, Mapping):
            raise PingAnAssetProviderError("fake search response must be an object")
        return response


class StaticPingAnAssetWorkflowPort:
    """Deterministic fake workflow transport for external-network tests."""

    mocked = True

    def __init__(self, responses: Mapping[tuple[int, str], Any]) -> None:
        self._responses = dict(responses)
        self.calls: list[dict[str, Any]] = []

    def run(self, *, app_id: str, workflow_id: int, query_data: Mapping[str, Any]) -> Any:
        args = query_data.get("args")
        values = list(args.values()) if isinstance(args, Mapping) else []
        query = str(values[0]) if values else ""
        self.calls.append(
            {
                "app_id": app_id,
                "workflow_id": workflow_id,
                "query": query,
                "query_data": dict(query_data),
            }
        )
        response = self._responses.get((workflow_id, query))
        if isinstance(response, Exception):
            raise response
        return response


class PingAnAssetLocatorService:
    """Execute the legacy lookup order without owning extraction or decisions."""

    def __init__(
        self,
        *,
        search_port: PingAnAssetSearchPort,
        workflow_port: PingAnAssetWorkflowPort | None,
        workflow_config: PingAnAssetWorkflowConfig | None,
        provider_mode: Literal["fake", "internal"],
        ownership_overrides: Sequence[PingAnAssetOwnershipOverride] = (),
    ) -> None:
        if (workflow_port is None) is not (workflow_config is None):
            raise PingAnAssetProviderConfigurationError("workflow port and workflow config must be provided together")
        port_mock_flags = [search_port.mocked]
        if workflow_port is not None:
            port_mock_flags.append(workflow_port.mocked)
        if provider_mode == "internal" and any(port_mock_flags):
            raise PingAnAssetProviderConfigurationError("internal provider mode cannot use mocked transports")
        if provider_mode == "fake" and not all(port_mock_flags):
            raise PingAnAssetProviderConfigurationError("fake provider mode cannot use internal transports")
        self._search = search_port
        self._workflow = workflow_port
        self._workflow_config = workflow_config
        self._provider_mode = provider_mode
        self._ownership_overrides = tuple(ownership_overrides)

    def locate(self, query: PingAnAssetLocationQuery | Mapping[str, Any]) -> PingAnAssetLocationResult:
        request = query if isinstance(query, PingAnAssetLocationQuery) else PingAnAssetLocationQuery.model_validate(query)
        attempts: list[PingAnAssetLocationAttempt] = []

        if request.asset_type is not PingAnAssetType.USER:
            for search_type in _search_type_plan(request.asset_type):
                candidates = self._try_search(request.query, search_type, attempts)
                if candidates:
                    return self._result(request, candidates=candidates, attempts=attempts)
                self._raise_if_latest_attempt_failed(attempts)

            for workflow_kind in _workflow_plan(request.asset_type):
                candidates = self._try_workflow(
                    stage="asset_to_bu",
                    lookup_kind=workflow_kind,
                    query=request.query,
                    asset_type=request.asset_type,
                    attempts=attempts,
                )
                if candidates:
                    return self._result(request, candidates=candidates, attempts=attempts)
                self._raise_if_latest_attempt_failed(attempts)

        um = request.um or (request.query if request.asset_type is PingAnAssetType.USER else None)
        if um:
            candidates = self._try_workflow(
                stage="um",
                lookup_kind="user",
                query=um,
                asset_type=PingAnAssetType.USER,
                attempts=attempts,
            )
            if candidates:
                return self._result(request, candidates=candidates, attempts=attempts)
            self._raise_if_latest_attempt_failed(attempts)

        if not attempts:
            raise PingAnAssetProviderUnavailableError(f"no PingAn asset location provider is configured for asset type {request.asset_type}")
        return self._result(request, candidates=[], attempts=attempts)

    @staticmethod
    def _raise_if_latest_attempt_failed(
        attempts: Sequence[PingAnAssetLocationAttempt],
    ) -> None:
        if attempts and attempts[-1].status == "failed":
            latest = attempts[-1]
            raise PingAnAssetProviderUnavailableError(
                f"PingAn asset location provider failed at {latest.stage}",
                attempts=attempts,
            )

    def _try_search(
        self,
        query: str,
        asset_type: PingAnAssetType,
        attempts: list[PingAnAssetLocationAttempt],
    ) -> list[PingAnAssetLocationCandidate]:
        started = monotonic()
        try:
            response = self._search.search(keyword=query, asset_types=[asset_type])
            response_code = _response_code(response)
            succeeded = str(response_code) == "200"
            candidates = _parse_search_candidates(response, fallback_asset_type=asset_type) if succeeded else []
            candidates = _apply_ownership_overrides(candidates, self._ownership_overrides)
            status: Literal["found", "not_found", "failed"] = "found" if candidates else ("not_found" if succeeded else "failed")
            attempts.append(
                PingAnAssetLocationAttempt(
                    stage="search_asset_info",
                    lookup_kind=asset_type.value,
                    status=status,
                    candidate_count=len(candidates),
                    response_code=response_code,
                    duration_ms=_elapsed_ms(started),
                    mocked=self._search.mocked,
                )
            )
            return candidates
        except Exception as exc:  # noqa: BLE001 - provider failures become sanitized attempt metadata
            attempts.append(
                PingAnAssetLocationAttempt(
                    stage="search_asset_info",
                    lookup_kind=asset_type.value,
                    status="failed",
                    duration_ms=_elapsed_ms(started),
                    mocked=self._search.mocked,
                    error_type=exc.__class__.__name__,
                    error_message=_sanitized_error_message(exc),
                )
            )
            return []

    def _try_workflow(
        self,
        *,
        stage: Literal["asset_to_bu", "um"],
        lookup_kind: Literal["terminal", "datacenter", "user"],
        query: str,
        asset_type: PingAnAssetType,
        attempts: list[PingAnAssetLocationAttempt],
    ) -> list[PingAnAssetLocationCandidate]:
        if self._workflow is None or self._workflow_config is None:
            return []
        started = monotonic()
        workflow_id = {
            "terminal": self._workflow_config.terminal_workflow_id,
            "datacenter": self._workflow_config.datacenter_workflow_id,
            "user": self._workflow_config.user_workflow_id,
        }[lookup_kind]
        try:
            raw = self._workflow.run(
                app_id=self._workflow_config.app_id,
                workflow_id=workflow_id,
                query_data=_workflow_query_data(
                    lookup_kind=lookup_kind,
                    query=query,
                    asset_type=asset_type,
                ),
            )
            candidates = _parse_workflow_candidates(
                raw,
                source="agent_locate_user" if stage == "um" else "agent_asset_to_bu",
                asset_type=asset_type,
            )
            attempts.append(
                PingAnAssetLocationAttempt(
                    stage=stage,
                    lookup_kind=lookup_kind,
                    status="found" if candidates else "not_found",
                    candidate_count=len(candidates),
                    duration_ms=_elapsed_ms(started),
                    mocked=self._workflow.mocked,
                )
            )
            return candidates
        except Exception as exc:  # noqa: BLE001 - provider failures become sanitized attempt metadata
            attempts.append(
                PingAnAssetLocationAttempt(
                    stage=stage,
                    lookup_kind=lookup_kind,
                    status="failed",
                    duration_ms=_elapsed_ms(started),
                    mocked=self._workflow.mocked,
                    error_type=exc.__class__.__name__,
                    error_message=_sanitized_error_message(exc),
                )
            )
            return []

    def _result(
        self,
        request: PingAnAssetLocationQuery,
        *,
        candidates: Sequence[PingAnAssetLocationCandidate],
        attempts: Sequence[PingAnAssetLocationAttempt],
    ) -> PingAnAssetLocationResult:
        unique = _dedupe_candidates(candidates)
        ownership_keys = {_ownership_key(item) for item in unique}
        resolved = len(ownership_keys) == 1 and bool(unique)
        ambiguous = len(ownership_keys) > 1
        selected = unique[0] if resolved else None
        mocked = self._provider_mode == "fake" or any(item.mocked for item in attempts)
        return PingAnAssetLocationResult(
            query=request.query,
            asset_type=request.asset_type or infer_pingan_asset_type(request.query),
            role=request.role or "",
            found=bool(unique),
            resolved=resolved,
            ambiguous=ambiguous,
            company_code=selected.company_code if selected else "",
            company_name=selected.company_name if selected else "",
            biz_group=selected.biz_group if selected else "",
            source=selected.source if selected else "",
            candidates=unique,
            attempts=list(attempts),
            mocked=mocked,
            provider_mode=self._provider_mode,
        )


def build_pingan_asset_locator_from_env(
    environ: Mapping[str, str] | None = None,
) -> PingAnAssetLocatorService:
    """Build a fake or internal provider without placing secrets in files."""

    env = dict(os.environ if environ is None else environ)
    mode = env.get("SOC_PINGAN_ASSET_PROVIDER_MODE", "fake").strip().lower()
    if mode == "fake":
        return _build_fake_locator()
    if mode != "internal":
        raise PingAnAssetProviderConfigurationError("SOC_PINGAN_ASSET_PROVIDER_MODE must be fake or internal")

    search = HttpPingAnZeusAssetSearchPort(
        base_url=_require_env(env, "SOC_PINGAN_ZEUS_BASE_URL"),
        allowed_hosts=_require_env(env, "SOC_PINGAN_ZEUS_ALLOWED_HOSTS").split(","),
        app_id=_require_env(env, "SOC_PINGAN_ZEUS_APP_ID"),
        app_key=_require_env(env, "SOC_PINGAN_ZEUS_APP_KEY"),
        signer=isec_sign,
        timeout_seconds=_float_env(env, "SOC_PINGAN_ZEUS_TIMEOUT_SECONDS", 10.0),
        endpoint_path=env.get("SOC_PINGAN_ZEUS_ASSET_PATH", "/public/searchAssetInfo"),
        company_code_header=env.get("SOC_PINGAN_ZEUS_COMPANY_CODE", "all"),
        query_type=_int_env(env, "SOC_PINGAN_ZEUS_QUERY_TYPE", 1),
        page_size=_int_env(env, "SOC_PINGAN_ZEUS_PAGE_SIZE", 10),
    )

    workflow: PingAnAssetWorkflowPort | None = None
    workflow_config: PingAnAssetWorkflowConfig | None = None
    if _bool_env(env, "SOC_PINGAN_ASSET_WORKFLOW_ENABLED", True):
        app_id = _require_env(env, "SOC_PINGAN_WORKFLOW_APP_ID")
        try:
            workflow = HttpPingAnAgentWorkflowPort(
                PingAnAgentWorkflowHttpConfig(
                    environment=_workflow_environment(env),
                    base_url=_require_env(env, "SOC_PINGAN_WORKFLOW_BASE_URL"),
                    allowed_hosts=_require_env(env, "SOC_PINGAN_WORKFLOW_ALLOWED_HOSTS"),
                    app_id=app_id,
                    app_secret=_require_env(env, "SOC_PINGAN_WORKFLOW_APP_SECRET"),
                    allow_prd=env.get("SOC_PINGAN_WORKFLOW_PRD_CONFIRMATION", "").strip() == "CALL_PINGAN_PRD",
                    auth_path=env.get("SOC_PINGAN_WORKFLOW_AUTH_PATH", "/appid/auth/login"),
                    request_timeout_seconds=_float_env(env, "SOC_PINGAN_WORKFLOW_REQUEST_TIMEOUT_SECONDS", 15.0),
                    workflow_timeout_seconds=_float_env(env, "SOC_PINGAN_WORKFLOW_TIMEOUT_SECONDS", 600.0),
                    poll_interval_seconds=_float_env(env, "SOC_PINGAN_WORKFLOW_POLL_INTERVAL_SECONDS", 2.0),
                    token_ttl_seconds=_float_env(env, "SOC_PINGAN_WORKFLOW_TOKEN_TTL_SECONDS", 3600.0),
                    max_request_bytes=_int_env(env, "SOC_PINGAN_WORKFLOW_MAX_REQUEST_BYTES", 1_000_000),
                    max_response_bytes=_int_env(env, "SOC_PINGAN_WORKFLOW_MAX_RESPONSE_BYTES", 2_000_000),
                )
            )
        except ValueError as exc:
            raise PingAnAssetProviderConfigurationError("PingAn Agent Platform workflow configuration is invalid") from exc
        workflow_config = PingAnAssetWorkflowConfig(
            app_id=app_id,
            terminal_workflow_id=_int_env_required(env, "SOC_PINGAN_WORKFLOW_TERMINAL_ID"),
            datacenter_workflow_id=_int_env_required(env, "SOC_PINGAN_WORKFLOW_DATACENTER_ID"),
            user_workflow_id=_int_env_required(env, "SOC_PINGAN_WORKFLOW_USER_ID"),
        )
    return PingAnAssetLocatorService(
        search_port=search,
        workflow_port=workflow,
        workflow_config=workflow_config,
        provider_mode="internal",
        ownership_overrides=_ownership_overrides_from_env(env),
    )


def infer_pingan_asset_type(value: str) -> PingAnAssetType:
    normalized = value.strip()
    try:
        ipaddress.ip_address(normalized)
    except ValueError:
        pass
    else:
        return PingAnAssetType.IP
    if normalized.startswith(("http://", "https://")):
        return PingAnAssetType.WEB
    if re.fullmatch(r"(?i)(?:ex-)?[a-z][a-z0-9._-]{2,}", normalized) and normalized.upper().startswith("UM"):
        return PingAnAssetType.USER
    if "." in normalized:
        return PingAnAssetType.DOMAIN
    return PingAnAssetType.HOST


def _build_fake_locator() -> PingAnAssetLocatorService:
    search = StaticPingAnAssetSearchPort(
        {
            ("10.10.1.5", ("IP",)): {
                "code": 200,
                "data": [
                    {
                        "type": "IP",
                        "data": [
                            {
                                "companyCode": "PA011",
                                "companyName": "PingAn Technology",
                                "bizGroup": "Payment Engineering",
                            }
                        ],
                    }
                ],
            }
        }
    )
    workflow = StaticPingAnAssetWorkflowPort(
        {
            (1087787, "203.0.113.10"): {
                "company_code": "PA009",
                "company_name": "Internet Edge Test",
            },
            (1092332, "UM001"): {
                "company_code": "PA011",
                "company_name": "Endpoint User Group",
            },
        }
    )
    return PingAnAssetLocatorService(
        search_port=search,
        workflow_port=workflow,
        workflow_config=PingAnAssetWorkflowConfig(
            app_id="FAKE-YHSYS",
            terminal_workflow_id=1087710,
            datacenter_workflow_id=1087787,
            user_workflow_id=1092332,
        ),
        provider_mode="fake",
    )


def _search_type_plan(asset_type: PingAnAssetType) -> list[PingAnAssetType]:
    if asset_type is PingAnAssetType.DOMAIN:
        return [PingAnAssetType.DOMAIN, PingAnAssetType.WEB]
    if asset_type is PingAnAssetType.WEB:
        return [PingAnAssetType.WEB, PingAnAssetType.DOMAIN]
    return [asset_type]


def _workflow_plan(asset_type: PingAnAssetType) -> list[Literal["terminal", "datacenter", "user"]]:
    if asset_type in {PingAnAssetType.IP, PingAnAssetType.HOST}:
        return ["datacenter", "terminal"]
    if asset_type is PingAnAssetType.DOMAIN:
        return ["datacenter"]
    return []


def _response_code(response: Mapping[str, Any]) -> int | str | None:
    return response.get("code")


def _parse_search_candidates(
    response: Mapping[str, Any],
    *,
    fallback_asset_type: PingAnAssetType,
) -> list[PingAnAssetLocationCandidate]:
    groups = response.get("data")
    if not isinstance(groups, list):
        return []
    candidates: list[PingAnAssetLocationCandidate] = []
    for group in groups[:20]:
        if not isinstance(group, Mapping):
            continue
        matched_type = _coerce_asset_type(group.get("type"), fallback=fallback_asset_type)
        items = group.get("data")
        if not isinstance(items, list):
            continue
        for item in items[:20]:
            if not isinstance(item, Mapping):
                continue
            candidate_data = {
                "company_code": item.get("companyCode") or item.get("company_code"),
                "company_name": item.get("companyName") or item.get("company_name"),
                "biz_group": item.get("bizGroup") or item.get("biz_group"),
                "subsystem_code": item.get("subsystemCode") or item.get("subsystem_code"),
                "source": "zeus_search_asset_info",
                "matched_asset_type": matched_type,
            }
            try:
                candidates.append(PingAnAssetLocationCandidate.model_validate(candidate_data))
            except ValueError:
                continue
    return _dedupe_candidates(candidates)


def _parse_workflow_candidates(
    raw: Any,
    *,
    source: Literal["agent_asset_to_bu", "agent_locate_user"],
    asset_type: PingAnAssetType,
) -> list[PingAnAssetLocationCandidate]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, Mapping):
        return []
    candidate_data = {
        "company_code": raw.get("company_code") or raw.get("companyCode"),
        "company_name": raw.get("company_name") or raw.get("companyName"),
        "biz_group": raw.get("biz_group") or raw.get("bizGroup"),
        "subsystem_code": raw.get("subsystem_code") or raw.get("subsystemCode"),
        "source": source,
        "matched_asset_type": asset_type,
    }
    try:
        return [PingAnAssetLocationCandidate.model_validate(candidate_data)]
    except ValueError:
        return []


def _workflow_query_data(
    *,
    lookup_kind: Literal["terminal", "datacenter", "user"],
    query: str,
    asset_type: PingAnAssetType,
) -> dict[str, Any]:
    if lookup_kind == "user":
        args = {"ums": query}
    elif asset_type is PingAnAssetType.DOMAIN:
        args = {"domain": query}
    elif asset_type is PingAnAssetType.HOST:
        args = {"host": query}
    else:
        args = {"ip": query}
    text = "，".join(f"{key}={value}" for key, value in args.items())
    return {
        "message": {
            "message_id": uuid4().hex,
            "by": PINGAN_LEGACY_WORKFLOW_OPERATOR,
            "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "content": json.dumps([{"type": "text", "text": text}], ensure_ascii=False),
            "content_type": "object_array",
        },
        "args": args,
    }


def _ownership_key(candidate: PingAnAssetLocationCandidate) -> tuple[str, str, str]:
    return (
        candidate.company_code.casefold(),
        candidate.biz_group.casefold(),
        candidate.company_name.casefold(),
    )


def _apply_ownership_overrides(
    candidates: Sequence[PingAnAssetLocationCandidate],
    overrides: Sequence[PingAnAssetOwnershipOverride],
) -> list[PingAnAssetLocationCandidate]:
    result: list[PingAnAssetLocationCandidate] = []
    for candidate in candidates:
        override = next(
            (item for item in overrides if item.match_biz_group.casefold() == candidate.biz_group.casefold()),
            None,
        )
        if override is None:
            result.append(candidate)
            continue
        result.append(
            candidate.model_copy(
                update={
                    "company_code": override.company_code or candidate.company_code,
                    "company_name": override.company_name or candidate.company_name,
                    "biz_group": override.biz_group or candidate.biz_group,
                }
            )
        )
    return _dedupe_candidates(result)


def _dedupe_candidates(
    candidates: Sequence[PingAnAssetLocationCandidate],
) -> list[PingAnAssetLocationCandidate]:
    result: list[PingAnAssetLocationCandidate] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for candidate in candidates:
        key = (
            *_ownership_key(candidate),
            candidate.source,
            candidate.matched_asset_type.value if candidate.matched_asset_type else "",
        )
        if key not in seen:
            seen.add(key)
            result.append(candidate)
    return result


def _coerce_asset_type(value: Any, *, fallback: PingAnAssetType) -> PingAnAssetType:
    if isinstance(value, str):
        try:
            return PingAnAssetType(value.strip().upper())
        except ValueError:
            pass
    return fallback


def _sanitized_error_message(exc: Exception) -> str:
    if isinstance(exc, httpx.TimeoutException):
        return "provider request timed out"
    if isinstance(exc, httpx.HTTPStatusError):
        return f"provider returned HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return "provider request failed"
    return "provider call failed"


def _elapsed_ms(started: float) -> int:
    return max(0, round((monotonic() - started) * 1000))


def _ownership_overrides_from_env(
    env: Mapping[str, str],
) -> list[PingAnAssetOwnershipOverride]:
    raw = env.get("SOC_PINGAN_ASSET_OWNERSHIP_OVERRIDES_JSON", "").strip()
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PingAnAssetProviderConfigurationError("SOC_PINGAN_ASSET_OWNERSHIP_OVERRIDES_JSON must be valid JSON") from exc
    if not isinstance(values, list):
        raise PingAnAssetProviderConfigurationError("SOC_PINGAN_ASSET_OWNERSHIP_OVERRIDES_JSON must be a JSON array")
    try:
        return [PingAnAssetOwnershipOverride.model_validate(value) for value in values]
    except ValueError as exc:
        raise PingAnAssetProviderConfigurationError("SOC_PINGAN_ASSET_OWNERSHIP_OVERRIDES_JSON contains an invalid rule") from exc


def _require_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise PingAnAssetProviderConfigurationError(f"required environment variable {name} is missing")
    return value


def _int_env_required(env: Mapping[str, str], name: str) -> int:
    return _int_env(env, name, None)


def _int_env(env: Mapping[str, str], name: str, default: int | None) -> int:
    raw = env.get(name)
    if raw is None or not raw.strip():
        if default is None:
            raise PingAnAssetProviderConfigurationError(f"required integer environment variable {name} is missing")
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise PingAnAssetProviderConfigurationError(f"environment variable {name} must be an integer") from exc


def _float_env(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise PingAnAssetProviderConfigurationError(f"environment variable {name} must be numeric") from exc


def _bool_env(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PingAnAssetProviderConfigurationError(f"environment variable {name} must be a boolean")


def _workflow_environment(env: Mapping[str, str]) -> Literal["dev", "stg", "prd"]:
    value = _require_env(env, "SOC_PINGAN_WORKFLOW_ENV").lower()
    if value not in {"dev", "stg", "prd"}:
        raise PingAnAssetProviderConfigurationError("SOC_PINGAN_WORKFLOW_ENV must be dev, stg, or prd")
    return value  # type: ignore[return-value]


__all__ = [
    "PINGAN_LEGACY_WORKFLOW_APP_ID",
    "PINGAN_LEGACY_WORKFLOW_OPERATOR",
    "HttpPingAnZeusAssetSearchPort",
    "PingAnAssetLocationAttempt",
    "PingAnAssetLocationCandidate",
    "PingAnAssetLocationQuery",
    "PingAnAssetLocationResult",
    "PingAnAssetLocatorService",
    "PingAnAssetOwnershipOverride",
    "PingAnAssetProviderConfigurationError",
    "PingAnAssetProviderError",
    "PingAnAssetProviderUnavailableError",
    "PingAnAssetSearchPort",
    "PingAnAssetType",
    "PingAnAssetWorkflowConfig",
    "PingAnAssetWorkflowPort",
    "StaticPingAnAssetSearchPort",
    "StaticPingAnAssetWorkflowPort",
    "build_pingan_asset_locator_from_env",
    "infer_pingan_asset_type",
]
