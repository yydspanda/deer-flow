"""Incremental review uses fixed report provenance, never a live default round."""

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import soc_bulk_review_learning as bulk
from scripts.test_soc_bulk_review_learning import (
    BASE,
    ExplicitExperimentAPI,
    candidate,
)


class IncrementalAPI(ExplicitExperimentAPI):
    def __init__(self, count=1):
        super().__init__(count)
        self.validation_completed = 10
        self.generation = "GEN-CURRENT"
        self.omit_none_generation = False
        self.round = {
            "round_id": "ROUND-VALIDATION-SOURCE",
            "experiment_id": self.experiment_id,
            "selection": {
                "batch": "validation",
                "scope": "all",
                "group_ids": [],
                "rule_codes": [],
                "alert_ids": [],
            },
            "purpose": "memory",
            "options": {
                "normalization_review_mode": "apply",
                "refresh_normalization": False,
                "tenant_policy_enabled": False,
                "tenant_policy_advisor_enabled": False,
                "tenant_policy_signal_providers_enabled": False,
            },
            "state": "completed",
            "version": 4,
            "learning_generation": self.generation,
            "superseded_by_round_id": None,
            "memory_mode": "snapshot",
            "memory_snapshot": [{"memory_id": "MEM-EXISTING", "version": 1}],
            "governance_hash": "a" * 64,
        }
        self.progress = {
            "selected_count": 10,
            "admitted_count": 10,
            "completed_count": 10,
            "failed_count": 0,
            "active_count": 0,
            "counts": {"completed": 10},
        }
        self.rows = [
            {
                "alert_id": f"ALERT-{index}",
                "run_id": f"RUN-VALIDATION-{index}",
                "job_id": f"JOB-{index}",
                "payload_hash": hashlib.sha256(str(index).encode()).hexdigest(),
                "group_id": "CG-ORIGINAL",
                "status": "completed",
                "attempt_count": 1,
                "snapshot_changed_during_run": False,
            }
            for index in range(10)
        ]
        self.status_overrides = {"learning": {}, "validation": {}}
        self.source_identity = {"source_sha256": "b" * 64}

    def get(self, path):
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        prefix = f"{BASE}/experiments/{self.experiment_id}"
        learning_round = {
            "round_id": "ROUND-LEARNING-CURRENT",
            "experiment_id": self.experiment_id,
            "selection": {"batch": "learning", "scope": "reuse"},
            "state": "completed",
            "version": 2,
            "created_at": "2026-10-16T00:00:00Z",
            "learning_generation": self.generation,
            "superseded_by_round_id": None,
        }
        if self.omit_none_generation and self.generation is None:
            learning_round.pop("learning_generation")
        if parsed.path == BASE + "/rounds":
            self.calls.append(("GET", path))
            assert query.get("experiment_id") == [self.experiment_id]
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [20])[0])
            return deepcopy([learning_round, self.round][offset : offset + limit])
        if parsed.path == f"{BASE}/rounds/{self.round['round_id']}":
            self.calls.append(("GET", path))
            return {**deepcopy(self.progress), "round": deepcopy(self.round)}
        if parsed.path == f"{BASE}/rounds/{self.round['round_id']}/results":
            self.calls.append(("GET", path))
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [100])[0])
            return {
                "round_id": self.round["round_id"],
                "total": len(self.rows),
                "items": deepcopy(self.rows[offset : offset + limit]),
                "source_identity": deepcopy(self.source_identity),
                "stage_scope": {
                    "stage_complete": True,
                    "learning_total": 10,
                    "validation_total": 10,
                    "batch_total": 10,
                },
            }
        if parsed.path == prefix + "/rounds":
            self.calls.append(("GET", path))
            return [
                {
                    "round_id": "ROUND-LEARNING-CURRENT",
                    "experiment_id": self.experiment_id,
                    "batch": "learning",
                    "state": "completed",
                    "created_at": "2026-10-16T00:00:00Z",
                }
            ]
        if parsed.path == f"{BASE}/rounds/ROUND-LEARNING-CURRENT":
            self.calls.append(("GET", path))
            return {"round": deepcopy(learning_round), "active_count": 0}
        response = super().get(path)
        if parsed.path == prefix + "/status":
            response.update(self.status_overrides[query["batch"][0]])
        return response


