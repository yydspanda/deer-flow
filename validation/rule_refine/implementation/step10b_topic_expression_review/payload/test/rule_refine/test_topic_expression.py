"""Expression repairs use the existing reviewer; the program never invents logic."""

import copy
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

SPEC = importlib.util.spec_from_file_location(
    "_topic_expression_fixtures", Path(__file__).with_name("test_topic_processor.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
ai = fixtures.ai


def request(phase="generate", draft=None):
    case = fixtures.case()
    return ai.build_request(
        case["rule"],
        case["current_samples"],
        case["history_samples"],
        case["coverage"],
        phase=phase,
        draft=draft,
    )


class TopicExpressionTests(unittest.TestCase):
    def setUp(self):
        self.request = request()
        self.reply = fixtures.reply(self.request)

    def parse(self, reply, req=None):
        return ai.parse_reply(json.dumps(reply), request=req or self.request)

    def test_invalid_expression_is_inert_original_draft_for_review(self):
        for expression in (
            "A && B",
            "A A",
            "A AND A",
            "A OR A",
            "!A",
            "(A",
            "A ||",
            "A & A",
            "A(A)",
        ):
            with self.subTest(expression=expression):
                reply = copy.deepcopy(self.reply)
                reply["filter"]["expression"] = expression
                parsed = self.parse(reply)
                self.assertEqual(parsed["filter"], reply["filter"])
                self.assertFalse(parsed["format_valid"])
                self.assertFalse(parsed["recommendation_ready"])
                self.assertFalse(parsed["topic_submission_ready"])
                self.assertTrue(parsed["validation_issues"])
                self.assertTrue(parsed["expression_repair_required"])
                self.assertTrue(parsed["reply_diagnostics"]["expression"]["error_code"])
                review = request("review", parsed)
                self.assertEqual(review["context"]["draft"]["filter"], reply["filter"])
                self.assertTrue(review["context"]["draft_validation_issues"])
                self.assertNotIn("recommendation", review["context"]["draft"])
                with self.assertRaises(
                    (ai.TopicContractError, ai.base.ReplyValidationError)
                ):
                    self.parse(reply, review)

    def test_repair_flag_cannot_relax_old_parser_or_review(self):
        changed = copy.deepcopy(self.reply)
        changed["filter"]["expression"] = "A && B"
        context = self.request["context"]
        kw = dict(rule=context["rule"], visible_samples=context["current_samples"])
        with self.assertRaises(ai.base.ReplyValidationError):
            ai.base.parse_reply(json.dumps(changed), phase="generate", **kw)
        with self.assertRaises(ValueError):
            ai.base.parse_reply(
                json.dumps(changed),
                phase="review",
                allow_invalid_draft_expression=True,
                **kw,
            )

    def test_all_conditions_still_checked_after_expression_error(self):
        for mutate in (
            lambda x: x["filter"]["A"].update(operator="equal"),
            lambda x: x["filter"]["A"].update(value=None),
            lambda x: x["filter"]["A"].pop("name"),
            lambda x: x.update(sample_alert_ids=["unseen-alert"]),
            lambda x: x["topic_review"].update(field_roles=[]),
            lambda x: x["filter"].update(topic="different-topic"),
        ):
            reply = copy.deepcopy(self.reply)
            reply["filter"]["expression"] = "A && B"
            mutate(reply)
            with self.assertRaises(
                (ai.TopicContractError, ai.base.ReplyValidationError)
            ):
                self.parse(reply)

    def test_expression_error_with_unknown_or_metadata_field_is_not_repairable(self):
        for field in ("unknown-field", "detail_hit_rule_name"):
            reply = copy.deepcopy(self.reply)
            reply["filter"]["expression"] = "A && B"
            reply["filter"]["A"]["name"] = field
            reply["topic_review"]["field_roles"][0]["name"] = field
            with self.assertRaisesRegex(
                ai.TopicContractError, "draft_expression_other_issues"
            ):
                self.parse(reply)

    def test_nonstring_empty_or_oversized_expression_stops(self):
        for expression in (None, 123, "", " ", "A " * 1025):
            reply = copy.deepcopy(self.reply)
            reply["filter"]["expression"] = expression
            with (
                self.subTest(expression_type=type(expression).__name__),
                self.assertRaises(
                    (ai.TopicContractError, ai.base.ReplyValidationError)
                ),
            ):
                self.parse(reply)

    def test_valid_generation_and_review_stay_unchanged(self):
        generated = self.parse(self.reply)
        reviewed = self.parse(self.reply, request("review", generated))
        self.assertFalse(generated["expression_repair_required"])
        self.assertFalse(generated["topic_submission_ready"])
        self.assertTrue(reviewed["topic_submission_ready"])
        self.assertTrue(reviewed["format_valid"])

    def test_missing_filter_not_reconstructed(self):
        reply = copy.deepcopy(self.reply)
        del reply["filter"]
        with self.assertRaises(ai.TopicContractError):
            self.parse(reply)

    def test_duplicate_conflict_still_stops_before_expression_repair(self):
        self.reply["filter"]["expression"] = "A && B"
        text = json.dumps(self.reply)[:-1] + ',"status":"no_change"}'
        with self.assertRaisesRegex(ai.TopicContractError, "duplicate_json_key"):
            ai.parse_reply(text, request=self.request)

    def test_diagnostics_distinguish_undefined_reference_from_missing_operator(self):
        for expression, problem in (
            ("A && B", "undefined_condition_alias"),
            ("A A", "missing_operator_between_references"),
            ("A AND A", "missing_operator_between_references"),
        ):
            self.reply["filter"]["expression"] = expression
            parsed = self.parse(self.reply)
            details = parsed["reply_diagnostics"]["expression"]
            self.assertEqual(details["problem"], problem)
            self.assertEqual(details["defined_aliases"], ["A"])
            self.assertNotIn("inventory-versions", json.dumps(details))

    def test_prompt_explicit_grammar_and_review_repair_task(self):
        prompt = self.request["messages"][0]["content"]
        self.assertIn("A && B", prompt)
        self.assertIn("AND", prompt)
        parsed = self.parse(self.reply)
        review = request("review", parsed)
        self.assertIn("表达式", review["messages"][0]["content"])
        self.assertIn("draft_validation_issues", review["context"])

    def test_diagnostics_never_save_values_or_unsupported_expression_text(self):
        reply = copy.deepcopy(self.reply)
        reply["filter"]["expression"] = "A && secret-field=='NEVER-SAVE-SECRET'"
        parsed = self.parse(reply)
        details = parsed["reply_diagnostics"]
        self.assertNotIn("NEVER-SAVE-SECRET", json.dumps(details))
        self.assertNotIn("inventory-versions", json.dumps(details))
        self.assertNotIn("expression_preview", details["expression"])
        self.assertLess(len(json.dumps(details)), 4096)

    def test_diagnostics_preserve_finite_size_on_oversized_and_unknown_alias(self):
        definition = copy.deepcopy(self.reply["filter"])
        definition["expression"] = "A " * 10000
        details = ai.topic_expression.inspect_filter(definition)
        self.assertLess(len(json.dumps(details)), 2048)
        self.assertNotIn("expression_preview", details)
        long_alias = "NEVERSAVE" * 10
        definition["expression"] = "A && " + long_alias
        details = ai.topic_expression.inspect_filter(definition)
        self.assertNotIn(long_alias, json.dumps(details))
        self.assertNotIn("expression_preview", details)

    def test_incomplete_fence_and_expression_error_do_not_reach_review(self):
        self.reply["filter"]["expression"] = "A && B"
        with self.assertRaisesRegex(
            ai.TopicContractError, "draft_expression_other_issues"
        ):
            ai.parse_reply("```json\n" + json.dumps(self.reply), request=self.request)

    def test_helper_and_base_share_same_recoverable_error_codes(self):
        self.assertEqual(
            ai.topic_expression.EXPRESSION_ERRORS, ai.base.DRAFT_EXPRESSION_ERRORS
        )


class TopicExpressionFlowTests(unittest.TestCase):
    def run_case(self, review_expression="A", extra_bad_field=False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        calls = []

        def generate(req):
            calls.append(req)
            answer = fixtures.reply(req)
            answer["filter"]["expression"] = (
                "A && B" if req["phase"] == "generate" else review_expression
            )
            if extra_bad_field:
                answer["filter"]["A"]["operator"] = "equal"
            return {**fixtures.response(req), "content": json.dumps(answer)}

        submit = Mock(return_value={"success": True, "code": 200, "rule_id": 123})
        processor = fixtures.module.TopicProcessor(
            Path(temporary.name),
            load_case=lambda _: fixtures.case(),
            load_rules=lambda: Mock(get_rule=Mock(return_value=fixtures.rule())),
            generate=generate,
            model="synthetic-model",
            budget=fixtures.Budget(),
            submit=submit,
        )
        outcome = processor.process(fixtures.signal())
        return processor, outcome, calls, submit

    def test_reviewer_repairs_in_two_calls_one_post(self):
        processor, outcome, calls, submit = self.run_case()
        self.assertEqual(outcome["status"], "received")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]["context"]["draft"]["filter"]["expression"], "A && B")
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[0]["expression"], "A")
        saved = json.loads(
            (Path(outcome["output_dir"]) / "generate.summary.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertFalse(saved["format_valid"])
        self.assertTrue(saved["expression_repair_required"])
        self.assertTrue(saved["reply_diagnostics"]["expression"]["undefined_aliases"])
        self.assertTrue(processor.verify_durable_result(fixtures.signal(), outcome))

    def test_review_still_invalid_stops_without_post_or_third_call(self):
        processor, outcome, calls, submit = self.run_case("A && B")
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(len(calls), 2)
        self.assertEqual(outcome["reason"], "invalid_expression_reference")
        self.assertEqual(
            outcome["reply_diagnostics"]["error_path"], ["filter", "expression"]
        )
        self.assertEqual(
            outcome["reply_diagnostics"]["expression"]["undefined_aliases"], ["B"]
        )
        submit.assert_not_called()
        saved = json.loads(
            (Path(outcome["output_dir"]) / "review.failed.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["reply_diagnostics"], outcome["reply_diagnostics"])
        self.assertNotIn("inventory-versions", json.dumps(saved))
        self.assertEqual(
            processor.process(fixtures.signal())["record_id"], outcome["record_id"]
        )
        self.assertEqual(len(calls), 2)
        self.assertTrue(processor.verify_durable_result(fixtures.signal(), outcome))

    def test_other_generation_error_not_promoted_to_review(self):
        _, outcome, calls, submit = self.run_case(extra_bad_field=True)
        self.assertEqual(outcome["status"], "blocked")
        self.assertEqual(len(calls), 1)
        submit.assert_not_called()


if __name__ == "__main__":
    unittest.main()
