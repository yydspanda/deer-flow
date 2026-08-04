from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from validation.compact_zeus.internal_batch.run_pingan_runtime_batch import (
    BatchExecutionConfig,
    execute_batch,
    main,
    prepare_batch_items,
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
) -> BatchExecutionConfig:
    return BatchExecutionConfig(
        source_path=tmp_path / "source.pkl",
        source_sha256=source_sha256,
        output_dir=tmp_path / "batch",
        analyzer_mode="stub",
        model_name=None,
        sensitive_evidence_mode="redact",
        persist=False,
        database_kind="none",
        workers=1,
        resume=resume,
        retry_failures=True,
        fail_fast=False,
        checkpoint_every=1,
    )
