from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.actions.mcp import (
    SocMcpToolDescriptor,
    SocMcpToolProviderError,
    build_mcp_action_adapter_registry_from_file,
)
from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    ReviewQueueItem,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.integrations.pingan.d12b_acceptance import PingAnAssetCaseMatrix
from soc_agent.integrations.pingan.d12b_evidence_acceptance import (
    PingAnD12BEvidenceAcceptanceStatus,
    PingAnD12BEvidenceCheckStatus,
    run_pingan_d12b_evidence_acceptance,
)

BACKEND_ROOT = Path(__file__).resolve().parents[1]
ACTION_CONFIG = BACKEND_ROOT / "samples" / "mcp" / "pingan_asset" / "action_adapters.json"
PRIVATE_QUERY = "10.20.30.40"
PRIVATE_COMPANY = "private-company-name"


@pytest.fixture
def repository(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'soc-d12b-evidence.db'}")
    create_soc_tables(engine)
    value = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    yield value
    engine.dispose()


def test_real_mcp_result_is_persisted_and_visible_in_shared_review_context(
    repository: SqlAlchemyAlertRepository,
) -> None:
    _seed_review(repository)
    provider = FakePingAnMcpProvider(_provider_result())
    registry = build_mcp_action_adapter_registry_from_file(ACTION_CONFIG, provider)

    report = run_pingan_d12b_evidence_acceptance(
        _matrix(),
        case_id="search-hit",
        queue_id="REV-D12B-1",
        thread_id="D12B-EVIDENCE-1",
        action_adapter_registry=registry,
        repository=repository,
    )

    assert report.status is PingAnD12BEvidenceAcceptanceStatus.PASSED
    assert report.action_status == "success"
    assert report.provider_mode == "internal"
    assert report.mocked_observed is False
    assert report.evidence_boundary == "investigation_only"
    assert report.decision_impact == "none"
    assert report.raw_response_included is False
    assert report.evidence_persisted is True
    assert report.review_context_visible is True
    assert report.lead_agent_context_visible is True
    assert report.run_state_unchanged is True
    assert report.review_state_unchanged is True
    assert report.web_or_tui_render_executed is False
    assert all(item.status is PingAnD12BEvidenceCheckStatus.PASSED for item in report.checks)
    assert provider.invocations == [
        {
            "tool_name": "pingan_asset_asset_locate",
            "server_name": "pingan_asset",
            "payload": {
                "query": PRIVATE_QUERY,
                "asset_type": "IP",
                "role": "victim",
            },
            "timeout_seconds": 15,
        }
    ]

    [evidence] = repository.list_evidence(thread_id="D12B-EVIDENCE-1")
    assert evidence.evidence_id == report.evidence_id
    assert evidence.mocked is False
    assert evidence.queue_id == "REV-D12B-1"
    assert evidence.run_id == "RUN-D12B-1"
    assert evidence.alert_id == "ALT-D12B-1"
    assert evidence.request_id == report.request_id
    assert evidence.trace_id == report.trace_id
    assert report.request_id is not None
    assert report.trace_id is not None
    assert evidence.result_payload["mcp_result"]["query"] == PRIVATE_QUERY
    assert evidence.result_payload["mcp_result"]["company_name"] == PRIVATE_COMPANY

    encoded = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert PRIVATE_QUERY not in encoded
    assert PRIVATE_COMPANY not in encoded
    assert report.contains_raw_query is False
    assert report.contains_raw_um is False
    assert report.contains_raw_provider_response is False


def test_missing_case_blocks_before_mcp_invocation(
    repository: SqlAlchemyAlertRepository,
) -> None:
    _seed_review(repository)
    provider = FakePingAnMcpProvider(_provider_result())
    registry = build_mcp_action_adapter_registry_from_file(ACTION_CONFIG, provider)

    report = run_pingan_d12b_evidence_acceptance(
        _matrix(),
        case_id="missing-case",
        queue_id="REV-D12B-1",
        action_adapter_registry=registry,
        repository=repository,
    )

    assert report.status is PingAnD12BEvidenceAcceptanceStatus.BLOCKED
    assert report.action_dispatch_attempted is False
    assert report.checks[0].detail_code == "case_not_found"
    assert provider.invocations == []
    assert repository.list_evidence(queue_id="REV-D12B-1") == []


def test_non_success_case_is_not_eligible_for_evidence_acceptance(
    repository: SqlAlchemyAlertRepository,
) -> None:
    _seed_review(repository)
    provider = FakePingAnMcpProvider(_provider_result())
    registry = build_mcp_action_adapter_registry_from_file(ACTION_CONFIG, provider)

    report = run_pingan_d12b_evidence_acceptance(
        _matrix(expected_outcome="not_found"),
        case_id="not-found",
        queue_id="REV-D12B-1",
        action_adapter_registry=registry,
        repository=repository,
    )

    assert report.status is PingAnD12BEvidenceAcceptanceStatus.BLOCKED
    assert report.checks[0].detail_code == "case_expected_outcome_not_found"
    assert provider.invocations == []