def write_report(api, directory):
    directory.mkdir()
    report = {
        "schema_version": "soc.corpus_round_report.v1",
        "round": deepcopy(api.round),
        "progress": deepcopy(api.progress),
        "source_identity": deepcopy(api.source_identity),
        "experiment": {
            "experiment_id": api.experiment_id,
            "plan_id": api.plan_id,
            "tenant_id": "pingan",
            "environment": "dev-corpus-eval",
        },
        "stage_scope": {
            "stage_complete": True,
            "batch_total": 10,
            "learning_total": 10,
            "validation_total": 10,
        },
        "rows": deepcopy(api.rows),
    }
    raw = json.dumps(report).encode("utf-8")
    (directory / "report.json").write_bytes(raw)
    (directory / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "soc.corpus_report_manifest.v1",
                "round_id": api.round["round_id"],
                "files": {"report.json": hashlib.sha256(raw).hexdigest()},
            }
        ),
        encoding="utf-8",
    )
    return directory


def plan_for(api, tmp_path):
    report_dir = write_report(api, tmp_path / "source report")
    output = tmp_path / "incremental review"
    bulk.build_plan(
        api, output, len(api.candidates), phase="incremental", report_dir=report_dir
    )
    return output / "plan.json"


def test_incremental_plan_uses_explicit_report_and_never_posts(tmp_path):
    api = IncrementalAPI()
    path = plan_for(api, tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert plan["schema_version"] == "soc.dev_learning_bulk_review_plan.v3"
    assert plan["review_phase"] == "incremental"
    assert plan["experiment_id"] == api.experiment_id
    assert plan["baseline_report"]["round_id"] == api.round["round_id"]
    assert plan["baseline_report"]["experiment_id"] == api.experiment_id
    assert len(plan["items"]) == 1
    assert not api.posts


def test_initial_review_remains_v2_and_rejects_started_validation(tmp_path):
    api = ExplicitExperimentAPI()
    output = tmp_path / "initial"
    bulk.build_plan(api, output, 1, experiment_id=api.experiment_id)
    assert (
        json.loads((output / "plan.json").read_text(encoding="utf-8"))["schema_version"]
        == "soc.dev_learning_bulk_review_plan.v2"
    )
    api.validation_completed = 1
    with pytest.raises(bulk.BulkReviewError):
        bulk.build_plan(api, tmp_path / "invalid", 1, experiment_id=api.experiment_id)
    assert not api.posts


@pytest.mark.parametrize(
    "defect", ["missing_manifest", "hash_mismatch", "manifest_round"]
)
def test_incremental_plan_rejects_unverified_export(tmp_path, defect):
    api = IncrementalAPI()
    report_dir = write_report(api, tmp_path / "source")
    manifest_path = report_dir / "manifest.json"
    if defect == "missing_manifest":
        manifest_path.unlink()
    elif defect == "hash_mismatch":
        with (report_dir / "report.json").open("a", encoding="utf-8") as stream:
            stream.write("\n")
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["round_id"] = "ROUND-ANOTHER"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(bulk.BulkReviewError):
        bulk.build_plan(
            api, tmp_path / "plan", 1, phase="incremental", report_dir=report_dir
        )
    assert not api.posts
    assert not (tmp_path / "plan" / "plan.json").exists()


@pytest.mark.parametrize(
    "defect",
    [
        "running",
        "active",
        "superseded",
        "generation",
        "learning_running",
        "validation_running",
        "manual_pending",
        "run_id",
        "status",
        "attempt_count",
        "payload_hash",
        "group_id",
        "extra_member",
        "missing_member",
    ],
)
def test_incremental_plan_rejects_stale_or_active_source(tmp_path, defect):
    api = IncrementalAPI()
    report_dir = write_report(api, tmp_path / "source")
    if defect == "running":
        api.round["state"] = "running"
    elif defect == "active":
        api.progress["active_count"] = 1
    elif defect == "superseded":
        api.round["superseded_by_round_id"] = "ROUND-REPLACEMENT"
    elif defect == "generation":
        api.generation = "GEN-RESTARTED"
    elif defect in {"learning_running", "validation_running"}:
        api.status_overrides[defect.split("_")[0]]["running"] = True
    elif defect == "manual_pending":
        api.status_overrides["validation"]["manual_pending"] = True
    elif defect == "run_id":
        api.rows[0]["run_id"] = "RUN-RETRY"
    elif defect == "status":
        api.rows[0]["status"] = "failed"
    elif defect == "attempt_count":
        api.rows[0]["attempt_count"] = 2
    elif defect == "payload_hash":
        api.rows[0]["payload_hash"] = "f" * 64
    elif defect == "group_id":
        api.rows[0]["group_id"] = "CG-CHANGED"
    elif defect == "extra_member":
        api.rows.append({**api.rows[-1], "alert_id": "ALERT-EXTRA"})
    else:
        api.rows.pop()
    with pytest.raises(bulk.BulkReviewError):
        bulk.build_plan(
            api, tmp_path / "plan", 1, phase="incremental", report_dir=report_dir
        )
    assert not api.posts
    assert not (tmp_path / "plan" / "plan.json").exists()


@pytest.mark.parametrize(
    "defect",
    ["learning", "scope", "filtered", "group_filter", "rule_filter", "duplicate"],
)
def test_incremental_plan_requires_full_unique_validation_membership(tmp_path, defect):
    api = IncrementalAPI()
    if defect == "learning":
        api.round["selection"]["batch"] = "learning"
    elif defect == "scope":
        api.round["selection"]["scope"] = "reuse"
    elif defect == "filtered":
        api.round["selection"]["alert_ids"] = [api.rows[0]["alert_id"]]
    elif defect == "group_filter":
        api.round["selection"]["group_ids"] = ["CG-ORIGINAL"]
    elif defect == "rule_filter":
        api.round["selection"]["rule_codes"] = ["RPAADM-TEST"]
    else:
        api.rows[-1] = deepcopy(api.rows[0])
    report_dir = write_report(api, tmp_path / "source")
    with pytest.raises(bulk.BulkReviewError):
        bulk.build_plan(
            api, tmp_path / "plan", 1, phase="incremental", report_dir=report_dir
        )
    assert not api.posts


def test_incremental_plan_rejects_other_explicit_experiment(tmp_path):
    api = IncrementalAPI()
    report_dir = write_report(api, tmp_path / "source")
    with pytest.raises(bulk.BulkReviewError):
        bulk.build_plan(
            api,
            tmp_path / "plan",
            1,
            experiment_id="EXP-ANOTHER",
            phase="incremental",
            report_dir=report_dir,
        )
    assert not api.posts


@pytest.mark.parametrize("validation_total", [9, 11])
@pytest.mark.parametrize("phase", ["plan", "apply"])
def test_incremental_rejects_current_validation_size_different_from_report(
    tmp_path, validation_total, phase
):
    api = IncrementalAPI()
    report_dir = write_report(api, tmp_path / "source")
    output = tmp_path / "plan"
    if phase == "apply":
        bulk.build_plan(api, output, 1, phase="incremental", report_dir=report_dir)
    api.status_overrides["validation"]["total"] = validation_total
    with pytest.raises(bulk.BulkReviewError):
        if phase == "plan":
            bulk.build_plan(api, output, 1, phase="incremental", report_dir=report_dir)
        else:
            bulk.apply_plan(api, output / "plan.json", concurrency=1)
    assert not api.posts


def test_incremental_plan_accepts_fixed_paused_full_scope_with_failed_and_queued(
    tmp_path,
):
    api = IncrementalAPI()
    api.round["state"] = "paused"
    api.rows[0].update(status="failed", run_id="RUN-FAILED")
    api.rows[1].update(status="queued", run_id=None, attempt_count=0)
    api.progress.update(
        completed_count=8,
        failed_count=1,
        counts={"completed": 8, "failed": 1, "queued": 1},
    )
    api.validation_completed = 8
    api.status_overrides["validation"].update(failed=1, remaining=1)
    path = plan_for(api, tmp_path)
    assert path.is_file()
    assert not api.posts


def test_incremental_plan_accepts_legacy_none_generation_if_current(tmp_path):
    api = IncrementalAPI()
    api.generation = None
    api.round["learning_generation"] = None
    assert plan_for(api, tmp_path).is_file()
    assert not api.posts


def test_incremental_legacy_omitted_generation_can_plan_and_apply(tmp_path):
    api = IncrementalAPI()
    api.generation = None
    api.omit_none_generation = True
    api.round.pop("learning_generation")
    path = plan_for(api, tmp_path)
    assert bulk.apply_plan(api, path, concurrency=1)["counts"] == {"completed": 1}


def test_incremental_legacy_omitted_generation_rejects_new_learning_generation(
    tmp_path,
):
    api = IncrementalAPI()
    api.generation = None
    api.omit_none_generation = True
    api.round.pop("learning_generation")
    path = plan_for(api, tmp_path)
    api.generation = "GEN-RESTARTED"
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path, concurrency=1)
    assert not api.posts


