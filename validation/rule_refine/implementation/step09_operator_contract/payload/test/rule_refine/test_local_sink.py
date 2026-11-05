"""人工 DEMO_RULE 本地收件测试；不加载原应用、CSV、真实配置或模型。"""

import argparse
import copy
import hashlib
import importlib
import json
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from pydantic import ValidationError

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_local_receipt_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
contracts = importlib.import_module(package.__name__ + ".contracts")
module = importlib.import_module(package.__name__ + ".local_sink")
LocalRecommendationSink = module.LocalRecommendationSink
LocalReceiptError = module.LocalReceiptError
ReceiptConflictError = module.ReceiptConflictError


def sample():
    return {
        "recommendation_id": "00000000-0000-4000-8000-000000000001",
        "company_code": "demo",
        "rule_code": "DEMO_RULE",
        "rule_name": "人工测试规则",
        "rule_config_version": "manual-demo-v1",
        "issue": "仅用于人工收件测试",
        "recommendation": "建议人工复核示例排除条件",
        "certainty": "人工构造，不代表规则有效性",
        "evidence": {
            "window_start": "2026-01-01T00:00:00Z",
            "window_end": "2026-01-02T00:00:00Z",
            "alert_count": 1,
            "sample_alert_ids": ["DEMO_ALERT_1"],
        },
        "filter": {
            "topic": "demo_topic",
            "expression": "A",
            "A": {"name": "demo_host", "value": "example.invalid", "operator": "Eq"},
        },
    }


