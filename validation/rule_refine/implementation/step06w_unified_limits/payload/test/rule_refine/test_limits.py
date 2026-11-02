"""Pure local budget configuration and canonical encoding regression tests."""

import importlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_limits_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
limits = importlib.import_module(package.__name__ + ".limits")
materials = importlib.import_module(package.__name__ + ".materials")


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.file = self.root / "config/rule_refine.limits.local.json"
        self.file.parent.mkdir()

    def write(self, value):
        self.file.write_text(json.dumps(value), encoding="utf-8")

    def test_missing_file_returns_documented_defaults_without_writing(self):
        settings = limits.get_limits(self.root)
        self.assertEqual(settings, limits.Limits())
        self.assertEqual(settings.input_bytes, 128 * 1024)
        self.assertEqual(settings.output_bytes, 64 * 1024)
        self.assertEqual(settings.timeout_seconds, 240)
        self.assertEqual(settings.retry_attempts, 3)
        self.assertEqual(settings.retry_delay_seconds, 60)
        self.assertFalse(self.file.exists())

    def test_partial_file_overrides_defaults_and_changes_are_read_next_time(self):
        self.write({"input_kib": 512, "timeout_seconds": 600})
        first = limits.get_limits(self.root)
        self.assertEqual((first.input_bytes, first.timeout_seconds), (512 * 1024, 600))
        self.assertEqual(first.output_kib, 64)
        self.write(
            {
                "input_kib": 1024,
                "output_kib": 128,
                "retry_attempts": 1,
                "retry_delay_seconds": 20,
            }
        )
        changed = limits.get_limits(self.root)
        self.assertEqual(
            (changed.input_kib, changed.output_kib, changed.retry_attempts),
            (1024, 128, 1),
        )

    def test_unknown_duplicate_and_invalid_json_fields_fail_closed(self):
        for raw in (
            "[]",
            "{",
            '{"input_kib":128,"input_kib":256}',
            '{"secret": "VALUE"}',
        ):
            self.file.write_text(raw, encoding="utf-8")
            with self.subTest(raw=raw), self.assertRaises(ValueError) as caught:
                limits.get_limits(self.root)
            self.assertNotIn("VALUE", str(caught.exception))
        self.file.write_bytes(b" " * 16385)
        with self.assertRaises(ValueError):
            limits.get_limits(self.root)

    def test_all_bounds_reject_bool_string_float_and_outside_values(self):
        for name, low, high in (
            ("input_kib", 64, 2048),
            ("output_kib", 16, 1024),
            ("timeout_seconds", 1, 600),
            ("retry_attempts", 1, 5),
            ("retry_delay_seconds", 1, 3600),
        ):
            for value in (low, high):
                self.assertEqual(getattr(limits.Limits(**{name: value}), name), value)
            for value in (low - 1, high + 1, True, str(low), float(low), None):
                self.write({name: value})
                with (
                    self.subTest(name=name, value=value),
                    self.assertRaises(ValueError),
                ):
                    limits.get_limits(self.root)

    def test_encoder_matches_canonical_json_and_exact_byte_limits(self):
        for value in (
            [0],
            [],
            {},
            {"": 0},
            False,
            None,
            "中文",
            '\\"\n',
            {"a": [1, 1.5, "x"]},
        ):
            raw = json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            self.assertEqual(materials._encode(value, len(raw)), raw)
            with self.assertRaises(ValueError):
                materials._encode(value, len(raw) - 1)

    def test_many_small_nodes_and_extra_wrappers_do_not_create_hidden_quota(self):
        data = [{"field_" + str(index): "x" for index in range(300)} for _ in range(20)]
        encoded = materials._encode(data, limits.CANDIDATE_BYTES)
        self.assertLess(len(encoded), 128 * 1024)
        wrapped = {
            "schema_version": "fixture",
            "materials": data,
            "analysis": {"status": "proposal"},
        }
        raw = materials._encode(wrapped, limits.RECORD_BYTES)
        self.assertEqual(json.loads(raw)["materials"], data)
        self.assertEqual(
            materials._encode([None] * 10001, 64 * 1024),
            b"[" + b"null," * 10000 + b"null]",
        )

    def test_cycles_nonfinite_and_non_json_remain_rejected(self):
        cycle = []
        cycle.append(cycle)
        deep = []
        for _ in range(70):
            deep = [deep]
        for value in (
            cycle,
            deep,
            {"x": float("nan")},
            {"x": float("inf")},
            {1: "x"},
            (1, 2),
            1 << 4097,
        ):
            with self.subTest(kind=type(value).__name__), self.assertRaises(ValueError):
                materials._encode(value, limits.CANDIDATE_BYTES)
        shared = ["value"]
        self.assertEqual(
            materials._encode([shared, shared], 128), b'[["value"],["value"]]'
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
