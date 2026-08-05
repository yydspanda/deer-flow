from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from validation.compact_zeus.internal_batch.run_pingan_runtime_batch import (
    BatchExecutionConfig,
    execute_batch,
    main,
    prepare_batch_items,
    _validate_live_mcp_tool_inventory,
)

from soc_agent.actions.mcp import SocMcpToolDescriptor
from soc_agent.contracts import (
    SocEnrichmentExecutionStatus,
    SocEnrichmentExecutionTrigger,
)


class _FakeRun:
    def __init__(self, alert_id: str, *, run_id: str) -> None:
        self.alert_id = alert_id
        self.run_id = run_id

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "alert_id": self.alert_id,
            "status": "needs_review",
            "model_name": "stub",
            "prompt_version": "stub",
            "analysis": {
                "verdict": "unknown",
                "confidence": 0.45,
                "recommended_action": "needs_human_review",
            },
            "decision": {
                "evidence_state": "partial",
                "needs_review": True,
                "automation_allowed": False,
            },
            "normalization_report": {
                "source_type": "edr",
                "adapter": "pingan_platform",
            },
            "analysis_evidence_grounding": {
                "grounded_count": 1,
                "ungrounded_count": 0,
            },
            "steps": [],
        }


class _FakeService:
    def __init__(self, *, fail_alert_ids: set[str] | None = None) -> None:
        self.calls: list[str] = []
        self.fail_alert_ids = fail_alert_ids or set()

    def analyze(self, payload: dict[str, object], *, context: object) -> _FakeRun:
        alert_id = str(payload["alert_id"])
        self.calls.append(alert_id)
        assert getattr(context, "idempotency_key").startswith("pingan-batch:")
        if alert_id in self.fail_alert_ids:
            raise TimeoutError("provider timeout")
        return _FakeRun(alert_id, run_id=f"RUN-{alert_id}")


class _FakeFailedRun:
    def __init__(self, alert_id: str, *, run_id: str) -> None:
        self.alert_id = alert_id
        self.run_id = run_id

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "run_id": self.run_id,
            "alert_id": self.alert_id,
            "status": "failed",
            "model_name": "stub",
            "prompt_version": "stub",
            "failure": {
                "error_type": "LLMOutputParseError",
                "retryable": False,
                "message": "invalid structured output",
            },
            "steps": [],
        }


class _FakePersistedFailureService:
    def analyze(self, payload: dict[str, object], *, context: object) -> _FakeFailedRun:
        alert_id = str(payload["alert_id"])
        return _FakeFailedRun(alert_id, run_id=f"RUN-FAILED-{alert_id}")


class _FakeReplayService:
    def __init__(self) -> None:
        self.analyze_calls: list[str] = []
        self.replay_calls: list[tuple[str, str]] = []

    def analyze(self, payload: dict[str, object], *, context: object) -> _FakeRun:
        self.analyze_calls.append(str(payload["alert_id"]))
        raise AssertionError("persisted failed runs must use replay")

    def replay(self, run_id: str, *, context: object) -> _FakeRun:
        idempotency_key = str(getattr(context, "idempotency_key"))
        self.replay_calls.append((run_id, idempotency_key))
        alert_id = run_id.removeprefix("RUN-FAILED-")
        return _FakeRun(alert_id, run_id=f"RUN-REPLAY-{alert_id}")


class _FakeMcpInventoryProvider:
    def __init__(self, descriptors: list[SocMcpToolDescriptor]) -> None:
        self._descriptors = descriptors

    def list_tools(self) -> list[SocMcpToolDescriptor]:
        return list(self._descriptors)


class _FakeInvestigationExecution:
    execution_id = "EEXEC-BATCH-001"
    status = SocEnrichmentExecutionStatus.COMPLETED
    last_error_type = None
    last_error = None
    retryable = False


