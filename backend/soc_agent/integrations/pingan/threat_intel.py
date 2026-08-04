"""PingAn ZEUS threat-intelligence provider behind the generic TI route.

Only reviewed, bounded fields from ``/public/indicatorSearch`` leave this
module. The legacy risk formula, whitelist rules, block decisions, and raw
provider response deliberately do not cross the integration boundary.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any, Literal, Protocol, Self
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from soc_agent.contracts import SocThreatIntelReputationRecord
from soc_agent.integrations.pingan.zeus_signing import isec_sign

_ANALYSIS_REPORT = "ipAnalyseReport"
_REPUTATION_REPORT = "ipReputationReport"
_SOURCE_NAME = "pingan_zeus_indicator_search"
_MAX_LABELS = 100
_MAX_LABEL_LENGTH = 256
_MAX_REPORT_ITEMS = 100
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_WARNINGS = 50


class PingAnThreatIntelProviderError(RuntimeError):
    """Base error for the PingAn threat-intelligence boundary."""


class PingAnThreatIntelConfigurationError(PingAnThreatIntelProviderError, ValueError):
    """Raised when internal provider configuration is incomplete or unsafe."""


class PingAnThreatIntelResponseError(PingAnThreatIntelProviderError):
    """Raised when ZEUS returns a response outside the reviewed contract."""


class PingAnThreatIntelUnavailableError(PingAnThreatIntelProviderError):
    """Raised when the provider failed, rather than returning a normal miss."""

    def __init__(self, message: str, *, error_type: str, duration_ms: float) -> None:
        super().__init__(message)
        self.error_type = error_type
        self.duration_ms = duration_ms


class PingAnThreatIntelQuery(BaseModel):
    """One validated IP reputation query."""

    model_config = ConfigDict(extra="forbid")

    ip: str = Field(min_length=1, max_length=64)

    @field_validator("ip", mode="before")
    @classmethod
    def _normalize_ip(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("ip must be a non-empty string")
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError as exc:
            raise ValueError("ip must be a valid IPv4 or IPv6 address") from exc


class PingAnThreatIntelLabelEvidence(BaseModel):
    """One provider label and every reviewed source path that asserted it."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=_MAX_LABEL_LENGTH)
    source_paths: list[str] = Field(min_length=1)


class PingAnThreatIntelReportSummary(BaseModel):
    """Bounded projection of one ZEUS report branch."""

    model_config = ConfigDict(extra="forbid")

    report_type: Literal["analysis", "reputation"]
    source_path: str
    present: bool
    labels: list[str] = Field(default_factory=list)
    updated_at_raw: str | None = None
    updated_at: datetime | None = None
    selected_context: dict[str, Any] = Field(default_factory=dict)


class PingAnThreatIntelResult(BaseModel):
    """Typed, investigation-only result returned by the PingAn MCP tool."""

    model_config = ConfigDict(extra="forbid")

    schema_version: str = "soc.pingan_threat_intel_result.v1"
    ip: str
    reputation_found: bool
    reputation: SocThreatIntelReputationRecord | None = None
    report_summaries: list[PingAnThreatIntelReportSummary] = Field(default_factory=list)
    label_evidence: list[PingAnThreatIntelLabelEvidence] = Field(default_factory=list)
    freshness_status: Literal["fresh", "stale", "unknown", "not_found"]
    queried_at: datetime
    duration_ms: float = Field(ge=0)
    response_sha256: str = Field(min_length=64, max_length=64)
    mapping_warnings: list[str] = Field(default_factory=list)
    mocked: bool
    provider_mode: Literal["fake", "internal"]
    evidence_boundary: Literal["investigation_only"] = "investigation_only"
    decision_impact: Literal["none"] = "none"
    automation_eligible: Literal[False] = False
    raw_response_included: Literal[False] = False

    @model_validator(mode="after")
    def _validate_found_contract(self) -> Self:
        if self.reputation_found != (self.reputation is not None):
            raise ValueError("reputation_found must match reputation presence")
        if self.reputation is not None:
            if self.reputation.ip != self.ip:
                raise ValueError("reputation IP must match the query IP")
            if self.reputation.mocked is not self.mocked:
                raise ValueError("reputation and result mock provenance must match")
        if not self.reputation_found and self.freshness_status != "not_found":
            raise ValueError("a not-found result must use freshness_status=not_found")
        return self


