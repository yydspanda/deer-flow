"""PingAn ZEUS security-tag provider behind the generic tag lookup route.

The provider preserves tag validity and source lineage as investigation
evidence. A tag match is not an authorized-activity fact, a benign verdict,
or permission to close an alert.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal, Protocol, Self
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts import SocSecurityTagRecord
from soc_agent.integrations.pingan.zeus_signing import (
    isec_sign,
    serialize_isec_json_body,
)
from soc_agent.integrations.pingan.zeus_target import (
    PingAnZeusTargetConfigurationError,
    load_pingan_zeus_target,
)

_SOURCE_NAME = "pingan_zeus_search_tag_content"
_MAX_RECORDS = 100
_MAX_LABELS = 100
_MAX_TEXT_LENGTH = 512
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_WARNINGS = 50


class PingAnSecurityTagProviderError(RuntimeError):
    """Base error for the PingAn security-tag boundary."""


class PingAnSecurityTagConfigurationError(PingAnSecurityTagProviderError, ValueError):
    """Raised when internal provider configuration is incomplete or unsafe."""


class PingAnSecurityTagResponseError(PingAnSecurityTagProviderError):
    """Raised when ZEUS returns data outside the reviewed response contract."""


class PingAnSecurityTagUnavailableError(PingAnSecurityTagProviderError):
    """Raised when the provider failed instead of returning a normal miss."""

    def __init__(self, message: str, *, error_type: str, duration_ms: float) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.duration_ms = duration_ms


class PingAnSecurityTagQuery(BaseModel):
    """One vendor-neutral entity lookup translated by the PingAn provider."""

    model_config = ConfigDict(extra="forbid")

    entity_key: str = Field(min_length=1, max_length=_MAX_TEXT_LENGTH)
    entity_type: str | None = Field(default=None, max_length=64)

    @field_validator("entity_key", mode="before")
    @classmethod
    def _normalize_entity_key(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("entity_key must be a non-empty string")
        return value.strip()

    @field_validator("entity_type", mode="before")
    @classmethod
    def _normalize_entity_type(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("entity_type must be a non-empty string when supplied")
        return value.strip().lower()


class PingAnSecurityTagItem(BaseModel):
    """One bounded provider record with explicit scope and validity state."""

    model_config = ConfigDict(extra="forbid")

    source_path: str
    tag_value: str | None = None
    tag_type: str | None = None
    tag_code: str | None = None
    labels: list[str] = Field(default_factory=list)
    provider_is_valid: bool | None = None
    expire_time_raw: str | None = None
    valid_until: datetime | None = None
    validity_status: Literal["active", "expired", "inactive", "unknown", "conflict"]
    exact_entity_match: bool
    open_ended_validity_accepted: bool = False


class PingAnSecurityTagResult(BaseModel):
    """Investigation-only result returned by the PingAn MCP tool."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.pingan_security_tag_result.v1"
    entity_key: str
    entity_type: str | None = None
    provider_records_found: bool
    security_tag_found: bool
    has_active: bool
    lookup_status: Literal[
        "active",
        "expired",
        "inactive",
        "conflicted",
        "unknown",
        "out_of_scope",
        "unusable",
        "not_found",
    ]
    security_tag: SocSecurityTagRecord | None = None
    records: list[PingAnSecurityTagItem] = Field(default_factory=list)
    provider_record_count: int = Field(ge=0)
    records_omitted_count: int = Field(ge=0)
    provider_version: str | None = None
    response_sha256: str = Field(min_length=64, max_length=64)
    source_freshness: Literal["unknown"] = "unknown"
    queried_at: datetime
    duration_ms: float = Field(ge=0)
    mapping_warnings: list[str] = Field(default_factory=list)
    mocked: bool
    provider_mode: Literal["fake", "internal"]
    evidence_boundary: Literal["investigation_only"] = "investigation_only"
    decision_impact: Literal["none"] = "none"
    authorization_fact_created: Literal[False] = False
    automation_eligible: Literal[False] = False
    raw_response_included: Literal[False] = False

    @model_validator(mode="after")
    def _validate_aggregate(self) -> Self:
        if self.security_tag_found != (self.security_tag is not None):
            raise ValueError("security_tag_found must match security_tag presence")
        if self.has_active and self.lookup_status != "active":
            raise ValueError("has_active requires lookup_status=active")
        if self.has_active and (self.security_tag is None or not self.security_tag.is_valid):
            raise ValueError("active results require an active security_tag record")
        if not self.security_tag_found and self.has_active:
            raise ValueError("an unmatched result cannot be active")
        if not self.provider_records_found and self.lookup_status != "not_found":
            raise ValueError("an empty provider result must use lookup_status=not_found")
        return self


