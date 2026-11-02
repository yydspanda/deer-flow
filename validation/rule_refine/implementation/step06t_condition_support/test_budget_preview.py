"""Synthetic-only tests of complete input sizing and immutable source guards."""

import copy
import hashlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

import budget_preview

HERE = Path(__file__).resolve().parent
SOURCE_FILE = HERE.parent / "step06r_historical_review/materials.py"
SOURCE = SOURCE_FILE.read_bytes()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


materials = load("budget_preview_old_materials", SOURCE_FILE)
inventory = load(
    "budget_preview_inventory",
    HERE.parent / "step06o_ingress_readiness/materials_check.py",
)
review = load(
    "budget_preview_old_review", HERE.parent / "step06r_historical_review/review.py"
)
analysis = SimpleNamespace(SYSTEM="人工测试系统说明。" + review.SAMPLE_SENTENCE)


class Rules:
    def __init__(self):
        self.rule = {
            "company_code": "SYNTHETIC",
            "topic": materials.TARGET[0],
            "rule_code": materials.TARGET[1],
            "rule_name": "人工规则",
            "rule_config_version": "synthetic-version",
            "source": {"kind": "synthetic_fixture"},
            "configuration": {"terms": [{"condition": "完整原条件"}]},
        }

    def get_rule(self, code, *, topic):
        if (topic, code) != materials.TARGET:
            raise ValueError("wrong_fixture_target")
        return self.rule


def sized_log(size):
    overhead = len(materials.encode({"value": ""}))
    return {"value": "x" * (size - overhead)}


def payload(identity, logs):
    return {
        "alert": {
            "alertId": identity,
            "hitLog": [
                {
                    "topic": materials.TARGET[0],
                    "ruleCode": materials.TARGET[1],
                    "zeusRawLogs": logs,
                }
            ],
        },
        "relatedAlertList": [],
    }


def frame(rows):
    return pd.DataFrame({"alert_full_data": rows})


