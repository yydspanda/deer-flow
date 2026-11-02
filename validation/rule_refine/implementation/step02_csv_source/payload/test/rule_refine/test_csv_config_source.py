"""人工 CSV 样例验证；不加载原应用、数据库、真实规则或模型。"""

import copy
import csv
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine/csv_config_source.py"
spec = importlib.util.spec_from_file_location("_rule_refine_csv_source", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
CsvRuleConfigSource = module.CsvRuleConfigSource
CsvRuleConfigError = module.CsvRuleConfigError
RuleNotFoundError = module.RuleNotFoundError
AmbiguousRuleError = module.AmbiguousRuleError
IDENTITY = ("company_code", "topic", "rule_code", "rule_name")


def encode(value):
    return json.dumps(value, ensure_ascii=False)


def sample():
    return {
        "company_code": "demo",
        "topic": "demo_topic",
        "rule_code": "DEMO_RULE",
        "rule_name": "人工测试规则",
        "terms": encode([{"term_code": "demo_term", "config": encode({"A": {"value": " example.invalid "}}), "standard_field_config": encode({"host": "source_host"}), "extra": "保留"}]),
        "standard_field_config": encode({"host": "source_host"}),
        "duplicate_removal": '{"window": 60}',
        "extra_note": ' 原样保留, "引号"\n第二行 ',
    }


def version(row):
    content = json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "csv-row-v1:sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


class CsvConfigSourceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def write(self, rows, *, fields=None, name="rules.csv"):
        path = self.directory / name
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields or list(sample()), extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def test_count_and_independent_identity_list(self):
        row = sample()
        source = CsvRuleConfigSource(self.write([row]))
        self.assertEqual(source.count, 1)
        self.assertEqual(source.list_rules(), [{key: row[key] for key in IDENTITY}])
        items = source.list_rules()
        items[0]["rule_name"] = "改动副本"
        items.append({})
        self.assertEqual(source.list_rules(), [{key: row[key] for key in IDENTITY}])
        with self.assertRaises(CsvRuleConfigError):
            CsvRuleConfigSource(self.write([], name="empty.csv"))

    def test_full_raw_record_parsed_configuration_and_provenance(self):
        row = sample()
        path = self.write([row])
        result = CsvRuleConfigSource(path).get_rule("DEMO_RULE")
        self.assertEqual({key: result[key] for key in IDENTITY}, {key: row[key] for key in IDENTITY})
        self.assertEqual(result["raw_record"], row)
        expected = copy.deepcopy(row)
        expected["terms"] = json.loads(row["terms"])
        for term in expected["terms"]:
            for key in ("config", "standard_field_config"):
                term[key] = json.loads(term[key])
        expected["standard_field_config"] = json.loads(row["standard_field_config"])
        self.assertEqual(result["configuration"], expected)
        self.assertEqual(result["rule_config_version"], version(row))
        self.assertEqual(result["source"], {"kind": "csv_snapshot", "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "file_name": path.name, "data_record_1based": 1})

    def test_get_rule_returns_independent_nested_values(self):
        source = CsvRuleConfigSource(self.write([sample()]))
        original = source.get_rule("DEMO_RULE")
        changed = source.get_rule("DEMO_RULE")
        changed["raw_record"]["rule_name"] = "改动副本"
        changed["configuration"]["terms"][0]["config"]["A"]["value"] = "changed.invalid"
        changed["configuration"]["standard_field_config"]["host"] = "changed"
        changed["source"]["file_name"] = "changed.csv"
        self.assertEqual(source.get_rule("DEMO_RULE"), original)

    def test_exact_scope_matching_and_not_found(self):
        rows = [sample(), {**sample(), "company_code": "other"}, {**sample(), "topic": "other_topic"}]
        source = CsvRuleConfigSource(self.write(rows))
        for scope, expected in (({"company_code": "demo", "topic": "demo_topic"}, rows[0]), ({"company_code": "other"}, rows[1]), ({"topic": "other_topic"}, rows[2])):
            with self.subTest(scope=scope):
                self.assertEqual(source.get_rule("DEMO_RULE", **scope)["raw_record"], expected)
        for code, scope in (("missing", {}), ("demo_rule", {}), (" DEMO_RULE", {}), ("DEMO_RULE ", {}), ("DEMO_RULE", {"company_code": "Demo"}), ("DEMO_RULE", {"topic": "demo_topic "}), ("DEMO_RULE", {"company_code": "other", "topic": "other_topic"})):
            with self.subTest(code=code, scope=scope):
                with self.assertRaises(RuleNotFoundError):
                    source.get_rule(code, **scope)
        for code, scope in (("", {}), (None, {}), (123, {}), ("DEMO_RULE", {"company_code": ""}), ("DEMO_RULE", {"topic": " \t "})):
            with self.subTest(invalid_code=code, scope=scope):
                with self.assertRaises(CsvRuleConfigError):
                    source.get_rule(code, **scope)

    def test_ambiguity_including_duplicate_rows_with_same_scope(self):
        for rows, scope in (([sample(), {**sample(), "company_code": "other"}], {}), ([sample(), {**sample(), "topic": "other"}], {"company_code": "demo"}), ([sample(), sample()], {"company_code": "demo", "topic": "demo_topic"})):
            with self.subTest(scope=scope, rows=len(rows)):
                source = CsvRuleConfigSource(self.write(rows))
                self.assertEqual(source.count, 2)
                with self.assertRaises(AmbiguousRuleError):
                    source.get_rule("DEMO_RULE", **scope)

    def test_required_columns_and_nonblank_identity(self):
        self.assertTrue(issubclass(CsvRuleConfigError, ValueError))
        for key in (*IDENTITY, "terms", "standard_field_config"):
            with self.subTest(missing_column=key):
                with self.assertRaises(CsvRuleConfigError):
                    CsvRuleConfigSource(self.write([sample()], fields=[field for field in sample() if field != key]))
        for key in ("company_code", "topic", "rule_code"):
            for value in ("", " \t "):
                with self.subTest(identity=key, value=value):
                    with self.assertRaises(CsvRuleConfigError):
                        CsvRuleConfigSource(self.write([{**sample(), key: value}]))
        row = {**sample(), "rule_name": ""}
        self.assertEqual(CsvRuleConfigSource(self.write([row])).get_rule("DEMO_RULE")["rule_name"], "")

    def test_invalid_json_types_duplicate_keys_and_nonfinite_values(self):
        cases = [("terms", value) for value in ("", "{}", "null", "[1]", "[null]", "NaN", '[{"config":"{}","config":"{}"}]', '[{"config":"{}","extra":NaN}]')]
        cases += [("config", value) for value in ("", None, {}, [], "[]", "null", "true", '{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":-Infinity}', '{"x":1e400}', '{"x":-1e400}')]
        for key in ("term_standard", "standard_field_config"):
            cases += [(key, value) for value in ("[", "[]", "true", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e400}', '{"x":-1e400}')]
        cases += [("term_standard", value) for value in ({}, [], 1)]
        for key, value in cases:
            with self.subTest(field=key, value=value):
                row = sample()
                if key in ("config", "term_standard"):
                    terms = json.loads(row["terms"])
                    terms[0]["config" if key == "config" else "standard_field_config"] = value
                    row["terms"] = encode(terms)
                else:
                    row[key] = value
                with self.assertRaises(CsvRuleConfigError):
                    CsvRuleConfigSource(self.write([row])).get_rule("DEMO_RULE")

    def test_optional_standard_configuration_empty_values(self):
        for top_value in ("", "null"):
            for term_value in ("", None, "null"):
                with self.subTest(top=top_value, term=term_value):
                    row = sample()
                    row["standard_field_config"] = top_value
                    terms = json.loads(row["terms"])
                    terms[0]["standard_field_config"] = term_value
                    row["terms"] = encode(terms)
                    result = CsvRuleConfigSource(self.write([row])).get_rule("DEMO_RULE")
                    self.assertEqual(result["raw_record"], row)
                    self.assertIsNone(result["configuration"]["standard_field_config"])
                    self.assertIsNone(result["configuration"]["terms"][0]["standard_field_config"])

    def test_version_ignores_file_name_column_order_and_record_position(self):
        row = sample()
        first = CsvRuleConfigSource(self.write([row])).get_rule("DEMO_RULE")
        path = self.write([{**sample(), "rule_code": "OTHER_RULE"}, row], fields=list(reversed(row)), name="reordered.csv")
        second = CsvRuleConfigSource(path).get_rule("DEMO_RULE")
        self.assertEqual(first["rule_config_version"], second["rule_config_version"])
        self.assertNotEqual(first["source"]["file_sha256"], second["source"]["file_sha256"])
        self.assertEqual(second["source"]["data_record_1based"], 2)

    def test_every_original_field_participates_in_version(self):
        original = sample()
        for key in original:
            with self.subTest(changed_field=key):
                row = {**original, key: original[key] + ("_changed" if key in IDENTITY else " ")}
                result = CsvRuleConfigSource(self.write([row])).get_rule(row["rule_code"])
                self.assertEqual(result["rule_config_version"], version(row))
                self.assertNotEqual(result["rule_config_version"], version(original))


if __name__ == "__main__":
    unittest.main(verbosity=2)
