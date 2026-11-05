"""New operator vocabulary and immutable saved-response compatibility, entirely offline."""

import copy
import hashlib
import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_surge_operator_contract_tests"
package = types.ModuleType(PACKAGE)
package.__path__ = [str(ROOT)]
sys.modules[PACKAGE] = package
ai = importlib.import_module(PACKAGE + ".surge_ai_review")
processor = importlib.import_module(PACKAGE + ".surge_processor")
Budget = importlib.import_module(PACKAGE + ".surge_budget").ContextBudget


def rule():
    return {
        "company_code": "synthetic-company",
        "topic": "synthetic-operator-topic",
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
        "issue": "Synthetic recurring event",
        "recommendation": "Review the synthetic condition",
        "certainty": "Structural fixture only",
        "filter": {
            "topic": rule()["topic"],
            "A": {"name": "command", "operator": operator, "value": "inventory"},
            "expression": "A",
        },
        "sample_alert_ids": ["synthetic-1"],
        "review_notes": "No production conclusion",
        "sample_request": None,
    }


def request(*, phase="generate", draft=None):
    return ai.build_request(
        rule(),
        samples(),
        [],
        {"synthetic": True},
        phase=phase,
        draft=draft,
        independent=phase == "generate",
        limits=Budget(),
    )


def frozen_legacy_request(*, phase="generate", draft=None):
    frozen = request(phase=phase, draft=draft)
    frozen["prompt_version"] = (
        "rule-surge-runtime-independent-generation-v4"
        if phase == "generate"
        else "rule-surge-runtime-review-v5"
    )
    frozen["context"].pop("operator_contract_version")
    frozen["context"].pop("draft_operator_projection", None)
    if draft is not None:
        frozen["context"]["draft"] = ai.review_draft_projection(
            draft,
            operator_contract_version=None,
        )
    frozen["messages"][0]["content"] = (
        "Frozen historical synthetic prompt: equal/contain/notcontain"
    )
    frozen["messages"][1]["content"] = ai.encoded(frozen["context"]).decode()
    frozen["prompt_sha256"] = hashlib.sha256(ai.encoded(frozen["messages"])).hexdigest()
    return frozen