class _FakeInvestigationResult:
    execution = _FakeInvestigationExecution()

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return {
            "schema_version": "soc.enrichment_workflow_result.v1",
            "execution": {
                "execution_id": "EEXEC-BATCH-001",
                "trigger": "internal_batch",
                "status": "completed",
                "plan": {
                    "status": "planned",
                    "actions": [{"action_id": "EA-001"}],
                },
                "attempt_count": 1,
                "success_count": 1,
                "not_found_count": 0,
                "failed_count": 0,
                "evidence_count": 1,
            },
            "attempts": [],
            "idempotent_replay": False,
            "provider_invocation_count": 1,
            "execution_persisted": True,
            "attempts_persisted": True,
            "evidence_persisted_count": 1,
            "base_run_mutated": False,
        }


class _FakeInvestigationService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def execute(self, command: object, *, context: object) -> _FakeInvestigationResult:
        self.calls.append((command, context))
        return _FakeInvestigationResult()


class _FakeProjection:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def model_dump(self, **_kwargs: object) -> dict[str, object]:
        return dict(self._payload)


class _FakeInvestigationReportingService:
    def get_report_bundle(
        self, execution_id: str
    ) -> tuple[_FakeProjection, _FakeProjection]:
        return self.get_shadow_report(execution_id), self.get_addendum(execution_id)

    def get_shadow_report(self, execution_id: str) -> _FakeProjection:
        assert execution_id == "EEXEC-BATCH-001"
        return _FakeProjection(
            {
                "schema_version": "soc.investigation_shadow_report.v1",
                "report_id": "ISHR-BATCH-001",
                "planned_action_count": 1,
                "attempt_count": 1,
                "retry_count": 0,
                "provider_invocation_count": 1,
                "success_count": 1,
                "not_found_count": 0,
                "failed_count": 0,
                "persisted_evidence_count": 1,
                "missing_evidence_count": 0,
                "evidence_coverage_ratio": 1.0,
                "attempt_latency_ms_p95": 12.0,
                "routes": [
                    {
                        "route": "asset.lookup",
                        "planned_action_count": 1,
                        "real_result_count": 1,
                        "mock_result_count": 0,
                    }
                ],
                "cost_measurement_status": "not_measured",
                "measurement_gaps": ["provider_cost_not_measured"],
                "base_run_mutated": False,
                "auto_close_allowed": False,
                "confirmed_memory_write_allowed": False,
                "high_risk_actions_allowed": False,
            }
        )

    def get_addendum(self, execution_id: str) -> _FakeProjection:
        assert execution_id == "EEXEC-BATCH-001"
        return _FakeProjection(
            {
                "schema_version": "soc.investigation_addendum.v1",
                "addendum_id": "IADD-BATCH-001",
                "execution_id": execution_id,
                "summary": "Read-only investigation completed.",
                "shadow_only": True,
                "decision_impact": "none",
            }
        )


def test_prepare_batch_items_preserves_valid_rows_and_reports_invalid_rows() -> None:
    frame = pd.DataFrame(
        [
            {
                "alert_id": 101,
                "alert_full_data": {
                    "alert_id": 101,
                    "alert_data": {"alert_id": 101, "topic": "edr"},
                },
            },
            {"alert_id": 102, "alert_full_data": None},
        ]
    )

    items, errors = prepare_batch_items(frame)

    assert [item.alert_id for item in items] == ["101"]
    assert items[0].source_index == 0
    assert len(items[0].payload_sha256) == 64
    assert errors == [
        {
            "source_index": 1,
            "error_type": "TypeError",
            "error": "alert_full_data must be an object",
        }
    ]


def test_prepare_batch_items_adds_missing_trusted_tenant_and_rejects_mismatch() -> None:
    frame = pd.DataFrame(
        [
            {"alert_full_data": {"alert_data": {"alert_id": 111, "topic": "edr"}}},
            {
                "alert_full_data": {
                    "alert_data": {
                        "alert_id": 112,
                        "topic": "edr",
                        "tenant_id": "another-tenant",
                    }
                }
            },
        ]
    )

    items, errors = prepare_batch_items(frame, default_tenant_id="pingan")

    assert len(items) == 1
    assert items[0].payload["tenant_id"] == "pingan"
    assert len(errors) == 1
    assert errors[0]["source_index"] == 1
    assert errors[0]["error_type"] == "ValueError"
    assert "does not match default tenant" in errors[0]["error"]


