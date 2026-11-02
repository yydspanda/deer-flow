bash <<'SOC_STEP05A'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
dependencies = {'my_workflows/zeus/rule_refine/contracts.py': '59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a', 'my_workflows/zeus/rule_refine/materials.py': '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9', 'my_workflows/zeus/rule_refine/rule_config_source.py': 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941'}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
files = {
    'my_workflows/zeus/rule_refine/filter_evaluation.py': r'''"""在提供的日志上试算条件组合；不代表 ZEUS 引擎语义或告警可忽略结论。"""

import hashlib

from .contracts import TOKEN, FilterDefinition
from .materials import _encode


def _postfix(expression):
    # FilterDefinition 已完整校验语法和规模；这里只转换优先级，不执行字符串代码。
    precedence = {"||": 1, "&&": 2}
    output, operators = [], []
    for token in TOKEN.findall(expression.strip()):
        if token == "(":
            operators.append(token)
        elif token == ")":
            while operators[-1] != "(":
                output.append(operators.pop())
            operators.pop()
        elif token in precedence:
            while operators and operators[-1] != "(" and precedence[operators[-1]] >= precedence[token]:
                output.append(operators.pop())
            operators.append(token)
        else:
            output.append(token)
    return output + list(reversed(operators))


def _condition(condition, log):
    name = condition.name
    if name not in log:
        return None, "missing_field"
    value = log[name]
    if value is None:
        return None, "null_value"
    if not isinstance(value, str):
        return None, "non_string_value"
    if condition.operator == "equal":
        return value == condition.value, None
    if condition.operator == "contain":
        return condition.value in value, None
    return condition.value not in value, None


def _combine(postfix, values):
    stack = []
    for token in postfix:
        if token not in ("&&", "||"):
            stack.append(values[token])
            continue
        right, left = stack.pop(), stack.pop()
        if token == "&&":
            result = False if left is False or right is False else None if left is None or right is None else True
        else:
            result = True if left is True or right is True else None if left is None or right is None else False
        stack.append(result)
    return stack[0]


def evaluate_filter(definition: dict, samples: list) -> dict:
    """最多 20 个告警、每个 100 条日志。字段名是顶层原样键，字符串区分大小写。

    调用方提供相同 topic 的日志；此函数不查询配置、不验证其版本或样本完整性。
    输出是逐日志匹配数，不是可忽略告警数、误报率或全窗口降噪率。
    """
    if not isinstance(definition, dict):
        raise ValueError("过滤条件必须为对象")
    _encode(definition, 64 * 1024)
    parsed = FilterDefinition.model_validate(definition)
    if not isinstance(samples, list) or len(samples) > 20:
        raise ValueError("每次最多提供 20 个告警样本")
    seen, empty = set(), []
    for sample in samples:
        if not isinstance(sample, dict):
            raise ValueError("样本必须为对象")
        alert_id = sample.get("alert_id")
        if not isinstance(alert_id, str) or not 1 <= len(alert_id) <= 512 or alert_id != alert_id.strip():
            raise ValueError("告警 ID 必须为 1～512 字符且无首尾空白")
        if alert_id in seen:
            raise ValueError("告警 ID 不能重复")
        seen.add(alert_id)
        logs = sample.get("raw_logs")
        if not isinstance(logs, list) or len(logs) > 100 or any(not isinstance(log, dict) for log in logs):
            raise ValueError("每个样本需要最多 100 条日志对象的列表")
        if not logs:
            empty.append(alert_id)
    raw = _encode({"filter": parsed.model_dump(mode="json"), "samples": samples}, 2 * 1024 * 1024)
    postfix = _postfix(parsed.expression)
    aliases = sorted(set(postfix) - {"&&", "||"})
    conditions = parsed.__pydantic_extra__
    counts = {"matched": 0, "not_matched": 0, "unknown": 0}
    results = []
    for sample in samples:
        for index, log in enumerate(sample["raw_logs"]):
            values, unknown = {}, {}
            for alias in aliases:
                values[alias], reason = _condition(conditions[alias], log)
                if reason:
                    unknown[alias] = reason
            value = _combine(postfix, values)
            status = "unknown" if value is None else "matched" if value else "not_matched"
            counts[status] += 1
            results.append(
                {"alert_id": sample["alert_id"], "log_index": index, "status": status, "unknown_conditions": unknown}
            )
    return {
        "schema_version": "sec_know.rule_filter_evaluation.v1",
        "status": "evaluated" if results else "insufficient_evidence",
        "scope": "provided_logs_only",
        "source_completeness": "not_asserted",
        "engine_compatibility": "not_verified",
        "configuration_version_check": "not_performed",
        "field_catalog_check": "not_performed",
        "automatic_apply": False,
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "input_alert_count": len(samples),
        "input_log_count": len(results),
        "counts": counts,
        "empty_alert_ids": empty,
        "results": results,
    }
''',
    'test/rule_refine/test_filter_evaluation.py': r'''"""人工日志的组合条件试算；不加载应用、不连接模型、数据库或 Redis。"""

import copy
import importlib
import json
import sys
import types
import unittest
from pathlib import Path

before_import = set(sys.modules)
source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_evaluation_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
evaluate = importlib.import_module(package.__name__ + ".filter_evaluation").evaluate_filter


def definition(expression="A"):
    return {
        "topic": "demo-topic",
        "expression": expression,
        **{name: {"name": name.lower(), "value": "yes", "operator": "equal"} for name in ("A", "B", "C")},
    }


def samples(*logs):
    return [{"alert_id": "demo-1", "raw_logs": list(logs)}]


def statuses(result):
    return [row["status"] for row in result["results"]]


def demo():
    return evaluate(
        definition("A && B"),
        samples({"a": "yes", "b": "yes"}, {"a": "yes", "b": "no"}, {"a": "no", "b": "yes"}, {"a": "yes"}),
    )


class FilterEvaluationTests(unittest.TestCase):
    def test_boolean_truth_tables_include_unknown(self):
        values = ("yes", "no", None)
        names = {"T": "matched", "F": "not_matched", "U": "unknown"}
        expected = {"&&": "TFUFFFUFU", "||": "TTTTFUTUU"}
        for operator, answers in expected.items():
            logs = [{"a": left, "b": right} for left in values for right in values]
            with self.subTest(operator=operator):
                self.assertEqual(
                    statuses(evaluate(definition("A " + operator + " B"), samples(*logs))), [names[c] for c in answers]
                )

    def test_precedence_parentheses_and_full_rhs_consumption(self):
        logs = samples({"a": "yes", "b": "no", "c": "no"})
        for expression, expected in (
            (" A || B && C ", "matched"),
            ("(A || B) && C", "not_matched"),
            ("A || (B && C) || A", "matched"),
        ):
            with self.subTest(expression=expression):
                self.assertEqual(statuses(evaluate(definition(expression), logs)), [expected])

    def test_conditions_cannot_be_joined_across_logs(self):
        result = evaluate(definition("A && B"), samples({"a": "yes", "b": "no"}, {"a": "no", "b": "yes"}))
        self.assertEqual(statuses(result), ["not_matched", "not_matched"])
        self.assertEqual(
            [(row["alert_id"], row["log_index"]) for row in result["results"]], [("demo-1", 0), ("demo-1", 1)]
        )

    def test_missing_null_and_non_string_are_unknown_for_negative_condition(self):
        rule = definition()
        rule["A"]["operator"] = "notcontain"
        result = evaluate(rule, samples({}, {"a": None}, {"a": 42}, {"a": False}, {"a": ["yes"]}, {"a": {"x": "yes"}}))
        self.assertEqual(statuses(result), ["unknown"] * 6)
        self.assertEqual(
            [row["unknown_conditions"]["A"] for row in result["results"]],
            ["missing_field", "null_value"] + ["non_string_value"] * 4,
        )

    def test_string_operators_are_case_sensitive_and_do_not_coerce(self):
        expected = {
            "equal": ["matched", "not_matched", "not_matched"],
            "contain": ["matched", "matched", "not_matched"],
            "notcontain": ["not_matched", "not_matched", "matched"],
        }
        for operator, answers in expected.items():
            rule = definition()
            rule["A"]["operator"] = operator
            with self.subTest(operator=operator):
                self.assertEqual(
                    statuses(evaluate(rule, samples({"a": "yes"}, {"a": "yes-long"}, {"a": "YES"}))), answers
                )

    def test_empty_string_equal_and_literal_dotted_field(self):
        rule = definition()
        rule["A"].update(name="request.host", value="")
        result = evaluate(rule, samples({"request.host": ""}, {"request": {"host": ""}}, {"request.host": " "}))
        self.assertEqual(statuses(result), ["matched", "unknown", "not_matched"])
        for operator in ("contain", "notcontain"):
            rule["A"]["operator"] = operator
            with self.assertRaises(ValueError):
                evaluate(rule, [])

    def test_only_used_aliases_report_unknown_even_for_decisive_results(self):
        result = evaluate(definition("A || B"), samples({"a": "yes"}))
        self.assertEqual(statuses(result), ["matched"])
        self.assertEqual(result["results"][0]["unknown_conditions"], {"B": "missing_field"})

    def test_invalid_expressions_aliases_and_operator_are_rejected(self):
        expressions = ["", "A B", "A &&", "A || X", "(A", "A)", "A and B", "__import__('os')", "A;B", "A | B"]
        expressions += ["(" * 33 + "A" + ")" * 33, " || ".join(["A"] * 129)]
        for expression in expressions:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                evaluate(definition(expression), [])
        for rule in (
            {**definition(), "bad": definition()["A"]},
            {**definition(), "A": {"name": "a", "value": "yes", "operator": "regex"}},
        ):
            with self.assertRaises(ValueError):
                evaluate(rule, [])

    def test_invalid_sample_shapes_ids_and_limits(self):
        invalid = (
            None,
            {},
            [None],
            [{"alert_id": "1"}],
            [{"alert_id": "1", "raw_logs": [None]}],
            samples({}) * 2,
            [{"alert_id": str(i), "raw_logs": []} for i in range(21)],
            samples(*([{}] * 101)),
        )
        for value in invalid:
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                evaluate(definition(), value)
        for alert_id in ("", " 1", "1 ", "x" * 513, 1, None):
            with self.subTest(alert_id=alert_id), self.assertRaises(ValueError):
                evaluate(definition(), [{"alert_id": alert_id, "raw_logs": []}])

    def test_json_size_and_unsupported_values_are_rejected(self):
        for value in ("x" * (2 * 1024 * 1024 + 1), float("nan"), object()):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(ValueError):
                evaluate(definition(), samples({"a": value}))

    def test_empty_evidence_and_scope_do_not_claim_alert_suppression(self):
        for entries, empty_ids in (([], []), (samples(), ["demo-1"])):
            result = evaluate(definition(), entries)
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertEqual(result["empty_alert_ids"], empty_ids)
            self.assertEqual(result["input_log_count"], 0)
            self.assertEqual(result["counts"], {"matched": 0, "not_matched": 0, "unknown": 0})
        self.assertEqual(result["scope"], "provided_logs_only")
        self.assertEqual(result["source_completeness"], "not_asserted")
        self.assertEqual(result["engine_compatibility"], "not_verified")
        self.assertEqual(result["configuration_version_check"], "not_performed")
        self.assertEqual(result["field_catalog_check"], "not_performed")
        self.assertIs(result["automatic_apply"], False)

    def test_unchanged_input_and_reproducible_hash_with_counts(self):
        rule, entries = definition(), samples({"a": "yes"})
        original = copy.deepcopy((rule, entries))
        result = evaluate(rule, entries)
        self.assertEqual((rule, entries), original)
        self.assertEqual(result["input_sha256"], evaluate(dict(reversed(list(rule.items()))), entries)["input_sha256"])
        entries[0]["raw_logs"][0]["a"] = "no"
        self.assertNotEqual(result["input_sha256"], evaluate(rule, entries)["input_sha256"])
        result = demo()
        self.assertEqual(result["counts"], {"matched": 1, "not_matched": 2, "unknown": 1})
        self.assertEqual(
            (result["input_alert_count"], result["input_log_count"], result["status"]), (1, 4, "evaluated")
        )

    def test_import_does_not_load_application_or_service_clients(self):
        loaded = set(sys.modules) - before_import
        self.assertFalse(
            any(
                name.split(".")[0] in {"my_workflows", "service", "model", "redis", "sqlalchemy", "openai"}
                for name in loaded
            )
        )


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        outcome = demo()
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": "passed",
                    "counts": outcome["counts"],
                    "cross_log_and_matched": any(row["status"] == "matched" for row in outcome["results"][1:3]),
                    "engine_compatibility": outcome["engine_compatibility"],
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
''',
}
expected = {'my_workflows/zeus/rule_refine/filter_evaluation.py': 'e47ec1171bb6ca5948f5b15fc0a8eaa158782f1d3910dbcad08d262eb8a615fe', 'test/rule_refine/test_filter_evaluation.py': 'dbf0e416955479c5e89cc6cf798418d9a98b5a040f048f900f8e9b71061bf81e'}
for name, content in files.items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改：" + name)
    compile(content, name, "exec")
    target = root / name
    if target.exists() and target.read_bytes() != raw:
        raise SystemExit("停止：已有不同版本，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("已存在且一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_filter_evaluation.py
.venv/bin/python test/rule_refine/test_filter_evaluation.py --demo
SOC_STEP05A