def test_incremental_plan_accepts_pause_after_export_but_fences_later_version(tmp_path):
    api = IncrementalAPI()
    report_dir = write_report(api, tmp_path / "source")
    api.round["version"] += 1
    api.round["state"] = "paused"
    output = tmp_path / "plan"
    bulk.build_plan(api, output, 1, phase="incremental", report_dir=report_dir)
    api.round["version"] += 1
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, output / "plan.json", concurrency=1)
    assert not api.posts


def test_incremental_plan_freezes_only_pending_and_keeps_unknown_core_blocked(tmp_path):
    api = IncrementalAPI(2)
    unknown = api.candidates[candidate(2)["candidate_id"]]
    unknown["scope_view"]["required_details"] = {}
    unknown["scope_view"]["unresolved_fingerprint_keys"] = ["behavior_fingerprint"]
    previous = candidate(3)
    previous["status"] = "confirmed"
    api.candidates[previous["candidate_id"]] = previous
    report_dir = write_report(api, tmp_path / "source")
    output = tmp_path / "plan"
    bulk.build_plan(api, output, 2, phase="incremental", report_dir=report_dir)
    plan = json.loads((output / "plan.json").read_text(encoding="utf-8"))
    assert {item["candidate_id"] for item in plan["items"]} == {
        candidate(1)["candidate_id"],
        candidate(2)["candidate_id"],
    }
    assert next(
        item
        for item in plan["items"]
        if item["candidate_id"] == unknown["candidate_id"]
    )["blocked_reason"]
    result = bulk.apply_plan(api, output / "plan.json", concurrency=1)
    assert result["counts"] == {"completed": 1, "blocked": 1}
    assert all(previous["candidate_id"] not in call[1] for call in api.posts)