class SurgeOperatorContractTests(unittest.TestCase):
    def parse(self, answer, **kwargs):
        return ai.parse_reply(
            json.dumps(answer),
            rule=rule(),
            visible_samples=samples(),
            **kwargs,
        )

    def test_all_six_new_operators_are_accepted_and_tagged(self):
        for operator in (
            "Eq",
            "Ne",
            "Contains",
            "NotContains",
            "StartsWith",
            "EndsWith",
        ):
            with self.subTest(operator=operator):
                parsed = self.parse(reply(operator))
                self.assertEqual(parsed["filter"]["A"]["operator"], operator)
                self.assertEqual(
                    parsed["operator_contract_version"], ai.OPERATOR_CONTRACT_VERSION
                )

    def test_new_value_contract_rejects_empty_and_nonstring_without_evaluation(self):
        for operator in (
            "Eq",
            "Ne",
            "Contains",
            "NotContains",
            "StartsWith",
            "EndsWith",
        ):
            for value in ("", None, False, 123, [], {}):
                with self.subTest(operator=operator, value=value):
                    answer = reply(operator)
                    answer["filter"]["A"]["value"] = value
                    with self.assertRaises(ai.ReplyValidationError) as caught:
                        self.parse(answer)
                    self.assertEqual(caught.exception.code, "invalid_condition_value")
                    self.assertEqual(caught.exception.path, ["filter", "A", "value"])
        # This is schema validation, not trial matching against sample values.
        answer = reply("Eq")
        answer["filter"]["A"]["value"] = " unmatched literal "
        parsed = self.parse(answer)
        self.assertEqual(parsed["filter"]["A"]["value"], " unmatched literal ")
        self.assertFalse(parsed["program_filter_evaluation_performed"])

    def test_frozen_legacy_values_remain_unchanged(self):
        for value in ("", None, False, 123, [], {}):
            with self.subTest(value=value):
                answer = reply("equal")
                answer["filter"]["A"]["value"] = value
                parsed = self.parse(answer, operator_contract_version=None)
                self.assertEqual(parsed["filter"], answer["filter"])
                self.assertNotIn("operator_contract_version", parsed)

    def test_new_replies_reject_old_values_and_wrong_case(self):
        for operator in (
            "equal",
            "notequal",
            "contain",
            "notcontain",
            "startWith",
            "endWith",
            "EQ",
            "contains",
            "Startswith",
        ):
            with self.subTest(operator=operator):
                with self.assertRaises(ai.ReplyValidationError) as caught:
                    self.parse(reply(operator))
                self.assertEqual(
                    caught.exception.code, "unsupported_condition_operator"
                )

    def test_every_new_prompt_uses_only_new_operator_instructions(self):
        for phase, independent in (
            ("generate", False),
            ("generate", True),
            ("review", False),
        ):
            with self.subTest(phase=phase, independent=independent):
                built = ai.build_request(
                    rule(),
                    samples(),
                    [],
                    {},
                    phase=phase,
                    draft=reply() if phase == "review" else None,
                    independent=independent,
                    limits=Budget(),
                )
                self.assertEqual(
                    ai.request_operator_contract_version(built),
                    ai.OPERATOR_CONTRACT_VERSION,
                )
                system = built["messages"][0]["content"]
                for op in (
                    "Eq",
                    "Ne",
                    "Contains",
                    "NotContains",
                    "StartsWith",
                    "EndsWith",
                ):
                    self.assertIn(op, system)
                for old in (
                    "operator为equal",
                    '"operator": "contain"',
                    "operator 仅 equal",
                ):
                    self.assertNotIn(old, system)

    def test_new_contract_cannot_silently_fall_back(self):
        for mutation in ("missing", "unknown", "downgrade"):
            frozen = request()
            if mutation == "missing":
                frozen["context"].pop("operator_contract_version")
            elif mutation == "unknown":
                frozen["context"]["operator_contract_version"] = "unknown"
            else:
                frozen["prompt_version"] = (
                    "rule-surge-runtime-independent-generation-v4"
                )
            with self.assertRaises(ai.ReplyValidationError):
                ai.request_operator_contract_version(frozen)
        with self.assertRaises(ai.ReplyValidationError):
            ai.request_operator_contract_version(
                {"prompt_version": "unknown", "context": {}}
            )

    def test_frozen_legacy_reply_keeps_its_original_operator_and_structure(self):
        frozen = frozen_legacy_request()
        self.assertIsNone(ai.request_operator_contract_version(frozen))
        for operator in ("equal", "contain", "notcontain"):
            original = reply(operator)
            parsed = self.parse(original, operator_contract_version=None)
            self.assertEqual(parsed["filter"], original["filter"])
            self.assertNotIn("operator_contract_version", parsed)
            self.assertEqual(parsed["output_field_normalizations"], [])
        # Legacy snapshots did not support these additional or new operators.
        for operator in ("Eq", "Ne", "notequal", "startWith"):
            with self.assertRaises(ai.ReplyValidationError):
                self.parse(reply(operator), operator_contract_version=None)

    def test_new_review_converts_only_draft_projection_preserving_original(self):
        draft = reply("contain")
        draft["recommendation"] = "Original prose containing equal and contain"
        original = copy.deepcopy(draft)
        frozen = request(phase="review", draft=draft)
        self.assertEqual(draft, original)
        self.assertEqual(
            frozen["context"]["draft"]["filter"]["A"]["operator"], "Contains"
        )
        self.assertEqual(
            frozen["context"]["draft_operator_projection"]["source_filter_sha256"],
            hashlib.sha256(ai.encoded(original["filter"])).hexdigest(),
        )
        self.assertNotIn("recommendation", frozen["context"]["draft"])
        self.assertEqual(
            frozen["context"]["draft"]["sample_alert_ids"], original["sample_alert_ids"]
        )

    def test_saved_legacy_response_is_recovered_without_request_or_rewrite(self):
        frozen = frozen_legacy_request()
        selection = {
            "request": frozen,
            "current_samples": samples(),
            "history_samples": [],
        }
        generate = Mock(side_effect=AssertionError("no new request allowed"))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            engine = processor.SurgeProcessor(
                folder,
                load_case=Mock(),
                load_rules=Mock(),
                generate=generate,
                model="synthetic-model",
                budget=Budget(),
            )
            original_content = json.dumps(reply("contain"))
            artifacts = {
                "started.json": {
                    "request_sha256": processor._hash(frozen),
                    "model": "synthetic-model",
                },
                "request.json": frozen,
                "response.json": {
                    "request_sha256": processor._hash(frozen),
                    "content": original_content,
                    "transport": {"finish_reason": "stop", "error_code": None},
                },
            }
            for name, value in artifacts.items():
                processor._write(folder / name, value)
            before = {name: (folder / name).read_bytes() for name in artifacts}
            result, calls, reused = engine._phase(
                folder, frozen, rule(), selection, "generate"
            )
            self.assertEqual(result["status"], "proposal")
            self.assertEqual(result["reply"]["filter"]["A"]["operator"], "contain")
            self.assertEqual(calls, 0)
            self.assertTrue(reused)
            second, second_calls, _ = engine._phase(
                folder, frozen, rule(), selection, "generate"
            )
            self.assertEqual(second, result)
            self.assertEqual(second_calls, 0)
            self.assertEqual(
                before, {name: (folder / name).read_bytes() for name in artifacts}
            )
            generate.assert_not_called()

    def test_unsent_saved_legacy_request_is_not_sent_after_upgrade(self):
        signal = {
            "schema_version": "sec_know.rule_burst_signal.v1",
            "mode": "demo",
            "namespace": "synthetic",
            "source_app": "test-app",
            "signal_id": "synthetic-signal",
            "company_code": rule()["company_code"],
            "topic": rule()["topic"],
            "rule_code": rule()["rule_code"],
            "sample_alert_ids": ["synthetic-1"],
            "current_count": 1,
            "previous_count": 0,
        }
        case = {
            "rule": rule(),
            "original_rule": rule(),
            "current_samples": samples(),
            "history_samples": [],
            "coverage": {"synthetic": True},
        }
        selection = {
            "request": frozen_legacy_request(),
            "current_samples": samples(),
            "history_samples": [],
            "selection": {"phase": "generate", "current_count": 1, "history_count": 0},
        }
        generate = Mock(side_effect=AssertionError("do not send obsolete prompt"))
        with tempfile.TemporaryDirectory() as directory:
            engine = processor.SurgeProcessor(
                directory,
                load_case=lambda _: case,
                load_rules=Mock(),
                generate=generate,
                model="synthetic-model",
                budget=Budget(),
            )
            _, _, job_id = processor._identity(signal)
            phase_dir = Path(directory) / "jobs" / job_id / "generate"
            processor._mkdir(phase_dir)
            processor._write(phase_dir / "selection.json", selection)
            before = (phase_dir / "selection.json").read_bytes()
            outcome = engine.process(signal)
            self.assertEqual(outcome["status"], "blocked")
            self.assertEqual(
                outcome["reason"], "saved_request_requires_new_operator_job"
            )
            self.assertEqual(outcome["model_calls_this_attempt"], 0)
            self.assertFalse((phase_dir / "started.json").exists())
            self.assertEqual(before, (phase_dir / "selection.json").read_bytes())
            generate.assert_not_called()

    def test_invalid_operator_contract_fails_selection_verification(self):
        frozen = request()
        frozen["context"].pop("operator_contract_version")
        frozen["messages"][1]["content"] = ai.encoded(frozen["context"]).decode()
        frozen["prompt_sha256"] = processor._hash(frozen["messages"])
        with tempfile.TemporaryDirectory() as directory:
            engine = processor.SurgeProcessor(
                directory,
                load_case=Mock(),
                load_rules=Mock(),
                generate=Mock(),
                model="synthetic-model",
                budget=Budget(),
            )
            with self.assertRaises(processor.DurableSurgeError):
                engine._verify_selection(
                    {
                        "request": frozen,
                        "current_samples": samples(),
                        "history_samples": [],
                    },
                    {
                        "rule": rule(),
                        "current_samples": samples(),
                        "history_samples": [],
                    },
                )


if __name__ == "__main__":
    unittest.main()