class PingAnThreatIntelSearchPort(Protocol):
    """Transport port for signed ZEUS ``indicatorSearch`` requests."""

    mocked: bool

    def query(self, *, ip: str) -> Mapping[str, Any]: ...


class HttpPingAnZeusThreatIntelPort:
    """Signed HTTP transport for the reviewed ZEUS indicator endpoint."""

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
        endpoint_path: str = "/public/indicatorSearch",
        client: httpx.Client | None = None,
    ) -> None:
        normalized_url = base_url.strip().rstrip("/") + "/"
        parsed = urlparse(normalized_url)
        hosts = {item.strip().lower() for item in allowed_hosts if item.strip()}
        if not app_id.strip() or not app_key:
            raise PingAnThreatIntelConfigurationError("ZEUS app ID and app key are required")
        if parsed.scheme != "https" or not parsed.hostname:
            raise PingAnThreatIntelConfigurationError("ZEUS threat-intel base URL must use HTTPS")
        if not hosts or parsed.hostname.lower() not in hosts:
            raise PingAnThreatIntelConfigurationError("ZEUS threat-intel host must match the configured allowlist")
        if timeout_seconds <= 0 or max_response_bytes <= 0:
            raise PingAnThreatIntelConfigurationError("ZEUS threat-intel timeout and response limit must be positive")
        if not endpoint_path.startswith("/") or urlparse(endpoint_path).scheme:
            raise PingAnThreatIntelConfigurationError("ZEUS threat-intel endpoint must be an absolute path")
        self._base_url = normalized_url
        self._app_id = app_id.strip()
        self._app_key = app_key
        self._allowed_hosts = hosts
        self._signer = signer
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._endpoint_path = endpoint_path.lstrip("/")
        self._client = client

    def query(self, *, ip: str) -> Mapping[str, Any]:
        request_body = {"resource": ip}
        headers = dict(self._signer(data=request_body, app_id=self._app_id, app_key=self._app_key))
        url = urljoin(self._base_url, self._endpoint_path)
        if (urlparse(url).hostname or "").lower() not in self._allowed_hosts:
            raise PingAnThreatIntelConfigurationError("resolved ZEUS threat-intel URL left the configured host allowlist")
        client = self._client or httpx.Client()
        owns_client = self._client is None
        try:
            response = client.post(
                url,
                json=request_body,
                headers=headers,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            if len(response.content) > self._max_response_bytes:
                raise PingAnThreatIntelResponseError("ZEUS indicatorSearch response exceeded the configured size limit")
            try:
                result = response.json()
            except ValueError as exc:
                raise PingAnThreatIntelResponseError("ZEUS indicatorSearch returned invalid JSON") from exc
        finally:
            if owns_client:
                client.close()
        if not isinstance(result, Mapping):
            raise PingAnThreatIntelResponseError("ZEUS indicatorSearch returned a non-object JSON response")
        return result


class StaticPingAnThreatIntelSearchPort:
    """Deterministic fake transport for external-network tests."""

    mocked = True

    def __init__(self, responses: Mapping[str, Any]) -> None:
        self._responses = dict(responses)
        self.calls: list[str] = []

    def query(self, *, ip: str) -> Mapping[str, Any]:
        self.calls.append(ip)
        response = self._responses.get(ip, _empty_response())
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, Mapping):
            raise PingAnThreatIntelResponseError("fake threat-intel response must be an object")
        return response


