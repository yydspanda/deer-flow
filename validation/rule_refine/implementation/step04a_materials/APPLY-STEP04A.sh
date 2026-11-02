bash <<'SOC_STEP04A'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
dep = root / 'my_workflows/zeus/rule_refine/rule_config_source.py'
if not (root / 'service/io_models.py').is_file() or not dep.is_file():
    raise SystemExit('停止：请确认在原项目且已经完成前面的规则查询模块。')
if hashlib.sha256(dep.read_bytes()).hexdigest() != 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941':
    raise SystemExit('停止：现有规则查询契约版本不同，未修改文件。')
files = {
    'my_workflows/zeus/rule_refine/materials.py': r'''"""组装规则突增分析材料；纯函数，不连接服务、不执行模型或规则修改。"""

import copy
import hashlib
import json
from datetime import datetime

from .rule_config_source import AmbiguousRuleError, RuleConfigSource, RuleNotFoundError, validate_rule_match


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        if 1 <= len(value) <= 512 and value != "待定":
            return value
    raise ValueError("材料身份字段必须是 1～512 字符的有效字符串")


def _alert_id(value):
    return _text(str(value) if type(value) is int and value >= 0 else value)


def _encode(value, limit):
    # 编码前限制遍历规模，避免超大字符串或深层对象先耗尽资源。
    budget, nodes = limit, 10000

    def visit(item, depth=0):
        nonlocal budget, nodes
        nodes -= 1
        if depth > 32 or nodes < 0:
            raise ValueError("JSON 材料层级或节点过多")
        if isinstance(item, str):
            budget -= len(item)
        elif item is None or type(item) in (bool, float, int):
            if type(item) is int and item.bit_length() > 4096:
                raise ValueError("JSON 数值过大")
            budget -= len(str(item))
        elif isinstance(item, (dict, list)):
            budget -= 2 * len(item) + 2
        else:
            raise ValueError("材料必须使用 JSON 类型")
        if budget < 0:
            raise ValueError("材料超过本步大小上限")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("JSON 字段名必须为字符串")
                visit(key, depth + 1)
                visit(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                visit(child, depth + 1)

    visit(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(raw) > limit:
        raise ValueError("材料超过本步大小上限")
    return raw


def _matching_hit(hit, scope):
    try:
        return _text(hit.get("topic")) == scope["topic"] and _text(hit.get("ruleCode")) == scope["rule_code"]
    except (AttributeError, ValueError):
        return False


def build_rule_materials(signal: dict, rules: RuleConfigSource, task_rows: list, *, source_kind: str) -> dict:
    """消费 v1 信号和已解码任务字典；最多 100 行、20 个告警引用。

    source_kind 只能由调用方选择；人工源仅可配 demo，正式任务源仅可配 live/replay。
    只组装触发时引用的样本，不声称覆盖整个统计窗口；任务时间不作为接收时间。
    """
    if not isinstance(signal, dict) or signal.get("schema_version") != "sec_know.rule_burst_signal.v1":
        raise ValueError("需要 v1 突增信号")
    mode = signal.get("mode")
    if (source_kind, mode) not in {
        ("synthetic_fixture", "demo"),
        ("workflow_task_store", "live"),
        ("workflow_task_store", "replay"),
    }:
        raise ValueError("材料来源和信号模式不匹配")
    scope = {
        key: _text(signal.get(key))
        for key in ("signal_id", "namespace", "source_app", "company_code", "topic", "rule_code")
    }
    times = [datetime.fromisoformat(signal[key]) for key in ("window_start", "window_end")]
    if any(at.utcoffset() is None for at in times) or times[0] >= times[1]:
        raise ValueError("信号窗口必须包含时区且起点早于终点")
    time_basis = signal.get("time_basis", "unknown")
    expected_basis = "redis_observed_at" if mode == "live" else "provided_received_at"
    if time_basis not in ("unknown", expected_basis):
        raise ValueError("信号时间依据与模式不一致")
    refs = signal.get("sample_alert_ids")
    if not isinstance(refs, list) or len(refs) > 20:
        raise ValueError("信号样本引用必须是最多 20 项的列表")
    refs = list(dict.fromkeys(_text(value) for value in refs))
    count = signal.get("current_count")
    if type(count) is not int or count < max(1, len(refs)):
        raise ValueError("信号计数无效")
    if not isinstance(task_rows, list) or len(task_rows) > 100:
        raise ValueError("每次最多提供 100 条任务记录")
    result = {
        "schema_version": "sec_know.rule_materials.v1",
        "status": "insufficient_evidence",
        "mode": mode,
        "material_source": source_kind,
        "signal_scope": scope,
        "window": {
            "start": times[0].isoformat(),
            "end": times[1].isoformat(),
            "count": count,
            "time_basis": time_basis,
        },
        "sample_basis": "signal_sample_alert_ids_only",
        "requested_sample_count": len(refs),
        "rule_version_basis": "configuration_fetched_for_materials_not_frozen_at_trigger",
        "rule": None,
        "samples": [],
        "issues": [],
        "duplicate_task_rows": 0,
        "stored_response_role": "unverified_reference_not_human_label",
    }

    def issue(code, alert_id=None):
        item = {"code": code}
        if alert_id is not None:
            item["alert_id"] = alert_id
        if item not in result["issues"]:
            result["issues"].append(item)

    try:
        # 与入口相同：先唯一匹配，再核对公司；不使用资产的企业字段。
        rule = rules.get_rule(scope["rule_code"], topic=scope["topic"])
    except (RuleNotFoundError, AmbiguousRuleError) as error:
        issue("rule_not_found" if isinstance(error, RuleNotFoundError) else "rule_ambiguous")
        return result
    validate_rule_match(rule, rule_code=scope["rule_code"], topic=scope["topic"], company_code=scope["company_code"])
    _encode(rule, 512 * 1024)
    result["rule"] = copy.deepcopy(rule)
    groups = {}
    invalid = set()
    wanted = set(refs)
    for row in task_rows:
        if (
            not isinstance(row, dict)
            or row.get("app_code") != scope["source_app"]
            or row.get("flow_id") != "alert_agent"
        ):
            continue
        try:
            alert_id = _alert_id(row.get("alert_id"))
        except ValueError:
            continue
        if alert_id not in wanted:
            continue
        try:
            task_id = _text(row.get("id"))
            data = row["alert_data"]
            _encode(data, 1024 * 1024)
            alert = data["alert"]
            if _alert_id(alert["alertId"]) != alert_id:
                raise ValueError("告警身份不一致")
            raw = _encode(alert, 1024 * 1024)
            digest = hashlib.sha256(raw).hexdigest()
            hits = alert["hitLog"]
            if not isinstance(hits, list) or len(hits) > 1000:
                raise ValueError("命中日志格式或数量无效")
            matching = [h for h in hits if _matching_hit(h, scope)]
            logs = []
            for hit in matching:
                items = hit.get("zeusRawLogs")
                if not isinstance(items, list) or any(not isinstance(item, dict) or not item for item in items):
                    raise ValueError("原日志需要非空对象列表")
                logs.extend(items)
            if not 1 <= len(logs) <= 100:
                raise ValueError("缺少对应原日志或日志过多")
            _encode(logs, 64 * 1024)
        except (ValueError, TypeError, KeyError, RecursionError):
            invalid.add(alert_id)
            issue("invalid_or_oversized_alert_material", alert_id)
            continue
        entry = groups.setdefault(alert_id, {"variants": {}, "tasks": set(), "responses": {}})
        if digest in entry["variants"]:
            result["duplicate_task_rows"] += 1
        entry["variants"][digest] = logs
        entry["tasks"].add(task_id)
        response = row.get("agent_response")
        if response not in (None, {}, [], ""):
            try:
                encoded = _encode(response, 16 * 1024)
                entry["responses"][encoded] = response
            except (ValueError, TypeError, RecursionError):
                issue("stored_response_omitted", alert_id)
    for alert_id in refs:
        entry = groups.get(alert_id)
        if entry is None:
            issue("sample_unavailable", alert_id)
            continue
        if alert_id in invalid or len(entry["variants"]) != 1:
            issue("sample_conflicting_or_invalid", alert_id)
            continue
        responses = list(entry["responses"].values())
        if len(responses) > 1:
            issue("stored_response_conflict", alert_id)
        digest, logs = next(iter(entry["variants"].items()))
        result["samples"].append(
            {
                "alert_id": alert_id,
                "task_ids": sorted(entry["tasks"]),
                "main_alert_sha256": digest,
                "raw_logs": copy.deepcopy(logs),
                "stored_response": copy.deepcopy(responses[0]) if len(responses) == 1 else None,
                "stored_response_status": "conflict" if len(responses) > 1 else "present" if responses else "missing",
            }
        )
    if result["samples"]:
        result["status"] = "partial" if result["issues"] else "ready"
    return result
''',
    'test/rule_refine/test_materials.py': r'''"""规则分析材料测试与人工演示；不读取真实任务，不连接外部服务。"""

import copy
import importlib
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_materials_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
build = importlib.import_module(package.__name__ + ".materials").build_rule_materials
common = importlib.import_module(package.__name__ + ".rule_config_source")


def signal(ids=None):
    return {
        "schema_version": "sec_know.rule_burst_signal.v1",
        "mode": "demo",
        "signal_id": "demo-signal",
        "namespace": "demo-namespace",
        "source_app": "zeus",
        "company_code": "demo-company",
        "topic": "demo-topic",
        "rule_code": "DEMO_RULE",
        "current_count": 1000,
        "window_start": "2026-01-01T00:00:00+00:00",
        "window_end": "2026-01-01T01:00:00+00:00",
        "time_basis": "provided_received_at",
        "sample_alert_ids": ["101"] if ids is None else ids,
    }


def row(alert_id="101", task_id="task-a"):
    return {
        "id": task_id,
        "app_code": "zeus",
        "flow_id": "alert_agent",
        "alert_id": alert_id,
        "created_date": "2026-01-02T10:00:00+00:00",
        "alert_data": {
            "alert": {
                "alertId": alert_id,
                "hitLog": [
                    {"topic": "demo-topic", "ruleCode": "DEMO_RULE", "zeusRawLogs": [{"host": "synthetic.invalid"}]}
                ],
            }
        },
        "agent_response": {"handling": "ignore"},
    }


def rules():
    return Mock(
        get_rule=Mock(
            return_value={
                "company_code": "demo-company",
                "topic": "demo-topic",
                "rule_code": "DEMO_RULE",
                "rule_name": "人工演示规则",
                "rule_config_version": "demo-config-v1",
                "source": {"kind": "synthetic_fixture"},
                "terms": [{"expression": "A"}],
            }
        )
    )


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.rules = rules()

    def call(self, rows=None, sig=None, **options):
        return build(
            signal() if sig is None else sig,
            self.rules,
            [row()] if rows is None else rows,
            source_kind=options.get("source_kind", "synthetic_fixture"),
        )

    def codes(self, result):
        return {item["code"] for item in result["issues"]}

    def test_ready_materials_keep_scope_rule_and_reference_provenance(self):
        result = self.call()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["signal_scope"]["company_code"], "demo-company")
        self.assertEqual(result["rule"]["rule_config_version"], "demo-config-v1")
        self.assertEqual(result["sample_basis"], "signal_sample_alert_ids_only")
        self.assertEqual(result["stored_response_role"], "unverified_reference_not_human_label")
        self.assertEqual(result["samples"][0]["stored_response_status"], "present")
        self.rules.get_rule.assert_called_once_with("DEMO_RULE", topic="demo-topic")

    def test_rule_missing_and_ambiguous_return_insufficient_evidence(self):
        for error, code in [
            (common.RuleNotFoundError, "rule_not_found"),
            (common.AmbiguousRuleError, "rule_ambiguous"),
        ]:
            self.rules.get_rule.side_effect = error("private detail")
            result = self.call()
            self.assertEqual((result["status"], result["samples"]), ("insufficient_evidence", []))
            self.assertEqual(self.codes(result), {code})

    def test_wrong_company_and_malformed_provider_results_are_rejected(self):
        original = self.rules.get_rule.return_value
        for value in [
            None,
            {},
            {**original, "company_code": "other"},
            {**original, "topic": "other"},
            {**original, "source": {}},
        ]:
            with self.subTest(value=value), self.assertRaises(common.RuleConfigError):
                self.rules.get_rule.return_value = value
                self.call()

    def test_duplicate_tasks_keep_one_alert_and_all_task_references(self):
        result = self.call([row(task_id="b"), row(task_id="a")])
        self.assertEqual((len(result["samples"]), result["duplicate_task_rows"]), (1, 1))
        self.assertEqual(result["samples"][0]["task_ids"], ["a", "b"])

    def test_hit_rule_identity_trims_whitespace_like_ingress(self):
        data = row()
        data["alert_data"]["alert"]["hitLog"][0].update(topic=" demo-topic ", ruleCode=" DEMO_RULE\t")
        self.assertEqual(self.call([data])["status"], "ready")

    def test_conflicting_alert_versions_are_excluded_not_arbitrarily_selected(self):
        changed = row(task_id="b")
        changed["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"][0]["host"] = "different.invalid"
        result = self.call([row(), changed])
        self.assertEqual(result["samples"], [])
        self.assertIn("sample_conflicting_or_invalid", self.codes(result))

    def test_response_conflict_keeps_raw_evidence_without_using_either_verdict(self):
        changed = row(task_id="b")
        changed["agent_response"] = {"handling": "transfer"}
        result = self.call([row(), changed])
        self.assertEqual(result["status"], "partial")
        sample = result["samples"][0]
        self.assertEqual(sample["raw_logs"], row()["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"])
        self.assertEqual((sample["stored_response"], sample["stored_response_status"]), (None, "conflict"))

    def test_bad_logs_or_mismatched_ids_cannot_be_rescued_by_a_valid_copy(self):
        for logs in [None, [], [{}], "raw", [{"large": "x" * 65536}]]:
            changed = row(task_id="bad")
            changed["alert_data"]["alert"]["hitLog"][0]["zeusRawLogs"] = logs
            self.assertEqual(self.call([row(), changed])["samples"], [])
        changed = row()
        changed["alert_data"]["alert"]["alertId"] = "different"
        self.assertEqual(self.call([changed])["samples"], [])

    def test_other_apps_flows_rules_and_unrequested_alerts_are_isolated(self):
        unrelated = [row("999"), {**row(), "app_code": "other"}, {**row(), "flow_id": "other"}]
        result = self.call([row(), *unrelated])
        self.assertEqual((result["status"], len(result["samples"]), result["duplicate_task_rows"]), ("ready", 1, 0))
        changed = row()
        changed["alert_data"]["alert"]["hitLog"][0]["ruleCode"] = "OTHER_RULE"
        self.assertEqual(self.call([changed])["samples"], [])
        valid = row()
        valid["alert_data"]["relatedAlertList"] = [changed["alert_data"]["alert"]]
        self.assertEqual(self.call([valid])["samples"][0]["raw_logs"], [{"host": "synthetic.invalid"}])

    def test_unbounded_alert_objects_are_excluded_without_recursion_errors(self):
        deep = []
        for _ in range(40):
            deep = [deep]
        cycle = []
        cycle.append(cycle)
        for label, value in [
            ("depth", deep),
            ("cycle", cycle),
            ("string", "x" * (1024 * 1024 + 1)),
            ("nodes", [None] * 10001),
            ("integer", 1 << 4097),
        ]:
            with self.subTest(case=label):
                data = row()
                data["alert_data"]["extra"] = value
                result = self.call([data])
                self.assertEqual((result["status"], result["samples"]), ("insufficient_evidence", []))
                self.assertIn("invalid_or_oversized_alert_material", self.codes(result))

    def test_oversized_response_is_omitted_but_valid_raw_logs_remain(self):
        data = row()
        data["agent_response"] = {"explanation": "x" * (16 * 1024 + 1)}
        result = self.call([data])
        self.assertEqual(result["status"], "partial")
        self.assertEqual(self.codes(result), {"stored_response_omitted"})
        sample = result["samples"][0]
        self.assertEqual(sample["raw_logs"], [{"host": "synthetic.invalid"}])
        self.assertEqual((sample["stored_response"], sample["stored_response_status"]), (None, "missing"))

    def test_empty_samples_and_bad_modes_windows_or_bounds(self):
        self.assertEqual(self.call([], signal([]))["status"], "insufficient_evidence")
        self.assertIn("sample_unavailable", self.codes(self.call([])))
        for update in [
            {"mode": "live"},
            {"current_count": True},
            {"current_count": 0},
            {"sample_alert_ids": [str(n) for n in range(21)]},
            {"window_start": "2026-01-01T00:00:00"},
        ]:
            with self.subTest(update=update), self.assertRaises(ValueError):
                self.call(sig={**signal(), **update})
        with self.assertRaises(ValueError):
            self.call([row()] * 101)
        live = self.call(
            sig={**signal(), "mode": "live", "time_basis": "redis_observed_at"}, source_kind="workflow_task_store"
        )
        self.assertEqual(live["status"], "ready")
        self.assertEqual(live["window"]["time_basis"], "redis_observed_at")

    def test_input_is_unchanged_and_returned_nested_data_is_independent(self):
        sig, rows = signal(), [row()]
        before = copy.deepcopy((sig, rows, self.rules.get_rule.return_value))
        result = self.call(rows, sig)
        self.assertEqual((sig, rows, self.rules.get_rule.return_value), before)
        result["rule"]["terms"][0]["expression"] = "changed"
        result["samples"][0]["raw_logs"][0]["host"] = "changed"
        result["samples"][0]["stored_response"]["handling"] = "changed"
        self.assertEqual((sig, rows, self.rules.get_rule.return_value), before)

    def test_task_creation_time_outside_window_does_not_redefine_receive_time(self):
        self.assertEqual(self.call()["status"], "ready")
        result = self.call([{**row(), "created_date": "1999-01-01T00:00:00Z"}])
        self.assertEqual(result["window"]["count"], 1000)
        self.assertEqual(result["window"]["time_basis"], "provided_received_at")
        self.assertEqual(len(result["samples"]), 1)

    def test_provider_transport_failures_propagate(self):
        failure = OSError("injected transport failure")
        self.rules.get_rule.side_effect = failure
        with self.assertRaises(OSError) as caught:
            self.call()
        self.assertIs(caught.exception, failure)


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        rows = [row(str(n), f"task-{n}") for n in range(101, 104)] + [row("101", "task-retry")]
        result = build(signal(["101", "102", "103"]), rules(), rows, source_kind="synthetic_fixture")
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": result["status"],
                    "input_task_rows": len(rows),
                    "requested_samples": result["requested_sample_count"],
                    "material_samples": len(result["samples"]),
                    "duplicate_task_rows": result["duplicate_task_rows"],
                    "issue_count": len(result["issues"]),
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
expected = {'my_workflows/zeus/rule_refine/materials.py': '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9', 'test/rule_refine/test_materials.py': '3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76'}
for name, content in files.items():
    if hashlib.sha256(content.encode("utf-8")).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改文件：" + name)
    compile(content, name, "exec")
    target = root / name
    if target.exists() and target.read_bytes() != content.encode("utf-8"):
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
.venv/bin/python test/rule_refine/test_materials.py
.venv/bin/python test/rule_refine/test_materials.py --demo
SOC_STEP04A