class BudgetPreviewTests(unittest.TestCase):
    def preview(self, material, rules=None, source=SOURCE, **kwargs):
        return budget_preview.build_preview(
            material,
            rules or Rules(),
            inventory,
            source,
            analysis=analysis,
            review=review,
            **kwargs,
        )

    def test_no_eight_or_sixteen_kib_log_cutoff_remains(self):
        logs = [sized_log(size) for size in (8192, 8193, 16385)]
        new, summary = self.preview(frame([payload("one", logs)]))
        self.assertEqual(summary["old"]["eligible_logs"], 1)
        self.assertEqual(summary["old"]["invalid_or_oversized_logs"], 2)
        self.assertEqual(summary["new"]["eligible_logs"], 3)
        self.assertEqual(summary["new"]["invalid_logs"], 0)
        self.assertNotIn("invalid_or_oversized_logs", new["coverage"])
        self.assertEqual(new["samples"][0]["raw_logs"], logs)
        self.assertEqual(new["schema_version"], "sec_know.historical_rule_context.v2")

    def test_one_hundred_kib_whole_log_fits_default_input_budget(self):
        log = sized_log(100 * 1024)
        new, summary = self.preview(frame([payload("large", [log])]))
        self.assertEqual(new["samples"][0]["raw_logs"], [log])
        self.assertLessEqual(summary["new"]["actual_input_bytes"], 128 * 1024)
        self.assertEqual(summary["max_input_kib"], 128)
        self.assertFalse(summary["input_budget_is_model_limit"])

    def test_total_request_budget_still_omits_eligible_alerts(self):
        new, summary = self.preview(
            frame([payload(str(i), [sized_log(10286)]) for i in range(50)])
        )
        self.assertEqual(summary["new"]["eligible_alerts"], 50)
        self.assertLess(summary["new"]["included_alerts"], 50)
        self.assertGreater(summary["new"]["budget_omitted_logs"], 0)
        self.assertLessEqual(summary["new"]["actual_input_bytes"], 128 * 1024)
        request = budget_preview.build_request(new, analysis, review, 128)
        self.assertEqual(
            request["input_bytes"], len(materials.encode(request["messages"]))
        )
        self.assertEqual(request["input_bytes"], summary["new"]["actual_input_bytes"])
        self.assertTrue(summary["eligibility_is_not_model_coverage"])
        self.assertFalse(summary["full_source_coverage_asserted"])

    def test_json_escaping_counts_and_tail_log_is_removed_whole(self):
        log = {"escaped": "\\" * 13000}
        new, summary = self.preview(
            frame([payload("one", [log]), payload("two", [log])]), max_input_kib=64
        )
        self.assertEqual(summary["new"]["tail_trimmed_logs"], 1)
        self.assertEqual(summary["new"]["tail_trimmed_alerts"], 1)
        self.assertEqual(new["samples"], [{"alert_id": "one", "raw_logs": [log]}])
        self.assertGreater(
            summary["new"]["actual_input_bytes"], summary["new"]["context_bytes"]
        )
        self.assertEqual(new["coverage"]["omitted_logs"], 1)

    def test_budget_removal_is_not_reported_as_invalid_log(self):
        new, summary = self.preview(
            frame([payload("too-large-for-input", [sized_log(150 * 1024)])]),
            max_input_kib=64,
        )
        self.assertEqual(summary["new"]["eligible_logs"], 1)
        self.assertEqual(summary["new"]["invalid_logs"], 0)
        self.assertEqual(summary["new"]["budget_omitted_logs"], 1)
        self.assertEqual(new["samples"], [])
        self.assertEqual(summary["status"], "no_usable_samples_within_input_budget")
        with self.assertRaisesRegex(ValueError, "no_usable_samples"):
            budget_preview.build_request(new, analysis, review)

    def test_same_order_twenty_alert_and_three_log_limits_are_preserved(self):
        material = frame(
            [
                payload(str(i), [{"position": str(j)} for j in range(5)])
                for i in range(25)
            ]
        )
        old = materials.build_context(material, Rules(), inventory)
        new, summary = self.preview(material)
        self.assertEqual(new["samples"], old["samples"])
        self.assertEqual(
            [sample["alert_id"] for sample in new["samples"]],
            [str(i) for i in range(20)],
        )
        self.assertTrue(all(len(sample["raw_logs"]) == 3 for sample in new["samples"]))
        self.assertEqual(summary["new"]["budget_omitted_logs"], 0)
        self.assertGreater(summary["new"]["omitted_logs"], 0)

    def test_input_values_and_original_source_file_remain_unchanged(self):
        rows = [payload("first", [sized_log(10000)])]
        before = copy.deepcopy(rows)
        rules = Rules()
        rule_before = copy.deepcopy(rules.rule)
        new, summary = self.preview(frame(rows), rules)
        self.assertEqual(rows, before)
        self.assertEqual(rules.rule, rule_before)
        self.assertEqual(SOURCE_FILE.read_bytes(), SOURCE)
        self.assertEqual(
            summary["old_source_sha256"], hashlib.sha256(SOURCE).hexdigest()
        )
        self.assertNotEqual(
            summary["old_source_sha256"], summary["preview_source_sha256"]
        )
        new["samples"][0]["raw_logs"][0]["value"] = "different"
        self.assertEqual(rows, before)

    def test_unknown_source_is_rejected_before_compilation_or_execution(self):
        for source in (
            SOURCE + b"\n",
            b"raise AssertionError('MUST NOT EXECUTE')",
            b"import os; os.environ.clear()",
            b"\xff",
        ):
            with (
                self.subTest(length=len(source)),
                patch(
                    "builtins.compile",
                    side_effect=AssertionError("must not compile unknown source"),
                ),
                patch(
                    "builtins.exec",
                    side_effect=AssertionError("must not execute unknown source"),
                ),
            ):
                with self.assertRaisesRegex(
                    ValueError, "projection_source_sha256_mismatch"
                ):
                    self.preview(None, source=source)

    def test_invalid_source_types_and_unbounded_source_are_rejected(self):
        for source in (None, SOURCE_FILE, {}, b"x" * (64 * 1024 + 1)):
            with self.subTest(source_type=type(source).__name__):
                with self.assertRaisesRegex(
                    ValueError, "reviewed_projection_source_required"
                ):
                    self.preview(None, source=source)

    def test_allowed_input_budget_range_and_strict_type(self):
        material = frame([payload("one", [{"value": "x"}])])
        for limit in (64, 128, 512):
            with self.subTest(limit=limit):
                new, summary = self.preview(material, max_input_kib=limit)
                self.assertEqual(summary["max_input_bytes"], limit * 1024)
                self.assertLessEqual(
                    budget_preview.build_request(new, analysis, review, limit)[
                        "input_bytes"
                    ],
                    limit * 1024,
                )
        for limit in (63, 513, True, 128.0, "128"):
            with self.subTest(limit=limit):
                with self.assertRaisesRegex(
                    ValueError, "max_input_kib_must_be_integer_64_to_512"
                ):
                    self.preview(material, max_input_kib=limit)

    def test_build_request_uses_new_version_and_rejects_oversize_messages(self):
        material = frame([payload("one", [sized_log(100 * 1024)])])
        new, summary = self.preview(material)
        request = budget_preview.build_request(new, analysis, review, 128)
        self.assertNotEqual(request["prompt_version"], review.PROMPT_VERSION)
        self.assertEqual(
            request["prompt_sha256"],
            hashlib.sha256(materials.encode(request["messages"])).hexdigest(),
        )
        old = materials.build_context(material, Rules(), inventory)
        old_request = budget_preview._request(old, analysis, review)
        self.assertEqual(
            summary["old"]["actual_input_bytes"],
            len(materials.encode(old_request["messages"])),
        )
        with self.assertRaisesRegex(ValueError, "historical_request_over_budget"):
            budget_preview.build_request(new, analysis, review, 64)

    def test_text_source_and_summary_remain_value_free(self):
        material = frame(
            [payload("PRIVATE-IDENTITY", [{"private-key": "PRIVATE-LOG-VALUE"}])]
        )
        first = self.preview(material)
        second = self.preview(material, source=SOURCE.decode("utf-8"))
        self.assertEqual(first, second)
        summary = json.dumps(first[1])
        self.assertNotIn("PRIVATE-", summary)
        self.assertNotIn("private-key", summary)
        self.assertEqual(first[1]["model_calls"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
