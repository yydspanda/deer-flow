"""Current pending review never requires an operator to resolve round IDs."""

from copy import deepcopy
import json
from urllib.parse import urlsplit

import pytest

from scripts import soc_bulk_review_learning as bulk
from scripts.test_soc_bulk_review_learning import BASE, ExplicitExperimentAPI, candidate


class PendingAPI(ExplicitExperimentAPI):
    def __init__(self, count=2):
        super().__init__(count)
        self.generation = "GEN-CURRENT"
        self.validation_completed = 8
        self.batch_overrides = {}
        self.quick_overrides = {
            "completed": 8,
            "remaining": 2,
            "active": 1,
            "running": True,
        }

    def get(self, path):
        if urlsplit(path).path == BASE + "/batches/learning":
            self.calls.append(("GET", path))
            return {
                "schema_version": "soc.corpus_batch_status.v1",
                "batch": "learning",
                "experiment_id": self.experiment_id,
                "plan_id": self.plan_id,
                "learning_generation": self.generation,
                **self.batch_overrides,
            }
        return super().get(path)


def plan_for(api, tmp_path, expected_count=None):
    folder = tmp_path / "pending"
    plan = bulk.build_plan(api, folder, expected_count, phase="pending")
    return folder / "plan.json", plan


def test_current_pending_works_without_completed_learning_or_report(tmp_path):
    api = PendingAPI()
    path, plan = plan_for(api, tmp_path)
    assert plan["schema_version"] == "soc.dev_learning_bulk_review_plan.v4"
    assert plan["review_phase"] == "pending"
    assert plan["current_scope"] == {
        "experiment_id": api.experiment_id,
        "plan_id": api.plan_id,
        "learning_generation": api.generation,
    }
    assert plan["expected_count"] == 2
    assert not api.posts
    result = bulk.apply_plan(api, path)
    assert result["counts"] == {"completed": 2}
    reviews = [call for call in api.posts if call[1].endswith("/review")]
    for call in reviews:
        payload = call[2]
        assert "当前共享待审" in payload["reason"]
        assert "第一批" not in payload["reason"]
        assert payload["metadata"]["review_phase"] == "pending"
        assert payload["metadata"]["learning_generation"] == api.generation
        assert "batch" not in payload["metadata"]
        assert "source_round_id" not in payload["metadata"]


@pytest.mark.parametrize("generation", [None, "GEN-SECOND"])
def test_empty_pending_plan_is_successful_noop(tmp_path, generation, capsys):
    api = PendingAPI(0)
    api.generation = generation
    path, plan = plan_for(api, tmp_path)
    assert plan["items"] == []
    assert bulk.apply_plan(api, path)["counts"] == {}
    assert bulk.review_status(path)["candidate_count"] == 0
    assert "没有待审核经验" in capsys.readouterr().out
    assert not api.posts


@pytest.mark.parametrize("change", ["generation", "experiment", "plan"])
def test_scope_change_after_plan_stops_before_mutation(tmp_path, change):
    api = PendingAPI()
    path, _ = plan_for(api, tmp_path)
    if change == "generation":
        api.generation = "GEN-RESTART"
    elif change == "experiment":
        api.experiment_id = "EXP-ANOTHER"
    else:
        api.plan_id = "e" * 64
    with pytest.raises(bulk.BulkReviewError, match="scope|Scope"):
        bulk.apply_plan(api, path)
    assert not api.posts


def test_generation_change_during_draft_does_not_review_or_start_more(tmp_path):
    class RestartDuringDraft(PendingAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/lesson-draft"):
                self.generation = "GEN-RESTART"
            return result

    api = RestartDuringDraft()
    path, _ = plan_for(api, tmp_path)
    with pytest.raises(bulk.BulkReviewError, match="scope|Scope"):
        bulk.apply_plan(api, path, concurrency=1)
    assert len(api.posts) == 1
    assert api.posts[0][1].endswith("/lesson-draft")


def test_new_pending_candidates_do_not_expand_or_block_frozen_plan(tmp_path):
    api = PendingAPI(1)
    path, _ = plan_for(api, tmp_path)
    added = candidate(2)
    api.candidates[added["candidate_id"]] = added
    assert bulk.apply_plan(api, path)["counts"] == {"completed": 1}
    assert api.candidates[added["candidate_id"]]["status"] == "pending_review"
    before = len(api.posts)
    assert bulk.apply_plan(api, path)["counts"] == {"completed": 1}
    assert len(api.posts) == before


def test_candidate_change_after_draft_blocks_review(tmp_path):
    class ChangeDuringDraft(PendingAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/lesson-draft"):
                next(iter(self.candidates.values()))["content"] = "Changed after plan"
            return result

    api = ChangeDuringDraft(1)
    path, _ = plan_for(api, tmp_path)
    assert bulk.apply_plan(api, path)["counts"] == {"blocked": 1}
    assert len(api.posts) == 1


def test_explicit_retry_keeps_completed_and_default_core_scope(tmp_path):
    api = PendingAPI()
    cid = next(iter(api.candidates))
    api.failures[f"{bulk.MEMORY}/{cid}/lesson-draft"] = [
        bulk.ApiError(504, "Synthetic timeout")
    ]
    path, plan = plan_for(api, tmp_path)
    first = bulk.apply_plan(api, path, concurrency=1)
    assert first["counts"] == {"draft_failed": 1, "completed": 1}
    second = bulk.apply_plan(api, path, retry_failed=True, concurrency=2)
    assert second["counts"] == {"completed": 2}
    assert len([call for call in api.posts if call[1].endswith("/review")]) == 2
    for call in api.posts:
        if call[1].endswith("/review"):
            frozen = next(
                item for item in plan["items"] if item["candidate_id"] in call[1]
            )
            assert call[2]["record_applicability"] == frozen["record_applicability"]


@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "other"},
        {"environment": "production"},
        {"fact_source_experiment_id": "EXP-FACTS"},
    ],
)
def test_wrong_experiment_scope_rejected(tmp_path, override):
    api = PendingAPI()
    api.detail_overrides = override
    with pytest.raises(bulk.BulkReviewError):
        plan_for(api, tmp_path)
    assert not api.posts


def test_count_is_optional_only_for_pending_and_mismatch_is_rejected(tmp_path):
    api = PendingAPI()
    with pytest.raises(bulk.BulkReviewError, match="count"):
        plan_for(api, tmp_path, expected_count=1)
    with pytest.raises(bulk.BulkReviewError, match="positive"):
        bulk.build_plan(api, tmp_path / "initial", None)


def test_pending_plan_cannot_be_reinterpreted_as_legacy_or_other_scope(tmp_path):
    api = PendingAPI()
    path, plan = plan_for(api, tmp_path)
    changed = deepcopy(plan)
    changed["current_scope"]["learning_generation"] = "GEN-OTHER"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path)
    assert not api.posts