def test_incremental_apply_keeps_reviewing_after_own_memory_governance_changes(
    tmp_path,
):
    class GovernanceChanges(IncrementalAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/review"):
                self.quick_overrides["blocked_reason"] = (
                    "memory_snapshot_changed_or_expired"
                )
            return result

    api = GovernanceChanges(3)
    path = plan_for(api, tmp_path)
    result = bulk.apply_plan(api, path, concurrency=1)
    assert result["counts"] == {"completed": 3}
    assert len(api.posts) == 6
    assert bulk.apply_plan(api, path, concurrency=1)["counts"] == {"completed": 3}
    assert len(api.posts) == 6


@pytest.mark.parametrize("defect", ["version", "generation", "superseded"])
def test_incremental_apply_stops_when_source_changes_between_candidates(
    tmp_path, defect
):
    class SourceChanges(IncrementalAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/review"):
                if defect == "version":
                    self.round["version"] += 1
                elif defect == "generation":
                    self.generation = "GEN-RESTARTED"
                else:
                    self.round["superseded_by_round_id"] = "ROUND-REPLACEMENT"
            return result

    api = SourceChanges(2)
    path = plan_for(api, tmp_path)
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path, concurrency=1)
    assert len(api.posts) == 2
    state = json.loads((path.parent / "state.json").read_text(encoding="utf-8"))
    assert state["items"][candidate(1)["candidate_id"]]["status"] == "completed"
    assert api.candidates[candidate(2)["candidate_id"]]["status"] == "pending_review"


def test_incremental_retry_is_explicit_and_never_repeats_completed(tmp_path):
    api = IncrementalAPI(2)
    path = plan_for(api, tmp_path)
    failed = candidate(2)["candidate_id"]
    api.failures[f"/api/soc/memory/candidates/{failed}/lesson-draft"] = [
        bulk.ApiError(504, "Synthetic timeout")
    ]
    assert bulk.apply_plan(api, path, concurrency=1)["counts"] == {
        "completed": 1,
        "draft_failed": 1,
    }
    posts = len(api.posts)
    assert bulk.apply_plan(api, path, concurrency=1)["counts"] == {
        "completed": 1,
        "draft_failed": 1,
    }
    assert len(api.posts) == posts
    assert bulk.apply_plan(api, path, retry_failed=True, concurrency=1)["counts"] == {
        "completed": 2,
    }
    assert len(api.posts) == posts + 2


def test_incremental_apply_checks_source_again_after_drafting_before_review(tmp_path):
    class ChangesDuringDraft(IncrementalAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/lesson-draft"):
                self.round["version"] += 1
            return result

    api = ChangesDuringDraft()
    path = plan_for(api, tmp_path)
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path, concurrency=1)
    assert len(api.posts) == 1
    assert api.posts[0][1].endswith("/lesson-draft")
    assert api.candidates[candidate(1)["candidate_id"]]["status"] == "pending_review"


def test_incremental_apply_uses_eight_inflight_candidates(tmp_path):
    class ParallelAPI(IncrementalAPI):
        def __init__(self):
            super().__init__(9)
            self.lock = threading.Lock()
            self.release = threading.Event()
            self.eight_started = threading.Event()
            self.active_drafts = 0
            self.peak_drafts = 0

        def post(self, path, payload, idempotency_key=None):
            if not path.endswith("/lesson-draft"):
                return super().post(path, payload, idempotency_key)
            with self.lock:
                self.active_drafts += 1
                self.peak_drafts = max(self.peak_drafts, self.active_drafts)
                if self.active_drafts == 8:
                    self.eight_started.set()
            try:
                assert self.release.wait(10), "test never released parallel drafts"
                return super().post(path, payload, idempotency_key)
            finally:
                with self.lock:
                    self.active_drafts -= 1

    api = ParallelAPI()
    path = plan_for(api, tmp_path)
    with ThreadPoolExecutor(max_workers=1) as runner:
        future = runner.submit(bulk.apply_plan, api, path, concurrency=8)
        try:
            assert api.eight_started.wait(5), "eight review pipelines did not start"
            assert api.peak_drafts == 8
        finally:
            api.release.set()
        assert future.result(timeout=10)["counts"] == {"completed": 9}
    assert api.peak_drafts == 8
    assert len(api.posts) == 18


@pytest.mark.parametrize("concurrency", [0, 9, True, 1.5])
def test_incremental_apply_rejects_invalid_concurrency_before_posts(
    tmp_path, concurrency
):
    api = IncrementalAPI()
    path = plan_for(api, tmp_path)
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path, concurrency=concurrency)
    assert not api.posts


