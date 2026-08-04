from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest
import yaml

from soc_agent.integrations.pingan.asset_location import PingAnAssetLocationAttempt
from soc_agent.integrations.pingan.d12b_acceptance import (
    PingAnAssetCaseMatrix,
    PingAnAssetCaseMatrixError,
    PingAnAssetCaseMatrixStatus,
    build_pingan_asset_case_matrix_plan,
    load_pingan_asset_case_matrix,
    run_pingan_asset_case_matrix,
)
from soc_agent.integrations.pingan.dev_validation import (
    PingAnAssetDirectSmokeReport,
    PingAnDevPreflightCheck,
    PingAnDevPreflightReport,
    write_validation_report,
)


def test_tracked_example_builds_complete_value_free_plan() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    source = backend_root / "samples" / "pingan_dev" / "d12b-test-cases.example.yaml"

    matrix = load_pingan_asset_case_matrix(source)
    plan = build_pingan_asset_case_matrix_plan(matrix)
    encoded = json.dumps(plan.model_dump(mode="json"), ensure_ascii=False)

    assert plan.complete is True
    assert plan.case_count == 7
    assert "<approved-" not in encoded
    assert "D12B_INVALID_ZEUS_APP_KEY" not in encoded
    assert "query" not in encoded
    assert "um-fallback" in encoded


def test_live_matrix_requires_local_name_and_private_permissions(tmp_path: Path) -> None:
    source = tmp_path / "d12b-test-cases.local.yaml"
    source.write_text(yaml.safe_dump(_matrix_payload()), encoding="utf-8")
    source.chmod(0o644)

    with pytest.raises(PingAnAssetCaseMatrixError, match="0600"):
        load_pingan_asset_case_matrix(source, require_private=True)

    source.chmod(0o600)
    loaded = load_pingan_asset_case_matrix(source, require_private=True)
    assert loaded.matrix_id == "d12b-test-matrix"

    wrong_name = tmp_path / "d12b-test-cases.yaml"
    wrong_name.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    wrong_name.chmod(0o600)
    with pytest.raises(PingAnAssetCaseMatrixError, match="filename"):
        load_pingan_asset_case_matrix(wrong_name, require_private=True)


def test_matrix_rejects_non_allowlisted_environment_override() -> None:
    payload = _matrix_payload()
    payload["cases"][0]["environment_overrides"] = {"PATH": "D12B_OTHER_PATH"}

    with pytest.raises(ValueError, match="not allowlisted"):
        PingAnAssetCaseMatrix.model_validate(payload)


def test_matrix_rejects_case_label_with_weaker_attempt_semantics() -> None:
    payload = _matrix_payload()
    payload["cases"][2]["expected_attempts"] = [{"stage": "um", "lookup_kind": "user", "status": "found"}]

    with pytest.raises(ValueError, match="missing required semantics"):
        PingAnAssetCaseMatrix.model_validate(payload)


def test_matrix_rejects_identifiers_that_embed_private_case_values() -> None:
    payload = _matrix_payload()
    payload["cases"][0]["case_id"] = "hit-10.0.0.1"

    with pytest.raises(ValueError, match="case_id must be an opaque label"):
        PingAnAssetCaseMatrix.model_validate(payload)

    payload = _matrix_payload()
    payload["matrix_id"] = "matrix-um00003"

    with pytest.raises(ValueError, match="matrix_id must be an opaque label"):
        PingAnAssetCaseMatrix.model_validate(payload)


def test_matrix_blocks_all_requests_when_base_preflight_fails() -> None:
    called = False

    def smoke_runner(_query, *, environ):
        nonlocal called
        called = True
        raise AssertionError(environ)

    report = run_pingan_asset_case_matrix(
        PingAnAssetCaseMatrix.model_validate(_matrix_payload()),
        environ={},
        preflight_runner=lambda _: _preflight(ready=False),
        smoke_runner=smoke_runner,
    )

    assert report.status is PingAnAssetCaseMatrixStatus.BLOCKED
    assert report.attempted_case_count == 0
    assert report.blocked_case_count == 7
    assert called is False
    assert {item.failure_reasons[0] for item in report.cases} == {"base_preflight_failed"}