def test_mocked_mcp_result_fails_gate_but_keeps_append_only_evidence(
    repository: SqlAlchemyAlertRepository,
) -> None:
    _seed_review(repository)
    provider = FakePingAnMcpProvider(_provider_result(mocked=True, provider_mode="fake"))
    registry = build_mcp_action_adapter_registry_from_file(ACTION_CONFIG, provider)

    report = run_pingan_d12b_evidence_acceptance(
        _matrix(),
        case_id="search-hit",
        queue_id="REV-D12B-1",
        action_adapter_registry=registry,
        repository=repository,
    )

    assert report.status is PingAnD12BEvidenceAcceptanceStatus.FAILED
    assert report.evidence_persisted is True
    assert report.mocked_observed is True
    assert report.run_state_unchanged is True
    assert report.review_state_unchanged is True
    assert {item.check_id for item in report.checks if item.status is PingAnD12BEvidenceCheckStatus.FAILED} == {"provider.real_internal", "evidence.real"}
    [evidence] = repository.list_evidence(queue_id="REV-D12B-1")
    assert evidence.mocked is True


def test_provider_failure_blocks_gate_without_persisting_evidence(
    repository: SqlAlchemyAlertRepository,
) -> None:
    _seed_review(repository)
    provider = FakePingAnMcpProvider(SocMcpToolProviderError("private provider details must not enter report"))
    registry = build_mcp_action_adapter_registry_from_file(ACTION_CONFIG, provider)

    report = run_pingan_d12b_evidence_acceptance(
        _matrix(),
        case_id="search-hit",
        queue_id="REV-D12B-1",
        action_adapter_registry=registry,
        repository=repository,
    )

    encoded = json.dumps(report.model_dump(mode="json"), ensure_ascii=False)
    assert report.status is PingAnD12BEvidenceAcceptanceStatus.BLOCKED
    assert report.action_dispatch_attempted is True
    assert report.action_status == "failed"
    assert report.error_type == "SocMcpToolProviderError"
    assert report.run_state_unchanged is True
    assert report.review_state_unchanged is True
    assert "private provider details" not in encoded
    assert repository.list_evidence(queue_id="REV-D12B-1") == []


class FakePingAnMcpProvider:
    def __init__(self, result: Mapping[str, Any] | Exception) -> None:
        self._result = result
        self.invocations: list[dict[str, Any]] = []

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        return [
            SocMcpToolDescriptor(
                name="pingan_asset_asset_locate",
                server="pingan_asset",
            )
        ]

    def invoke(
        self,
        tool_name: str,
        payload: Mapping[str, Any],
        *,
        timeout_seconds: int,
        server_name: str | None = None,
    ) -> Mapping[str, Any]:
        self.invocations.append(
            {
                "tool_name": tool_name,
                "server_name": server_name,
                "payload": dict(payload),
                "timeout_seconds": timeout_seconds,
            }
        )
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


def _seed_review(repository: SqlAlchemyAlertRepository) -> None:
    repository.save_run(
        AnalysisRun(
            run_id="RUN-D12B-1",
            alert_id="ALT-D12B-1",
            status=AnalysisRunStatus.SUCCESS,
        )
    )
    repository.save_review_item(
        ReviewQueueItem(
            queue_id="REV-D12B-1",
            run_id="RUN-D12B-1",
            alert_id="ALT-D12B-1",
            reason="D12-B evidence acceptance",
        )
    )


def _matrix(*, expected_outcome: str = "found") -> PingAnAssetCaseMatrix:
    if expected_outcome == "found":
        case = {
            "case_id": "search-hit",
            "kind": "search_hit",
            "query": PRIVATE_QUERY,
            "asset_type": "IP",
            "role": "victim",
            "expected_outcome": "found",
            "expected_attempts": [{"stage": "search_asset_info", "status": "found"}],
            "forbidden_stages": ["asset_to_bu", "um"],
        }
        required_case_kinds = ["search_hit"]
    else:
        case = {
            "case_id": "not-found",
            "kind": "not_found",
            "query": PRIVATE_QUERY,
            "asset_type": "IP",
            "role": "victim",
            "expected_outcome": "not_found",
            "expected_attempts": [{"stage": "search_asset_info", "status": "not_found"}],
        }
        required_case_kinds = ["not_found"]
    return PingAnAssetCaseMatrix.model_validate(
        {
            "matrix_id": "d12b-evidence-test",
            "required_case_kinds": required_case_kinds,
            "cases": [case],
        }
    )


def _provider_result(
    *,
    mocked: bool = False,
    provider_mode: str = "internal",
) -> dict[str, Any]:
    return {
        "schema_version": "soc.pingan_asset_location_result.v1",
        "query": PRIVATE_QUERY,
        "asset_type": "IP",
        "role": "victim",
        "found": True,
        "resolved": True,
        "ambiguous": False,
        "company_code": "PA001",
        "company_name": PRIVATE_COMPANY,
        "biz_group": "private-business-group",
        "source": "zeus",
        "candidates": [],
        "attempts": [],
        "mocked": mocked,
        "provider_mode": provider_mode,
        "evidence_boundary": "investigation_only",
        "decision_impact": "none",
        "raw_response_included": False,
    }
