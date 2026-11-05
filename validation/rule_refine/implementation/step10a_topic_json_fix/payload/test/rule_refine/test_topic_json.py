"""Synthetic duplicate-key boundaries; no live model or ZEUS requests."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    "_topic_json_fixtures", Path(__file__).with_name("test_topic_processor.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
ai = fixtures.ai


def request(phase="review"):
    case = fixtures.case()
    return ai.build_request(
        case["rule"],
        case["current_samples"],
        case["history_samples"],
        case["coverage"],
        phase=phase,
        draft=fixtures.reply({"context": case}) if phase == "review" else None,
    )


def with_member(text, key, value):
    return text[:-1] + "," + json.dumps(key) + ":" + json.dumps(value) + "}"


class TopicJSONTests(unittest.TestCase):
    def setUp(self):
        self.request = request()
        self.reply = fixtures.reply(self.request)
        self.text = json.dumps(self.reply)

    def parse(self, text):
        return ai.parse_reply(text, request=self.request)

    def test_unique_reply_is_unchanged_and_ready(self):
        result = self.parse(self.text)
        self.assertTrue(result["topic_submission_ready"])
        self.assertEqual(result["filter"], self.reply["filter"])
        self.assertEqual(
            result["json_diagnostics"]["identical_duplicates_collapsed"], 0
        )

    def test_identical_root_filter_and_nested_values_collapse(self):
        text = with_member(self.text, "filter", self.reply["filter"])
        text = text.replace('"operator": "Eq"', '"operator": "Eq", "operator": "Eq"')
        result = self.parse(text)
        self.assertTrue(result["topic_submission_ready"])
        self.assertEqual(result["filter"], self.reply["filter"])
        self.assertEqual(
            result["json_diagnostics"]["identical_duplicates_collapsed"], 3
        )

    def test_object_key_order_is_irrelevant_but_array_order_matters(self):
        reversed_filter = dict(reversed(list(self.reply["filter"].items())))
        self.assertTrue(
            self.parse(with_member(self.text, "filter", reversed_filter))[
                "topic_submission_ready"
            ]
        )
        changed = dict(self.reply, sample_alert_ids=["current0", "current1"])
        with self.assertRaisesRegex(ai.TopicContractError, "duplicate_json_key"):
            self.parse(
                with_member(
                    json.dumps(changed), "sample_alert_ids", ["current1", "current0"]
                )
            )

    def test_conflicting_filter_never_chooses_either_value(self):
        changed = copy.deepcopy(self.reply["filter"])
        changed["A"]["value"] = "different-operation"
        for first, second in (
            (self.reply["filter"], changed),
            (changed, self.reply["filter"]),
        ):
            with (
                self.subTest(first=first),
                self.assertRaises(ai.TopicContractError) as caught,
            ):
                self.parse(
                    with_member(
                        json.dumps(dict(self.reply, filter=first)), "filter", second
                    )
                )
            self.assertEqual(caught.exception.code, "duplicate_json_key")
            self.assertEqual(caught.exception.diagnostics["conflicting_duplicates"], 1)
            self.assertNotIn(
                "different-operation", json.dumps(caught.exception.diagnostics)
            )

    def test_conflicting_decision_and_condition_alias_are_rejected(self):
        for before, after in (
            ('"decision": "submit"', '"decision": "withhold", "decision": "submit"'),
            ('"expression": "A"', '"expression": "A", "expression": "!A"'),
            (
                '"value": "inventory-versions"',
                '"value": "inventory-versions", "value": "other"',
            ),
        ):
            with (
                self.subTest(before=before),
                self.assertRaisesRegex(ai.TopicContractError, "duplicate_json_key"),
            ):
                self.parse(self.text.replace(before, after))

    def test_two_identical_members_followed_by_conflict_still_rejected(self):
        text = with_member(
            with_member(self.text, "status", "proposal"), "status", "no_change"
        )
        with self.assertRaises(ai.TopicContractError) as caught:
            self.parse(text)
        self.assertEqual(
            caught.exception.diagnostics["identical_duplicates_collapsed"], 1
        )
        self.assertEqual(caught.exception.diagnostics["conflicting_duplicates"], 1)

    def test_duplicate_alias_and_conflicting_child_of_duplicate_filter_rejected(self):
        definition = self.reply["filter"]
        changed = dict(definition["A"], value="other")
        nested = with_member(json.dumps(definition), "A", changed)
        text = self.text[:-1] + ',"filter":' + nested + "}"
        with self.assertRaisesRegex(ai.TopicContractError, "duplicate_json_key"):
            self.parse(text)

    def test_python_equality_and_float_rounding_cannot_hide_conflicts(self):
        for left, right in (
            ("true", "1"),
            ("1", "1.0"),
            ('"1"', "1"),
            ("1.0000000000000001", "1.0"),
            ("-0.0", "0.0"),
        ):
            text = self.text[:-1] + ',"extra":' + left + ',"extra":' + right + "}"
            with (
                self.subTest(left=left, right=right),
                self.assertRaisesRegex(ai.TopicContractError, "duplicate_json_key"),
            ):
                self.parse(text)

    def test_escaped_duplicate_key_and_string_values_are_decoded(self):
        text = self.text[:-1] + ',"statu\\u0073":"propo\\u0073al"}'
        self.assertTrue(self.parse(text)["topic_submission_ready"])

    def test_fence_warning_survives_normalization(self):
        duplicated = with_member(self.text, "status", "proposal")
        self.assertTrue(
            self.parse("```json\n" + duplicated + "\n```")["topic_submission_ready"]
        )
        parsed = self.parse("```json\n" + duplicated)
        self.assertIn("missing_closing_json_fence", parsed["output_warnings"])
        self.assertFalse(parsed["topic_submission_ready"])

    def test_invalid_or_extra_json_never_salvaged(self):
        for text in (
            self.text[:-1],
            self.text + "{}",
            self.text + " trailing prose",
            with_member(self.text, "extra", float("nan")),
            self.text[:-1] + ',"extra":1e999}',
            "[" * 70 + "0" + "]" * 70,
        ):
            with (
                self.subTest(text=text[-30:]),
                self.assertRaises(
                    (ai.TopicContractError, ai.base.ReplyValidationError)
                ),
            ):
                self.parse(text)

    def test_diagnostics_do_not_save_unknown_keys_or_values_and_are_bounded(self):
        text = self.text
        for i in range(35):
            key = "NEVER-SAVE-UNKNOWN-KEY-" + str(i)
            text = with_member(
                with_member(text, key, "NEVER-SAVE-VALUE-1"), key, "NEVER-SAVE-VALUE-2"
            )
        with self.assertRaises(ai.TopicContractError) as caught:
            self.parse(text)
        details = caught.exception.diagnostics
        self.assertEqual(details["conflicting_duplicates"], 35)
        self.assertTrue(details["locations_truncated"])
        self.assertLessEqual(len(details["duplicate_locations"]), 16)
        self.assertNotIn("NEVER-SAVE", json.dumps(details))
        self.assertLess(len(json.dumps(details)), 10000)

    def test_normalization_does_not_bypass_field_validation(self):
        changed = copy.deepcopy(self.reply)
        changed["filter"]["A"]["name"] = "unknown-event-field"
        changed["topic_review"]["field_roles"][0]["name"] = "unknown-event-field"
        parsed = self.parse(
            with_member(json.dumps(changed), "filter", changed["filter"])
        )
        self.assertTrue(parsed["validation_issues"])
        self.assertFalse(parsed["topic_submission_ready"])

    def test_normalization_does_not_invent_missing_filter(self):
        changed = dict(self.reply)
        del changed["filter"]
        with self.assertRaisesRegex(
            ai.TopicContractError, "complete_topic_reply_required"
        ):
            self.parse(with_member(json.dumps(changed), "status", "proposal"))

    def test_invisible_reference_still_blocks(self):
        changed = dict(self.reply, sample_alert_ids=["unseen-alert"])
        try:
            parsed = self.parse(with_member(json.dumps(changed), "status", "proposal"))
        except ai.base.ReplyValidationError:
            return
        self.assertFalse(parsed["topic_submission_ready"])

    def test_identical_numeric_literals_and_repeated_nested_array_are_preserved(self):
        # Normalizer is purely structural; final schema still rejects this extra key.
        text = '{"numbers":[1,1.0,-0.0],"numbers":[1,1.0,-0.0]}'
        normalized, diagnostics = ai.topic_json.normalize(text)
        self.assertEqual(normalized, '{"numbers":[1,1.0,-0.0]}')
        self.assertEqual(diagnostics["identical_duplicates_collapsed"], 1)

    def test_collapse_is_idempotent(self):
        duplicate = with_member(self.text, "filter", self.reply["filter"])
        normalized, _ = ai.topic_json.normalize(duplicate)
        again, diagnostics = ai.topic_json.normalize(normalized)
        self.assertEqual(normalized, again)
        self.assertEqual(diagnostics["identical_duplicates_collapsed"], 0)

    def test_prompt_requires_unique_keys_in_both_phases(self):
        for phase in ("generate", "review"):
            prompt = request(phase)
            self.assertIn("每个键只能出现一次", prompt["messages"][0]["content"])
            self.assertIn("重复键", prompt["messages"][-1]["content"])


class TopicJSONFlowTests(unittest.TestCase):
    def run_case(self, conflict_phase=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        calls = []

        def generate(req):
            calls.append(req["phase"])
            answer = fixtures.response(req)
            answer["content"] = with_member(
                answer["content"],
                "status",
                "no_change" if req["phase"] == conflict_phase else "proposal",
            )
            return answer

        submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        processor = fixtures.module.TopicProcessor(
            Path(tmp.name),
            load_case=lambda _: fixtures.case(),
            load_rules=lambda: Mock(get_rule=Mock(return_value=fixtures.rule())),
            generate=generate,
            model="synthetic-model",
            budget=fixtures.Budget(),
            submit=submit,
        )
        outcome = processor.process(fixtures.signal())
        return processor, outcome, calls, submit

    def test_identical_duplicates_keep_two_calls_and_one_post(self):
        processor, outcome, calls, submit = self.run_case()
        self.assertEqual(outcome["status"], "received")
        self.assertEqual(calls, ["generate", "review"])
        submit.assert_called_once()
        folder = Path(outcome["output_dir"])
        for phase in calls:
            saved = json.loads(
                (folder / (phase + ".summary.json")).read_text(encoding="utf-8")
            )
            self.assertEqual(
                saved["json_diagnostics"]["identical_duplicates_collapsed"], 1
            )
            self.assertIn("reply_sha256", saved)
        self.assertTrue(processor.verify_durable_result(fixtures.signal(), outcome))

    def test_conflict_stops_without_retry_or_post_and_saves_small_diagnostics(self):
        for phase in ("generate", "review"):
            with self.subTest(phase=phase):
                processor, outcome, calls, submit = self.run_case(phase)
                self.assertEqual(outcome["status"], "blocked")
                self.assertEqual(
                    calls,
                    ["generate"] if phase == "generate" else ["generate", "review"],
                )
                submit.assert_not_called()
                failed = json.loads(
                    (Path(outcome["output_dir"]) / (phase + ".failed.json")).read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(failed["error_code"], "duplicate_json_key")
                self.assertEqual(
                    outcome["json_diagnostics"], failed["json_diagnostics"]
                )
                self.assertEqual(
                    failed["json_diagnostics"]["conflicting_duplicates"], 1
                )
                self.assertNotIn("content", failed)
                self.assertTrue(
                    processor.verify_durable_result(fixtures.signal(), outcome)
                )
                self.assertEqual(
                    processor.process(fixtures.signal())["record_id"],
                    outcome["record_id"],
                )
                submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
