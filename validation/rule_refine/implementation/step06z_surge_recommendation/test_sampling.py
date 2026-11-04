"""Whole-original and fresh-review sampling checks; no services or model calls."""

import copy
import importlib.util
import sys
import unittest
from pathlib import Path


HERE = Path(__file__).resolve().parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ai_review = load("ai_review", HERE / "ai_review.py")
sampling = load("sampling", HERE / "sampling.py")


class Budget:
    def __init__(self, tokens=16000):
        self.input_token_budget = tokens
        self.input_bytes = 32 * 1024 * 1024

    def audit(self):
        return {"context_capacity_verified": False, "model_output_token_limit": None}


def sample(index, *, prefix="now", size=1000):
    return {
        "alert_id": f"{prefix}-{index:03d}",
        "created_at": f"2026-11-02T10:{index // 60:02d}:{index % 60:02d}",
        "raw_logs": [
            {
                "vendor_specific_message": "routine" if index % 2 else "odd",
                "payload": "x" * size,
                "nested": {"message": str(index)},
            }
        ],
    }


def case(count=30, history=2, size=1000):
    return {
        "rule": {
            "topic": "synthetic-vendor",
            "rule_code": "SYNTHETIC",
            "configuration": {"terms": []},
        },
        "current_samples": [sample(index, size=size) for index in range(count)],
        "history_samples": [
            sample(index, prefix="old", size=300) for index in range(history)
        ],
        "coverage": {
            "current_alert_count": count,
            "history_selected_unique_alerts": history,
        },
    }


def draft(identity, *, request=None):
    return {
        "status": "proposal",
        "issue": "主要重复活动",
        "recommendation": "运营确认后排除",
        "sample_alert_ids": [identity],
        "filter": {
            "topic": "synthetic-vendor",
            "A": {
                "name": "vendor_specific_message",
                "operator": "contain",
                "value": "routine",
            },
            "expression": "A",
        },
        "sample_request": request,
    }