class PingAnThreatIntelService:
    """Map ZEUS response semantics into bounded, vendor-neutral reputation."""

    def __init__(
        self,
        *,
        search_port: PingAnThreatIntelSearchPort,
        provider_mode: Literal["fake", "internal"],
        freshness_days: int,
        provider_timezone: str = "Asia/Shanghai",
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if freshness_days <= 0:
            raise PingAnThreatIntelConfigurationError("threat-intel freshness days must be positive")
        if provider_mode == "internal" and search_port.mocked:
            raise PingAnThreatIntelConfigurationError("internal threat-intel mode cannot use a mocked transport")
        if provider_mode == "fake" and not search_port.mocked:
            raise PingAnThreatIntelConfigurationError("fake threat-intel mode cannot use an internal transport")
        try:
            timezone = ZoneInfo(provider_timezone)
        except ZoneInfoNotFoundError as exc:
            raise PingAnThreatIntelConfigurationError("unknown threat-intel provider timezone") from exc
        self._search = search_port
        self._provider_mode = provider_mode
        self._freshness_days = freshness_days
        self._provider_timezone = timezone
        self._now = now or (lambda: datetime.now(UTC))

    def lookup(self, query: PingAnThreatIntelQuery | Mapping[str, Any]) -> PingAnThreatIntelResult:
        request = query if isinstance(query, PingAnThreatIntelQuery) else PingAnThreatIntelQuery.model_validate(query)
        started = monotonic()
        queried_at = _ensure_aware(self._now())
        try:
            response = self._search.query(ip=request.ip)
            result = self._map_response(
                ip=request.ip,
                response=response,
                queried_at=queried_at,
                duration_ms=_elapsed_ms(started),
            )
        except PingAnThreatIntelResponseError:
            raise
        except Exception as exc:  # noqa: BLE001 - transport failures are sanitized at this boundary
            raise PingAnThreatIntelUnavailableError(
                "PingAn ZEUS threat-intelligence provider is unavailable",
                error_type=exc.__class__.__name__,
                duration_ms=_elapsed_ms(started),
            ) from exc
        return result

    def _map_response(
        self,
        *,
        ip: str,
        response: Mapping[str, Any],
        queried_at: datetime,
        duration_ms: float,
    ) -> PingAnThreatIntelResult:
        analysis, reputation, missing_reports = _extract_report_entries(response, ip=ip)
        warnings = [f"ZEUS response omitted reviewed report branch {name}." for name in missing_reports]
        analysis_summary, analysis_labels = _analysis_summary(
            analysis,
            ip=ip,
            provider_timezone=self._provider_timezone,
            warnings=warnings,
        )
        reputation_summary, reputation_labels = _reputation_summary(
            reputation,
            ip=ip,
            provider_timezone=self._provider_timezone,
            warnings=warnings,
        )
        label_evidence = _merge_label_evidence([*analysis_labels, *reputation_labels], warnings=warnings)
        found = analysis is not None or reputation is not None
        response_sha256 = _response_sha256(response)
        summaries = [analysis_summary, reputation_summary]
        updated_at = max((item.updated_at for item in summaries if item.updated_at is not None), default=None)
        freshness_status, expires_at = _freshness(
            found=found,
            updated_at=updated_at,
            queried_at=queried_at,
            freshness_days=self._freshness_days,
            warnings=warnings,
        )
        record = None
        if found:
            labels = [item.label for item in label_evidence]
            location = reputation_summary.selected_context.get("location")
            record = SocThreatIntelReputationRecord(
                ip=ip,
                labels=labels,
                confidence=None,
                score=None,
                last_seen=None,
                geo=_format_geo(location),
                source=_SOURCE_NAME,
                expires_at=expires_at,
                stale=freshness_status != "fresh",
                mocked=self._search.mocked,
                attributes={
                    "provider_schema": "pingan.zeus.indicatorSearch",
                    "provider_updated_at": updated_at.isoformat() if updated_at else None,
                    "freshness_status": freshness_status,
                    "freshness_max_age_days": self._freshness_days,
                    "selected_context": {item.report_type: item.selected_context for item in summaries if item.selected_context},
                    "source_lineage": [item.source_path for item in summaries if item.present],
                    "response_sha256": response_sha256,
                    "provider_score_mapped": False,
                    "provider_confidence_mapped": False,
                },
            )
        return PingAnThreatIntelResult(
            ip=ip,
            reputation_found=found,
            reputation=record,
            report_summaries=summaries,
            label_evidence=label_evidence,
            freshness_status=freshness_status,
            queried_at=queried_at,
            duration_ms=duration_ms,
            response_sha256=response_sha256,
            mapping_warnings=_bounded_warnings(warnings),
            mocked=self._search.mocked,
            provider_mode=self._provider_mode,
        )


def build_pingan_threat_intel_service_from_env(
    environ: Mapping[str, str] | None = None,
) -> PingAnThreatIntelService:
    """Build the explicit fake or fail-closed internal TI provider."""

    env = dict(os.environ if environ is None else environ)
    mode = env.get("SOC_PINGAN_THREAT_INTEL_PROVIDER_MODE", "fake").strip().lower()
    freshness_days = _positive_int(env.get("SOC_PINGAN_THREAT_INTEL_FRESHNESS_DAYS", "180"), name="SOC_PINGAN_THREAT_INTEL_FRESHNESS_DAYS")
    timezone = env.get("SOC_PINGAN_THREAT_INTEL_TIMEZONE", "Asia/Shanghai").strip()
    if mode == "fake":
        return PingAnThreatIntelService(
            search_port=StaticPingAnThreatIntelSearchPort(_fake_responses()),
            provider_mode="fake",
            freshness_days=freshness_days,
            provider_timezone=timezone,
        )
    if mode != "internal":
        raise PingAnThreatIntelConfigurationError("SOC_PINGAN_THREAT_INTEL_PROVIDER_MODE must be fake or internal")
    allowed_hosts = [item for item in _require_env(env, "SOC_PINGAN_ZEUS_ALLOWED_HOSTS").split(",")]
    port = HttpPingAnZeusThreatIntelPort(
        base_url=_require_env(env, "SOC_PINGAN_ZEUS_BASE_URL"),
        app_id=_require_env(env, "SOC_PINGAN_ZEUS_APP_ID"),
        app_key=_require_env(env, "SOC_PINGAN_ZEUS_APP_KEY"),
        allowed_hosts=allowed_hosts,
        timeout_seconds=_positive_float(
            env.get("SOC_PINGAN_THREAT_INTEL_TIMEOUT_SECONDS", "10"),
            name="SOC_PINGAN_THREAT_INTEL_TIMEOUT_SECONDS",
        ),
        max_response_bytes=_positive_int(
            env.get("SOC_PINGAN_THREAT_INTEL_MAX_RESPONSE_BYTES", str(_MAX_RESPONSE_BYTES)),
            name="SOC_PINGAN_THREAT_INTEL_MAX_RESPONSE_BYTES",
        ),
        endpoint_path=env.get("SOC_PINGAN_THREAT_INTEL_PATH", "/public/indicatorSearch").strip(),
    )
    return PingAnThreatIntelService(
        search_port=port,
        provider_mode="internal",
        freshness_days=freshness_days,
        provider_timezone=timezone,
    )


def _extract_report_entries(
    response: Mapping[str, Any],
    *,
    ip: str,
) -> tuple[Mapping[str, Any] | None, Mapping[str, Any] | None, list[str]]:
    data = response.get("data")
    if not isinstance(data, Mapping):
        raise PingAnThreatIntelResponseError("ZEUS indicatorSearch response data must be an object")
    intelligence = data.get("intelligenceInformation")
    if not isinstance(intelligence, Mapping):
        raise PingAnThreatIntelResponseError("ZEUS indicatorSearch intelligenceInformation must be an object")
    if _ANALYSIS_REPORT not in intelligence and _REPUTATION_REPORT not in intelligence:
        raise PingAnThreatIntelResponseError("ZEUS indicatorSearch omitted both reviewed report branches")

    missing: list[str] = []
    entries: dict[str, Mapping[str, Any] | None] = {}
    for report_name in (_ANALYSIS_REPORT, _REPUTATION_REPORT):
        if report_name not in intelligence:
            missing.append(report_name)
            entries[report_name] = None
            continue
        report = intelligence[report_name]
        if not isinstance(report, Mapping) or not isinstance(report.get("data"), Mapping):
            raise PingAnThreatIntelResponseError(f"ZEUS {report_name}.data must be an object")
        entries[report_name] = _entry_for_ip(report["data"], ip=ip, report_name=report_name)
    return entries[_ANALYSIS_REPORT], entries[_REPUTATION_REPORT], missing


def _entry_for_ip(
    data: Mapping[str, Any],
    *,
    ip: str,
    report_name: str,
) -> Mapping[str, Any] | None:
    for raw_key, raw_value in data.items():
        try:
            normalized_key = str(ipaddress.ip_address(str(raw_key).strip()))
        except ValueError:
            continue
        if normalized_key != ip:
            continue
        if raw_value in (None, {}):
            return None
        if not isinstance(raw_value, Mapping):
            raise PingAnThreatIntelResponseError(f"ZEUS {report_name}.data[{ip}] must be an object")
        return raw_value
    return None


def _analysis_summary(
    entry: Mapping[str, Any] | None,
    *,
    ip: str,
    provider_timezone: ZoneInfo,
    warnings: list[str],
) -> tuple[PingAnThreatIntelReportSummary, list[tuple[str, str]]]:
    source_path = f"data.intelligenceInformation.{_ANALYSIS_REPORT}.data[{ip}]"
    if entry is None:
        return PingAnThreatIntelReportSummary(report_type="analysis", source_path=source_path, present=False), []
    _record_unmapped_keys(
        entry,
        allowed={"intelligences", "update_time"},
        path=source_path,
        warnings=warnings,
    )
    labels: list[tuple[str, str]] = []
    intelligences = entry.get("intelligences")
    if intelligences is not None and not isinstance(intelligences, Mapping):
        warnings.append(f"{source_path}.intelligences was not an object and was omitted.")
    labs = intelligences.get("threatbook_lab") if isinstance(intelligences, Mapping) else None
    if isinstance(intelligences, Mapping):
        _record_unmapped_keys(
            intelligences,
            allowed={"threatbook_lab"},
            path=f"{source_path}.intelligences",
            warnings=warnings,
        )
    if labs is not None and not isinstance(labs, list):
        warnings.append(f"{source_path}.intelligences.threatbook_lab was not an array and was omitted.")
    lab_items = labs if isinstance(labs, list) else []
    if len(lab_items) > _MAX_REPORT_ITEMS:
        warnings.append(f"{source_path}.intelligences.threatbook_lab exceeded {_MAX_REPORT_ITEMS} items; excess entries were omitted.")
    for index, lab in enumerate(lab_items[:_MAX_REPORT_ITEMS]):
        if not isinstance(lab, Mapping):
            warnings.append(f"{source_path}.intelligences.threatbook_lab[{index}] was not an object and was omitted.")
            continue
        _record_unmapped_keys(
            lab,
            allowed={"intel_types", "intel_tags"},
            path=f"{source_path}.intelligences.threatbook_lab[{index}]",
            warnings=warnings,
        )
        for key in ("intel_types", "intel_tags"):
            labels.extend(
                _string_values_with_paths(
                    lab.get(key),
                    path=f"{source_path}.intelligences.threatbook_lab[{index}].{key}",
                    warnings=warnings,
                )
            )
    updated_at_raw, updated_at = _provider_time(
        entry.get("update_time"),
        path=f"{source_path}.update_time",
        provider_timezone=provider_timezone,
        warnings=warnings,
    )
    return (
        PingAnThreatIntelReportSummary(
            report_type="analysis",
            source_path=source_path,
            present=True,
            labels=_ordered_unique(label for label, _ in labels),
            updated_at_raw=updated_at_raw,
            updated_at=updated_at,
        ),
        labels,
    )


def _reputation_summary(
    entry: Mapping[str, Any] | None,
    *,
    ip: str,
    provider_timezone: ZoneInfo,
    warnings: list[str],
) -> tuple[PingAnThreatIntelReportSummary, list[tuple[str, str]]]:
    source_path = f"data.intelligenceInformation.{_REPUTATION_REPORT}.data[{ip}]"
    if entry is None:
        return PingAnThreatIntelReportSummary(report_type="reputation", source_path=source_path, present=False), []
    _record_unmapped_keys(
        entry,
        allowed={"judgments", "tags_classes", "scene", "basic", "update_time"},
        path=source_path,
        warnings=warnings,
    )
    labels = _string_values_with_paths(entry.get("judgments"), path=f"{source_path}.judgments", warnings=warnings)
    tag_classes = entry.get("tags_classes")
    if tag_classes is not None and not isinstance(tag_classes, list):
        warnings.append(f"{source_path}.tags_classes was not an array and was omitted.")
    tag_items = tag_classes if isinstance(tag_classes, list) else []
    if len(tag_items) > _MAX_REPORT_ITEMS:
        warnings.append(f"{source_path}.tags_classes exceeded {_MAX_REPORT_ITEMS} items; excess entries were omitted.")
    for index, item in enumerate(tag_items[:_MAX_REPORT_ITEMS]):
        if not isinstance(item, Mapping):
            warnings.append(f"{source_path}.tags_classes[{index}] was not an object and was omitted.")
            continue
        _record_unmapped_keys(
            item,
            allowed={"tags_type", "tags"},
            path=f"{source_path}.tags_classes[{index}]",
            warnings=warnings,
        )
        labels.extend(
            _string_values_with_paths(
                item.get("tags_type"),
                path=f"{source_path}.tags_classes[{index}].tags_type",
                warnings=warnings,
                allow_scalar=True,
            )
        )
        labels.extend(
            _string_values_with_paths(
                item.get("tags"),
                path=f"{source_path}.tags_classes[{index}].tags",
                warnings=warnings,
            )
        )
    context: dict[str, Any] = {}
    scene = _bounded_string(entry.get("scene"))
    if scene:
        context["scene"] = scene
    elif entry.get("scene") is not None:
        warnings.append(f"{source_path}.scene was not a usable string and was omitted.")
    basic = entry.get("basic")
    if basic is not None and not isinstance(basic, Mapping):
        warnings.append(f"{source_path}.basic was not an object and was omitted.")
    if isinstance(basic, Mapping):
        _record_unmapped_keys(
            basic,
            allowed={"carrier", "location"},
            path=f"{source_path}.basic",
            warnings=warnings,
        )
        carrier = _bounded_string(basic.get("carrier"))
        if carrier:
            context["carrier"] = carrier
        elif basic.get("carrier") is not None:
            warnings.append(f"{source_path}.basic.carrier was not a usable string and was omitted.")
        location = basic.get("location")
        if location is not None and not isinstance(location, Mapping):
            warnings.append(f"{source_path}.basic.location was not an object and was omitted.")
        if isinstance(location, Mapping):
            _record_unmapped_keys(
                location,
                allowed={"country", "province", "city"},
                path=f"{source_path}.basic.location",
                warnings=warnings,
            )
            selected_location = {key: value for key in ("country", "province", "city") if (value := _bounded_string(location.get(key)))}
            if selected_location:
                context["location"] = selected_location
    updated_at_raw, updated_at = _provider_time(
        entry.get("update_time"),
        path=f"{source_path}.update_time",
        provider_timezone=provider_timezone,
        warnings=warnings,
    )
    return (
        PingAnThreatIntelReportSummary(
            report_type="reputation",
            source_path=source_path,
            present=True,
            labels=_ordered_unique(label for label, _ in labels),
            updated_at_raw=updated_at_raw,
            updated_at=updated_at,
            selected_context=context,
        ),
        labels,
    )


def _string_values_with_paths(
    value: Any,
    *,
    path: str,
    warnings: list[str],
    allow_scalar: bool = False,
) -> list[tuple[str, str]]:
    if value is None:
        return []
    scalar_value = allow_scalar and isinstance(value, str)
    values = [value] if scalar_value else value
    if not isinstance(values, list):
        warnings.append(f"{path} was not an array and was omitted.")
        return []
    result: list[tuple[str, str]] = []
    if len(values) > _MAX_REPORT_ITEMS:
        warnings.append(f"{path} exceeded {_MAX_REPORT_ITEMS} values; excess values were omitted.")
    for index, item in enumerate(values[:_MAX_REPORT_ITEMS]):
        if isinstance(item, str) and len(item.strip()) > _MAX_LABEL_LENGTH:
            warnings.append(f"{path}[{index}] exceeded {_MAX_LABEL_LENGTH} characters and was truncated.")
        label = _bounded_string(item)
        if not label:
            if item is not None:
                warnings.append(f"{path}[{index}] was not a usable string and was omitted.")
            continue
        result.append((label, path if scalar_value else f"{path}[{index}]"))
    return result


def _merge_label_evidence(
    values: Sequence[tuple[str, str]],
    *,
    warnings: list[str],
) -> list[PingAnThreatIntelLabelEvidence]:
    paths_by_label: dict[str, list[str]] = {}
    original_by_label: dict[str, str] = {}
    for label, path in values:
        key = label.casefold()
        original_by_label.setdefault(key, label)
        paths = paths_by_label.setdefault(key, [])
        if path not in paths:
            paths.append(path)
    if len(paths_by_label) > _MAX_LABELS:
        warnings.append(f"Provider returned more than {_MAX_LABELS} unique labels; excess labels were omitted.")
    return [PingAnThreatIntelLabelEvidence(label=original_by_label[key], source_paths=paths_by_label[key]) for key in list(paths_by_label)[:_MAX_LABELS]]


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


def _provider_time(
    value: Any,
    *,
    path: str,
    provider_timezone: ZoneInfo,
    warnings: list[str],
) -> tuple[str | None, datetime | None]:
    raw = _bounded_string(value)
    if not raw:
        if value is not None:
            warnings.append(f"{path} was not a usable timestamp string; freshness remains unknown for this report.")
        return None, None
    parsed: datetime | None = None
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
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
        warnings.append(f"{path} could not be parsed; freshness remains unknown for this report.")
        return raw, None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=provider_timezone)
    return raw, parsed.astimezone(UTC)


