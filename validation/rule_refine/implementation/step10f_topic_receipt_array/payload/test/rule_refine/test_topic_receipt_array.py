"""Single-rule batch receipts; synthetic responses only, never resend unknown POSTs."""

import importlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    "_topic_receipt_array_fixtures", Path(__file__).with_name("test_topic_processor.py")
)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
delivery = importlib.import_module(fixtures.PACKAGE + ".topic_delivery")


def payload():
    return {
        "topic": "synthetic-topic",
        "A": {"name": "action", "operator": "Eq", "value": "inventory-versions"},
        "expression": "A",
        "executeType": [3],
    }


class ReceiptArrayTests(unittest.TestCase):
    def setUp(self):
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        for target in (
            "socket.create_connection",
            "socket.socket.connect",
            "socket.socket.connect_ex",
        ):
            self.guards.enter_context(
                patch(target, side_effect=AssertionError("offline-only test"))
            )
        temporary = tempfile.TemporaryDirectory(prefix="topic-array-receipt-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def deliver(self, root, submit):
        return delivery.TopicDelivery(root, submit).deliver(
            payload(), company_code="synthetic-company", scope="synthetic-scope"
        )

    def test_single_integer_array_saved_as_scalar_and_second_call_is_duplicate(self):
        submit = Mock(return_value={"success": True, "code": 200, "rule_id": [153]})
        first = self.deliver(self.root, submit)
        self.assertEqual(first["status"], "submitted")
        self.assertEqual(first["rule_id"], 153)
        saved = json.loads(
            (self.root / first["delivery_id"] / "result.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(saved["rule_id"], 153)
        self.assertIs(type(saved["rule_id"]), int)
        later = Mock(side_effect=AssertionError("must not submit twice"))
        second = self.deliver(self.root, later)
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["rule_id"], 153)
        submit.assert_called_once_with(payload())
        later.assert_not_called()

    def test_single_string_array_and_historical_scalar_ids_remain_valid(self):
        for index, (returned, expected) in enumerate(
            ((["rule-153"], "rule-153"), (153, 153), ("rule-153", "rule-153"))
        ):
            with self.subTest(returned=returned):
                submit = Mock(
                    return_value={"success": True, "code": 200, "rule_id": returned}
                )
                result = self.deliver(self.root / str(index), submit)
                self.assertEqual(result["status"], "submitted")
                self.assertEqual(result["rule_id"], expected)
                self.assertIs(type(result["rule_id"]), type(expected))

    def test_invalid_or_ambiguous_arrays_are_unknown_and_never_retried(self):
        identities = (
            [],
            [153, 154],
            [153, 153],
            [[153]],
            [True],
            [False],
            [0],
            [-1],
            [None],
            [{}],
            [""],
            [" 153"],
            ["id\ninvalid"],
            ["x" * 257],
            [1.5],
        )
        for index, value in enumerate(identities):
            with self.subTest(value=value):
                submit = Mock(
                    return_value={"success": True, "code": 200, "rule_id": value}
                )
                root = self.root / str(index)
                first = self.deliver(root, submit)
                self.assertEqual(first["status"], "submission_unknown")
                self.assertIsNone(first["rule_id"])
                self.assertEqual(self.deliver(root, submit), first)
                submit.assert_called_once()

    def test_success_flag_is_required_even_with_a_valid_single_id_array(self):
        for index, success in enumerate((False, None, 1, "true")):
            with self.subTest(success=success):
                result = self.deliver(
                    self.root / str(index),
                    Mock(
                        return_value={"success": success, "code": 200, "rule_id": [153]}
                    ),
                )
                self.assertEqual(result["status"], "submission_unknown")
                self.assertIsNone(result["rule_id"])

    def test_non_success_code_does_not_become_success_from_valid_id_array(self):
        for index, code in enumerate((69220, 500, "200", True)):
            with self.subTest(code=code):
                result = self.deliver(
                    self.root / str(index),
                    Mock(
                        return_value={"success": True, "code": code, "rule_id": [153]}
                    ),
                )
                self.assertEqual(
                    result["status"],
                    "rejected" if type(code) is int else "submission_unknown",
                )
                self.assertIsNone(result["rule_id"])

    def test_old_unknown_code_200_null_id_is_not_recovered_or_reposted(self):
        # This is the complete old saved receipt shape. The old implementation
        # discarded the successful response's array and did not persist raw.
        first_submit = Mock(
            return_value={"success": True, "code": 200, "rule_id": None}
        )
        original = self.deliver(self.root, first_submit)
        self.assertEqual(original["status"], "submission_unknown")
        self.assertEqual(original["reason"], "success_receipt_incomplete")
        self.assertEqual(original["code"], 200)
        self.assertIsNone(original["rule_id"])
        path = self.root / original["delivery_id"] / "result.json"
        before = path.read_bytes()
        later_submit = Mock(
            return_value={"success": True, "code": 200, "rule_id": [153]}
        )
        result = self.deliver(self.root, later_submit)
        self.assertEqual(result, original)
        self.assertEqual(path.read_bytes(), before)
        later_submit.assert_not_called()
        first_submit.assert_called_once()

    def test_actual_two_phase_tree_workflow_accepts_array_receipt_and_reuses_result(
        self,
    ):
        def generate(request):
            answer = fixtures.reply(request)
            answer["filter"] = {
                "topic": "synthetic-topic",
                "match": {
                    "name": "action",
                    "operator": "Eq",
                    "value": "inventory-versions",
                    "field_role": "event_field",
                    "basis": "Synthetic original event action",
                },
            }
            del answer["topic_review"]["field_roles"]
            return {**fixtures.response(request), "content": json.dumps(answer)}

        model = Mock(side_effect=generate)
        submit = Mock(return_value={"success": True, "code": 200, "rule_id": [153]})
        processor = fixtures.module.TopicProcessor(
            self.root / "workflow",
            load_case=lambda _: fixtures.case(),
            load_rules=lambda: Mock(get_rule=Mock(return_value=fixtures.rule())),
            generate=model,
            model="synthetic-model",
            budget=fixtures.Budget(),
            submit=submit,
            ai_contract="topic-condition-tree-v1",
        )
        first = processor.process(fixtures.signal())
        self.assertEqual(first["status"], "received")
        self.assertEqual(first["zeus_delivery_status"], "submitted")
        self.assertEqual(first["zeus_rule_id"], 153)
        self.assertTrue(processor.verify_durable_result(fixtures.signal(), first))
        second = processor.process(fixtures.signal())
        self.assertEqual(second["record_id"], first["record_id"])
        self.assertEqual(second["model_calls_this_attempt"], 0)
        self.assertEqual(model.call_count, 2)
        submit.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