def canonical(payload):
    expanded = contracts.RuleRecommendation.model_validate(payload).model_dump(
        mode="json"
    )
    return json.dumps(
        expanded,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


class LocalReceiptTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name).resolve() / "receipts"
        self.sink = LocalRecommendationSink(self.directory)

    def saved_file(self):
        return self.directory / (sample()["recommendation_id"] + ".json")

    def assert_only_receipt(self):
        self.assertEqual(list(self.directory.iterdir()), [self.saved_file()])

    def test_receipt_full_json_hash_permissions_and_unchanged_input(self):
        payload = sample()
        original = copy.deepcopy(payload)
        receipt = self.sink.submit(payload)
        raw = self.saved_file().read_bytes()
        self.assertEqual(self.sink.directory, self.directory)
        self.assertEqual(payload, original)
        self.assertEqual(raw, canonical(payload))
        self.assertIn("人工测试规则".encode("utf-8"), raw)
        self.assertEqual(receipt["recommendation_id"], payload["recommendation_id"])
        self.assertEqual(receipt["delivery_mode"], "mock")
        self.assertEqual(receipt["status"], "received")
        self.assertIs(receipt["applied"], False)
        self.assertIs(receipt["duplicate"], False)
        self.assertEqual(receipt["content_sha256"], hashlib.sha256(raw).hexdigest())
        saved = json.loads(raw)
        self.assertIs(saved["automatic_apply"], False)
        self.assertEqual(saved["change_type"], "add_exclusion")
        self.assertEqual(saved["evidence"]["count_basis"], "received_unique_alerts")
        self.assertNotIn("delivery_mode", saved)
        self.assertEqual(stat.S_IMODE(self.saved_file().stat().st_mode), 0o600)
        self.assert_only_receipt()

    def test_missing_id_and_non_dictionary_are_rejected(self):
        self.assertTrue(issubclass(LocalReceiptError, ValueError))
        missing = sample()
        missing.pop("recommendation_id")
        for payload in (
            missing,
            None,
            [],
            "{}",
            contracts.RuleRecommendation.model_validate(sample()),
        ):
            with self.subTest(payload_type=type(payload).__name__):
                with self.assertRaises(LocalReceiptError):
                    self.sink.submit(payload)
        self.assertFalse(self.directory.exists())

    def test_invalid_contract_is_rejected_before_creating_directory(self):
        cases = [
            ("recommendation_id", "../../outside"),
            ("recommendation_id", None),
            ("automatic_apply", True),
            ("automatic_apply", "false"),
            ("automatic_apply", 0),
            ("change_type", "delete_rule"),
            ("rule_code", " "),
            ("unexpected_field", "forbidden"),
            ("filter", {**sample()["filter"], "expression": "UNKNOWN"}),
        ]
        for key, value in cases:
            with self.subTest(field=key, value=value):
                with self.assertRaises((ValidationError, LocalReceiptError)):
                    self.sink.submit({**sample(), key: value})
        self.assertFalse(self.directory.exists())

    def test_existing_directory_needs_no_csv_or_other_configuration(self):
        self.directory.mkdir()
        receipt = LocalRecommendationSink(str(self.directory)).submit(sample())
        self.assertIs(receipt["applied"], False)
        self.assertEqual(
            json.loads(self.saved_file().read_text(encoding="utf-8"))[
                "rule_config_version"
            ],
            "manual-demo-v1",
        )
        self.assert_only_receipt()

    def test_duplicate_normalizes_uuid_defaults_and_dictionary_order(self):
        original = sample()
        first = self.sink.submit(original)
        normalized = json.loads(canonical(original))
        normalized["recommendation_id"] = "{" + original["recommendation_id"] + "}"
        normalized["rule_name"] = "  人工测试规则  "
        normalized["filter"] = dict(reversed(list(normalized["filter"].items())))
        reordered = dict(reversed(list(normalized.items())))
        second = LocalRecommendationSink(self.directory).submit(reordered)
        self.assertIs(second["duplicate"], True)
        self.assertEqual({**first, "duplicate": True}, second)
        self.assertEqual(self.saved_file().read_bytes(), canonical(original))
        self.assert_only_receipt()

    def test_same_id_different_content_keeps_original_file(self):
        self.assertTrue(issubclass(ReceiptConflictError, LocalReceiptError))
        self.sink.submit(sample())
        original = self.saved_file().read_bytes()
        changed = {**sample(), "issue": "另一项人工建议"}
        with self.assertRaises(ReceiptConflictError):
            LocalRecommendationSink(self.directory).submit(changed)
        self.assertEqual(self.saved_file().read_bytes(), original)
        self.assert_only_receipt()

    def test_write_fsync_and_link_failures_clean_temporary_files(self):
        real_fdopen = os.fdopen

        @contextmanager
        def failed_write(*args, **kwargs):
            with real_fdopen(*args, **kwargs) as stream:
                with patch.object(
                    stream, "write", side_effect=OSError("injected write failure")
                ):
                    yield stream

        for operation in ("write", "fsync", "link"):
            with self.subTest(operation=operation):
                target = "fdopen" if operation == "write" else operation
                effect = (
                    failed_write
                    if operation == "write"
                    else OSError("injected " + operation + " failure")
                )
                with patch.object(module.os, target, side_effect=effect):
                    with self.assertRaises((OSError, LocalReceiptError)):
                        self.sink.submit(sample())
                self.assertFalse(self.saved_file().exists())
                if self.directory.exists():
                    self.assertEqual(list(self.directory.iterdir()), [])

    def test_atomic_link_publishes_complete_fsynced_private_file(self):
        real_link = os.link
        expected = canonical(sample())

        def checked_link(src, dst, *args, **kwargs):
            temporary = Path(src)
            self.assertEqual(temporary.parent, self.directory)
            self.assertTrue(temporary.name.startswith(".receipt-"))
            self.assertEqual(temporary.suffix, ".tmp")
            self.assertEqual(temporary.read_bytes(), expected)
            self.assertEqual(stat.S_IMODE(temporary.stat().st_mode), 0o600)
            self.assertGreaterEqual(fsync.call_count, 1)
            self.assertEqual(Path(dst), self.saved_file())
            self.assertFalse(self.saved_file().exists())
            return real_link(src, dst, *args, **kwargs)

        with patch.object(module.os, "fsync", wraps=os.fsync) as fsync:
            with patch.object(module.os, "link", side_effect=checked_link) as link:
                self.sink.submit(sample())
        link.assert_called_once()
        self.assert_only_receipt()

    def test_two_independent_sinks_concurrently_receive_same_content(self):
        barrier = threading.Barrier(2)

        def submit(_):
            sink = LocalRecommendationSink(self.directory)
            barrier.wait(timeout=10)
            return sink.submit(sample())

        with ThreadPoolExecutor(max_workers=2) as pool:
            receipts = list(pool.map(submit, range(2)))
        self.assertEqual(sorted(item["duplicate"] for item in receipts), [False, True])
        self.assertEqual(receipts[0]["content_sha256"], receipts[1]["content_sha256"])
        self.assertEqual(self.saved_file().read_bytes(), canonical(sample()))
        self.assert_only_receipt()

    def test_two_independent_sinks_concurrently_conflict_without_overwrite(self):
        barrier = threading.Barrier(2)
        payloads = [sample(), {**sample(), "issue": "另一项人工建议"}]

        def submit(payload):
            sink = LocalRecommendationSink(self.directory)
            barrier.wait(timeout=10)
            try:
                return (payload, sink.submit(payload))
            except ReceiptConflictError:
                return (payload, None)

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(submit, payloads))
        winners = [
            (payload, receipt) for payload, receipt in outcomes if receipt is not None
        ]
        self.assertEqual(len(winners), 1)
        payload, receipt = winners[0]
        self.assertIs(receipt["duplicate"], False)
        self.assertEqual(self.saved_file().read_bytes(), canonical(payload))
        self.assertEqual(
            receipt["content_sha256"], hashlib.sha256(canonical(payload)).hexdigest()
        )
        self.assert_only_receipt()


if __name__ == "__main__":
    if "--demo-dir" in sys.argv:
        parser = argparse.ArgumentParser(
            description="提交人工 DEMO_RULE 样例两次；仅保存本地 mock 回执。"
        )
        parser.add_argument("--demo-dir", required=True)
        args = parser.parse_args()
        sink = LocalRecommendationSink(args.demo_dir)
        first = sink.submit(sample())
        second = sink.submit(sample())
        print(
            json.dumps(
                {
                    "delivery_mode": second["delivery_mode"],
                    "first_duplicate": first["duplicate"],
                    "second_duplicate": second["duplicate"],
                    "applied": second["applied"],
                    "saved_file": str(
                        sink.directory / (second["recommendation_id"] + ".json")
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