class SamplingTests(unittest.TestCase):
    def test_spread_covers_timeline_instead_of_prefix_without_repetition(self):
        ordered = list(range(64))
        result = list(sampling.spread(ordered))
        self.assertEqual(sorted(result), ordered)
        self.assertEqual(len(set(result)), 64)
        self.assertLess(min(result[:7]), 16)
        self.assertGreater(max(result[:7]), 48)

    def test_generation_preserves_complete_logs_and_case_unchanged(self):
        source = case()
        before = copy.deepcopy(source)
        selection = sampling.select(source, Budget())
        source_by_id = {s["alert_id"]: s for s in source["current_samples"]}
        self.assertGreater(len(selection["current_samples"]), 1)
        self.assertLess(len(selection["current_samples"]), len(source_by_id))
        for selected in selection["current_samples"]:
            self.assertEqual(
                selected["raw_logs"], source_by_id[selected["alert_id"]]["raw_logs"]
            )
        self.assertEqual(source, before)
        self.assertEqual(
            selection["request"]["context"]["history_samples"],
            source["history_samples"],
        )
        self.assertFalse(selection["selection"]["filter_replay_performed"])

    def test_review_preserves_cited_originals_and_adds_new_current(self):
        source, budget = case(), Budget()
        generation = sampling.select(source, budget)
        cited = generation["current_samples"][0]
        output = draft(cited["alert_id"])
        review = sampling.select(source, budget, draft=output, generation=generation)
        generation_ids = {s["alert_id"] for s in generation["current_samples"]}
        review_by_id = {s["alert_id"]: s for s in review["current_samples"]}
        self.assertEqual(review_by_id[cited["alert_id"]], cited)
        self.assertTrue(set(review_by_id) - generation_ids)
        self.assertEqual(set(review_by_id) & generation_ids, {cited["alert_id"]})
        self.assertEqual(review["history_samples"], generation["history_samples"])
        self.assertTrue(review["request"]["context"]["history_reused_from_generation"])

    def test_small_sources_still_reserve_fresh_current_for_review(self):
        source, budget = case(count=8, history=0, size=20), Budget(800000)
        generation = sampling.select(source, budget)
        output = draft(generation["current_samples"][0]["alert_id"])
        review = sampling.select(source, budget, draft=output, generation=generation)
        self.assertGreater(review["selection"]["new_current_count"], 0)

    def test_no_history_still_prepares_both_phases(self):
        source, budget = case(history=0), Budget()
        generation = sampling.select(source, budget)
        output = draft(generation["current_samples"][0]["alert_id"])
        review = sampling.select(source, budget, draft=output, generation=generation)
        self.assertEqual(review["history_samples"], [])
        self.assertEqual(review["selection"]["history_count"], 0)
        self.assertGreater(review["selection"]["new_current_count"], 0)

    def test_ai_request_is_dynamic_and_only_takes_examples(self):
        rows = [sample(index, size=20) for index in range(10)]
        requested = sampling.requested_examples(
            rows, {"field": "vendor_specific_message", "text": "routine"}, {"now-000"}
        )
        self.assertEqual(len(requested), 2)
        self.assertNotIn("now-000", {s["alert_id"] for s in requested})
        values = {s["raw_logs"][0]["vendor_specific_message"] for s in requested}
        self.assertEqual(values, {"routine", "odd"})
        self.assertTrue(all(set(row) == set(rows[0]) for row in requested))

    def test_ai_request_resolves_visible_nested_field(self):
        rows = [sample(index, size=20) for index in range(3)]
        requested = sampling.requested_examples(
            rows, {"field": "nested.message", "text": "1"}, set()
        )
        self.assertEqual(len(requested), 2)
        self.assertEqual(
            {s["raw_logs"][0]["nested"]["message"] == "1" for s in requested},
            {True, False},
        )

    def test_mixed_alert_follows_positive_example_with_all_original_logs(self):
        positive, mixed, later_negative = sample(1), sample(3), sample(4)
        mixed["raw_logs"].extend(
            [
                {"vendor_specific_message": "different activity", "details": "full"},
                {"unrelated_field": "also retained"},
            ]
        )
        rows = [positive, mixed, later_negative]
        before = copy.deepcopy(rows)
        request = {"field": "vendor_specific_message", "text": "routine"}
        selected = sampling.requested_examples(rows, request, set())
        self.assertEqual(
            [item["alert_id"] for item in selected], ["now-001", "now-003"]
        )
        self.assertEqual(selected[1]["raw_logs"], before[1]["raw_logs"])
        self.assertEqual(rows, before)
        excluded = sampling.requested_examples(rows, request, {"now-003"})
        self.assertEqual(
            [item["alert_id"] for item in excluded], ["now-001", "now-004"]
        )

    def test_single_mixed_alert_satisfies_both_examples_without_duplicate(self):
        mixed = sample(1)
        mixed["raw_logs"].append({"vendor_specific_message": "different activity"})
        later_negative = sample(2)
        selected = sampling.requested_examples(
            [mixed, later_negative],
            {"field": "vendor_specific_message", "text": "routine"},
            set(),
        )
        self.assertEqual([item["alert_id"] for item in selected], ["now-001"])
        self.assertEqual(selected[0]["raw_logs"], mixed["raw_logs"])

    def test_missing_or_nonstring_requested_fields_are_not_negative_examples(self):
        rows = [sample(index, size=20) for index in range(3)]
        rows[0]["raw_logs"] = [{"other": "value"}]
        rows[1]["raw_logs"] = [{"vendor_specific_message": 17}]
        result = sampling.requested_examples(
            rows, {"field": "vendor_specific_message", "text": "routine"}, set()
        )
        self.assertEqual([s["alert_id"] for s in result], ["now-002"])

    def test_history_budget_reduction_retains_whole_latest_originals(self):
        source = case(history=3)
        source["history_samples"][2]["raw_logs"][0]["payload"] = "H" * 30000
        selection = sampling.select(source, Budget())
        self.assertEqual(selection["history_samples"], source["history_samples"][:2])
        self.assertGreater(len(selection["current_samples"]), 0)

    def test_uncited_history_yields_space_when_no_current_original_fits(self):
        source = case(count=8, history=1, size=3000)
        source["history_samples"][0]["raw_logs"][0]["payload"] = "H" * 9100
        selection = sampling.select(source, Budget(16000))
        self.assertGreaterEqual(len(selection["current_samples"]), 1)
        self.assertEqual(selection["history_samples"], [])
        self.assertEqual(selection["selection"]["history_count"], 0)
        originals = {row["alert_id"]: row for row in source["current_samples"]}
        for selected in selection["current_samples"]:
            self.assertEqual(
                selected["raw_logs"], originals[selected["alert_id"]]["raw_logs"]
            )

    def test_cited_history_never_silently_removed_to_make_review_fit(self):
        source = case(count=4, history=1, size=30)
        source["history_samples"][0]["raw_logs"][0]["payload"] = "H" * 30000
        generation = {
            "current_samples": [source["current_samples"][0]],
            "history_samples": source["history_samples"],
        }
        output = draft("old-000")
        try:
            result = sampling.select(
                source, Budget(), draft=output, generation=generation
            )
        except ValueError:
            return
        self.assertIn("old-000", {s["alert_id"] for s in result["history_samples"]})

    def test_no_whole_current_fits_is_explicit_failure(self):
        with self.assertRaises(ValueError):
            sampling.select(case(count=2, history=0, size=50000), Budget())

    def test_unrelated_material_error_not_treated_as_budget(self):
        source = case()
        source["current_samples"][0]["raw_logs"] = "bad"
        with self.assertRaises(ValueError):
            sampling.select(source, Budget())


if __name__ == "__main__":
    unittest.main(verbosity=2)