def test_execute_batch_writes_private_artifacts_and_resumes_completed_rows(
    tmp_path: Path,
) -> None:
    frame = _frame([201, 202])
    items, errors = prepare_batch_items(frame)
    service = _FakeService()
    config = _config(tmp_path, resume=False)

    first = execute_batch(
        items,
        analysis_service=service,
        config=config,
        source_row_count=len(frame),
        source_errors=errors,
    )

    assert first["status"] == "completed"
    assert first["summary"]["completed_count"] == 2
    assert service.calls == ["201", "202"]
    assert (tmp_path / "batch/manifest.json").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "batch/results.jsonl").stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "batch").stat().st_mode & 0o777 == 0o700

    resumed_service = _FakeService()
    resumed = execute_batch(
        items,
        analysis_service=resumed_service,
        config=_config(tmp_path, resume=True),
        source_row_count=len(frame),
    )

    assert resumed["status"] == "completed"
    assert resumed["batch_id"] == first["batch_id"]
    assert resumed["started_at"] == first["started_at"]
    assert "resumed_at" in resumed
    assert resumed["execution"]["resumed_completed_count"] == 2
    assert resumed_service.calls == []


def test_execute_batch_retries_failed_rows_on_resume(tmp_path: Path) -> None:
    frame = _frame([301, 302])
    items, _ = prepare_batch_items(frame)

    first = execute_batch(
        items,
        analysis_service=_FakeService(fail_alert_ids={"302"}),
        config=_config(tmp_path, resume=False),
        source_row_count=len(frame),
    )
    assert first["status"] == "completed_with_failures"
    assert first["summary"]["failed_count"] == 1

    resumed_service = _FakeService()
    resumed = execute_batch(
        items,
        analysis_service=resumed_service,
        config=_config(tmp_path, resume=True),
        source_row_count=len(frame),
    )

    assert resumed["status"] == "completed"
    assert resumed["summary"]["failed_count"] == 0
    assert resumed_service.calls == ["302"]


def test_persisted_failed_analysis_uses_linked_replay_on_resume(
    tmp_path: Path,
) -> None:
    frame = _frame([303])
    items, _ = prepare_batch_items(frame)
    first_config = replace(
        _config(tmp_path, resume=False),
        persist=True,
        database_kind="sqlite",
    )

    first = execute_batch(
        items,
        analysis_service=_FakePersistedFailureService(),
        config=first_config,
        source_row_count=1,
    )
    assert first["summary"]["failed_count"] == 1

    replay_service = _FakeReplayService()
    resumed = execute_batch(
        items,
        analysis_service=replay_service,
        config=replace(first_config, resume=True),
        source_row_count=1,
    )

    assert resumed["status"] == "completed"
    assert replay_service.analyze_calls == []
    assert len(replay_service.replay_calls) == 1
    retry_run_id, retry_idempotency_key = replay_service.replay_calls[0]
    assert retry_run_id == "RUN-FAILED-303"
    assert retry_idempotency_key.endswith(":analysis-retry:RUN-FAILED-303")
    [item_path] = (tmp_path / "batch/items").glob("*.json")
    record = json.loads(item_path.read_text(encoding="utf-8"))
    assert record["analysis_run"]["run_id"] == "RUN-REPLAY-303"
    assert record["execution"]["analysis_retry_of_run_id"] == "RUN-FAILED-303"


