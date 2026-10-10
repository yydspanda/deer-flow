"""Isolated bulk-review tests; never use a real Gateway, model or database."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import csv
import io
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import stat
import threading
from urllib.parse import parse_qs, urlsplit

import pytest

from scripts import soc_bulk_review_learning as bulk

BASE = "/api/soc/dev/corpus-workbench"
EXPERIMENT = "EXP-TEST-LEARNING"
CORE = ["observed_process:sh", "command_operation:change_sudoers"]


def candidate(number=1):
    cid = f"MC-{number:012d}"
    return {
        "schema_version": "soc.memory_candidate.v1",
        "decision_impact": "detection_decision",
        "candidate_id": cid,
        "status": "pending_review",
        "candidate_type": "detection_lesson",
        "tenant_id": "pingan",
        "summary": "Synthetic candidate for bulk review",
        "content": "Synthetic alert evidence only",
        "updated_at": "2026-10-10T01:00:00+00:00",
        "source": {"source_id": f"sample-{number}", "run_id": f"RUN-{number}"},
        "metadata": {},
        "facets": {"environment": ["dev-corpus-eval"], "behavior_component_core": CORE},
        "applicability": {
            "schema_version": "soc.memory_applicability.v1",
            "policy_version": "soc.memory_applicability_policy.v4",
            "profile_id": "pingan.soc",
            "profile_version": "13",
            "feature_schema_version": "pingan.soc.memory_features.v11",
            "required_facets": {
                "environment": ["dev-corpus-eval"],
                "detection_key": ["test:detector"],
                "behavior_fingerprint": [f"fingerprint-{number}"],
                "behavior_strength": ["strong"],
            },
            "optional_facets": {"entity": ["ip:192.0.2.1"]},
            "excluded_facets": {"category": ["excluded-category"]},
            "selected_behavior_components": CORE.copy(),
            "covered_behavior_components": CORE.copy(),
            "reuse_conditions": [],
            "minimum_optional_matches": 0,
            "minimum_strong_anchor_matches": 1,
            "context_only_required_facet_keys": [],
            "context_only_missing_facet_keys": [],
            "context_only_similarity_facet_keys": [],
        },
        "scope_view": {
            "schema_version": "soc.memory_scope_view.v1",
            "required_details": {
                "behavior_fingerprint": {"behavior_component": CORE.copy()}
            },
            "unresolved_fingerprint_keys": [],
            "options": [
                {
                    "key": "entity",
                    "values": ["ip:192.0.2.1"],
                    "kind": "additional",
                    "covered_values": [],
                }
            ],
        },
    }


def draft(candidate_id):
    rationale = "Operator selected false positive for this DEV evaluation."
    return {
        "schema_version": "soc.memory_business_lesson_draft.v1",
        "candidate_id": candidate_id,
        "reviewer_verdict": "false_positive",
        "lesson": {
            "schema_version": "soc.memory_business_lesson.v2",
            "detection_scenario": "Synthetic command operation",
            "observed_event": "Synthetic change to a sudoers file",
            "conclusion": "Operator reviewed this synthetic event as false positive.",
            "business_rationale": [rationale],
            "applicability_conditions": [
                "All approved technical conditions must match."
            ],
            "generalization_boundaries": [
                "Do not expand beyond reviewed technical conditions."
            ],
            "invalidation_conditions": [
                "New unreviewed behavior requires another review."
            ],
            "handling_guidance": [
                "Reuse only within the reviewed technical conditions."
            ],
        },
        "supporting_source_refs": ["D-001"],
        "rationale_sources": [{"statement": rationale, "source_refs": ["D-001"]}],
        "source_catalog": [
            {
                "source_ref": "D-001",
                "source_kind": "reviewer_verdict",
                "label": "Operator verdict",
                "value": "false_positive",
            }
        ],
        "uncertainties": [],
        "provenance": {
            "generator_id": "fixture",
            "model_name": "fake",
            "prompt_version": "fixture-v1",
            "prompt_hash": "a" * 64,
            "response_hash": "b" * 64,
        },
        "decision_impact": "none",
        "review_required": True,
        "persistence_performed": False,
        "generated_at": "2026-10-10T01:00:00+00:00",
    }


class FakeAPI:
    """In-memory endpoint double with exact request recording and fault injection."""

    url = "http://127.0.0.1:2026"

    def __init__(self, count=1):
        self.candidates = {
            item["candidate_id"]: item for item in map(candidate, range(1, count + 1))
        }
        self.calls = []
        self.failures = {}
        self.environment = "dev"
        self.quick_overrides = {}
        self.experiment_id = EXPERIMENT

    @property
    def posts(self):
        return [call for call in self.calls if call[0] == "POST"]

    def get(self, path):
        self.calls.append(("GET", path))
        parsed = urlsplit(path)
        query = parse_qs(parsed.query)
        route = parsed.path
        if route == BASE + "/quick-validation":
            batch = query.get("batch", ["learning"])[0]
            return {
                "experiment_id": self.experiment_id,
                "batch": batch,
                "scope": "all",
                "total": 2997 if batch == "learning" else 12284,
                "completed": 2997 if batch == "learning" else 0,
                "failed": 0,
                "active": 0,
                "remaining": 0 if batch == "learning" else 12284,
                "running": False,
                "manual_pending": False,
                "blocked_reason": None,
                "pending_candidates": sum(
                    c["status"] == "pending_review" for c in self.candidates.values()
                ),
                "items": [],
                **self.quick_overrides,
            }
        if route == BASE + "/experiments":
            return [
                {
                    "experiment_id": self.experiment_id,
                    "tenant_id": "pingan",
                    "environment": "dev-corpus-eval",
                    "plan_id": "a" * 64,
                    "source_identity": {},
                    "member_count": 15281,
                }
            ]
        if route == BASE + f"/experiments/{self.experiment_id}/candidates":
            rows = [
                {k: deepcopy(v) for k, v in c.items() if k != "scope_view"}
                for c in self.candidates.values()
                if c["status"] == "pending_review"
            ]
            offset = int(query.get("offset", [0])[0])
            limit = int(query.get("limit", [100])[0])
            return {
                "items": rows[offset : offset + limit],
                "total": len(rows),
                "limit": limit,
                "offset": offset,
            }
        if route.startswith("/api/soc/memory/candidates/"):
            return deepcopy(self.candidates[route.rsplit("/", 1)[-1]])
        if route == BASE:
            return {
                "safety": {
                    "environment": self.environment,
                    "database_backend": "sqlite",
                    "database_file": "soc_agent_dev.db",
                    "external_action_execution": False,
                    "memory_scope": "dev-corpus-eval",
                }
            }
        raise AssertionError(f"Unexpected GET {path}")

    def post(self, path, payload, idempotency_key=None):
        self.calls.append(("POST", path, deepcopy(payload), idempotency_key))
        failure = self.failures.get(path)
        if failure:
            if isinstance(failure, list):
                failure = failure.pop(0) if failure else None
            if failure:
                raise failure
        cid = path.split("/candidates/", 1)[1].split("/", 1)[0]
        if path.endswith("/lesson-draft"):
            return draft(cid)
        if path.endswith("/review"):
            self.candidates[cid]["status"] = "confirmed"
            return {
                "schema_version": "soc.memory_candidate_review_result.v1",
                "decision": "confirm",
                "candidate": deepcopy(self.candidates[cid]),
                "memory_record": {
                    "memory_id": "MEM-" + cid,
                    "source_candidate_id": cid,
                    "status": "confirmed",
                    "reviewed_verdict": "false_positive",
                    "retrieval_enabled": True,
                    "version": 1,
                    "decision_directive": {
                        "target_verdict": "false_positive",
                        "effect": "override",
                        "review_effect": "clear",
                    },
                },
            }
        raise AssertionError(f"Unexpected POST {path}")


def plan_for(api, tmp_path, count=1):
    output = tmp_path / "review plan"
    bulk.build_plan(api, output, expected_count=count)
    return output / "plan.json"


def test_plan_hash_is_stable_and_binds_all_values():
    assert bulk.canonical_hash({"b": 2, "a": 1}) == bulk.canonical_hash(
        {"a": 1, "b": 2}
    )
    assert bulk.canonical_hash({"a": 1}) != bulk.canonical_hash({"a": 2})


def test_plan_collects_all_117_candidates_without_posts(tmp_path):
    api = FakeAPI(117)
    path = plan_for(api, tmp_path, 117)
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert len(plan["items"]) == 117
    assert len({row["candidate_id"] for row in plan["items"]}) == 117
    assert plan["schema_version"] == "soc.dev_learning_bulk_review_plan.v2"
    assert plan["review_policy"] == {
        "review_after_days": 180,
        "validity_days": 365,
    }
    assert any("offset=100" in call[1] for call in api.calls)
    assert not api.posts
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_count_mismatch_cannot_mutate_or_publish_plan(tmp_path):
    api = FakeAPI(2)
    with pytest.raises(bulk.BulkReviewError):
        bulk.build_plan(api, tmp_path / "plan", expected_count=117)
    assert not api.posts
    assert not (tmp_path / "plan" / "plan.json").exists()


def test_core_defaults_preserve_machine_scope_and_optional_conditions():
    original = candidate()
    before = deepcopy(original)
    result = bulk.default_applicability(original)
    assert set(result["selected_behavior_components"]) == set(CORE)
    assert result["covered_behavior_components"] == CORE
    for field in (
        "required_facets",
        "optional_facets",
        "excluded_facets",
        "reuse_conditions",
    ):
        assert result[field] == original["applicability"][field]
    assert original == before


def test_default_selection_fills_all_verified_core_when_no_selection_saved():
    item = candidate()
    item["applicability"]["selected_behavior_components"] = None
    item["applicability"]["covered_behavior_components"] = None
    item["applicability"]["policy_version"] = "soc.memory_applicability_policy.v1"
    scope = bulk.default_applicability(item)
    assert set(scope["selected_behavior_components"]) == set(CORE)
    assert set(scope["covered_behavior_components"]) == set(CORE)
    assert scope["optional_facets"] == item["applicability"]["optional_facets"]
    assert scope["reuse_conditions"] == []


def test_saved_narrow_selection_and_reuse_conditions_are_preserved():
    item = candidate()
    item["applicability"]["selected_behavior_components"] = [CORE[0]]
    item["applicability"]["reuse_conditions"] = [
        {
            "condition_key": "role_entity:source_ip",
            "facet_key": "role_entity",
            "values": ["source_ip:192.0.2.1"],
        }
    ]
    scope = bulk.default_applicability(item)
    assert scope["selected_behavior_components"] == [CORE[0]]
    assert scope["covered_behavior_components"] == CORE
    assert scope["reuse_conditions"] == item["applicability"]["reuse_conditions"]


def test_unverified_core_is_recorded_as_blocked_not_fabricated(tmp_path):
    api = FakeAPI()
    item = next(iter(api.candidates.values()))
    item["scope_view"]["required_details"] = {}
    path = plan_for(api, tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    assert plan["items"][0]["blocked_reason"]
    bulk.apply_plan(api, path)
    assert not api.posts


@pytest.mark.parametrize(
    "field,value",
    [
        ("candidate_id", "MC-OTHER"),
        ("reviewer_verdict", "true_positive"),
        ("persistence_performed", True),
        ("decision_impact", "detection_decision"),
        ("review_required", False),
    ],
)
def test_draft_identity_verdict_and_no_write_contract(field, value):
    generated = draft("MC-TEST")
    generated[field] = value
    with pytest.raises(bulk.BulkReviewError):
        bulk.validate_draft("MC-TEST", generated)


def test_legacy_or_incomplete_business_lesson_is_not_approved():
    generated = draft("MC-TEST")
    generated["lesson"]["schema_version"] = "soc.memory_business_lesson.v1"
    with pytest.raises(bulk.BulkReviewError):
        bulk.validate_draft("MC-TEST", generated)
    generated = draft("MC-TEST")
    generated["lesson"]["business_rationale"] = []
    with pytest.raises(bulk.BulkReviewError):
        bulk.validate_draft("MC-TEST", generated)


def test_review_payload_uses_false_positive_exact_reuse_with_fixed_time():
    item = candidate()
    scope = bulk.default_applicability(item)
    generated = draft(item["candidate_id"])
    now = datetime(2026, 10, 10, 2, tzinfo=UTC)
    payload = bulk.review_payload(item, scope, generated, EXPERIMENT, now=now)
    assert payload["decision"] == "confirm"
    assert payload["confirmed_verdict"] == "false_positive"
    assert payload["record_applicability"] == scope
    assert payload["record_lesson"]["schema_version"] == "soc.memory_business_lesson.v2"
    assert payload["apply_to_future_matches"] is True
    assert payload["clear_review_on_match"] is True
    assert payload["activate_retrieval"] is True
    assert payload["activation_review_after_days"] == 180
    assert datetime.fromisoformat(payload["activation_valid_until"]) == now + timedelta(
        days=365
    )
    assert payload["metadata"]["individual_manual_review_claimed"] is False
    assert payload == bulk.review_payload(item, scope, generated, EXPERIMENT, now=now)


def test_new_review_window_respects_shorter_source_validity():
    item = candidate()
    now = datetime(2026, 10, 10, 2, tzinfo=UTC)
    item["validity"] = {"valid_until": (now + timedelta(days=90)).isoformat()}
    with pytest.raises(bulk.BulkReviewError, match="default review window"):
        bulk.review_payload(
            item,
            bulk.default_applicability(item),
            draft(item["candidate_id"]),
            EXPERIMENT,
            now=now,
        )


def test_legacy_plan_resumes_frozen_30_day_review_without_reapproving_completed(
    tmp_path,
):
    api = FakeAPI(2)
    path = plan_for(api, tmp_path, 2)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["schema_version"] = "soc.dev_learning_bulk_review_plan.v1"
    plan.pop("review_policy", None)
    path.write_text(json.dumps(plan), encoding="utf-8")
    original_plan_bytes = path.read_bytes()
    first, second = list(api.candidates)
    endpoint = f"/api/soc/memory/candidates/{second}/review"
    api.failures[endpoint] = bulk.ApiError(None, "response lost")
    result = bulk.apply_plan(api, path, concurrency=1)
    assert result["counts"] == {"completed": 1, "review_unknown": 1}
    review_calls = [call for call in api.posts if call[1].endswith("/review")]
    assert all(call[2]["activation_review_after_days"] == 30 for call in review_calls)
    expiry = datetime.fromisoformat(review_calls[-1][2]["activation_valid_until"])
    assert (
        datetime.now(UTC) + timedelta(days=59)
        < expiry
        < datetime.now(UTC) + timedelta(days=61)
    )
    state_before = json.loads((path.parent / "state.json").read_text(encoding="utf-8"))
    del api.failures[endpoint]
    result = bulk.apply_plan(api, path, retry_failed=True, concurrency=1)
    assert result["counts"] == {"completed": 2}
    assert api.posts[-1] == review_calls[-1]
    assert len(api.posts) == 5
    assert path.read_bytes() == original_plan_bytes
    state_after = json.loads((path.parent / "state.json").read_text(encoding="utf-8"))
    assert state_after["items"][first] == state_before["items"][first]


@pytest.mark.parametrize(
    ("schema", "policy"),
    [
        (
            "soc.dev_learning_bulk_review_plan.v1",
            {"review_after_days": 180, "validity_days": 365},
        ),
        ("soc.dev_learning_bulk_review_plan.v2", None),
        (
            "soc.dev_learning_bulk_review_plan.v2",
            {"review_after_days": 30, "validity_days": 60},
        ),
        (
            "soc.dev_learning_bulk_review_plan.v2",
            {"review_after_days": 180, "validity_days": 9999},
        ),
        (
            "soc.dev_learning_bulk_review_plan.v2",
            {"review_after_days": 180.0, "validity_days": 365},
        ),
    ],
)
def test_plan_rejects_changed_or_missing_review_policy(tmp_path, schema, policy):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    plan = json.loads(path.read_text(encoding="utf-8"))
    plan["schema_version"] = schema
    if policy is None:
        plan.pop("review_policy", None)
    else:
        plan["review_policy"] = policy
    path.write_text(json.dumps(plan), encoding="utf-8")
    with pytest.raises(bulk.BulkReviewError, match="review policy"):
        bulk.apply_plan(api, path)
    assert not api.posts


def test_review_payload_matches_actual_backend_command_contract(monkeypatch):
    """Validate the wire payload without constructing any service or Runtime."""
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    from soc_agent.contracts import SocMemoryCandidateReviewCommand

    item = candidate()
    payload = bulk.review_payload(
        item,
        bulk.default_applicability(item),
        draft(item["candidate_id"]),
        EXPERIMENT,
        now=datetime(2026, 10, 10, 2, tzinfo=UTC),
    )
    command = SocMemoryCandidateReviewCommand.model_validate(
        {"candidate_id": item["candidate_id"], **payload}
    )
    assert command.candidate_id == item["candidate_id"]
    assert command.confirmed_verdict.value == "false_positive"
    assert command.record_lesson.schema_version == "soc.memory_business_lesson.v2"
    assert command.record_applicability.schema_version == "soc.memory_applicability.v1"
    assert command.record_applicability.selected_behavior_components == sorted(CORE)
    assert command.apply_to_future_matches is True
    assert command.clear_review_on_match is True
    assert command.activate_retrieval is True
    assert command.replaces_memory_id is None
    assert command.expected_replaced_version is None
    assert command.restore_predecessor is False
    assert command.expected_predecessor_version is None


@pytest.mark.parametrize(
    "override", [{"active": 1}, {"running": True}, {"manual_pending": True}]
)
def test_active_learning_cannot_be_planned(tmp_path, override):
    api = FakeAPI()
    api.quick_overrides = override
    with pytest.raises(bulk.BulkReviewError):
        plan_for(api, tmp_path)
    assert not api.posts


def test_plan_requires_dev_runtime(tmp_path):
    api = FakeAPI()
    next(iter(api.candidates.values()))["applicability"]["required_facets"][
        "environment"
    ] = ["stg"]
    with pytest.raises(bulk.BulkReviewError):
        plan_for(api, tmp_path)
    assert not api.posts


def test_apply_blocks_changed_candidate_scope(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    item = next(iter(api.candidates.values()))
    item["applicability"]["required_facets"]["detection_key"] = ["changed:detector"]
    bulk.apply_plan(api, path)
    assert not api.posts


def test_apply_skips_already_confirmed_candidate(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    next(iter(api.candidates.values()))["status"] = "confirmed"
    bulk.apply_plan(api, path)
    assert not api.posts


def test_apply_rejects_another_host_or_experiment(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    api.url = "http://localhost:8090"
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path)
    assert not api.posts


def test_apply_generates_once_then_reviews_and_reuses_receipt(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    bulk.apply_plan(api, path)
    posts = api.posts
    assert [row[1].rsplit("/", 1)[-1] for row in posts] == ["lesson-draft", "review"]
    assert posts[0][2]["reviewer_verdict"] == "false_positive"
    assert set(posts[0][2]["selected_behavior_components"]) == set(CORE)
    assert not posts[0][2].get("promoted_facet_keys")
    assert not posts[0][2].get("promoted_facet_values")
    assert posts[1][3]
    bulk.apply_plan(api, path)
    assert api.posts == posts
    for name in ("plan.json", "state.json", "receipts.json", "receipts.csv"):
        assert stat.S_IMODE((path.parent / name).stat().st_mode) == 0o600


def test_unknown_draft_timeout_is_not_retried_implicitly(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    cid = next(iter(api.candidates))
    endpoint = f"/api/soc/memory/candidates/{cid}/lesson-draft"
    api.failures[endpoint] = bulk.ApiError(None, "timeout after submission")
    bulk.apply_plan(api, path)
    assert len(api.posts) == 1
    del api.failures[endpoint]
    bulk.apply_plan(api, path)
    assert len(api.posts) == 1
    bulk.apply_plan(api, path, retry_failed=True)
    assert [r[1].rsplit("/", 1)[-1] for r in api.posts] == [
        "lesson-draft",
        "lesson-draft",
        "review",
    ]


def test_review_retry_reuses_cached_draft_body_dates_and_idempotency_key(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    cid = next(iter(api.candidates))
    endpoint = f"/api/soc/memory/candidates/{cid}/review"
    api.failures[endpoint] = bulk.ApiError(None, "response lost")
    bulk.apply_plan(api, path)
    first = deepcopy(api.posts)
    assert len(first) == 2
    del api.failures[endpoint]
    bulk.apply_plan(api, path)
    assert len(api.posts) == 3
    assert api.posts[-1] == first[-1]


def test_conflicting_candidate_does_not_auto_replace_or_block_next(tmp_path):
    api = FakeAPI(2)
    path = plan_for(api, tmp_path, 2)
    first, second = list(api.candidates)
    api.failures[f"/api/soc/memory/candidates/{first}/review"] = bulk.ApiError(
        409, "overlapping reviewed scope"
    )
    bulk.apply_plan(api, path)
    assert api.candidates[first]["status"] == "pending_review"
    assert api.candidates[second]["status"] == "confirmed"
    review_calls = [r for r in api.posts if r[1].endswith("/review")]
    assert len(review_calls) == 2
    assert all(not r[2].get("replaces_memory_id") for r in review_calls)
    assert all(not r[2].get("expected_replaced_version") for r in review_calls)
    assert all(not r[2].get("restore_predecessor") for r in review_calls)
    assert all(not r[2].get("expected_predecessor_version") for r in review_calls)
    assert all("revision" not in r[1] for r in api.posts)


def test_auth_failure_stops_remaining_candidates(tmp_path):
    api = FakeAPI(2)
    path = plan_for(api, tmp_path, 2)
    first = next(iter(api.candidates))
    api.failures[f"/api/soc/memory/candidates/{first}/lesson-draft"] = bulk.ApiError(
        403, "reviewer authentication required"
    )
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path, concurrency=1)
    assert len(api.posts) == 1


def test_cli_apply_without_explicit_confirmation_never_constructs_api(
    tmp_path, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("API must not be created before confirmation")

    monkeypatch.setattr(bulk, "ApiClient", forbidden)
    try:
        result = bulk.main(["apply", "--plan", str(tmp_path / "plan.json")])
    except SystemExit as exc:
        result = exc.code
    assert result != 0


@pytest.mark.parametrize(
    "url",
    [
        "http://192.0.2.1:2026",
        "https://example.test",
        "http://localhost:2026/other",
        "http://user:password@localhost:2026",
    ],
)
def test_transport_rejects_nonlocal_or_ambiguous_targets(url):
    with pytest.raises(bulk.BulkReviewError):
        bulk.ApiClient(url)


def test_pagination_drift_prevents_plan_publication(tmp_path):
    class ChangingAPI(FakeAPI):
        def get(self, path):
            result = super().get(path)
            if "/candidates?" in path and "offset=100" in path:
                result["total"] += 1
            return result

    api = ChangingAPI(117)
    with pytest.raises(bulk.BulkReviewError):
        plan_for(api, tmp_path, 117)
    assert not api.posts
    assert not (tmp_path / "review plan" / "plan.json").exists()


def test_candidate_changed_after_draft_caches_draft_without_review(tmp_path):
    class ChangeAfterDraft(FakeAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/lesson-draft"):
                next(iter(self.candidates.values()))["summary"] = (
                    "Changed after model result"
                )
            return result

    api = ChangeAfterDraft()
    original = deepcopy(next(iter(api.candidates.values())))
    path = plan_for(api, tmp_path)
    bulk.apply_plan(api, path)
    assert len(api.posts) == 1
    api.candidates[original["candidate_id"]] = original
    bulk.apply_plan(api, path, retry_failed=True)
    assert [call[1].rsplit("/", 1)[-1] for call in api.posts] == [
        "lesson-draft",
        "review",
    ]


def test_redirect_is_rejected_before_any_followup_request(monkeypatch):
    class RedirectingOpener:
        def open(self, request, timeout):
            assert request.full_url == "http://127.0.0.1:2026/api/soc/test"
            return bulk._NoRedirect().redirect_request(
                request, None, 302, "Moved", {}, "http://192.0.2.99/collect"
            )

    monkeypatch.setattr(
        bulk.urllib.request, "build_opener", lambda *handlers: RedirectingOpener()
    )
    client = bulk.ApiClient("http://127.0.0.1:2026")
    with pytest.raises(bulk.ApiError) as error:
        client.get("/api/soc/test")
    assert error.value.status == 302


@pytest.mark.parametrize(
    ("method", "path", "expected_timeout"),
    [
        ("POST", "/api/soc/memory/candidates/MC-TEST/lesson-draft", 1810.0),
        ("POST", "/api/soc/memory/candidates/MC-TEST/review", 610.0),
        ("GET", "/api/soc/memory/candidates/MC-TEST", 610.0),
    ],
)
def test_synchronous_draft_wait_covers_proxy_budget(
    monkeypatch, method, path, expected_timeout
):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.method, timeout))
            return io.BytesIO(b"{}")

    monkeypatch.setattr(bulk.urllib.request, "build_opener", lambda *args: Opener())
    client = bulk.ApiClient("http://127.0.0.1:2026")
    assert client._request(method, path) == {}
    assert calls == [(method, expected_timeout)]


def test_non_json_gateway_timeout_is_identified_without_body_or_retry(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            raise bulk.urllib.error.HTTPError(
                request.full_url,
                504,
                "Gateway Timeout",
                {},
                io.BytesIO(b"private html"),
            )

    monkeypatch.setattr(bulk.urllib.request, "build_opener", lambda *args: Opener())
    client = bulk.ApiClient("http://127.0.0.1:2026")
    with pytest.raises(bulk.ApiError) as error:
        client.post("/api/soc/memory/candidates/MC-TEST/lesson-draft", {})
    assert error.value.status == 504
    assert "Gateway timeout" in error.value.detail
    assert "private html" not in error.value.detail
    assert len(calls) == 1


def test_plan_scope_cannot_be_edited_to_widen_reuse(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    saved = json.loads(path.read_text(encoding="utf-8"))
    saved["items"][0]["record_applicability"]["required_facets"].pop("detection_key")
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path)
    assert not api.posts


def test_experiment_drift_stops_before_mutation(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    api.experiment_id = "EXP-OTHER"
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path)
    assert not api.posts


def test_lost_review_response_leaves_confirmed_candidate_unverified_without_repost(
    tmp_path,
):
    class LostResponseAPI(FakeAPI):
        lost = False

        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/review") and not self.lost:
                self.lost = True
                raise bulk.ApiError(None, "response lost after successful commit")
            return result

    api = LostResponseAPI()
    path = plan_for(api, tmp_path)
    first = bulk.apply_plan(api, path)
    assert first["counts"] == {"review_unknown": 1}
    assert next(iter(api.candidates.values()))["status"] == "confirmed"
    original_requests = deepcopy(api.posts)
    second = bulk.apply_plan(api, path)
    assert second["counts"] == {"confirmed_unverified": 1}
    assert api.posts == original_requests
    third = bulk.apply_plan(api, path, retry_failed=True)
    assert third["counts"] == {"confirmed_unverified": 1}
    assert api.posts == original_requests


def test_manual_confirmation_after_unreceived_review_is_never_overwritten(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    cid = next(iter(api.candidates))
    endpoint = f"/api/soc/memory/candidates/{cid}/review"
    api.failures[endpoint] = bulk.ApiError(None, "connection failed before receipt")
    first = bulk.apply_plan(api, path)
    assert first["counts"] == {"review_unknown": 1}
    assert api.candidates[cid]["status"] == "pending_review"
    del api.failures[endpoint]
    api.candidates[cid].update(
        status="confirmed",
        reviewed_verdict="true_positive",
        reviewed_by="other-reviewer",
    )
    original_requests = deepcopy(api.posts)
    receipt = bulk.apply_plan(api, path, retry_failed=True)
    assert receipt["counts"] == {"confirmed_unverified": 1}
    assert api.posts == original_requests
    assert api.candidates[cid]["reviewed_verdict"] == "true_positive"
    bulk.apply_plan(api, path, retry_failed=True)
    assert api.posts == original_requests


def test_cached_review_cannot_be_changed_to_different_decision(tmp_path):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    cid = next(iter(api.candidates))
    endpoint = f"/api/soc/memory/candidates/{cid}/review"
    api.failures[endpoint] = bulk.ApiError(None, "response lost")
    bulk.apply_plan(api, path)
    before = deepcopy(api.posts)
    state_path = path.parent / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["items"][cid]["review_payload"]["confirmed_verdict"] = "true_positive"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    del api.failures[endpoint]
    bulk.apply_plan(api, path)
    assert api.posts == before


def test_next_candidate_is_not_reviewed_if_batch_starts_again(tmp_path):
    class ConcurrentBatchAPI(FakeAPI):
        def post(self, path, payload, idempotency_key=None):
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/review"):
                self.quick_overrides["active"] = 1
            return result

    api = ConcurrentBatchAPI(2)
    path = plan_for(api, tmp_path, 2)
    receipt = bulk.apply_plan(api, path, concurrency=1)
    assert receipt["counts"] == {"completed": 1, "blocked": 1}
    assert len(api.posts) == 2
    assert list(api.candidates.values())[1]["status"] == "pending_review"


def test_help_is_available_without_network_or_application_imports(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("--help must not construct an API client")

    monkeypatch.setattr(bulk, "ApiClient", forbidden)
    with pytest.raises(SystemExit) as result:
        bulk.main(["--help"])
    assert result.value.code == 0


def test_parallel_apply_uses_eight_rolling_slots_and_durable_checkpoints(
    tmp_path, monkeypatch
):
    class ParallelAPI(FakeAPI):
        def __init__(self):
            super().__init__(17)
            self.guard = threading.Lock()
            self.first_wave = threading.Barrier(8)
            self.replacement_started = threading.Event()
            self.active_candidates = set()
            self.maximum_active = 0
            self.completed = set()

        def post(self, path, payload, idempotency_key=None):
            cid = path.split("/candidates/", 1)[1].split("/", 1)[0]
            number = int(cid.removeprefix("MC-"))
            if path.endswith("/lesson-draft"):
                with self.guard:
                    self.active_candidates.add(cid)
                    self.maximum_active = max(
                        self.maximum_active, len(self.active_candidates)
                    )
                if number <= 8:
                    self.first_wave.wait(timeout=10)
                if number == 9:
                    self.replacement_started.set()
            elif number == 1:
                assert self.replacement_started.wait(10), (
                    "The ninth candidate must start while one initial slot is occupied"
                )
            result = super().post(path, payload, idempotency_key)
            if path.endswith("/review"):
                with self.guard:
                    self.active_candidates.remove(cid)
                    self.completed.add(cid)
            return result

    api = ParallelAPI()
    path = plan_for(api, tmp_path, 17)
    original_receipts = bulk._receipts
    writers = 0
    maximum_writers = 0
    snapshots = []
    writer_guard = threading.Lock()

    def checked_receipts(directory, plan, state):
        nonlocal writers, maximum_writers
        with writer_guard:
            writers += 1
            maximum_writers = max(maximum_writers, writers)
        try:
            result = original_receipts(directory, plan, state)
            saved_state = json.loads((directory / "state.json").read_text())
            saved_receipts = json.loads((directory / "receipts.json").read_text())
            with (directory / "receipts.csv").open(newline="") as stream:
                csv_rows = list(csv.DictReader(stream))
            states = {
                item["candidate_id"]: saved_state["items"]
                .get(item["candidate_id"], {})
                .get("status", "pending")
                for item in plan["items"]
            }
            assert {r["candidate_id"]: r["status"] for r in csv_rows} == states
            assert {
                r["candidate_id"]: r["status"] for r in saved_receipts["items"]
            } == states
            snapshots.append(saved_receipts)
            return result
        finally:
            with writer_guard:
                writers -= 1

    monkeypatch.setattr(bulk, "_receipts", checked_receipts)
    # Omitting concurrency verifies the public function's default, not just CLI parsing.
    result = bulk.apply_plan(api, path)
    assert result["counts"] == {"completed": 17}
    assert api.maximum_active == 8
    assert len(api.completed) == 17
    assert not api.active_candidates
    assert maximum_writers == 1
    assert snapshots[-1]["counts"] == {"completed": 17}
    assert len(api.posts) == 34
    assert len({r[3] for r in api.posts if r[1].endswith("/review")}) == 17
    before = deepcopy(api.posts)
    resumed = bulk.apply_plan(api, path)
    assert resumed["counts"] == {"completed": 17}
    assert api.posts == before
    for name in ("plan.json", "state.json", "receipts.json", "receipts.csv"):
        assert stat.S_IMODE((path.parent / name).stat().st_mode) == 0o600


def test_concurrency_one_preserves_serial_execution(tmp_path):
    api = FakeAPI(3)
    path = plan_for(api, tmp_path, 3)
    receipt = bulk.apply_plan(api, path, concurrency=1)
    assert receipt["counts"] == {"completed": 3}
    assert [r[1] for r in api.posts] == [
        f"/api/soc/memory/candidates/{cid}/{operation}"
        for cid in api.candidates
        for operation in ("lesson-draft", "review")
    ]


@pytest.mark.parametrize("concurrency", [0, 9])
def test_apply_rejects_concurrency_outside_one_to_eight(tmp_path, concurrency):
    api = FakeAPI()
    path = plan_for(api, tmp_path)
    with pytest.raises(bulk.BulkReviewError):
        bulk.apply_plan(api, path, concurrency=concurrency)
    assert not api.posts


@pytest.mark.parametrize("arguments, expected", [([], 8), (["--concurrency", "1"], 1)])
def test_cli_uses_default_eight_or_explicit_single_worker(
    tmp_path, monkeypatch, arguments, expected
):
    api = FakeAPI()
    applied = []
    monkeypatch.setattr(bulk, "ApiClient", lambda url: api)

    def apply(client, path, retry_failed=False, concurrency=None):
        applied.append((client, path, retry_failed, concurrency))
        return {"counts": {"completed": 117}}

    monkeypatch.setattr(bulk, "apply_plan", apply)
    path = str(tmp_path / "plan.json")
    result = bulk.main(
        ["apply", "--plan", path, "--confirm", "APPROVE-FALSE-POSITIVE", *arguments]
    )
    assert result == 0
    assert applied == [(api, path, False, expected)]
    assert not api.posts


@pytest.mark.parametrize("concurrency", ["0", "9"])
def test_cli_rejects_invalid_concurrency_without_api(
    tmp_path, monkeypatch, concurrency
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Invalid concurrency must not construct a client")

    monkeypatch.setattr(bulk, "ApiClient", forbidden)
    with pytest.raises(SystemExit) as result:
        bulk.main(
            [
                "apply",
                "--plan",
                str(tmp_path / "plan.json"),
                "--confirm",
                "APPROVE-FALSE-POSITIVE",
                "--concurrency",
                concurrency,
            ]
        )
    assert result.value.code == 2


@pytest.mark.parametrize("status", [401, 403])
def test_parallel_auth_failure_stops_new_posts_and_preserves_inflight_results(
    tmp_path, monkeypatch, status
):
    fatal_observed = threading.Event()
    review_inflight = threading.Event()

    class AuthFailureAPI(FakeAPI):
        def __init__(self):
            super().__init__(12)
            self.first_wave = threading.Barrier(8)
            self.guard = threading.Lock()
            self.entered_posts = []
            self.posts_after_fatal = []
            cid = next(iter(self.candidates))
            self.failures[f"/api/soc/memory/candidates/{cid}/lesson-draft"] = (
                bulk.ApiError(status, "authentication changed during the first wave")
            )

        def post(self, path, payload, idempotency_key=None):
            with self.guard:
                self.entered_posts.append(path)
                if fatal_observed.is_set():
                    self.posts_after_fatal.append(path)
            cid = path.split("/candidates/", 1)[1].split("/", 1)[0]
            number = int(cid.removeprefix("MC-"))
            if path.endswith("/lesson-draft"):
                assert number <= 8, "No new candidate may start after the auth failure"
                self.first_wave.wait(timeout=10)
                if number == 1:
                    assert review_inflight.wait(10)
                elif number != 2:
                    assert fatal_observed.wait(10)
            else:
                assert number == 2, "Only the already-admitted review may finish"
                review_inflight.set()
                assert fatal_observed.wait(10)
            return super().post(path, payload, idempotency_key)

    api = AuthFailureAPI()
    path = plan_for(api, tmp_path, 12)
    original_receipts = bulk._receipts

    def observe_auth_checkpoint(directory, plan, state):
        result = original_receipts(directory, plan, state)
        if any(saved.get("error_code") == status for saved in state["items"].values()):
            fatal_observed.set()
        return result

    monkeypatch.setattr(bulk, "_receipts", observe_auth_checkpoint)
    with pytest.raises(bulk.BulkReviewError, match="[Aa]uthentication"):
        bulk.apply_plan(api, path, concurrency=8)
    assert fatal_observed.is_set()
    assert not api.posts_after_fatal
    assert len(api.entered_posts) == 9
    receipt = json.loads((path.parent / "receipts.json").read_text())
    state = json.loads((path.parent / "state.json").read_text())
    ids = list(api.candidates)
    assert len(receipt["items"]) == 12
    assert sum(receipt["counts"].values()) == 12
    assert state["items"][ids[0]]["error_code"] == status
    assert state["items"][ids[1]]["status"] == "completed"
    assert state["items"][ids[1]]["memory_id"] == "MEM-" + ids[1]
    for cid in ids[2:8]:
        assert state["items"][cid]["draft"]["candidate_id"] == cid
        assert api.candidates[cid]["status"] == "pending_review"
    for cid in ids[8:]:
        assert state["items"].get(cid, {}).get("status", "pending") == "pending"
        assert api.candidates[cid]["status"] == "pending_review"


def test_interrupt_stops_dispatch_drains_eight_drafts_and_resumes_cached_work(
    tmp_path, monkeypatch
):
    first_wave_entered = threading.Event()
    release_inflight = threading.Event()

    class InterruptedAPI(FakeAPI):
        def __init__(self):
            super().__init__(12)
            self.first_attempt = True
            self.first_wave = threading.Barrier(8, action=first_wave_entered.set)
            self.entered_posts = []

        def post(self, path, payload, idempotency_key=None):
            self.entered_posts.append(path)
            if self.first_attempt:
                assert path.endswith("/lesson-draft")
                self.first_wave.wait(timeout=10)
                assert release_inflight.wait(10)
            return super().post(path, payload, idempotency_key)

    class ObservedExecutor(ThreadPoolExecutor):
        def shutdown(self, wait=True, *, cancel_futures=False):
            assert wait is True
            assert cancel_futures is True
            release_inflight.set()
            return super().shutdown(wait=wait, cancel_futures=cancel_futures)

    def interrupt_wait(*args, **kwargs):
        assert first_wave_entered.wait(10)
        raise KeyboardInterrupt

    api = InterruptedAPI()
    path = plan_for(api, tmp_path, 12)
    with monkeypatch.context() as patch:
        patch.setattr(bulk, "wait", interrupt_wait)
        patch.setattr(bulk, "ThreadPoolExecutor", ObservedExecutor)
        with pytest.raises(KeyboardInterrupt):
            bulk.apply_plan(api, path, concurrency=8)
    assert len(api.entered_posts) == 8
    assert len(api.posts) == 8
    assert all(call[1].endswith("/lesson-draft") for call in api.posts)
    receipt = json.loads((path.parent / "receipts.json").read_text())
    state = json.loads((path.parent / "state.json").read_text())
    assert receipt["counts"] == {"draft_ready": 8, "pending": 4}
    for cid in list(api.candidates)[:8]:
        assert state["items"][cid]["draft"]["candidate_id"] == cid
    api.first_attempt = False
    resumed = bulk.apply_plan(api, path)
    assert resumed["counts"] == {"completed": 12}
    assert len([r for r in api.posts if r[1].endswith("/lesson-draft")]) == 12
    assert len([r for r in api.posts if r[1].endswith("/review")]) == 12


def test_cli_reports_interrupt_as_130_without_retry(tmp_path, monkeypatch):
    api = FakeAPI()
    calls = []
    monkeypatch.setattr(bulk, "ApiClient", lambda url: api)

    def interrupted(*args, **kwargs):
        calls.append((args, kwargs))
        raise KeyboardInterrupt

    monkeypatch.setattr(bulk, "apply_plan", interrupted)
    result = bulk.main(
        [
            "apply",
            "--plan",
            str(tmp_path / "plan.json"),
            "--confirm",
            "APPROVE-FALSE-POSITIVE",
        ]
    )
    assert result == 130
    assert len(calls) == 1
    assert not api.posts
