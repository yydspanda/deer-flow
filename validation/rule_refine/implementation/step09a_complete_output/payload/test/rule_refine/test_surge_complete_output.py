"""Complete-output prompts and diagnostics; synthetic fixtures and no model calls."""

import copy
import hashlib
import importlib
import json
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_surge_complete_output_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
ai = importlib.import_module(PACKAGE + ".surge_ai_review")


def rule():
    return {
        "company_code": "synthetic-company",
        "topic": "synthetic-complete-output-topic",
        "rule_code": "synthetic-rule",
        "rule_name": "Synthetic detector",
        "rule_config_version": "fixture-v1",
        "configuration": {"terms": []},
    }


def samples():
    return [{"alert_id": "synthetic-1", "raw_logs": [{"command": "inventory-read"}]}]


def reply(operator="Contains"):
    return {
        "status": "proposal",
        "filter": {
            "topic": rule()["topic"],
            "A": {"name": "command", "operator": operator, "value": "inventory"},
            "expression": "A",
        },
        "sample_alert_ids": ["synthetic-1"],
        "issue": "Synthetic recurring event",
        "recommendation": "Review the synthetic condition",
        "certainty": "Structural fixture only",
        "review_notes": "No production conclusion",
        "sample_request": None,
    }


def build(*, phase="generate", independent=True, limits=None):
    return ai.build_request(
        rule(),
        samples(),
        [],
        {"synthetic": True},
        phase=phase,
        draft=reply() if phase == "review" else None,
        independent=independent,
        limits=limits,
    )