def test_matrix_runs_all_cases_and_keeps_private_values_out_of_report() -> None:
    matrix = PingAnAssetCaseMatrix.model_validate(_matrix_payload())
    private_env = {
        "D12B_INVALID_ZEUS_APP_KEY": "invalid-secret-value",
        "D12B_TIMEOUT_ZEUS_BASE_URL": "https://timeout.dev.internal",
        "D12B_TIMEOUT_ZEUS_ALLOWED_HOSTS": "timeout.dev.internal",
        "D12B_TIMEOUT_SECONDS": "1",
    }
    by_query = {case.query: case for case in matrix.cases}

    def smoke_runner(query, *, environ):
        case = by_query[query.query]
        if case.kind.value == "authentication_failure":
            assert environ["SOC_PINGAN_ZEUS_APP_KEY"] == "invalid-secret-value"
        if case.kind.value == "timeout":
            assert environ["SOC_PINGAN_ZEUS_BASE_URL"] == "https://timeout.dev.internal"
        return _smoke_for(case)

    report = run_pingan_asset_case_matrix(
        matrix,
        environ=private_env,
        preflight_runner=lambda _: _preflight(),
        smoke_runner=smoke_runner,
    )
    encoded = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)

    assert report.status is PingAnAssetCaseMatrixStatus.PASSED
    assert report.attempted_case_count == 7
    assert report.passed_case_count == 7
    assert report.failed_case_count == 0
    assert report.blocked_case_count == 0
    for case in matrix.cases:
        assert case.query not in encoded
        if case.um:
            assert case.um not in encoded
    assert "invalid-secret-value" not in encoded
    assert "timeout.dev.internal" not in encoded
    assert "D12B_INVALID_ZEUS_APP_KEY" not in encoded
    assert "company-sensitive" not in encoded
    assert report.contains_raw_queries is False
    assert report.contains_raw_provider_responses is False


def test_matrix_fails_when_fallback_trace_does_not_match_expectation() -> None:
    payload = _matrix_payload()
    payload["required_case_kinds"] = ["asset_to_bu_fallback"]
    payload["cases"] = [payload["cases"][1]]
    matrix = PingAnAssetCaseMatrix.model_validate(payload)
    case = matrix.cases[0]
    smoke = _smoke_for(case)
    smoke.attempts = [
        PingAnAssetLocationAttempt(
            stage="search_asset_info",
            lookup_kind="IP",
            status="found",
            candidate_count=1,
            mocked=False,
        )
    ]

    report = run_pingan_asset_case_matrix(
        matrix,
        environ={},
        preflight_runner=lambda _: _preflight(),
        smoke_runner=lambda _query, *, environ: smoke,
    )

    assert report.status is PingAnAssetCaseMatrixStatus.FAILED
    assert report.failed_case_count == 1
    assert any(reason.startswith("missing_expected_attempt") for reason in report.cases[0].failure_reasons)


def test_validation_report_is_atomically_written_with_private_mode(tmp_path: Path) -> None:
    destination = tmp_path / "reports" / "preflight.json"

    write_validation_report(_preflight(), destination)

    assert json.loads(destination.read_text(encoding="utf-8"))["ready"] is True
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    assert list(destination.parent.glob(f".{destination.name}.*")) == []


