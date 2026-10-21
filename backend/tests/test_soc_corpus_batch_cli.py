"""Batch operations keep internal round selection and report assembly off the CLI."""

import copy
import json

import httpx
import pytest

from soc_agent.demo.corpus_experiment_cli import main

PREFIX = "/api/soc/dev/corpus-workbench"


def batch_fixture():
    rounds = [{"round_id": f"ROUND-{n}", "experiment_id": "EXP-test", "options": {"normalization_review_mode": "apply"}, "memory_snapshot": [], "selection": {"batch": "validation", "scope": "all"}} for n in (1, 2)]
    state = {
        "schema_version": "soc.corpus_batch_status.v1",
        "batch": "validation",
        "experiment_id": "EXP-test",
        "report_token": "a" * 64,
        "total": 2,
        "completed": 2,
        "failed": 0,
        "active": 0,
        "remaining": 0,
        "running": False,
        "blocked_reason": None,
        "semantic_failed_count": 0,
        "source_rounds": rounds,
        "source_identity": {"sha256": "test-source"},
        "experiment": {"experiment_id": "EXP-test", "plan_id": "f" * 64},
        "stage_scope": {"batch_total": 2, "learning_total": 5, "validation_total": 2, "stage_complete": True},
        "report_mode": "memory_applicability",
    }
    rows = [
        {
            "alert_id": str(n),
            "source_round_id": f"ROUND-{n}",
            "status": "completed",
            "run_id": f"RUN-{n}",
            "validation_tier": "main" if n == 1 else "supplementary",
            "snapshot_changed_during_run": False,
            "summary": {"memory_uses": [], "recommended_handling": "ignore" if n == 1 else "transfer", "total_duration_ms": 1000 * n},
        }
        for n in (1, 2)
    ]
    return state, rows


@pytest.mark.parametrize("batch", ["learning", "validation"])
def test_batch_status_prints_compact_counts_without_memory_inventory(batch, capsys):
    state, _ = batch_fixture()
    state["batch"] = batch

    def handle(request):
        assert request.url.path == f"{PREFIX}/batches/{batch}"
        return httpx.Response(200, json=state)

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["status", "--batch", batch, "--watch"], client=client) == 0
    output = capsys.readouterr().out
    assert json.loads(output)["completed_count"] == 2
    assert "ROUND-" not in output
    assert "memory_snapshot" not in output


@pytest.mark.parametrize("kind", ["failed", "semantic_failed"])
def test_batch_retry_sends_kind_and_manual_concurrency_without_selecting_round(kind):
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == f"{PREFIX}/batches/learning/retry"
        assert request.method == "POST"
        assert json.loads(request.content) == {"kind": kind, "concurrency": 8}
        return httpx.Response(202, json={"retried": 2, "batch": "learning"})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["retry", "--batch", "learning", "--kind", kind, "--concurrency", "8"], client=client) == 0
    assert len(calls) == 1


def test_batch_export_joins_sources_without_inventing_a_round(tmp_path):
    state, rows = batch_fixture()

    def handle(request):
        if request.url.path.endswith("/results"):
            assert request.url.params["report_token"] == state["report_token"]
            return httpx.Response(200, json={**state, "items": rows})
        return httpx.Response(200, json=state)

    output = tmp_path / "report"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["export", "--batch", "validation", "--output-dir", str(output)], client=client) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["schema_version"] == "soc.corpus_batch_report.v1"
    assert "round" not in report
    assert report["rows"] == rows
    assert report["source_rounds"] == state["source_rounds"]
    assert report["stage_conclusion_allowed"] is True
    assert "粗分组：预估有适用历史经验" in (output / "REPORT.md").read_text()


def test_batch_export_detects_new_results_before_writing(tmp_path):
    state, rows = batch_fixture()
    calls = 0

    def handle(request):
        nonlocal calls
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={**state, "items": rows})
        calls += 1
        result = copy.deepcopy(state)
        if calls > 1:
            result["report_token"] = "b" * 64
        return httpx.Response(200, json=result)

    output = tmp_path / "changed"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["export", "--batch", "validation", "--output-dir", str(output)], client=client) == 1
    assert not output.exists()


def test_batch_pause_uses_the_selected_batch_only():
    def handle(request):
        assert request.url.path == PREFIX + "/quick-validation"
        assert json.loads(request.content) == {"batch": "validation", "scope": "all", "action": "pause"}
        return httpx.Response(202, json={"accepted": True})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["pause", "--batch", "validation"], client=client) == 0


def test_batch_accumulate_uses_only_current_completed_sources(capsys):
    state, rows = batch_fixture()
    rows[1]["status"] = "failed"
    for source in state["source_rounds"]:
        source["version"] = 1
    posted = []

    def handle(request):
        if request.method == "POST":
            assert request.url.path == PREFIX + "/rounds/ROUND-1/accumulate"
            posted.append(json.loads(request.content))
            return httpx.Response(200, json={"counts": {"accumulated": 1}, "items": [], "model_calls": 0})
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={**state, "items": rows})
        return httpx.Response(200, json=state)

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["accumulate", "--batch", "validation"], client=client) == 0
    assert posted == [{"expected_round_version": 1, "source_runs": {"1": "RUN-1"}}]
    assert "ROUND-" not in capsys.readouterr().out


def test_batch_resume_saves_concurrency_and_uses_the_batch_saved_settings():
    options = {"normalization_review_mode": "apply", "tenant_policy_enabled": False}
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/configuration"):
            assert request.url.params["batch"] == "validation"
            return httpx.Response(200, json={"saved_options": options, "defaults": {"normalization_review_mode": "off"}})
        body = json.loads(request.content)
        if request.url.path.endswith("/concurrency"):
            assert body == {"max_concurrency": 8}
        else:
            assert body == {"batch": "validation", "scope": "all", "action": "start", "options": options}
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["resume", "--batch", "validation", "--concurrency", "8"], client=client) == 0
    assert [method for method, _ in calls] == ["POST", "GET", "POST"]


def test_batch_export_refuses_missing_or_duplicate_members(tmp_path):
    state, rows = batch_fixture()

    def handle(request):
        if request.url.path.endswith("/results"):
            return httpx.Response(200, json={**state, "items": [rows[0], rows[0]]})
        return httpx.Response(200, json=state)

    output = tmp_path / "duplicate"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["export", "--batch", "validation", "--output-dir", str(output)], client=client) == 1
    assert not output.exists()


def test_unstarted_batch_exports_a_diagnostic_without_experiment(tmp_path):
    state, rows = batch_fixture()
    state.update(experiment=None, source_rounds=[], completed=0, remaining=2)
    state["stage_scope"]["stage_complete"] = False
    for row in rows:
        row.update(status="remaining", run_id=None, source_round_id=None, summary={})

    def handle(request):
        return httpx.Response(200, json={**state, **({"items": rows} if request.url.path.endswith("/results") else {})})

    output = tmp_path / "unstarted"
    with httpx.Client(transport=httpx.MockTransport(handle), base_url="http://localhost:2026") as client:
        assert main(["export", "--batch", "validation", "--output-dir", str(output)], client=client) == 0
    report = json.loads((output / "report.json").read_text())
    assert report["stage_conclusion_allowed"] is False
