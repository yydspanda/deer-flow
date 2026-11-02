"""建议、材料与当前配置串联校验；人工回复及假配置提供器，无外部调用。"""

import copy
import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "_rule_refine_check_fixtures", Path(__file__).with_name("test_analysis.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
check = importlib.import_module(
    fixtures.fixtures.package.__name__ + ".proposal_check"
).check_proposal


def analyze(data, answer=None):
    return fixtures.analysis.analyze_materials(
        data,
        Mock(return_value=json.dumps(fixtures.draft() if answer is None else answer)),
    )


def loader(data):
    return Mock(
        return_value=Mock(get_rule=Mock(return_value=copy.deepcopy(data["rule"])))
    )


class ProposalCheckTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.materials()
        self.result = analyze(self.data)
        self.load = loader(self.data)

    def blocked(self, result, reason):
        self.assertEqual((result["status"], result["reason"]), ("blocked", reason))
        self.assertIsNone(result["recommendation"])

    def test_reviewable_preserves_id_uses_fresh_provider_and_does_not_mutate(self):
        before = copy.deepcopy((self.data, self.result))
        outcome = check(self.data, self.result, self.load)
        self.assertEqual(outcome["status"], "reviewable")
        self.assertEqual(outcome["recommendation"], self.result["recommendation"])
        self.assertEqual((self.data, self.result), before)
        self.assertIs(outcome["automatic_apply"], False)
        self.assertEqual(outcome["engine_compatibility"], "not_verified")
        self.assertEqual(
            [
                outcome[key]
                for key in ("response_source", "material_mode", "material_source")
            ],
            ["synthetic_reply", "demo", "synthetic_fixture"],
        )
        self.assertEqual(
            outcome["evaluation"]["counts"],
            {"matched": 1, "not_matched": 0, "unknown": 0},
        )
        self.load.assert_called_once_with()
        self.load.return_value.get_rule.assert_called_once_with(
            "DEMO_RULE", topic="demo-topic"
        )
        check(self.data, self.result, self.load)
        self.assertEqual(self.load.call_count, 2)

    def test_numeric_equal_and_large_log_survive_end_to_end(self):
        data = fixtures.materials()
        data["samples"][0]["raw_logs"][0].update(port=443, body="x" * (100 * 1024))
        answer = fixtures.draft()
        answer["filter"]["B"] = {"name": "port", "operator": "equal", "value": "443"}
        answer["filter"]["expression"] = "A && B"
        generated = analyze(data, answer)
        outcome = check(data, generated, loader(data))
        self.assertEqual(generated["status"], "proposal")
        self.assertEqual(outcome["status"], "reviewable")
        self.assertEqual(outcome["evaluation"]["counts"]["matched"], 1)
        self.assertEqual(outcome["coverage"]["omitted_log_count"], 0)
        self.assertFalse(outcome["automatic_apply"])
        self.assertEqual(outcome["engine_compatibility"], "not_verified")

    def test_twenty_large_alerts_can_be_saved_and_rechecked_with_original_store(self):
        rows = []
        for index in range(20):
            row = fixtures.fixtures.row(str(101 + index), "large-task-" + str(index))
            row["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["body"] = "x" * (
                110 * 1024
            )
            rows.append(row)
        data = fixtures.materials(rows)
        generated = analyze(data)
        self.assertEqual(generated["status"], "proposal")
        persistence = importlib.import_module(
            fixtures.fixtures.package.__name__ + ".saved_delivery"
        )
        with tempfile.TemporaryDirectory() as directory:
            store = persistence.LocalAnalysisStore(directory)
            saved = store.save(data, generated)
            bundle = store.load(saved["record_id"])
        self.assertEqual(bundle["materials"], data)
        self.assertEqual(bundle["analysis"], generated)
        checked = check(bundle["materials"], bundle["analysis"], loader(data))
        self.assertEqual(checked["status"], "reviewable")
        self.assertLessEqual(generated["input_bytes"], 128 * 1024)
        self.assertFalse(checked["automatic_apply"])

    def test_prompt_hash_version_and_provenance_mismatch_prevent_provider_lookup(self):
        for key, value in (
            ("prompt_sha256", "0" * 64),
            ("prompt_version", "old"),
            ("material_mode", "live"),
            ("material_source", "other"),
            ("rule_source", {"kind": "other"}),
        ):
            changed = {**self.result, key: value}
            with self.subTest(key=key):
                self.blocked(check(self.data, changed, self.load), "prompt_mismatch")
        self.data["samples"][0]["raw_logs"][0]["host"] = "changed.invalid"
        self.blocked(check(self.data, self.result, self.load), "prompt_mismatch")
        self.load.assert_not_called()

    def test_recommendation_identity_window_count_and_descriptions_are_bound(self):
        missing_id = copy.deepcopy(self.result)
        del missing_id["recommendation"]["recommendation_id"]
        self.blocked(check(self.data, missing_id, self.load), "recommendation_mismatch")
        mutations = [
            (key, "different")
            for key in (
                "company_code",
                "rule_code",
                "rule_name",
                "rule_config_version",
                "issue",
            )
        ]
        for key, value in mutations:
            changed = copy.deepcopy(self.result)
            changed["recommendation"][key] = value
            with self.subTest(key=key):
                self.blocked(
                    check(self.data, changed, self.load), "recommendation_mismatch"
                )
        for key, value in (
            ("alert_count", 1001),
            ("window_start", "2025-12-31T23:00:00+00:00"),
            ("sample_alert_ids", ["999"]),
        ):
            changed = copy.deepcopy(self.result)
            changed["recommendation"]["evidence"][key] = value
            self.blocked(
                check(self.data, changed, self.load), "recommendation_mismatch"
            )
        self.load.assert_not_called()

    def test_individually_supported_conditions_must_match_the_same_log(self):
        self.data["samples"][0]["raw_logs"] = [
            {"host": "synthetic.invalid", "path": "/other"},
            {"host": "other.invalid", "path": "/health"},
        ]
        answer = fixtures.draft()
        answer["filter"].update(
            expression="A && B",
            B={"name": "path", "value": "/health", "operator": "equal"},
        )
        result = analyze(self.data, answer)
        self.assertEqual(result["status"], "proposal")
        outcome = check(self.data, result, self.load)
        self.blocked(outcome, "cited_samples_not_supported")
        self.assertEqual(outcome["unsupported_sample_alert_ids"], ["101"])
        self.load.assert_not_called()

    def test_every_cited_sample_needs_a_definite_match(self):
        data = fixtures.materials(
            [fixtures.fixtures.row(), fixtures.fixtures.row("102", "task-b")]
        )
        data["samples"][1]["raw_logs"] = [{"host": "different.invalid"}]
        answer = {**fixtures.draft(), "sample_alert_ids": ["101", "102"]}
        outcome = check(data, analyze(data, answer), loader(data))
        self.blocked(outcome, "cited_samples_not_supported")
        self.assertEqual(outcome["unsupported_sample_alert_ids"], ["102"])

    def test_unknown_cited_sample_is_not_a_match(self):
        data = fixtures.materials(
            [fixtures.fixtures.row(), fixtures.fixtures.row("102", "task-b")]
        )
        data["samples"][0]["raw_logs"] = [{"other": "present"}]
        answer = {**fixtures.draft(), "sample_alert_ids": ["101", "102"]}
        outcome = check(data, analyze(data, answer), loader(data))
        self.blocked(outcome, "cited_samples_not_supported")
        self.assertEqual(outcome["evaluation"]["counts"]["unknown"], 1)

    def test_coverage_keeps_fully_omitted_and_unavailable_samples(self):
        data = fixtures.materials(
            [fixtures.fixtures.row(), fixtures.fixtures.row("102", "task-b")]
        )
        data["samples"][0]["raw_logs"] *= 4
        data["samples"][1]["raw_logs"] = [
            {"host": "synthetic.invalid", "large": "x" * (150 * 1024)}
        ]
        data.update(
            status="partial",
            requested_sample_count=3,
            issues=[{"code": "sample_unavailable", "alert_id": "103"}],
        )
        outcome = check(data, analyze(data), loader(data))
        self.assertEqual(outcome["status"], "reviewable")
        coverage = outcome["coverage"]
        self.assertEqual(
            [
                coverage[k]
                for k in (
                    "requested_alert_count",
                    "input_alert_count",
                    "projected_alert_count",
                    "unavailable_alert_count",
                )
            ],
            [3, 2, 1, 1],
        )
        self.assertEqual(
            [
                coverage[k]
                for k in ("input_log_count", "projected_log_count", "omitted_log_count")
            ],
            [5, 3, 2],
        )
        self.assertEqual(
            coverage["per_alert"][1],
            {
                "alert_id": "102",
                "input_logs": 1,
                "projected_logs": 0,
                "omitted_logs": 1,
            },
        )
        self.assertEqual(outcome["material_issues"], data["issues"])

    def test_configuration_version_or_content_change_blocks_even_if_version_reused(
        self,
    ):
        for key, value in (
            ("rule_config_version", "new-v2"),
            ("rule_name", "new-name"),
            ("configuration", {"terms": []}),
        ):
            fresh = loader(self.data)
            fresh.return_value.get_rule.return_value[key] = value
            with self.subTest(key=key):
                self.blocked(
                    check(self.data, self.result, fresh), "configuration_changed"
                )

    def test_configuration_source_and_identity_cannot_be_switched(self):
        for key, value in (
            ("company_code", "different"),
            ("topic", "different"),
            ("rule_code", "different"),
            ("source", {"kind": "other"}),
        ):
            fresh = loader(self.data)
            fresh.return_value.get_rule.return_value[key] = value
            with self.subTest(key=key):
                outcome = check(self.data, self.result, fresh)
                self.assertEqual(outcome["status"], "blocked")
                self.assertIsNone(outcome["recommendation"])

    def test_unrelated_csv_snapshot_metadata_change_is_allowed(self):
        self.data["rule"]["source"] = {"kind": "csv_snapshot", "file_sha256": "old"}
        fresh = loader(self.data)
        fresh.return_value.get_rule.return_value["source"]["file_sha256"] = "new"
        self.assertEqual(
            check(self.data, analyze(self.data), fresh)["status"], "reviewable"
        )

    def test_provider_failures_are_visible_without_exception_contents(self):
        common = fixtures.fixtures.common
        for error in (
            RuntimeError,
            common.RuleNotFoundError,
            common.AmbiguousRuleError,
        ):
            fresh = Mock(side_effect=error("SECRET provider body"))
            outcome = check(self.data, self.result, fresh)
            self.blocked(outcome, "configuration_unavailable")
            self.assertNotIn("SECRET", json.dumps(outcome))
            fresh = loader(self.data)
            fresh.return_value.get_rule.side_effect = error("SECRET lookup body")
            outcome = check(self.data, self.result, fresh)
            self.blocked(outcome, "configuration_unavailable")
            self.assertNotIn("SECRET", json.dumps(outcome))

    def test_nonproposal_and_empty_materials_do_not_lookup_configuration(self):
        for status in (
            "no_change",
            "insufficient_evidence",
            "needs_existing_condition_change",
        ):
            answer = {
                **fixtures.draft(),
                "status": status,
                "filter": None,
                "sample_alert_ids": [],
            }
            result = analyze(self.data, answer)
            self.assertEqual(
                check(self.data, result, self.load)["status"], "not_applicable"
            )
            self.blocked(
                check(self.data, {**result, "prompt_sha256": "wrong"}, self.load),
                "prompt_mismatch",
            )
        empty = fixtures.materials([])
        self.assertEqual(
            check(empty, analyze(empty), self.load)["status"], "not_applicable"
        )
        self.blocked(
            check(self.data, {**self.result, "status": "analysis_failed"}, self.load),
            "analysis_failed",
        )
        self.load.assert_not_called()

    def test_malformed_results_or_materials_fail_before_provider_lookup(self):
        for value in (
            None,
            {},
            {**self.result, "schema_version": "other"},
            {**self.result, "status": "other"},
        ):
            self.blocked(check(self.data, value, self.load), "invalid_analysis")
        for value in (None, {}, {**self.data, "samples": "bad"}):
            self.blocked(check(value, self.result, self.load), "invalid_materials")
        self.load.assert_not_called()


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        data = fixtures.materials()
        analysis = analyze(data)
        result = check(data, analysis, loader(data))
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": result["status"],
                    "counts": result["evaluation"]["counts"],
                    "recommendation_id_preserved": result["recommendation"][
                        "recommendation_id"
                    ]
                    == analysis["recommendation"]["recommendation_id"],
                    "automatic_apply": result["automatic_apply"],
                    "engine_compatibility": result["engine_compatibility"],
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