class PingAnSecurityTagSearchPort(Protocol):
    """Transport port for signed ZEUS ``searchTagContent`` requests."""

    mocked: bool

    def query(self, *, entity_key: str) -> Mapping[str, Any]: ...


class HttpPingAnZeusSecurityTagPort:
    """Signed HTTP transport for the reviewed ZEUS security-tag endpoint."""

    mocked = False

    def __init__(
        self,
        *,
        base_url: str,
        app_id: str,
        app_key: str,
        allowed_hosts: Sequence[str],
        signer: Callable[..., Mapping[str, Any]] = isec_sign,
        timeout_seconds: float = 10.0,
        max_response_bytes: int = _MAX_RESPONSE_BYTES,
        endpoint_path: str = "/public/searchTagContent",
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/") + "/"
        parsed = urlparse(normalized_url)
        hosts = {item.strip().lower() for item in allowed_hosts if item.strip()}
        if not app_id.strip() or not app_key:
            raise PingAnSecurityTagConfigurationError("ZEUS app ID and app key are required")
        if parsed.scheme != "https" or not parsed.hostname:
            raise PingAnSecurityTagConfigurationError("ZEUS security-tag base URL must use HTTPS")
        if not hosts or parsed.hostname.lower() not in hosts:
            raise PingAnSecurityTagConfigurationError("ZEUS security-tag host must match the configured allowlist")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise PingAnSecurityTagConfigurationError("ZEUS security-tag timeout and response limit must be positive")
        if not endpoint_path.startswith("/") or urlparse(endpoint_path).scheme:
            raise PingAnSecurityTagConfigurationError("ZEUS security-tag endpoint must be an absolute path")
        self._base_url = normalized_url
        self._app_id = app_id.strip()
        self._app_key = app_key
        self._allowed_hosts = hosts
        self._signer = signer
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._endpoint_path = endpoint_path.lstrip("/")
        self._client = client

    def query(self, *, entity_key: str) -> Mapping[str, Any]:
        request_body = {"keywords": [entity_key]}
        headers = dict(self._signer(data=request_body, app_id=self._app_id, app_key=self._app_key))
        headers["Content-Type"] = "application/json"
        wire_body = serialize_isec_json_body(request_body)
        url = urljoin(self._base_url, self._endpoint_path)
        if (urlparse(url).hostname or "").lower() not in self._allowed_hosts:
            raise PingAnSecurityTagConfigurationError("resolved ZEUS security-tag URL left the configured host allowlist")
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            response = client.post(
                url,
                content=wire_body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise PingAnSecurityTagResponseError("ZEUS searchTagContent response exceeded the configured size limit")
            try:
                result = response.json()
            except ValueError as exc:
                raise PingAnSecurityTagResponseError("ZEUS searchTagContent returned invalid JSON") from exc
        finally:
            if owns_client:
                client.close()
        if not isinstance(result, Mapping):
            raise PingAnSecurityTagResponseError("ZEUS searchTagContent returned a non-object JSON response")
        return result


class StaticPingAnSecurityTagSearchPort:
    """Deterministic fake transport for external-network tests."""

    mocked = True

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self._responses = dict(responses)
        self.calls: list[str] = []

    def query(self, *, entity_key: str) -> Mapping[str, Any]:
        self.calls.append(entity_key)
        response = self._responses.get(entity_key, {"data": []})
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, Mapping):
            raise PingAnSecurityTagResponseError("fake security-tag response must be an object")
        return response


class PingAnSecurityTagService:
    """Map ZEUS tag records into bounded, investigation-only evidence."""

    def __init__(
        self,
        *,
        search_port: PingAnSecurityTagSearchPort,
        provider_mode: Literal["fake", "internal"],
        provider_timezone: str = "Asia/Shanghai",
        allow_open_ended_validity: bool = False,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if provider_mode == "internal" and search_port.mocked:
            raise PingAnSecurityTagConfigurationError("internal security-tag mode cannot use a mocked transport")
        if provider_mode == "fake" and not search_port.mocked:
            raise PingAnSecurityTagConfigurationError("fake security-tag mode cannot use an internal transport")
        try:
            timezone = ZoneInfo(provider_timezone)
        except ZoneInfoNotFoundError as exc:
            raise PingAnSecurityTagConfigurationError("unknown security-tag provider timezone") from exc
        self._search = search_port
        self._provider_mode = provider_mode
        self._provider_timezone = timezone
        self._allow_open_ended_validity = allow_open_ended_validity
        self._now = now or (lambda: datetime.now(UTC))

    def lookup(self, query: PingAnSecurityTagQuery | Mapping[str, Any]) -> PingAnSecurityTagResult:
        request = query if isinstance(query, PingAnSecurityTagQuery) else PingAnSecurityTagQuery.model_validate(query)
        started = monotonic()
        queried_at = _ensure_aware(self._now())
        try:
            response = self._search.query(entity_key=request.entity_key)
            return self._map_response(
                query=request,
                response=response,
                queried_at=queried_at,
                duration_ms=_elapsed_ms(started),
            )
        except PingAnSecurityTagResponseError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures are sanitized here
            raise PingAnSecurityTagUnavailableError(
                "PingAn ZEUS security-tag provider is unavailable",
                error_type=exc.__class__.__name__,
                duration_ms=_elapsed_ms(started),
            ) from exc

    def _map_response(
        self,
        *,
        query: PingAnSecurityTagQuery,
        response: Mapping[str, Any],
        queried_at: datetime,
        duration_ms: float,
    ) -> PingAnSecurityTagResult:
        raw_records = _response_records(response)
        warnings: list[str] = []
        if len(raw_records) > _MAX_RECORDS:
            warnings.append(f"Provider returned more than {_MAX_RECORDS} records; excess records were omitted.")
        records = [
            mapped
            for index, item in enumerate(raw_records[:_MAX_RECORDS])
            if (
                mapped := _map_record(
                    item,
                    index=index,
                    query=query,
                    queried_at=queried_at,
                    provider_timezone=self._provider_timezone,
                    allow_open_ended_validity=self._allow_open_ended_validity,
                    warnings=warnings,
                )
            )
            is not None
        ]
        exact_records = [item for item in records if item.exact_entity_match]
        lookup_status = _lookup_status(raw_records=raw_records, records=records, exact_records=exact_records)
        has_active = lookup_status == "active"
        response_sha256 = _response_sha256(response)
        aggregate = _aggregate_security_tag(
            query=query,
            exact_records=exact_records,
            has_active=has_active,
            response_sha256=response_sha256,
            mocked=self._search.mocked,
            lookup_status=lookup_status,
        )
        return PingAnSecurityTagResult(
            entity_key=query.entity_key,
            entity_type=query.entity_type,
            provider_records_found=bool(raw_records),
            security_tag_found=bool(exact_records),
            has_active=has_active,
            lookup_status=lookup_status,
            security_tag=aggregate,
            records=records,
            provider_record_count=len(raw_records),
            records_omitted_count=max(0, len(raw_records) - len(records)),
            provider_version=None,
            response_sha256=response_sha256,
            queried_at=queried_at,
            duration_ms=duration_ms,
            mapping_warnings=_bounded_warnings(warnings),
            mocked=self._search.mocked,
            provider_mode=self._provider_mode,
        )


def build_pingan_security_tag_service_from_env(
    environ: Mapping[str, str] | None = None,
) -> PingAnSecurityTagService:
    """Build the explicit fake or fail-closed internal security-tag provider."""

    env = dict(os.environ if environ is None else environ)
    mode = env.get("SOC_PINGAN_SECURITY_TAG_PROVIDER_MODE", "fake").strip().lower()
    timezone = env.get("SOC_PINGAN_SECURITY_TAG_TIMEZONE", "Asia/Shanghai").strip()
    allow_open_ended = _environment_bool(
        env.get("SOC_PINGAN_SECURITY_TAG_ALLOW_OPEN_ENDED_VALIDITY", "false"),
        name="SOC_PINGAN_SECURITY_TAG_ALLOW_OPEN_ENDED_VALIDITY",
    )
    if mode == "fake":
        return PingAnSecurityTagService(
            search_port=StaticPingAnSecurityTagSearchPort(_fake_responses()),
            provider_mode="fake",
            provider_timezone=timezone,
            allow_open_ended_validity=allow_open_ended,
        )
    if mode != "internal":
        raise PingAnSecurityTagConfigurationError("SOC_PINGAN_SECURITY_TAG_PROVIDER_MODE must be fake or internal")
    try:
        zeus = load_pingan_zeus_target(env)
    except PingAnZeusTargetConfigurationError as exc:
        raise PingAnSecurityTagConfigurationError(str(exc)) from exc
    port = HttpPingAnZeusSecurityTagPort(
        base_url=zeus.base_url,
        app_id=zeus.app_id,
        app_key=zeus.app_key,
        allowed_hosts=zeus.allowed_hosts,
        timeout_seconds=_positive_float(
            env.get("SOC_PINGAN_SECURITY_TAG_TIMEOUT_SECONDS", "10"),
            name="SOC_PINGAN_SECURITY_TAG_TIMEOUT_SECONDS",
        ),
        max_response_bytes=_positive_int(
            env.get("SOC_PINGAN_SECURITY_TAG_MAX_RESPONSE_BYTES", str(_MAX_RESPONSE_BYTES)),
            name="SOC_PINGAN_SECURITY_TAG_MAX_RESPONSE_BYTES",
        ),
        endpoint_path=env.get("SOC_PINGAN_SECURITY_TAG_PATH", "/public/searchTagContent").strip(),
    )
    return PingAnSecurityTagService(
        search_port=port,
        provider_mode="internal",
        provider_timezone=timezone,
        allow_open_ended_validity=allow_open_ended,
    )


def _response_records(response: Mapping[str, Any]) -> list[Any]:
    response_code = response.get("code")
    if response_code is not None and str(response_code) != "200":
        raise PingAnSecurityTagResponseError("ZEUS searchTagContent returned a non-success response code")
    if "data" not in response:
        raise PingAnSecurityTagResponseError("ZEUS searchTagContent response omitted data")
    data = response.get("data")
    if data is None:
        raise PingAnSecurityTagResponseError("ZEUS searchTagContent response data must be an array")
    if not isinstance(data, list):
        raise PingAnSecurityTagResponseError("ZEUS searchTagContent response data must be an array")
    return data


def _map_record(
    value: Any,
    *,
    index: int,
    query: PingAnSecurityTagQuery,
    queried_at: datetime,
    provider_timezone: ZoneInfo,
    allow_open_ended_validity: bool,
    warnings: list[str],
) -> PingAnSecurityTagItem | None:
    path = f"data[{index}]"
    if not isinstance(value, Mapping):
        warnings.append(f"{path} was not an object and was omitted.")
        return None
    _record_unmapped_keys(
        value,
        allowed={"tagValue", "tagType", "tagCode", "isValid", "expireTime", "labels"},
        path=path,
        warnings=warnings,
    )
    tag_value = _bounded_string(value.get("tagValue"), path=f"{path}.tagValue", warnings=warnings)
    tag_type = _bounded_string(value.get("tagType"), path=f"{path}.tagType", warnings=warnings)
    tag_code = _bounded_string(value.get("tagCode"), path=f"{path}.tagCode", warnings=warnings)
    labels = _string_list(value.get("labels"), path=f"{path}.labels", warnings=warnings)
    provider_is_valid = value.get("isValid")
    if provider_is_valid is not None and not isinstance(provider_is_valid, bool):
        warnings.append(f"{path}.isValid was not boolean; validity remains unknown.")
        provider_is_valid = None
    expire_raw, valid_until = _provider_time(
        value.get("expireTime"),
        path=f"{path}.expireTime",
        provider_timezone=provider_timezone,
        warnings=warnings,
    )
    validity, open_ended_accepted = _validity_status(
        provider_is_valid=provider_is_valid,
        expire_time_supplied=value.get("expireTime") not in (None, ""),
        valid_until=valid_until,
        queried_at=queried_at,
        allow_open_ended_validity=allow_open_ended_validity,
    )
    if provider_is_valid is True and valid_until is not None and valid_until <= queried_at:
        warnings.append(f"{path} asserted isValid=true but expireTime is not after query time.")
    return PingAnSecurityTagItem(
        source_path=path,
        tag_value=tag_value,
        tag_type=tag_type,
        tag_code=tag_code,
        labels=labels,
        provider_is_valid=provider_is_valid,
        expire_time_raw=expire_raw,
        valid_until=valid_until,
        validity_status=validity,
        exact_entity_match=_entity_values_match(tag_value, query.entity_key, entity_type=query.entity_type),
        open_ended_validity_accepted=open_ended_accepted,
    )


def _validity_status(
    *,
    provider_is_valid: bool | None,
    expire_time_supplied: bool,
    valid_until: datetime | None,
    queried_at: datetime,
    allow_open_ended_validity: bool,
) -> tuple[Literal["active", "expired", "inactive", "unknown", "conflict"], bool]:
    if provider_is_valid is True:
        if valid_until is not None:
            return ("active", False) if valid_until > queried_at else ("conflict", False)
        if not expire_time_supplied and allow_open_ended_validity:
            return "active", True
        return "unknown", False
    if valid_until is not None and valid_until <= queried_at:
        return "expired", False
    if provider_is_valid is False:
        return "inactive", False
    return "unknown", False


def _lookup_status(
    *,
    raw_records: Sequence[Any],
    records: Sequence[PingAnSecurityTagItem],
    exact_records: Sequence[PingAnSecurityTagItem],
) -> Literal["active", "expired", "inactive", "conflicted", "unknown", "out_of_scope", "unusable", "not_found"]:
    if not raw_records:
        return "not_found"
    if not records:
        return "unusable"
    if not exact_records:
        if not any(item.tag_value for item in records):
            return "unusable"
        return "out_of_scope"
    statuses = {item.validity_status for item in exact_records}
    if "conflict" in statuses:
        return "conflicted"
    if "active" in statuses:
        return "active"
    if statuses == {"expired"}:
        return "expired"
    if statuses <= {"expired", "inactive"} and "inactive" in statuses:
        return "inactive"
    return "unknown"


def _aggregate_security_tag(
    *,
    query: PingAnSecurityTagQuery,
    exact_records: Sequence[PingAnSecurityTagItem],
    has_active: bool,
    response_sha256: str,
    mocked: bool,
    lookup_status: str,
) -> SocSecurityTagRecord | None:
    if not exact_records:
        return None
    selected = [item for item in exact_records if item.validity_status == "active"] if has_active else list(exact_records)
    labels = _ordered_unique([label for item in selected for label in item.labels])
    tag_types = _ordered_unique([item.tag_type for item in selected if item.tag_type])
    active_expirations = [item.valid_until for item in selected if item.validity_status == "active" and item.valid_until is not None]
    return SocSecurityTagRecord(
        entity_key=query.entity_key,
        entity_type=query.entity_type,
        labels=labels,
        tag_types=tag_types,
        is_valid=has_active,
        valid_until=min(active_expirations) if active_expirations else None,
        source=_SOURCE_NAME,
        mocked=mocked,
        attributes={
            "provider_schema": "pingan.zeus.searchTagContent",
            "lookup_status": lookup_status,
            "provider_version": None,
            "response_sha256": response_sha256,
            "source_freshness": "unknown",
            "record_source_paths": [item.source_path for item in exact_records],
            "active_record_count": sum(item.validity_status == "active" for item in exact_records),
            "authorization_fact_created": False,
            "decision_impact": "none",
        },
    )


def _provider_time(
    value: Any,
    *,
    path: str,
    provider_timezone: ZoneInfo,
    warnings: list[str],
) -> tuple[str | None, datetime | None]:
    if value in (None, ""):
        return None, None
    if not isinstance(value, str):
        warnings.append(f"{path} was not a timestamp string; validity remains unknown.")
        return None, None
    raw = value.strip()[:_MAX_TEXT_LENGTH]
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    parsed: datetime | None = None
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(raw, pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        warnings.append(f"{path} could not be parsed; validity remains unknown.")
        return raw, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=provider_timezone)
    return raw, parsed.astimezone(UTC)


def _entity_values_match(provider_value: str | None, query_value: str, *, entity_type: str | None) -> bool:
    if provider_value is None:
        return False
    try:
        provider_ip = ipaddress.ip_address(provider_value.strip())
        query_ip = ipaddress.ip_address(query_value.strip())
    except ValueError:
        if entity_type == "ip":
            return False
    else:
        return provider_ip == query_ip
    if entity_type in {"domain", "host", "hostname"}:
        return provider_value.strip().rstrip(".").casefold() == query_value.strip().rstrip(".").casefold()
    return provider_value.strip().casefold() == query_value.strip().casefold()


def _string_list(value: Any, *, path: str, warnings: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        warnings.append(f"{path} was not an array and was omitted.")
        return []
    result: list[str] = []
    for index, item in enumerate(value[:_MAX_LABELS]):
        bounded = _bounded_string(item, path=f"{path}[{index}]", warnings=warnings)
        if bounded:
            result.append(bounded)
    if len(value) > _MAX_LABELS:
        warnings.append(f"{path} exceeded {_MAX_LABELS} values; excess labels were omitted.")
    return _ordered_unique(result)


def _bounded_string(value: Any, *, path: str, warnings: list[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        warnings.append(f"{path} was not a string and was omitted.")
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if len(normalized) > _MAX_TEXT_LENGTH:
        warnings.append(f"{path} exceeded {_MAX_TEXT_LENGTH} characters and was truncated.")
    return normalized[:_MAX_TEXT_LENGTH]


def _record_unmapped_keys(
    value: Mapping[str, Any],
    *,
    allowed: set[str],
    path: str,
    warnings: list[str],
) -> None:
    omitted = sorted(str(key) for key in value if str(key) not in allowed)
    if not omitted:
        return
    visible = omitted[:20]
    suffix = f" (+{len(omitted) - len(visible)} more)" if len(omitted) > len(visible) else ""
    warnings.append(f"{path} omitted unreviewed fields: {', '.join(visible)}{suffix}.")


def _response_sha256(response: Mapping[str, Any]) -> str:
    encoded = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _ordered_unique(values: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result[:_MAX_LABELS]


def _bounded_warnings(values: Sequence[str]) -> list[str]:
    return _ordered_unique(values)[:_MAX_WARNINGS]


def _fake_responses() -> dict[str, Any]:
    return {
        "203.0.113.10": {
            "data": [
                {
                    "tagValue": "203.0.113.10",
                    "tagType": "security_test",
                    "tagCode": "DEV-AUTHORIZED-TEST",
                    "isValid": True,
                    "expireTime": "2099-12-31 23:59:59",
                    "labels": ["authorized_security_test"],
                }
            ]
        }
    }


def _require_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise PingAnSecurityTagConfigurationError(f"{name} is required in internal security-tag mode")
    return value


def _positive_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise PingAnSecurityTagConfigurationError(f"{name} must be numeric") from exc
    if parsed <= 0:
        raise PingAnSecurityTagConfigurationError(f"{name} must be positive")
    return parsed


def _positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PingAnSecurityTagConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise PingAnSecurityTagConfigurationError(f"{name} must be positive")
    return parsed


def _environment_bool(value: str, *, name: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise PingAnSecurityTagConfigurationError(f"{name} must be a boolean")


def _ensure_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (monotonic() - started) * 1000), 3)


__all__ = [
    "HttpPingAnZeusSecurityTagPort",
    "PingAnSecurityTagConfigurationError",
    "PingAnSecurityTagItem",
    "PingAnSecurityTagProviderError",
    "PingAnSecurityTagQuery",
    "PingAnSecurityTagResponseError",
    "PingAnSecurityTagResult",
    "PingAnSecurityTagService",
    "PingAnSecurityTagUnavailableError",
    "StaticPingAnSecurityTagSearchPort",
    "build_pingan_security_tag_service_from_env",
]