def test_resume_rejects_changed_source_fingerprint(tmp_path: Path) -> None:
    frame = _frame([401])
    items, _ = prepare_batch_items(frame)
    execute_batch(
        items,
        analysis_service=_FakeService(),
        config=_config(tmp_path, resume=False),
        source_row_count=1,
    )

    changed = _config(tmp_path, resume=True, source_sha256="b" * 64)
    with pytest.raises(ValueError, match="source.sha256"):
        execute_batch(
            items,
            analysis_service=_FakeService(),
            config=changed,
            source_row_count=1,
        )


def test_execute_batch_explicitly_runs_persisted_internal_investigation(
    tmp_path: Path,
) -> None:
    frame = _frame([451])
    items, _ = prepare_batch_items(frame)
    investigation_service = _FakeInvestigationService()

    manifest = execute_batch(
        items,
        analysis_service=_FakeService(),
        investigation_service=investigation_service,
        investigation_reporting_service=_FakeInvestigationReportingService(),
        config=_config(tmp_path, resume=False, enrichment=True),
        source_row_count=1,
    )

    assert manifest["status"] == "completed"
    assert manifest["summary"]["investigation_status_counts"] == {"completed": 1}
    command, context = investigation_service.calls[0]
    assert command.run_id == "RUN-451"
    assert command.trigger is SocEnrichmentExecutionTrigger.INTERNAL_BATCH
    assert context.idempotency_key.endswith(":investigation")
    [item_path] = (tmp_path / "batch/items").glob("*.json")
    record = json.loads(item_path.read_text(encoding="utf-8"))
    assert record["analysis_run"]["run_id"] == "RUN-451"
    assert record["summary"]["investigation_execution_id"] == "EEXEC-BATCH-001"
    assert record["investigation_workflow"]["base_run_mutated"] is False
    assert record["investigation_shadow_report"]["report_id"] == "ISHR-BATCH-001"
    assert record["investigation_addendum"]["addendum_id"] == "IADD-BATCH-001"
    assert manifest["summary"]["investigation_shadow"]["evidence_coverage_ratio"] == 1.0
    assert (
        manifest["summary"]["investigation_shadow"][
            "unauthorized_base_run_mutation_count"
        ]
        == 0
    )
    assert (
        manifest["summary"]["investigation_shadow"][
            "confirmed_memory_write_allowed_count"
        ]
        == 0
    )


def test_live_plan_only_does_not_require_execution_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "alerts.pkl"
    source.write_bytes(b"restricted-loader-fixture")
    monkeypatch.setattr(
        "validation.compact_zeus.internal_batch.run_pingan_runtime_batch.load_dataframe_pickle",
        lambda *_args, **_kwargs: _frame([501, 502]),
    )

    exit_code = main(
        [
            "--source",
            str(source),
            "--analyzer-mode",
            "llm",
            "--model-name",
            "deepseek-v4-flash",
            "--limit",
            "1",
            "--plan-only",
        ]
    )

    assert exit_code == 0
    output = capsys.readouterr().out
    assert '"estimated_model_call_count": 1' in output