class CompleteOutputTests(unittest.TestCase):
    def parse(self, value, **kwargs):
        return ai.parse_reply(
            json.dumps(value), rule=rule(), visible_samples=samples(), **kwargs
        )

    def test_prose_only_proposal_remains_blocked_with_all_absent_fields_reported(self):
        answer = {
            "status": "proposal",
            "issue": "A synthetic recurring operation",
            "recommendation": "Use its actual command as an exclusion candidate",
            "certainty": "Evidence is limited, therefore",
        }
        original = copy.deepcopy(answer)
        with self.assertRaises(ai.ReplyValidationError) as caught:
            self.parse(answer)
        self.assertEqual(caught.exception.code, "missing_reply_field")
        self.assertEqual(caught.exception.path, ["filter"])
        diagnostic = ai.reply_structure_diagnostics(json.dumps(answer))
        self.assertEqual(
            diagnostic["missing_reply_fields"], ["filter", "sample_alert_ids"]
        )
        self.assertEqual(
            diagnostic["missing_template_fields"],
            ["filter", "sample_alert_ids", "review_notes", "sample_request"],
        )
        self.assertEqual(answer, original)

    def test_missing_references_are_not_synthesized_from_prose(self):
        answer = reply()
        del answer["sample_alert_ids"]
        answer["review_notes"] = "Example synthetic-1 was visible"
        with self.assertRaises(ai.ReplyValidationError) as caught:
            self.parse(answer)
        self.assertEqual(caught.exception.code, "proposal_requires_sample_references")
        self.assertEqual(
            ai.reply_structure_diagnostics(json.dumps(answer))["missing_reply_fields"],
            ["sample_alert_ids"],
        )

    def test_observed_exact_reference_alias_keeps_its_existing_parser_behavior(self):
        answer = reply()
        answer[ai.OBSERVED_REFERENCE_ALIAS] = answer.pop("sample_alert_ids")
        self.assertEqual(self.parse(answer)["sample_alert_ids"], ["synthetic-1"])
        diagnostic = ai.reply_structure_diagnostics(json.dumps(answer))
        self.assertEqual(diagnostic["missing_reply_fields"], [])
        self.assertEqual(diagnostic["missing_template_fields"], ["sample_alert_ids"])

    def test_invalid_alias_does_not_hide_absent_references(self):
        for aliases in (None, [], [1], ["synthetic-1", "synthetic-1"]):
            with self.subTest(aliases=aliases):
                answer = reply()
                del answer["sample_alert_ids"]
                answer[ai.OBSERVED_REFERENCE_ALIAS] = aliases
                self.assertEqual(
                    ai.reply_structure_diagnostics(json.dumps(answer))[
                        "missing_reply_fields"
                    ],
                    ["sample_alert_ids"],
                )

    def test_optional_template_fields_are_distinguished_from_required_fields(self):
        answer = reply()
        for key in ("certainty", "review_notes", "sample_request"):
            del answer[key]
        parsed = self.parse(answer)
        self.assertEqual(parsed["status"], "proposal")
        diagnostic = ai.reply_structure_diagnostics(json.dumps(answer))
        self.assertEqual(diagnostic["missing_reply_fields"], [])
        self.assertEqual(
            diagnostic["missing_template_fields"],
            ["certainty", "review_notes", "sample_request"],
        )

    def test_no_proposal_still_requires_explicit_null_filter(self):
        answer = reply()
        answer.update(status="insufficient_evidence", filter=None, sample_alert_ids=[])
        self.assertEqual(self.parse(answer)["status"], "insufficient_evidence")
        self.assertEqual(
            ai.reply_structure_diagnostics(json.dumps(answer))["missing_reply_fields"],
            [],
        )
        del answer["filter"]
        with self.assertRaises(ai.ReplyValidationError) as caught:
            self.parse(answer)
        self.assertEqual(caught.exception.path, ["filter"])

    def test_diagnostic_does_not_mask_json_errors_or_guess_keys(self):
        for text in (
            None,
            "",
            '{"status":',
            "[]",
            '{"status":"proposal","status":"no_change"}',
        ):
            with self.subTest(text=text):
                self.assertEqual(ai.reply_structure_diagnostics(text), {})

    def test_all_six_new_operators_and_unchanged_literals_are_preserved(self):
        for operator in (
            "Eq",
            "Ne",
            "Contains",
            "NotContains",
            "StartsWith",
            "EndsWith",
        ):
            with self.subTest(operator=operator):
                answer = reply(operator)
                answer["filter"]["A"]["value"] = " inventory "
                parsed = self.parse(answer)
                self.assertEqual(parsed["filter"], answer["filter"])
                self.assertEqual(
                    ai.reply_structure_diagnostics(json.dumps(answer)),
                    {
                        "missing_reply_fields": [],
                        "missing_template_fields": [],
                    },
                )

    def test_all_new_phases_put_conditions_first_and_remind_after_unchanged_evidence(
        self,
    ):
        template = json.loads(ai.REPLY_TEMPLATE)
        self.assertEqual(tuple(template), ai.REPLY_FIELD_ORDER)
        for phase, independent in (
            ("generate", True),
            ("generate", False),
            ("review", False),
        ):
            with self.subTest(phase=phase, independent=independent):
                request = build(phase=phase, independent=independent)
                self.assertEqual(
                    ai.request_output_contract_version(request),
                    ai.OUTPUT_CONTRACT_VERSION,
                )
                messages = request["messages"]
                self.assertEqual(len(messages), 3)
                self.assertEqual(json.loads(messages[1]["content"]), request["context"])
                self.assertEqual(request["context"]["current_samples"], samples())
                self.assertEqual(
                    messages[2], {"role": "user", "content": ai.OUTPUT_REMINDER}
                )
                self.assertIn("insufficient_evidence", messages[2]["content"])
                self.assertIn("filter:null", messages[2]["content"])
                self.assertNotIn("net1", messages[2]["content"])
                self.assertEqual(
                    request["prompt_sha256"],
                    hashlib.sha256(ai.encoded(messages)).hexdigest(),
                )
                self.assertEqual(request["input_bytes"], len(ai.encoded(messages)))
                self.assertEqual(
                    request["estimated_input_tokens"],
                    256
                    + sum(
                        len(message["content"].encode("utf-8")) + 64
                        for message in messages
                    ),
                )
                self.assertIsNone(request["model_output_token_limit"])

    def test_tail_reminder_is_not_allowed_to_exceed_the_input_budget(self):
        request = build()
        without_tail = 256 + sum(
            len(message["content"].encode("utf-8")) + 64
            for message in request["messages"][:2]
        )
        self.assertLess(without_tail, request["estimated_input_tokens"])
        limits = types.SimpleNamespace(
            input_token_budget=without_tail,
            input_bytes=32 * 1024 * 1024,
            audit=lambda: {},
        )
        with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
            build(limits=limits)

    def test_new_output_contract_cannot_be_deleted_forged_or_downgraded(self):
        for value in (None, "", "future-output-contract"):
            request = build()
            if value is None:
                del request["context"]["output_contract_version"]
            else:
                request["context"]["output_contract_version"] = value
            with self.assertRaises(ai.ReplyValidationError) as caught:
                ai.request_output_contract_version(request)
            self.assertEqual(caught.exception.code, "output_contract_version_mismatch")
        for old in (
            "rule-surge-runtime-review-v6",
            "rule-surge-runtime-independent-generation-v5",
            "unknown",
        ):
            request = build()
            request["prompt_version"] = old
            with self.assertRaises(ai.ReplyValidationError):
                ai.request_output_contract_version(request)

    def test_previous_operator_prompts_keep_six_operator_and_field_usage_contracts(
        self,
    ):
        for prompt in (
            "rule-surge-runtime-review-v6",
            "rule-surge-runtime-independent-generation-v5",
        ):
            request = build()
            request["prompt_version"] = prompt
            del request["context"]["output_contract_version"]
            request["messages"] = request["messages"][:2]
            request["messages"][1]["content"] = ai.encoded(request["context"]).decode()
            request["prompt_sha256"] = hashlib.sha256(
                ai.encoded(request["messages"])
            ).hexdigest()
            before = copy.deepcopy(request)
            self.assertIsNone(ai.request_output_contract_version(request))
            self.assertEqual(
                ai.request_operator_contract_version(request),
                ai.OPERATOR_CONTRACT_VERSION,
            )
            usage = ai.request_field_usage_version(request)
            for operator in (
                "Eq",
                "Ne",
                "Contains",
                "NotContains",
                "StartsWith",
                "EndsWith",
            ):
                parsed = self.parse(
                    reply(operator),
                    field_usage_policy_version=usage,
                    operator_contract_version=ai.request_operator_contract_version(
                        request
                    ),
                )
                self.assertEqual(parsed["filter"]["A"]["operator"], operator)
            self.assertEqual(request, before)


if __name__ == "__main__":
    unittest.main()
