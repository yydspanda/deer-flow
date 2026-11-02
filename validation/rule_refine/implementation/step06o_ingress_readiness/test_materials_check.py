import ast
import io
import json
import pickle
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import materials_check as tool


def payload(identity="PRIVATE-alert", path="/health"):
    return {
        "alert_id": identity,
        "alert_data": {
            "alert": {
                "alertId": identity,
                "hitLog": [
                    {
                        "topic": "demo-http",
                        "ruleCode": "DEMO-RULE",
                        "zeusRawLogs": [{"path": path, "private": "SECRET-RAW"}],
                    }
                ],
            }
        },
    }


class MaterialTests(unittest.TestCase):
    def test_allowlist_is_identical_to_existing_reviewed_loader(self):
        source = (
            Path(__file__).resolve().parents[3]
            / "compact_zeus/shared/restricted_dataframe_pickle.py"
        )
        tree = ast.parse(source.read_text(encoding="utf-8"))
        assignment = next(
            n
            for n in tree.body
            if isinstance(n, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "ALLOWED_PICKLE_GLOBALS"
                for t in n.targets
            )
        )
        self.assertEqual(
            tool.ALLOWED_PICKLE_GLOBALS, ast.literal_eval(assignment.value)
        )

    def test_real_dataframe_roundtrip_and_counts_without_raw_output(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / tool.SAMPLE
            one = payload()
            one["alert_data"]["alert"]["hitLog"] *= 2
            two = payload("PRIVATE-second")
            frame = pd.DataFrame({"alert_full_data": [one, json.dumps(one), two, None]})
            # Preserve the corpus format across Pandas 2/3 default index changes.
            frame.columns = pd.Index(frame.columns.tolist(), dtype=object)
            path.write_bytes(pickle.dumps(frame))
            before = path.read_bytes()
            out = tool.sample_info(path, Counter({("demo-http", "DEMO-RULE"): 1}))
            self.assertEqual(out["status"], "inspected")
            self.assertEqual(out["unique_alert_ids"], 2)
            self.assertEqual(out["duplicate_identity_rows"], 1)
            self.assertEqual(out["usable_unique_alerts"], 2)
            self.assertEqual(out["top_10_rules"][0]["unique_alerts"], 2)
            self.assertTrue(out["top_10_rules"][0]["csv_unique_match"])
            self.assertNotIn("PRIVATE", json.dumps(out))
            self.assertNotIn("SECRET", json.dumps(out))
            self.assertEqual(path.read_bytes(), before)

    def test_unapproved_pickle_globals_are_rejected(self):
        with self.assertRaises(pickle.UnpicklingError):
            tool.RestrictedDataFrameUnpickler(io.BytesIO(b"cbuiltins\neval\n.")).load()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / tool.SAMPLE
            path.write_bytes(b"cbuiltins\neval\n.")
            out = tool.sample_info(path, None)
            self.assertEqual(out["error_type"], "UnpicklingError")
            self.assertNotIn("eval", json.dumps(out))

    def test_missing_and_large_samples_do_not_load_or_create(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / tool.SAMPLE
            self.assertEqual(tool.sample_info(path, None)["status"], "sample_not_found")
            self.assertFalse(path.exists())
            with path.open("wb") as stream:
                stream.truncate(tool.MAX_PICKLE + 1)
            with patch.object(
                tool, "bounded_read", side_effect=AssertionError("must not read")
            ):
                self.assertEqual(
                    tool.sample_info(path, None)["status"],
                    "sample_too_large_not_loaded",
                )

    def test_unknown_shapes_and_identity_mismatches_do_not_get_repaired(self):
        self.assertEqual(
            tool.inspect_frame(pd.DataFrame({"unknown": [1]}), None)["status"],
            "unrecognized_columns",
        )
        bad = payload()
        bad["alert_id"] = "different"
        frame = pd.DataFrame({"alert_full_data": [bad, payload(), "invalid-json"]})
        out = tool.inspect_frame(frame, Counter({("demo-http", "DEMO-RULE"): 2}))
        self.assertEqual(out["usable_unique_alerts"], 1)
        self.assertEqual(out["issue_counts"]["identity_mismatch"], 1)
        self.assertFalse(out["top_10_rules"][0]["csv_unique_match"])
        self.assertEqual(bad["alert_id"], "different")

    def test_only_first_200_rows_are_inspected(self):
        frame = pd.DataFrame({"alert_full_data": [payload(str(i)) for i in range(201)]})
        out = tool.inspect_frame(frame, None)
        self.assertEqual(
            (out["total_rows"], out["inspected_rows"], out["usable_unique_alerts"]),
            (201, 200, 200),
        )
        self.assertIsNone(out["top_10_rules"][0]["csv_unique_match"])

    def test_dictionary_budget_and_raw_log_limit(self):
        oversized = payload("large")
        oversized["extra"] = "x" * (2 * 1024 * 1024 + 1)
        cyclic = payload("cycle")
        cyclic["extra"] = cyclic
        truncated = payload("many-logs")
        truncated["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"] = [None] * 100 + [
            {"path": "/health"}
        ]
        frame = pd.DataFrame({"alert_full_data": [oversized, cyclic, truncated]})
        out = tool.inspect_frame(frame, None)
        self.assertEqual(out["usable_unique_alerts"], 0)
        self.assertEqual(out["issue_counts"]["row_not_bounded_json"], 2)
        self.assertEqual(out["issue_counts"]["raw_logs_truncated"], 1)

    def test_injector_is_parsed_without_executing_or_exporting_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "validation/inject_priority9_tasks.py"
            path.parent.mkdir()
            path.write_text(
                "raise RuntimeError('MUST-NOT-EXECUTE')\nAPI_KEY='SECRET-KEY'\nBASE_ALERT_DATA={}\nparser.add_argument('--count', default=5)\nparser.add_argument('--api-key', default=API_KEY)\nrandom.randint(1, 2)\n",
                encoding="utf-8",
            )
            out = tool.injector_info(root)
            self.assertEqual(out["defaults"]["count"], 5)
            self.assertTrue(out["inline_fixture_present"])
            self.assertTrue(out["random_id_call_present"])
            self.assertFalse(out["script_executed"])
            self.assertNotIn("SECRET", json.dumps(out))

    def test_other_three_files_are_stat_only_and_csv_is_reference_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "nas/original"
            original.mkdir(parents=True)
            for name in tool.FILENAMES[1:]:
                (original / name).write_bytes(b"must-not-unpickle")
            csv_path = root / "sec-rule-refine-data/rules.csv"
            csv_path.parent.mkdir()
            csv_path.write_text(
                "topic,rule_code\ndemo-http,DEMO-RULE\n", encoding="utf-8"
            )
            original_read = tool.bounded_read
            reads = []

            def guarded_read(path, limit):
                reads.append(path.name)
                self.assertNotIn(path.name, tool.FILENAMES[1:])
                return original_read(path, limit)

            with (
                patch("pathlib.Path.home", return_value=root),
                patch.object(tool, "bounded_read", side_effect=guarded_read),
            ):
                out = tool.check_materials(root)
            self.assertEqual(out["reference_csv"]["records"], 1)
            self.assertFalse(out["reference_csv"]["running_process_source_verified"])
            self.assertFalse(out["volume_signal_created"])
            self.assertEqual(out["sample"]["status"], "sample_not_found")
            self.assertIn("rules.csv", reads)


if __name__ == "__main__":
    unittest.main(verbosity=2)