def test_investigation_plan_only_validates_config_without_persistence_or_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "alerts.pkl"
    source.write_bytes(b"restricted-loader-fixture")
    monkeypatch.setattr(
        "validation.compact_zeus.internal_batch.run_pingan_runtime_batch.load_dataframe_pickle",
        lambda *_args, **_kwargs: _frame([511]),
    )
    root = Path(__file__).resolve().parents[3]

    exit_code = main(
        [
            "--source",
            str(source),
            "--limit",
            "1",
            "--plan-only",
            "--enrichment-composition",
            str(root / "backend/samples/enrichment/enabled.dev-mcp.yaml"),
            "--enrichment-action-config",
            str(root / "backend/samples/mcp/soc_dev_action_adapters.json"),
            "--enrichment-extensions-config",
            str(root / "backend/samples/mcp/soc_dev_extensions_config.json"),
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["execution"]["investigation_enrichment_enabled"] is True
    assert len(output["execution"]["enrichment_action_config_sha256s"]) == 1
    assert len(output["execution"]["enrichment_extensions_config_sha256"]) == 64
    assert output["execution"]["fixed_runtime_independently_usable"] is True


def test_investigation_plan_only_rejects_disabled_composition(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "alerts.pkl"
    source.write_bytes(b"restricted-loader-fixture")
    monkeypatch.setattr(
        "validation.compact_zeus.internal_batch.run_pingan_runtime_batch.load_dataframe_pickle",
        lambda *_args, **_kwargs: _frame([512]),
    )
    root = Path(__file__).resolve().parents[3]

    exit_code = main(
        [
            "--source",
            str(source),
            "--limit",
            "1",
            "--plan-only",
            "--enrichment-composition",
            str(root / "backend/samples/enrichment/disabled.yaml"),
            "--enrichment-action-config",
            str(root / "backend/samples/mcp/soc_dev_action_adapters.json"),
            "--enrichment-extensions-config",
            str(root / "backend/samples/mcp/soc_dev_extensions_config.json"),
        ]
    )

    assert exit_code == 2
    assert "requires an enabled composition" in capsys.readouterr().err


def test_investigation_plan_requires_explicit_extensions_config(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "alerts.pkl"
    source.write_bytes(b"restricted-loader-fixture")
    monkeypatch.setattr(
        "validation.compact_zeus.internal_batch.run_pingan_runtime_batch.load_dataframe_pickle",
        lambda *_args, **_kwargs: _frame([513]),
    )
    root = Path(__file__).resolve().parents[3]

    exit_code = main(
        [
            "--source",
            str(source),
            "--limit",
            "1",
            "--plan-only",
            "--enrichment-composition",
            str(root / "backend/samples/enrichment/enabled.dev-mcp.yaml"),
            "--enrichment-action-config",
            str(root / "backend/samples/mcp/soc_dev_action_adapters.json"),
        ]
    )

    assert exit_code == 2
    assert (
        "--enrichment-extensions-config must be provided together"
        in capsys.readouterr().err
    )


def test_live_mcp_preflight_requires_every_configured_server_tool() -> None:
    root = Path(__file__).resolve().parents[3]
    action_config = root / "backend/samples/mcp/pingan_asset/action_adapters.json"

    with pytest.raises(ValueError, match="pingan_asset/pingan_asset_asset_locate"):
        _validate_live_mcp_tool_inventory(
            _FakeMcpInventoryProvider([]),
            [action_config],
        )

    tool_names = _validate_live_mcp_tool_inventory(
        _FakeMcpInventoryProvider(
            [
                SocMcpToolDescriptor(
                    name="pingan_asset_asset_locate",
                    server="pingan_asset",
                )
            ]
        ),
        [action_config],
    )
    assert tool_names == ("pingan_asset_asset_locate",)


def _frame(alert_ids: list[int]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "alert_id": alert_id,
                "alert_full_data": {
                    "alert_id": alert_id,
                    "alert_data": {"alert_id": alert_id, "topic": "edr"},
                },
            }
            for alert_id in alert_ids
        ]
    )


def _config(
    tmp_path: Path,
    *,
    resume: bool,
    source_sha256: str = "a" * 64,
    enrichment: bool = False,
) -> BatchExecutionConfig:
    return BatchExecutionConfig(
        source_path=tmp_path / "source.pkl",
        source_sha256=source_sha256,
        output_dir=tmp_path / "batch",
        analyzer_mode="stub",
        model_name=None,
        sensitive_evidence_mode="redact",
        persist=enrichment,
        database_kind="sqlite" if enrichment else "none",
        workers=1,
        resume=resume,
        retry_failures=True,
        fail_fast=False,
        checkpoint_every=1,
        investigation_enrichment_enabled=enrichment,
        enrichment_composition_sha256="b" * 64 if enrichment else None,
        enrichment_action_config_sha256s=(("c" * 64,) if enrichment else ()),
        enrichment_extensions_config_sha256="d" * 64 if enrichment else None,
    )
