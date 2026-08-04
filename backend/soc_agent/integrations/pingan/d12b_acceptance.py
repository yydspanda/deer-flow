"""Reproducible D12-B acceptance matrix for the real PingAn asset Provider."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from soc_agent.integrations.pingan.asset_location import (
    PingAnAssetLocationAttempt,
    PingAnAssetLocationQuery,
    PingAnAssetType,
)
from soc_agent.integrations.pingan.dev_validation import (
    PingAnAssetDirectSmokeReport,
    PingAnDevPreflightReport,
    run_pingan_asset_direct_smoke,
    run_pingan_dev_preflight,
)

PingAnAssetExpectedOutcome = Literal[
    "found",
    "not_found",
    "ambiguous",
    "authentication_failed",
    "timeout",
]
PingAnAssetActualOutcome = Literal[
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
PingAnAssetAttemptStage = Literal["search_asset_info", "asset_to_bu", "um"]
PingAnAssetAttemptStatus = Literal["found", "not_found", "failed"]

_LIVE_FILENAME_PATTERN = re.compile(r".+\.local\.(?:json|ya?ml)\Z", re.IGNORECASE)
_ENV_REFERENCE_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{2,127}\Z")
_MAX_MATRIX_BYTES = 1024 * 1024
_ALLOWED_OVERRIDE_TARGETS = frozenset(
    {
        "SOC_PINGAN_ZEUS_ALLOWED_HOSTS",
        "SOC_PINGAN_ZEUS_APP_ID",
        "SOC_PINGAN_ZEUS_APP_KEY",
        "SOC_PINGAN_ZEUS_BASE_URL",
        "SOC_PINGAN_ZEUS_TIMEOUT_SECONDS",
    }
)


class PingAnAssetCaseMatrixError(ValueError):
    """Raised when a private D12-B case matrix is unsafe or invalid."""


class PingAnAssetCaseKind(StrEnum):
    SEARCH_HIT = "search_hit"
    ASSET_TO_BU_FALLBACK = "asset_to_bu_fallback"
    UM_FALLBACK = "um_fallback"
    NOT_FOUND = "not_found"
    AMBIGUOUS = "ambiguous"
    AUTHENTICATION_FAILURE = "authentication_failure"
    TIMEOUT = "timeout"


_EXPECTED_OUTCOME_BY_KIND: dict[PingAnAssetCaseKind, PingAnAssetExpectedOutcome] = {
    PingAnAssetCaseKind.SEARCH_HIT: "found",
    PingAnAssetCaseKind.ASSET_TO_BU_FALLBACK: "found",
    PingAnAssetCaseKind.UM_FALLBACK: "found",
    PingAnAssetCaseKind.NOT_FOUND: "not_found",
    PingAnAssetCaseKind.AMBIGUOUS: "ambiguous",
    PingAnAssetCaseKind.AUTHENTICATION_FAILURE: "authentication_failed",
    PingAnAssetCaseKind.TIMEOUT: "timeout",
}


class PingAnAssetCaseExecutionStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PingAnAssetCaseMatrixStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class PingAnAssetExpectedAttempt(BaseModel):
    """One attempt that must appear in order in the Provider trace."""

    model_config = ConfigDict(extra="forbid")

    stage: PingAnAssetAttemptStage
    lookup_kind: str | None = Field(default=None, min_length=1, max_length=64)
    status: PingAnAssetAttemptStatus


class PingAnAssetCaseSpec(BaseModel):
    """Private inputs and public expectations for one approved internal case."""

    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    kind: PingAnAssetCaseKind
    query: str = Field(min_length=1, max_length=2048)
    asset_type: PingAnAssetType
    role: str = Field(default="", max_length=64)
    um: str | None = Field(default=None, max_length=256)
    expected_outcome: PingAnAssetExpectedOutcome
    expected_attempts: list[PingAnAssetExpectedAttempt] = Field(min_length=1)
    forbidden_stages: list[PingAnAssetAttemptStage] = Field(default_factory=list)
    environment_overrides: dict[str, str] = Field(default_factory=dict)

    @field_validator("query", "role", "um", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("environment_overrides")
    @classmethod
    def _validate_environment_overrides(cls, value: dict[str, str]) -> dict[str, str]:
        for target, source in value.items():
            if target not in _ALLOWED_OVERRIDE_TARGETS:
                raise ValueError(f"environment override target is not allowlisted: {target}")
            if not _ENV_REFERENCE_PATTERN.fullmatch(source):
                raise ValueError(f"environment override source must be an environment variable name: {target}")
        return value

    @model_validator(mode="after")
    def _validate_case_semantics(self) -> PingAnAssetCaseSpec:
        expected = _EXPECTED_OUTCOME_BY_KIND[self.kind]
        if self.expected_outcome != expected:
            raise ValueError(f"{self.kind.value} requires expected_outcome={expected}")
        if self.kind is PingAnAssetCaseKind.UM_FALLBACK and not self.um:
            raise ValueError("um_fallback requires an explicit UM value")
        if len(self.forbidden_stages) != len(set(self.forbidden_stages)):
            raise ValueError("forbidden_stages must not contain duplicates")
        required_attempts = {
            PingAnAssetCaseKind.SEARCH_HIT: (("search_asset_info", "found"),),
            PingAnAssetCaseKind.ASSET_TO_BU_FALLBACK: (
                ("search_asset_info", "not_found"),
                ("asset_to_bu", "found"),
            ),
            PingAnAssetCaseKind.UM_FALLBACK: (
                ("search_asset_info", "not_found"),
                ("asset_to_bu", "not_found"),
                ("um", "found"),
            ),
            PingAnAssetCaseKind.AUTHENTICATION_FAILURE: (("search_asset_info", "failed"),),
            PingAnAssetCaseKind.TIMEOUT: (("search_asset_info", "failed"),),
        }.get(self.kind, ())
        configured = {(item.stage, item.status) for item in self.expected_attempts}
        missing = [item for item in required_attempts if item not in configured]
        if missing:
            rendered = ", ".join(f"{stage}:{status}" for stage, status in missing)
            raise ValueError(f"{self.kind.value} expected_attempts missing required semantics: {rendered}")
        if self.kind in {
            PingAnAssetCaseKind.SEARCH_HIT,
            PingAnAssetCaseKind.AUTHENTICATION_FAILURE,
            PingAnAssetCaseKind.TIMEOUT,
        } and not {"asset_to_bu", "um"}.issubset(self.forbidden_stages):
            raise ValueError(f"{self.kind.value} must forbid asset_to_bu and um fallback stages")
        if self.kind is PingAnAssetCaseKind.ASSET_TO_BU_FALLBACK and "um" not in self.forbidden_stages:
            raise ValueError("asset_to_bu_fallback must forbid the um stage")
        if _identifier_contains_private_value(self.case_id, self.query, self.um):
            raise ValueError("case_id must be an opaque label and must not contain query or UM values")
        return self

    def to_query(self) -> PingAnAssetLocationQuery:
        return PingAnAssetLocationQuery(
            query=self.query,
            asset_type=self.asset_type,
            role=self.role,
            um=self.um,
        )


class PingAnAssetCaseMatrix(BaseModel):
    """Versioned private case matrix; raw inputs never enter aggregate reports."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_asset_case_matrix.v1"] = "soc.pingan_asset_case_matrix.v1"
    matrix_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    required_case_kinds: list[PingAnAssetCaseKind] = Field(default_factory=lambda: list(PingAnAssetCaseKind))
    cases: list[PingAnAssetCaseSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_items(self) -> PingAnAssetCaseMatrix:
        case_ids = [item.case_id for item in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("case_id values must be unique")
        if len(self.required_case_kinds) != len(set(self.required_case_kinds)):
            raise ValueError("required_case_kinds must not contain duplicates")
        if any(_identifier_contains_private_value(self.matrix_id, item.query, item.um) for item in self.cases):
            raise ValueError("matrix_id must be an opaque label and must not contain query or UM values")
        return self


class PingAnAssetCasePlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    kind: PingAnAssetCaseKind
    asset_type: PingAnAssetType
    role: str
    has_um: bool
    expected_outcome: PingAnAssetExpectedOutcome
    expected_attempts: list[PingAnAssetExpectedAttempt]
    forbidden_stages: list[PingAnAssetAttemptStage]
    environment_override_targets: list[str]


class PingAnAssetCaseMatrixPlan(BaseModel):
    """Safe preview that proves coverage without revealing test values."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_asset_case_matrix_plan.v1"] = "soc.pingan_asset_case_matrix_plan.v1"
    matrix_id: str
    complete: bool
    required_case_kinds: list[PingAnAssetCaseKind]
    observed_case_kinds: list[PingAnAssetCaseKind]
    missing_case_kinds: list[PingAnAssetCaseKind]
    case_count: int = Field(ge=0)
    cases: list[PingAnAssetCasePlanItem]
    external_requests_issued: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PingAnAssetAttemptSummary(BaseModel):
    """Credential-free subset of one Provider attempt."""

    model_config = ConfigDict(extra="forbid")

    stage: PingAnAssetAttemptStage
    lookup_kind: str
    status: PingAnAssetAttemptStatus
    candidate_count: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    error_type: str | None = None
    mocked: bool


class PingAnAssetCaseResult(BaseModel):
    """One bounded acceptance result without query, UM, result body, or secret."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    kind: PingAnAssetCaseKind
    status: PingAnAssetCaseExecutionStatus
    expected_outcome: PingAnAssetExpectedOutcome
    actual_outcome: PingAnAssetActualOutcome | None = None
    query_hash: str = Field(min_length=64, max_length=64)
    asset_type: PingAnAssetType
    role: str
    has_um: bool
    duration_ms: int = Field(ge=0)
    provider_mode: str
    mocked_observed: bool | None = None
    attempts: list[PingAnAssetAttemptSummary] = Field(default_factory=list)
    failure_reasons: list[str] = Field(default_factory=list)
    error_type: str | None = None


class PingAnAssetCaseMatrixReport(BaseModel):
    """Aggregate D12-B evidence suitable for controlled internal handoff."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["soc.pingan_asset_case_matrix_report.v1"] = "soc.pingan_asset_case_matrix_report.v1"
    matrix_id: str
    matrix_fingerprint: str = Field(min_length=64, max_length=64)
    status: PingAnAssetCaseMatrixStatus
    required_case_kinds: list[PingAnAssetCaseKind]
    observed_case_kinds: list[PingAnAssetCaseKind]
    missing_case_kinds: list[PingAnAssetCaseKind]
    total_case_count: int = Field(ge=0)
    attempted_case_count: int = Field(ge=0)
    passed_case_count: int = Field(ge=0)
    failed_case_count: int = Field(ge=0)
    blocked_case_count: int = Field(ge=0)
    preflight: PingAnDevPreflightReport
    cases: list[PingAnAssetCaseResult]
    contains_raw_queries: Literal[False] = False
    contains_raw_provider_responses: Literal[False] = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class PingAnAssetSmokeRunner(Protocol):
    def __call__(
        self,
        query: PingAnAssetLocationQuery,
        *,
        environ: Mapping[str, str],
    ) -> PingAnAssetDirectSmokeReport: ...


def load_pingan_asset_case_matrix(
    path: str | Path,
    *,
    require_private: bool = False,
) -> PingAnAssetCaseMatrix:
    """Load JSON/YAML with bounded size and optional live-file permission checks."""

    source = Path(path)
    if require_private:
        _validate_private_matrix_path(source)
    if not source.is_file():
        raise PingAnAssetCaseMatrixError("D12-B case matrix file does not exist.")
    if source.stat().st_size > _MAX_MATRIX_BYTES:
        raise PingAnAssetCaseMatrixError("D12-B case matrix exceeds the 1 MiB safety limit.")
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PingAnAssetCaseMatrixError(f"D12-B case matrix cannot be read ({exc.__class__.__name__}).") from exc
    if not isinstance(loaded, Mapping):
        raise PingAnAssetCaseMatrixError("D12-B case matrix root must be an object.")
    try:
        return PingAnAssetCaseMatrix.model_validate(loaded)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}" for error in exc.errors(include_input=False, include_url=False))
        raise PingAnAssetCaseMatrixError(f"D12-B case matrix validation failed: {details}") from exc


def build_pingan_asset_case_matrix_plan(
    matrix: PingAnAssetCaseMatrix,
) -> PingAnAssetCaseMatrixPlan:
    observed = _ordered_unique(item.kind for item in matrix.cases)
    missing = [item for item in matrix.required_case_kinds if item not in observed]
    return PingAnAssetCaseMatrixPlan(
        matrix_id=matrix.matrix_id,
        complete=not missing,
        required_case_kinds=matrix.required_case_kinds,
        observed_case_kinds=observed,
        missing_case_kinds=missing,
        case_count=len(matrix.cases),
        cases=[
            PingAnAssetCasePlanItem(
                case_id=item.case_id,
                kind=item.kind,
                asset_type=item.asset_type,
                role=item.role,
                has_um=bool(item.um),
                expected_outcome=item.expected_outcome,
                expected_attempts=item.expected_attempts,
                forbidden_stages=item.forbidden_stages,
                environment_override_targets=sorted(item.environment_overrides),
            )
            for item in matrix.cases
        ],
    )


def run_pingan_asset_case_matrix(
    matrix: PingAnAssetCaseMatrix,
    *,
    environ: Mapping[str, str] | None = None,
    preflight_runner: Callable[[Mapping[str, str]], PingAnDevPreflightReport] = run_pingan_dev_preflight,
    smoke_runner: PingAnAssetSmokeRunner = run_pingan_asset_direct_smoke,
) -> PingAnAssetCaseMatrixReport:
    """Run approved real-Provider cases; callers must explicitly confirm live IO."""

    env = dict(os.environ if environ is None else environ)
    plan = build_pingan_asset_case_matrix_plan(matrix)
    preflight = preflight_runner(env)
    if not preflight.ready:
        results = [_blocked_case_result(case, provider_mode=preflight.provider_mode, reason="base_preflight_failed") for case in matrix.cases]
        return _build_matrix_report(matrix, plan=plan, preflight=preflight, results=results)

    results: list[PingAnAssetCaseResult] = []
    for case in matrix.cases:
        input_reason = _live_input_block_reason(case)
        if input_reason:
            results.append(
                _blocked_case_result(
                    case,
                    provider_mode=preflight.provider_mode,
                    reason=input_reason,
                )
            )
            continue
        case_env, override_reason = _resolve_case_environment(case, env)
        if override_reason:
            results.append(
                _blocked_case_result(
                    case,
                    provider_mode=preflight.provider_mode,
                    reason=override_reason,
                )
            )
            continue
        try:
            smoke = smoke_runner(case.to_query(), environ=case_env)
        except Exception as exc:  # noqa: BLE001 - aggregate report must remain bounded
            results.append(
                PingAnAssetCaseResult(
                    case_id=case.case_id,
                    kind=case.kind,
                    status=PingAnAssetCaseExecutionStatus.FAILED,
                    expected_outcome=case.expected_outcome,
                    query_hash=_query_hash(case.query),
                    asset_type=case.asset_type,
                    role=case.role,
                    has_um=bool(case.um),
                    duration_ms=0,
                    provider_mode=preflight.provider_mode,
                    failure_reasons=["smoke_runner_failed"],
                    error_type=exc.__class__.__name__,
                )
            )
            continue
        results.append(_evaluate_smoke(case, smoke))
    return _build_matrix_report(matrix, plan=plan, preflight=preflight, results=results)


def _validate_private_matrix_path(path: Path) -> None:
    if path.is_symlink():
        raise PingAnAssetCaseMatrixError("Live D12-B case matrix must not be a symbolic link.")
    if not _LIVE_FILENAME_PATTERN.fullmatch(path.name):
        raise PingAnAssetCaseMatrixError("Live D12-B case matrix filename must end in .local.json/.yaml/.yml.")
    if path.exists() and stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise PingAnAssetCaseMatrixError("Live D12-B case matrix permissions must be 0600 or stricter.")


def _live_input_block_reason(case: PingAnAssetCaseSpec) -> str | None:
    if _looks_like_placeholder(case.query):
        return "case_query_is_placeholder"
    if case.um and _looks_like_placeholder(case.um):
        return "case_um_is_placeholder"
    return None


def _resolve_case_environment(
    case: PingAnAssetCaseSpec,
    base: Mapping[str, str],
) -> tuple[dict[str, str], str | None]:
    resolved = dict(base)
    for target, source in case.environment_overrides.items():
        value = base.get(source, "")
        if not value:
            return resolved, f"environment_override_source_missing:{source}"
        if _looks_like_placeholder(value):
            return resolved, f"environment_override_source_is_placeholder:{source}"
        resolved[target] = value
    return resolved, None


def _looks_like_placeholder(value: str) -> bool:
    normalized = value.strip()
    return normalized.startswith("<") and normalized.endswith(">")


def _identifier_contains_private_value(
    identifier: str,
    *values: str | None,
) -> bool:
    normalized_identifier = identifier.casefold()
    return any(len(normalized) >= 3 and normalized in normalized_identifier for value in values if value is not None and (normalized := value.strip().casefold()))


def _evaluate_smoke(
    case: PingAnAssetCaseSpec,
    smoke: PingAnAssetDirectSmokeReport,
) -> PingAnAssetCaseResult:
    failures: list[str] = []
    if smoke.outcome != case.expected_outcome:
        failures.append(f"outcome_mismatch:expected={case.expected_outcome}:actual={smoke.outcome}")
    missing_attempt = _first_missing_expected_attempt(case.expected_attempts, smoke.attempts)
    if missing_attempt is not None:
        lookup = missing_attempt.lookup_kind or "*"
        failures.append(f"missing_expected_attempt:{missing_attempt.stage}:{lookup}:{missing_attempt.status}")
    observed_stages = {item.stage for item in smoke.attempts}
    for stage in case.forbidden_stages:
        if stage in observed_stages:
            failures.append(f"forbidden_stage_observed:{stage}")
    if smoke.preflight.provider_mode != "internal":
        failures.append("provider_mode_not_internal")
    mocked_observed = any(item.mocked for item in smoke.attempts)
    if mocked_observed:
        failures.append("mocked_attempt_observed")
    if smoke.outcome in {"found", "not_found", "ambiguous"}:
        if smoke.result is None:
            failures.append("typed_result_missing")
        elif smoke.result.get("provider_mode") != "internal" or smoke.result.get("mocked") is not False:
            failures.append("typed_result_not_real_internal")

    return PingAnAssetCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        status=(PingAnAssetCaseExecutionStatus.FAILED if failures else PingAnAssetCaseExecutionStatus.PASSED),
        expected_outcome=case.expected_outcome,
        actual_outcome=smoke.outcome,
        query_hash=smoke.query_hash,
        asset_type=case.asset_type,
        role=case.role,
        has_um=bool(case.um),
        duration_ms=smoke.duration_ms,
        provider_mode=smoke.preflight.provider_mode,
        mocked_observed=mocked_observed,
        attempts=[_summarize_attempt(item) for item in smoke.attempts],
        failure_reasons=failures,
        error_type=smoke.error_type,
    )


def _first_missing_expected_attempt(
    expected: Sequence[PingAnAssetExpectedAttempt],
    actual: Sequence[PingAnAssetLocationAttempt],
) -> PingAnAssetExpectedAttempt | None:
    expected_index = 0
    for attempt in actual:
        required = expected[expected_index]
        if attempt.stage == required.stage and attempt.status == required.status and (required.lookup_kind is None or attempt.lookup_kind == required.lookup_kind):
            expected_index += 1
            if expected_index == len(expected):
                return None
    return expected[expected_index]


def _summarize_attempt(attempt: PingAnAssetLocationAttempt) -> PingAnAssetAttemptSummary:
    return PingAnAssetAttemptSummary(
        stage=attempt.stage,
        lookup_kind=attempt.lookup_kind,
        status=attempt.status,
        candidate_count=attempt.candidate_count,
        duration_ms=attempt.duration_ms,
        error_type=attempt.error_type,
        mocked=attempt.mocked,
    )


def _blocked_case_result(
    case: PingAnAssetCaseSpec,
    *,
    provider_mode: str,
    reason: str,
) -> PingAnAssetCaseResult:
    return PingAnAssetCaseResult(
        case_id=case.case_id,
        kind=case.kind,
        status=PingAnAssetCaseExecutionStatus.BLOCKED,
        expected_outcome=case.expected_outcome,
        query_hash=_query_hash(case.query),
        asset_type=case.asset_type,
        role=case.role,
        has_um=bool(case.um),
        duration_ms=0,
        provider_mode=provider_mode,
        failure_reasons=[reason],
    )


def _build_matrix_report(
    matrix: PingAnAssetCaseMatrix,
    *,
    plan: PingAnAssetCaseMatrixPlan,
    preflight: PingAnDevPreflightReport,
    results: list[PingAnAssetCaseResult],
) -> PingAnAssetCaseMatrixReport:
    passed = sum(item.status is PingAnAssetCaseExecutionStatus.PASSED for item in results)
    failed = sum(item.status is PingAnAssetCaseExecutionStatus.FAILED for item in results)
    blocked = sum(item.status is PingAnAssetCaseExecutionStatus.BLOCKED for item in results)
    if not preflight.ready or blocked:
        status = PingAnAssetCaseMatrixStatus.BLOCKED
    elif failed or plan.missing_case_kinds:
        status = PingAnAssetCaseMatrixStatus.FAILED
    else:
        status = PingAnAssetCaseMatrixStatus.PASSED
    return PingAnAssetCaseMatrixReport(
        matrix_id=matrix.matrix_id,
        matrix_fingerprint=_matrix_fingerprint(matrix),
        status=status,
        required_case_kinds=plan.required_case_kinds,
        observed_case_kinds=plan.observed_case_kinds,
        missing_case_kinds=plan.missing_case_kinds,
        total_case_count=len(results),
        attempted_case_count=sum(item.actual_outcome is not None for item in results),
        passed_case_count=passed,
        failed_case_count=failed,
        blocked_case_count=blocked,
        preflight=preflight,
        cases=results,
    )


def _matrix_fingerprint(matrix: PingAnAssetCaseMatrix) -> str:
    bounded = {
        "schema_version": matrix.schema_version,
        "matrix_id": matrix.matrix_id,
        "required_case_kinds": [item.value for item in matrix.required_case_kinds],
        "cases": [
            {
                "case_id": item.case_id,
                "kind": item.kind.value,
                "query_hash": _query_hash(item.query),
                "asset_type": item.asset_type.value,
                "role": item.role,
                "has_um": bool(item.um),
                "expected_outcome": item.expected_outcome,
                "expected_attempts": [attempt.model_dump(mode="json") for attempt in item.expected_attempts],
                "forbidden_stages": item.forbidden_stages,
                "environment_override_targets": sorted(item.environment_overrides),
            }
            for item in matrix.cases
        ],
    }
    encoded = json.dumps(bounded, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()


def _ordered_unique(values: Iterable[PingAnAssetCaseKind]) -> list[PingAnAssetCaseKind]:
    result: list[PingAnAssetCaseKind] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


__all__ = [
    "PingAnAssetAttemptSummary",
    "PingAnAssetCaseExecutionStatus",
    "PingAnAssetCaseKind",
    "PingAnAssetCaseMatrix",
    "PingAnAssetCaseMatrixError",
    "PingAnAssetCaseMatrixPlan",
    "PingAnAssetCaseMatrixReport",
    "PingAnAssetCaseMatrixStatus",
    "PingAnAssetCaseResult",
    "PingAnAssetCaseSpec",
    "PingAnAssetExpectedAttempt",
    "build_pingan_asset_case_matrix_plan",
    "load_pingan_asset_case_matrix",
    "run_pingan_asset_case_matrix",
]