def test_local_status_counts_pending_blocked_without_state_and_makes_no_api(
    tmp_path, monkeypatch, capsys
):
    api = IncrementalAPI(2)
    unknown = api.candidates[candidate(2)["candidate_id"]]
    unknown["scope_view"]["required_details"] = {}
    unknown["scope_view"]["unresolved_fingerprint_keys"] = ["behavior_fingerprint"]
    path = plan_for(api, tmp_path)
    before = {
        entry.name: entry.read_bytes()
        for entry in path.parent.iterdir()
        if entry.is_file()
    }
    monkeypatch.setattr(
        bulk,
        "ApiClient",
        lambda *args, **kwargs: pytest.fail("status must not construct an HTTP client"),
    )
    assert bulk.review_status(path)["counts"] == {"pending": 1, "blocked": 1}
    assert bulk.main(["status", "--plan", str(path)]) == 0
    assert "blocked" in capsys.readouterr().out
    assert before == {
        entry.name: entry.read_bytes()
        for entry in path.parent.iterdir()
        if entry.is_file()
    }


def test_local_status_reads_completed_receipts_without_changing_files(tmp_path):
    api = IncrementalAPI()
    path = plan_for(api, tmp_path)
    bulk.apply_plan(api, path)
    before = {
        entry.name: entry.read_bytes()
        for entry in path.parent.iterdir()
        if entry.is_file()
    }
    calls = len(api.calls)
    assert bulk.review_status(path)["counts"] == {"completed": 1}
    assert len(api.calls) == calls
    assert before == {
        entry.name: entry.read_bytes()
        for entry in path.parent.iterdir()
        if entry.is_file()
    }


def test_local_status_rejects_state_for_another_plan(tmp_path):
    api = IncrementalAPI()
    path = plan_for(api, tmp_path)
    (path.parent / "state.json").write_text(
        json.dumps(
            {
                "plan_hash": "0" * 64,
                "items": {},
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(bulk.BulkReviewError):
        bulk.review_status(path)
