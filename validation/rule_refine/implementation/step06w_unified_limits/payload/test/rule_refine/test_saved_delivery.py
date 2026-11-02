"""本地分析留档与交付恢复测试；仅人工数据，不连接模型、数据库或 Redis。"""

import copy
import importlib
import importlib.util
import json
import os
import stat
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "_rule_saved_fixtures", Path(__file__).with_name("test_proposal_check.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
package = fixtures.fixtures.fixtures.package.__name__
delivery = importlib.import_module(package + ".saved_delivery")
Sink = importlib.import_module(package + ".local_sink").LocalRecommendationSink


class SavedDeliveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.store = delivery.LocalAnalysisStore(self.root / "artifacts")
        self.data = fixtures.fixtures.materials()
        self.analysis = fixtures.analyze(self.data)
        self.load = fixtures.loader(self.data)
        self.sink = Sink(self.root / "receipts")

    def save(self):
        return self.store.save(self.data, self.analysis)

    def send(self, saved, sink=None):
        return delivery.deliver_saved(
            self.store,
            saved["record_id"],
            self.load,
            self.sink if sink is None else sink,
        )

    def test_roundtrip_canonical_deduplication_and_unchanged_inputs(self):
        before = copy.deepcopy((self.data, self.analysis))
        first = self.save()
        second = self.store.save(dict(reversed(list(self.data.items()))), self.analysis)
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(first["record_id"], second["record_id"])
        bundle = self.store.load(first["record_id"])
        self.assertEqual((bundle["materials"], bundle["analysis"]), before)
        self.assertEqual((self.data, self.analysis), before)
        self.analysis["diagnostic"] = "different content"
        third = self.save()
        self.assertNotEqual(first["record_id"], third["record_id"])
        self.assertEqual(self.store.load(first["record_id"]), bundle)

    def test_field_dense_materials_and_result_wrappers_save_without_node_failure(self):
        base = fixtures.fixtures.fixtures
        rows = []
        for index in range(20):
            item = base.row(str(101 + index), "dense-" + str(index))
            item["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"] = [
                {"host": "synthetic.invalid", **{"f" + str(n): "x" for n in range(300)}}
            ]
            rows.append(item)
        self.data = fixtures.fixtures.materials(rows)
        self.analysis = fixtures.analyze(self.data)
        self.assertEqual(self.analysis["status"], "proposal")
        saved = self.save()
        loaded = self.store.load(saved["record_id"])
        self.assertEqual(loaded["materials"], self.data)
        self.assertEqual(loaded["analysis"], self.analysis)

    def test_manual_review_is_saved_with_readable_summary_and_no_submission(self):
        self.analysis = {
            **self.analysis,
            "status": "manual_review",
            "recommendation": None,
            "issue": "需要核对",
            "advice": "运营核对",
            "certainty": "待确认",
            "proposed_filter": {"expression": "A"},
            "review_reason": "local_evaluation_not_supported",
        }
        saved, sink = self.save(), Mock()
        with patch.object(
            delivery,
            "check_proposal",
            return_value={
                "status": "manual_review",
                "reason": "local_evaluation_not_supported",
            },
        ):
            outcome = self.send(saved, sink)
        self.assertEqual(outcome["status"], "manual_review")
        self.assertEqual(outcome["manual_review"]["issue"], "需要核对")
        self.assertEqual(
            outcome["manual_review"]["proposed_filter"], {"expression": "A"}
        )
        sink.submit.assert_not_called()
        self.assertFalse(outcome["automatic_apply"])

    def test_concurrent_identical_saves_publish_one_complete_record(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.save(), range(16)))
        self.assertEqual(len({row["record_id"] for row in results}), 1)
        self.assertEqual(sum(not row["duplicate"] for row in results), 1)
        self.assertEqual(len(list((self.store.directory / "records").iterdir())), 1)
        self.assertEqual(
            self.store.load(results[0]["record_id"])["analysis"], self.analysis
        )

    def test_new_storage_and_files_have_private_permissions(self):
        saved = self.save()
        self.send(saved)
        self.assertEqual(self.store.directory, (self.root / "artifacts").resolve())
        for path in self.store.directory.rglob("*"):
            self.assertEqual(
                stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600
            )

    def test_invalid_ids_corrupt_records_and_symlinks_are_rejected(self):
        saved = self.save()
        for identity in ("../outside", "x" * 64, "A" * 64, "", None):
            with (
                self.subTest(identity=identity),
                self.assertRaises((ValueError, OSError)),
            ):
                self.store.load(identity)
        path = Path(saved["saved_file"])
        original = path.read_bytes()
        path.write_bytes(original + b" ")
        with self.assertRaises(ValueError):
            self.store.load(saved["record_id"])
        path.unlink()
        outside = self.root / "outside.json"
        outside.write_bytes(original)
        path.symlink_to(outside)
        with self.assertRaises((ValueError, OSError)):
            self.store.load(saved["record_id"])
        path.unlink()
        os.mkfifo(path)
        with self.assertRaises(ValueError):
            self.store.load(saved["record_id"])

    def test_oversized_or_invalid_input_is_rejected_before_publication(self):
        for data, analysis in (
            (None, self.analysis),
            (self.data, None),
            ({**self.data, "large": "x" * (delivery.LIMIT + 1)}, self.analysis),
        ):
            with self.assertRaises(ValueError):
                self.store.save(data, analysis)
        self.assertFalse(self.store.directory.exists())

    def test_repeat_delivery_uses_same_id_and_retains_attempt_history(self):
        saved = self.save()
        first, second = self.send(saved), self.send(saved)
        self.assertEqual((first["status"], second["status"]), ("received", "received"))
        self.assertFalse(first["receipt"]["duplicate"])
        self.assertTrue(second["receipt"]["duplicate"])
        self.assertEqual(
            first["receipt"]["recommendation_id"],
            self.analysis["recommendation"]["recommendation_id"],
        )
        self.assertEqual(
            first["receipt"]["recommendation_id"],
            second["receipt"]["recommendation_id"],
        )
        self.assertIs(second["automatic_apply"], False)
        self.assertEqual(
            len(list((self.store.directory / "attempts").rglob("*.result.json"))), 2
        )
        self.assertEqual(len(list(self.sink.directory.glob("*.json"))), 1)

    def test_lost_reply_after_receipt_is_unconfirmed_and_retry_is_duplicate(self):
        saved = self.save()

        def lost(payload):
            self.sink.submit(payload)
            raise TimeoutError("SECRET lost reply")

        first = self.send(saved, Mock(submit=Mock(side_effect=lost)))
        self.assertEqual(first["status"], "delivery_unconfirmed")
        self.assertNotIn("SECRET", json.dumps(first))
        retry = self.send(saved)
        self.assertEqual(retry["status"], "received")
        self.assertTrue(retry["receipt"]["duplicate"])
        self.assertEqual(
            retry["receipt"]["recommendation_id"],
            self.analysis["recommendation"]["recommendation_id"],
        )

    def test_wrong_receipt_identity_hash_or_authority_is_not_accepted(self):
        saved = self.save()
        valid = self.sink.submit(self.analysis["recommendation"])
        for key, value in (
            ("recommendation_id", "wrong"),
            ("content_sha256", "0" * 64),
            ("status", "applied"),
            ("applied", True),
            ("duplicate", 1),
            ("delivery_mode", "live"),
            ("extra", "SECRET"),
        ):
            with self.subTest(key=key):
                outcome = self.send(
                    saved, Mock(submit=Mock(return_value={**valid, key: value}))
                )
                self.assertEqual(outcome["status"], "delivery_unconfirmed")
                self.assertIsNone(outcome["receipt"])

    def test_configuration_change_stops_new_attempt_and_keeps_old_receipt(self):
        saved = self.save()
        first = self.send(saved)
        self.load.return_value.get_rule.return_value["rule_config_version"] = "changed"
        sink = Mock()
        outcome = self.send(saved, sink)
        self.assertEqual(
            (outcome["status"], outcome["check_reason"]),
            ("blocked", "configuration_changed"),
        )
        sink.submit.assert_not_called()
        historical = (
            self.store.directory
            / "attempts"
            / saved["record_id"]
            / (first["attempt_id"] + ".result.json")
        )
        self.assertEqual(
            json.loads(historical.read_text(encoding="utf-8"))["status"], "received"
        )

    def test_nonproposal_and_failed_analysis_are_saved_without_submission(self):
        for status, expected in (
            ("insufficient_evidence", "not_applicable"),
            ("analysis_failed", "blocked"),
        ):
            if status == "analysis_failed":
                self.analysis = {
                    **self.analysis,
                    "status": status,
                    "recommendation": None,
                }
            else:
                answer = {
                    **fixtures.fixtures.draft(),
                    "status": status,
                    "filter": None,
                    "sample_alert_ids": [],
                }
                self.analysis = fixtures.analyze(self.data, answer)
            sink = Mock()
            outcome = self.send(self.save(), sink)
            self.assertEqual(outcome["status"], expected)
            sink.submit.assert_not_called()

    def test_request_publication_failure_prevents_submission(self):
        saved, sink = self.save(), Mock()
        with (
            patch.object(delivery, "_publish", side_effect=OSError("disk full")),
            self.assertRaises(OSError),
        ):
            self.send(saved, sink)
        sink.submit.assert_not_called()

    def test_result_publication_failure_preserves_receipt_and_retry_recovers(self):
        saved = self.save()
        publish = delivery._publish

        def fail_result(path, content):
            if str(path).endswith(".result.json"):
                raise OSError("result disk full")
            return publish(path, content)

        with (
            patch.object(delivery, "_publish", side_effect=fail_result),
            self.assertRaises(OSError),
        ):
            self.send(saved)
        self.assertEqual(len(list(self.sink.directory.glob("*.json"))), 1)
        retry = self.send(saved)
        self.assertEqual(retry["status"], "received")
        self.assertTrue(retry["receipt"]["duplicate"])
        self.assertEqual(
            len(list((self.store.directory / "attempts").rglob("*.request.json"))), 2
        )
        self.assertEqual(
            len(list((self.store.directory / "attempts").rglob("*.result.json"))), 1
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        parent = Path.home() / "sec-rule-refine-data"
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = Path(
            tempfile.mkdtemp(prefix="saved-delivery-demo-", dir=parent)
        ).resolve()
        store = delivery.LocalAnalysisStore(root / "artifacts")
        data = fixtures.fixtures.materials()
        analysis = fixtures.analyze(data)
        saved = store.save(data, analysis)
        repeat = store.save(data, analysis)
        sink = Sink(root / "receipts")

        def lost_reply(payload):
            sink.submit(payload)
            raise TimeoutError("人工模拟：接收已完成，但回执丢失")

        first = delivery.deliver_saved(
            store,
            saved["record_id"],
            fixtures.loader(data),
            Mock(submit=Mock(side_effect=lost_reply)),
        )
        retry = delivery.deliver_saved(
            store, saved["record_id"], fixtures.loader(data), sink
        )
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": retry["status"],
                    "saved_record_reused": repeat["duplicate"]
                    and saved["record_id"] == repeat["record_id"],
                    "first_delivery": first["status"],
                    "retry_duplicate": retry["receipt"]["duplicate"],
                    "recommendation_id_preserved": retry["receipt"]["recommendation_id"]
                    == analysis["recommendation"]["recommendation_id"],
                    "automatic_apply": False,
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                    "output_dir": str(root),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