def _freshness(
    *,
    found: bool,
    updated_at: datetime | None,
    queried_at: datetime,
    freshness_days: int,
    warnings: list[str],
) -> tuple[Literal["fresh", "stale", "unknown", "not_found"], datetime | None]:
    if not found:
        return "not_found", None
    if updated_at is None:
        return "unknown", None
    if updated_at > queried_at + timedelta(days=1):
        warnings.append("Provider update time is unexpectedly in the future; freshness was not trusted.")
        return "unknown", None
    expires_at = updated_at + timedelta(days=freshness_days)
    return ("fresh" if queried_at <= expires_at else "stale"), expires_at


def _format_geo(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    parts = [_bounded_string(value.get(key)) for key in ("country", "province", "city")]
    formatted = " ".join(item for item in parts if item)
    return formatted or None


def _response_sha256(response: Mapping[str, Any]) -> str:
    encoded = json.dumps(response, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _bounded_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:_MAX_LABEL_LENGTH] if normalized else None


def _ordered_unique(values: Sequence[str] | Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result[:_MAX_LABELS]


def _bounded_warnings(warnings: Sequence[str]) -> list[str]:
    return _ordered_unique(warnings)[:_MAX_WARNINGS]


def _ensure_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _elapsed_ms(started: float) -> float:
    return round(max(0.0, (monotonic() - started) * 1000), 3)


def _positive_int(value: str, *, name: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise PingAnThreatIntelConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise PingAnThreatIntelConfigurationError(f"{name} must be positive")
    return parsed


def _positive_float(value: str, *, name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise PingAnThreatIntelConfigurationError(f"{name} must be a number") from exc
    if parsed <= 0:
        raise PingAnThreatIntelConfigurationError(f"{name} must be positive")
    return parsed


def _require_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise PingAnThreatIntelConfigurationError(f"{name} is required in internal provider mode")
    return value


def _empty_response() -> dict[str, Any]:
    return {
        "data": {
            "intelligenceInformation": {
                _ANALYSIS_REPORT: {"data": {}},
                _REPUTATION_REPORT: {"data": {}},
            }
        }
    }


def _fake_responses() -> dict[str, Any]:
    ip = "203.0.113.10"
    return {
        ip: {
            "data": {
                "intelligenceInformation": {
                    _ANALYSIS_REPORT: {
                        "data": {
                            ip: {
                                "intelligences": {
                                    "threatbook_lab": [
                                        {
                                            "intel_types": ["C2"],
                                            "intel_tags": ["malware_infrastructure"],
                                        }
                                    ]
                                },
                                "update_time": "2026-08-01 12:00:00",
                            }
                        }
                    },
                    _REPUTATION_REPORT: {
                        "data": {
                            ip: {
                                "judgments": ["Suspicious"],
                                "tags_classes": [{"tags_type": "ThreatActor", "tags": ["Botnet"]}],
                                "basic": {
                                    "carrier": "example-carrier",
                                    "location": {"country": "ZZ"},
                                },
                                "update_time": "2026-08-01 12:00:00",
                            }
                        }
                    },
                }
            }
        }
    }


__all__ = [
    "HttpPingAnZeusThreatIntelPort",
    "PingAnThreatIntelConfigurationError",
    "PingAnThreatIntelLabelEvidence",
    "PingAnThreatIntelProviderError",
    "PingAnThreatIntelQuery",
    "PingAnThreatIntelReportSummary",
    "PingAnThreatIntelResponseError",
    "PingAnThreatIntelResult",
    "PingAnThreatIntelService",
    "PingAnThreatIntelUnavailableError",
    "StaticPingAnThreatIntelSearchPort",
    "build_pingan_threat_intel_service_from_env",
]
