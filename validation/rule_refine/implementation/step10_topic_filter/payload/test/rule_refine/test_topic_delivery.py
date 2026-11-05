"""Synthetic delivery receipts with no HTTP, model, Redis or business database."""

import copy
import importlib
import json
import multiprocessing
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
PACKAGE = "_isolated_topic_delivery"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [str(ROOT)]
    sys.modules[PACKAGE] = package
module = importlib.import_module(PACKAGE + ".topic_delivery")


def payload():
    return {
        "topic": "synthetic-topic",
        "A": {"name": "command", "operator": "Eq", "value": "inventory --versions"},
        "expression": "A",
        "executeType": [3],
    }


def accepted(_payload):
    return {"success": True, "code": 200, "rule_id": 123, "raw": {"secret": "hidden"}}


def competing_process(directory, ready, release, queue):
    def submit(_payload):
        ready.set()
        if not release.wait(timeout=10):
            raise RuntimeError("test_timeout")
        return accepted(_payload)

    result = module.TopicDelivery(directory, submit).deliver(
        payload(), company_code="company", scope="prd-test"
    )
    queue.put(result["status"])


class TopicDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def deliver(self, submit, value=None, **kwargs):
        return module.TopicDelivery(self.root, submit).deliver(
            value or payload(),
            company_code=kwargs.pop("company_code", "company"),
            scope=kwargs.pop("scope", "prd-test"),
            **kwargs,
        )

    def test_success_then_duplicate_across_new_instances(self):
        submit = Mock(side_effect=accepted)
        first = self.deliver(submit)
        second = self.deliver(submit, dict(reversed(list(payload().items()))))
        self.assertEqual(first["status"], "submitted")
        self.assertEqual(second["status"], "duplicate")
        self.assertEqual(second["rule_id"], 123)
        self.assertEqual(first["delivery_id"], second["delivery_id"])
        self.assertEqual(submit.call_count, 1)
        self.assertEqual(first["activation_state"], "managed_by_zeus")
        self.assertNotIn("raw", first)
        stored = "".join(p.read_text() for p in self.root.rglob("*.json"))
        self.assertNotIn("hidden", stored)
        self.assertLess(len(stored.encode()), 4096)

    def test_different_company_scope_type_and_filter_are_separate(self):
        submit = Mock(side_effect=accepted)
        results = [self.deliver(submit), self.deliver(submit, company_code="other")]
        results.append(self.deliver(submit, scope="stg-test"))
        value = payload()
        value["executeType"] = [1]
        results.append(self.deliver(submit, value))
        value = payload()
        value["A"]["value"] = "different"
        results.append(self.deliver(submit, value))
        self.assertEqual(len({x["delivery_id"] for x in results}), 5)
        self.assertEqual(submit.call_count, 5)

    def test_no_history_or_trigger_rule_in_wire_payload(self):
        submit = Mock(side_effect=accepted)
        self.deliver(submit)
        self.assertEqual(submit.call_args.args, (payload(),))

    def test_timeout_is_unknown_and_never_retried(self):
        submit = Mock(side_effect=TimeoutError("secret provider body"))
        first, second = self.deliver(submit), self.deliver(submit)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "submission_unknown")
        self.assertEqual(submit.call_count, 1)
        self.assertNotIn("secret", json.dumps(first))

    def test_process_exit_after_started_is_never_retried(self):
        submit = Mock(side_effect=SystemExit(1))
        with self.assertRaises(SystemExit):
            self.deliver(submit)
        second = self.deliver(submit)
        self.assertEqual(second["status"], "submission_unknown")
        self.assertEqual(second["reason"], "previous_post_result_unknown")
        self.assertEqual(submit.call_count, 1)

    def test_persistence_failure_before_post_prevents_post(self):
        submit = Mock(side_effect=accepted)
        with patch.object(module, "_write", side_effect=OSError("disk_full")):
            with self.assertRaises(OSError):
                self.deliver(submit)
        submit.assert_not_called()

    def test_result_write_failure_after_post_never_resends(self):
        submit = Mock(side_effect=accepted)
        write = module._write

        def limited(path, value):
            if path.name == "result.json":
                raise OSError("disk_full")
            return write(path, value)

        with patch.object(module, "_write", side_effect=limited):
            with self.assertRaises(OSError):
                self.deliver(submit)
        result = self.deliver(submit)
        self.assertEqual(result["status"], "submission_unknown")
        self.assertEqual(submit.call_count, 1)

    def test_server_rejection_is_saved_without_body_and_not_retried(self):
        submit = Mock(
            return_value={"success": False, "code": 69220, "message": "secret"}
        )
        first, second = self.deliver(submit), self.deliver(submit)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "rejected")
        self.assertEqual(first["code"], 69220)
        self.assertEqual(submit.call_count, 1)
        self.assertNotIn("secret", json.dumps(first))

    def test_invalid_success_receipt_is_unknown(self):
        for identity in (None, "", {}, [], True, 0, -1, "id\ninvalid", "x" * 257):
            with self.subTest(identity=identity), tempfile.TemporaryDirectory() as root:
                submit = Mock(
                    return_value={"success": True, "code": 200, "rule_id": identity}
                )
                delivery = module.TopicDelivery(root, submit)
                result = delivery.deliver(
                    payload(), company_code="company", scope="prd-test"
                )
                self.assertEqual(result["status"], "submission_unknown")
                delivery.deliver(payload(), company_code="company", scope="prd-test")
                self.assertEqual(submit.call_count, 1)

    def test_nonstandard_responses_are_unknown(self):
        for response in (
            None,
            [],
            {},
            {"code": True},
            {"code": "200"},
            {"code": 200, "rule_id": 1},
        ):
            with self.subTest(response=response), tempfile.TemporaryDirectory() as root:
                result = module.TopicDelivery(
                    root, Mock(return_value=response)
                ).deliver(payload(), company_code="company", scope="prd-test")
                self.assertEqual(result["status"], "submission_unknown")

    def test_invalid_wire_payload_never_calls_submit(self):
        values = []
        for invalid in ([], [-1], [True], [0, 3], ["3"], [4], None):
            value = payload()
            value["executeType"] = invalid
            values.append(value)
        for field in ("items", "terms", "id", "rule_code"):
            value = payload()
            value[field] = {}
            values.append(value)
        for expression in ("", "A &&", "Z", "A or A", "A;system()", "!(A)"):
            value = payload()
            value["expression"] = expression
            values.append(value)
        value = payload()
        value["B"] = copy.deepcopy(value["A"])
        values.append(value)
        value = payload()
        value["A"]["operator"] = "equal"
        values.append(value)
        value = payload()
        value["A"]["value"] = None
        values.append(value)
        value = payload()
        value["A"]["value"] = "x" * 4097
        values.append(value)
        value = payload()
        value["topic"] = "x" * 101
        values.append(value)
        value = payload()
        del value["executeType"]
        values.append(value)
        for value in values:
            with self.subTest(value=value):
                submit = Mock(side_effect=accepted)
                with self.assertRaises(ValueError):
                    self.deliver(submit, value)
                submit.assert_not_called()

    def test_all_canonical_operators_and_expression_pass(self):
        for operator in module.OPERATORS:
            with self.subTest(operator=operator):
                value = payload()
                value["A"]["operator"] = operator
                value["B"] = copy.deepcopy(value["A"])
                value["expression"] = "(A && B) || A"
                self.assertEqual(self.deliver(accepted, value)["status"], "submitted")

    def test_symlink_lock_or_record_is_rejected(self):
        result = self.deliver(accepted)
        folder = self.root / result["delivery_id"]
        target = folder / "result.json"
        saved = self.root / "saved.json"
        target.rename(saved)
        target.symlink_to(saved)
        submit = Mock(side_effect=accepted)
        with self.assertRaises(OSError):
            self.deliver(submit)
        submit.assert_not_called()

    def test_tampered_receipt_does_not_allow_resend(self):
        result = self.deliver(accepted)
        target = self.root / result["delivery_id"] / "result.json"
        record = json.loads(target.read_text())
        record["delivery_id"] = "wrong"
        target.write_text(json.dumps(record))
        submit = Mock(side_effect=accepted)
        with self.assertRaises(ValueError):
            self.deliver(submit)
        submit.assert_not_called()

    @unittest.skipUnless(hasattr(os, "fork"), "requires POSIX NAS locking")
    def test_simultaneous_workers_only_one_submit(self):
        context = multiprocessing.get_context("fork")
        ready, release, queue = context.Event(), context.Event(), context.Queue()
        process = context.Process(
            target=competing_process, args=(str(self.root), ready, release, queue)
        )
        process.start()
        try:
            self.assertTrue(ready.wait(timeout=10))
            submit = Mock(side_effect=accepted)
            self.assertEqual(self.deliver(submit)["status"], "busy")
            submit.assert_not_called()
            release.set()
            process.join(timeout=10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(queue.get(timeout=2), "submitted")
            self.assertEqual(self.deliver(submit)["status"], "duplicate")
            submit.assert_not_called()
        finally:
            release.set()
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            queue.close()


if __name__ == "__main__":
    unittest.main()