def _matrix_payload() -> dict:
    return {
        "schema_version": "soc.pingan_asset_case_matrix.v1",
        "matrix_id": "d12b-test-matrix",
        "required_case_kinds": [
            "search_hit",
            "asset_to_bu_fallback",
            "um_fallback",
            "not_found",
            "ambiguous",
            "authentication_failure",
            "timeout",
        ],
        "cases": [
            {
                "case_id": "search-hit",
                "kind": "search_hit",
                "query": "10.0.0.1",
                "asset_type": "IP",
                "role": "victim",
                "expected_outcome": "found",
                "expected_attempts": [{"stage": "search_asset_info", "status": "found"}],
                "forbidden_stages": ["asset_to_bu", "um"],
            },
            {
                "case_id": "asset-fallback",
                "kind": "asset_to_bu_fallback",
                "query": "10.0.0.2",
                "asset_type": "IP",
                "role": "victim",
                "expected_outcome": "found",
                "expected_attempts": [
                    {"stage": "search_asset_info", "status": "not_found"},
                    {"stage": "asset_to_bu", "status": "found"},
                ],
                "forbidden_stages": ["um"],
            },
            {
                "case_id": "um-fallback",
                "kind": "um_fallback",
                "query": "10.0.0.3",
                "asset_type": "IP",
                "role": "victim",
                "um": "UM00003",
                "expected_outcome": "found",
                "expected_attempts": [
                    {"stage": "search_asset_info", "status": "not_found"},
                    {"stage": "asset_to_bu", "lookup_kind": "datacenter", "status": "not_found"},
                    {"stage": "asset_to_bu", "lookup_kind": "terminal", "status": "not_found"},
                    {"stage": "um", "lookup_kind": "user", "status": "found"},
                ],
            },
            {
                "case_id": "not-found",
                "kind": "not_found",
                "query": "10.0.0.4",
                "asset_type": "IP",
                "role": "related_asset",
                "expected_outcome": "not_found",
                "expected_attempts": [
                    {"stage": "search_asset_info", "status": "not_found"},
                    {"stage": "asset_to_bu", "lookup_kind": "datacenter", "status": "not_found"},
                    {"stage": "asset_to_bu", "lookup_kind": "terminal", "status": "not_found"},
                ],
                "forbidden_stages": ["um"],
            },
            {
                "case_id": "ambiguous",
                "kind": "ambiguous",
                "query": "shared.dev.example",
                "asset_type": "DOMAIN",
                "role": "related_asset",
                "expected_outcome": "ambiguous",
                "expected_attempts": [{"stage": "search_asset_info", "status": "found"}],
                "forbidden_stages": ["asset_to_bu", "um"],
            },
            {
                "case_id": "auth-failure",
                "kind": "authentication_failure",
                "query": "10.0.0.5",
                "asset_type": "IP",
                "role": "victim",
                "expected_outcome": "authentication_failed",
                "expected_attempts": [{"stage": "search_asset_info", "status": "failed"}],
                "forbidden_stages": ["asset_to_bu", "um"],
                "environment_overrides": {
                    "SOC_PINGAN_ZEUS_APP_KEY": "D12B_INVALID_ZEUS_APP_KEY",
                },
            },
            {
                "case_id": "timeout",
                "kind": "timeout",
                "query": "10.0.0.6",
                "asset_type": "IP",
                "role": "victim",
                "expected_outcome": "timeout",
                "expected_attempts": [{"stage": "search_asset_info", "status": "failed"}],
                "forbidden_stages": ["asset_to_bu", "um"],
                "environment_overrides": {
                    "SOC_PINGAN_ZEUS_BASE_URL": "D12B_TIMEOUT_ZEUS_BASE_URL",
                    "SOC_PINGAN_ZEUS_ALLOWED_HOSTS": "D12B_TIMEOUT_ZEUS_ALLOWED_HOSTS",
                    "SOC_PINGAN_ZEUS_TIMEOUT_SECONDS": "D12B_TIMEOUT_SECONDS",
                },
            },
        ],
    }


def _preflight(*, ready: bool = True) -> PingAnDevPreflightReport:
    return PingAnDevPreflightReport(
        environment="dev",
        provider_mode="internal",
        model_profile="deepseek-v4-flash",
        ready=ready,
        checks=[
            PingAnDevPreflightCheck(
                check_id="test",
                status="passed" if ready else "failed",
                detail="ready" if ready else "blocked",
            )
        ],
    )


def _smoke_for(case) -> PingAnAssetDirectSmokeReport:
    attempts_by_kind = {
        "search_hit": [("search_asset_info", "IP", "found")],
        "asset_to_bu_fallback": [
            ("search_asset_info", "IP", "not_found"),
            ("asset_to_bu", "datacenter", "found"),
        ],
        "um_fallback": [
            ("search_asset_info", "IP", "not_found"),
            ("asset_to_bu", "datacenter", "not_found"),
            ("asset_to_bu", "terminal", "not_found"),
            ("um", "user", "found"),
        ],
        "not_found": [
            ("search_asset_info", "IP", "not_found"),
            ("asset_to_bu", "datacenter", "not_found"),
            ("asset_to_bu", "terminal", "not_found"),
        ],
        "ambiguous": [("search_asset_info", "DOMAIN", "found")],
        "authentication_failure": [("search_asset_info", "IP", "failed")],
        "timeout": [("search_asset_info", "IP", "failed")],
    }
    attempts = [
        PingAnAssetLocationAttempt(
            stage=stage,
            lookup_kind=lookup_kind,
            status=status,
            candidate_count=(2 if case.kind.value == "ambiguous" else 1) if status == "found" else 0,
            duration_ms=3,
            mocked=False,
            error_type=("HTTPStatusError" if case.kind.value == "authentication_failure" else "ReadTimeout") if status == "failed" else None,
        )
        for stage, lookup_kind, status in attempts_by_kind[case.kind.value]
    ]
    success = case.expected_outcome in {"found", "not_found", "ambiguous"}
    return PingAnAssetDirectSmokeReport(
        outcome=case.expected_outcome,
        query_hash=hashlib.sha256(case.query.encode("utf-8")).hexdigest(),
        asset_type=case.asset_type.value,
        role=case.role,
        duration_ms=5,
        preflight=_preflight(),
        result=(
            {
                "query": "<omitted; see query_hash>",
                "company_name": "company-sensitive",
                "mocked": False,
                "provider_mode": "internal",
            }
            if success
            else None
        ),
        attempts=attempts,
        error_type=None if success else attempts[-1].error_type,
    )
