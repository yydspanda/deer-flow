bash <<'SOC_STEP02'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
if not (root / "service/io_models.py").is_file() or not (root / "my_workflows/zeus/rule_refine/contracts.py").is_file():
    raise SystemExit("停止：请确认位于原项目，且第1步已完成")
csv_path = Path.home() / "sec-rule-refine-data/rules.csv"
expected = "729b0892ce3bd455d09f8d8b93d40afcf2d78a6f28d25e7753b640c1ac7e1976"
if not csv_path.is_file() or hashlib.sha256(csv_path.read_bytes()).hexdigest() != expected:
    raise SystemExit("停止：CSV不存在或版本与已核对文件不同，请贴回结果")
files = {
    'my_workflows/zeus/rule_refine/csv_config_source.py': r'''"""从 CSV 快照读取规则，后续可由 ZEUS 查询实现替换。"""

import copy
import csv
import hashlib
import io
import json
import math
from pathlib import Path


class CsvRuleConfigError(ValueError):
    pass


class RuleNotFoundError(CsvRuleConfigError):
    pass


class AmbiguousRuleError(CsvRuleConfigError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON存在重复字段")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("JSON包含非标准数值")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("JSON数值超出可表示范围")
    return value


def _parse_json(raw, expected_type, location, nullable=False):
    if nullable and raw in (None, ""):
        return None
    try:
        if not isinstance(raw, str):
            raise ValueError("需要JSON文本")
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant, parse_float=_finite_float)
        if value is None and nullable:
            return None
        if not isinstance(value, expected_type):
            raise ValueError("JSON容器类型不正确")
        return value
    except (ValueError, TypeError, RecursionError):
        raise CsvRuleConfigError(f"{location}：JSON格式或类型不正确") from None


def _configuration(row, ordinal):
    result = dict(row)
    location = f"第{ordinal}条记录"
    terms = _parse_json(row["terms"], list, location + ".terms")
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            raise CsvRuleConfigError(f"{location}.terms[{index}]：必须为对象")
        term_location = f"{location}.terms[{index}]"
        term["config"] = _parse_json(term.get("config"), dict, term_location + ".config")
        if "standard_field_config" in term:
            term["standard_field_config"] = _parse_json(term["standard_field_config"], dict, term_location + ".standard_field_config", nullable=True)
    result["terms"] = terms
    result["standard_field_config"] = _parse_json(row["standard_field_config"], dict, location + ".standard_field_config", nullable=True)
    return result


class CsvRuleConfigSource:
    def __init__(self, path):
        path = Path(path)
        raw = path.read_bytes()
        self.file_sha256 = hashlib.sha256(raw).hexdigest()
        self._entries = []
        self._by_code = {}
        required = {"company_code", "topic", "rule_code", "rule_name", "terms", "standard_field_config"}
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)) or not required.issubset(headers):
                raise CsvRuleConfigError("CSV缺少必需列或存在重复列名")
            for ordinal, row in enumerate(reader, 1):
                if None in row or any(value is None for value in row.values()):
                    raise CsvRuleConfigError(f"第{ordinal}条记录：列数不正确")
                if any(not row[key].strip() for key in ("company_code", "topic", "rule_code")):
                    raise CsvRuleConfigError(f"第{ordinal}条记录：企业、topic或规则编号为空")
                canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                entry = {
                    "company_code": row["company_code"],
                    "topic": row["topic"],
                    "rule_code": row["rule_code"],
                    "rule_name": row["rule_name"],
                    "rule_config_version": "csv-row-v1:sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "raw_record": dict(row),
                    "configuration": _configuration(row, ordinal),
                    "source": {
                        "kind": "csv_snapshot",
                        "file_name": path.name,
                        "file_sha256": self.file_sha256,
                        "data_record_1based": ordinal,
                    },
                }
                self._entries.append(entry)
                self._by_code.setdefault(row["rule_code"], []).append(entry)
        except (UnicodeDecodeError, csv.Error):
            raise CsvRuleConfigError("文件不是有效的UTF-8 CSV") from None
        if not self._entries:
            raise CsvRuleConfigError("CSV没有规则记录")

    @property
    def count(self):
        return len(self._entries)

    def list_rules(self):
        return [{key: entry[key] for key in ("company_code", "topic", "rule_code", "rule_name")} for entry in self._entries]

    def get_rule(self, rule_code, *, company_code=None, topic=None):
        for value in (rule_code, company_code, topic):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CsvRuleConfigError("查询条件必须是非空字符串")
        if rule_code is None:
            raise CsvRuleConfigError("必须提供规则编号")
        matches = [entry for entry in self._by_code.get(rule_code, []) if (company_code is None or entry["company_code"] == company_code) and (topic is None or entry["topic"] == topic)]
        if not matches:
            raise RuleNotFoundError("未找到符合指定范围的规则")
        if len(matches) != 1:
            raise AmbiguousRuleError("规则不唯一：请明确企业和topic；若已指定，检查重复配置记录")
        return copy.deepcopy(matches[0])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="只读检查CSV规则配置")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rule")
    parser.add_argument("--company")
    parser.add_argument("--topic")
    args = parser.parse_args()
    try:
        source = CsvRuleConfigSource(args.csv)
        summary = {"loaded_rules": source.count, "source": "csv_snapshot", "file_sha256": source.file_sha256}
        if args.rule:
            rule = source.get_rule(args.rule, company_code=args.company, topic=args.topic)
            summary["selected_rule"] = {key: rule[key] for key in ("rule_code", "rule_name", "topic", "rule_config_version")}
            summary["selected_rule"]["term_count"] = len(rule["configuration"]["terms"])
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (CsvRuleConfigError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
''',
    'test/rule_refine/test_csv_config_source.py': r'''"""人工 CSV 样例验证；不加载原应用、数据库、真实规则或模型。"""

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
''',
}
for name, content in files.items():
    target = root / name
    if target.exists() and (not target.is_file() or target.read_text(encoding="utf-8") != content):
        raise SystemExit("停止：存在不同内容，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("内容一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_csv_config_source.py
.venv/bin/python my_workflows/zeus/rule_refine/csv_config_source.py   --csv "$HOME/sec-rule-refine-data/rules.csv" --rule RPAADM_002624
SOC_STEP02
