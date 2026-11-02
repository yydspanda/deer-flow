bash <<'SOC_STEP03A'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
from pathlib import Path

root = Path.cwd()
if not (root / "service/io_models.py").is_file() or not (root / "my_workflows/zeus/rule_refine/csv_config_source.py").is_file():
    raise SystemExit("停止：请确认位于原项目，且 CSV 查询模块已安装")
files = {
    'my_workflows/zeus/rule_refine/ingress.py': r'''"""提取单次接收告警的规则观察；不连接服务，不进行跨请求计数。"""

import hashlib
import json
from datetime import datetime, timezone

from .csv_config_source import AmbiguousRuleError, RuleNotFoundError

MAX_HITS = 1000


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        if 1 <= len(value) <= 512 and value != "待定":
            return value
    return None


def _identifier(value):
    if type(value) is int and value >= 0:
        value = str(value)
    return _text(value)


def normalize_received_alert(payload, rules, *, received_at, mode="demo", company_code=None):
    """company_code 只能由可信调用上下文提供，不能直接使用请求 app_code。"""
    if not isinstance(received_at, datetime) or received_at.utcoffset() is None:
        raise ValueError("received_at 必须是带时区的 datetime")
    if not isinstance(mode, str) or mode not in {"live", "replay", "demo"}:
        raise ValueError("mode 必须是 live、replay 或 demo")
    if company_code is not None:
        company_code = _text(company_code)
        if company_code is None:
            raise ValueError("company_code 必须是非空企业编码")
    timestamp = received_at.astimezone(timezone.utc).isoformat()
    result = {
        "schema_version": "sec_know.rule_observations.v1",
        "status": "skipped",
        "observations": [],
        "issues": [],
        "duplicate_hit_count": 0,
    }

    def issue(code, location):
        result["issues"].append({"code": code, "location": location})

    if mode == "live" and company_code is None:
        issue("company_scope_required", "company_code")
        return result
    if not isinstance(payload, dict):
        issue("invalid_request", "request")
        return result
    source_app = _text(payload.get("app_code"))
    alert_id = _identifier(payload.get("alert_id"))
    if source_app is None or alert_id is None:
        issue(
            "invalid_app_code" if source_app is None else "invalid_alert_id",
            "app_code" if source_app is None else "alert_id",
        )
        return result
    data = payload.get("alert_data")
    if not isinstance(data, dict):
        issue("missing_alert_data", "alert_data")
        return result
    alert = data.get("alert")
    if not isinstance(alert, dict):
        issue("invalid_alert", "alert_data.alert")
        return result
    inner_id = _identifier(alert.get("alertId"))
    if inner_id is None:
        issue("invalid_alert_id", "alert_data.alert.alertId")
        return result
    if inner_id != alert_id:
        issue("alert_identity_mismatch", "alert_data.alert.alertId")
        return result
    hits = alert.get("hitLog")
    location = "alert_data.alert.hitLog"
    if hits is None or hits == []:
        issue("missing_hit_logs", location)
        return result
    if not isinstance(hits, list):
        issue("invalid_hit_logs", location)
        return result
    if len(hits) > MAX_HITS:
        issue("too_many_hit_logs", location)
        return result
    seen = set()
    for index, hit in enumerate(hits):
        item_location = f"{location}[{index}]"
        if not isinstance(hit, dict):
            issue("invalid_hit", item_location)
            continue
        topic = _text(hit.get("topic"))
        rule_code = _text(hit.get("ruleCode"))
        if topic is None or rule_code is None:
            issue("invalid_rule_identity", item_location)
            continue
        identity = (topic, rule_code)
        if identity in seen:
            result["duplicate_hit_count"] += 1
            continue
        seen.add(identity)
        try:
            rule = rules.get_rule(rule_code, company_code=company_code, topic=topic)
        except RuleNotFoundError:
            issue("rule_not_found", item_location)
            continue
        except AmbiguousRuleError:
            issue("rule_ambiguous", item_location)
            continue
        key_parts = ["sec_know.rule_occurrence.v1", mode, source_app, rule["company_code"], topic, rule_code, alert_id]
        key_bytes = json.dumps(key_parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result["observations"].append(
            {
                "source_app": source_app,
                "company_code": rule["company_code"],
                "company_scope_source": ("explicit" if company_code is not None else "csv_unique_match"),
                "topic": topic,
                "rule_code": rule_code,
                "rule_name": rule["rule_name"],
                "rule_config_version": rule["rule_config_version"],
                "rule_source": dict(rule["source"]),
                "alert_id": alert_id,
                "received_at": timestamp,
                "mode": mode,
                "occurrence_key": "sha256:" + hashlib.sha256(key_bytes).hexdigest(),
            }
        )
    if result["observations"]:
        result["status"] = "partial" if result["issues"] else "ready"
    return result
''',
    'test/rule_refine/test_ingress.py': r'''"""人工告警归一化测试；不导入原应用或连接任何服务。"""

import copy
import csv
import importlib
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_ingress_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
csv_module = importlib.import_module(package.__name__ + ".csv_config_source")
normalize = importlib.import_module(package.__name__ + ".ingress").normalize_received_alert
NOW = datetime(2026, 1, 1, 8, tzinfo=timezone(timedelta(hours=8)))


def sample():
    hit = {"topic": "demo_topic", "ruleCode": "DEMO_A"}
    return {
        "app_code": "zeus",
        "alert_id": "101",
        "session_id": "first",
        "alert_data": {
            "alert": {
                "alertId": 101,
                "profileCode": "NOT_A_RULE",
                "hitLog": [hit, dict(hit), {"topic": "demo_topic", "ruleCode": "DEMO_B"}],
            },
            "relatedAlertList": [{"alertId": 999, "hitLog": [hit]}],
        },
    }


def make_rules(directory, rows=None, filename="rules.csv"):
    path = Path(directory) / filename
    rows = (
        rows
        if rows is not None
        else [
            ("demo_company", "demo_topic", "DEMO_A", "人工规则A"),
            ("demo_company", "demo_topic", "DEMO_B", "人工规则B"),
        ]
    )
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["company_code", "topic", "rule_code", "rule_name", "terms", "standard_field_config"])
        writer.writerows([*row, "[]", ""] for row in rows)
    return csv_module.CsvRuleConfigSource(path)


class IngressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name).resolve()
        self.rules = make_rules(self.directory)

    def call(self, payload=None, **options):
        return normalize(
            sample() if payload is None else payload, self.rules, received_at=options.pop("received_at", NOW), **options
        )

    def test_multiple_rules_duplicate_hits_and_input_preserved(self):
        payload = sample()
        original = copy.deepcopy(payload)
        result = self.call(payload)
        self.assertEqual(payload, original)
        self.assertEqual(result["schema_version"], "sec_know.rule_observations.v1")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["issues"], [])
        self.assertEqual(result["duplicate_hit_count"], 1)
        observations = result["observations"]
        self.assertEqual([r["rule_code"] for r in observations], ["DEMO_A", "DEMO_B"])
        for item in observations:
            self.assertEqual(item["alert_id"], "101")
            self.assertEqual(item["source_app"], "zeus")
            self.assertEqual(item["company_code"], "demo_company")
            self.assertEqual(item["company_scope_source"], "csv_unique_match")
            self.assertEqual(item["mode"], "demo")
            self.assertEqual(datetime.fromisoformat(item["received_at"]), NOW)
            self.assertEqual(datetime.fromisoformat(item["received_at"]).utcoffset(), timedelta(0))

    def test_repush_time_session_flow_and_config_revision_keep_keys(self):
        original = self.call()["observations"]
        payload = sample()
        payload.update(session_id="retry", flow_id="different")
        changed_rules = make_rules(
            self.directory,
            [("demo_company", "demo_topic", "DEMO_A", "新名称"), ("demo_company", "demo_topic", "DEMO_B", "新名称")],
            "changed.csv",
        )
        second = normalize(payload, changed_rules, received_at=NOW + timedelta(hours=1))["observations"]
        self.assertEqual([x["occurrence_key"] for x in original], [x["occurrence_key"] for x in second])
        self.assertNotEqual(original[0]["rule_config_version"], second[0]["rule_config_version"])

    def test_keys_isolate_source_alert_topic_rule_company_and_mode(self):
        keys = {r["occurrence_key"] for r in self.call()["observations"]}
        self.assertEqual(len(keys), 2)
        variants = []
        for field, value in [("app_code", "another_source"), ("alert_id", "102")]:
            payload = sample()
            payload[field] = value
            if field == "alert_id":
                payload["alert_data"]["alert"]["alertId"] = 102
            variants.append(self.call(payload)["observations"][0])
        variants.append(self.call(mode="replay")["observations"][0])
        for company, topic in [("other_company", "demo_topic"), ("demo_company", "other_topic")]:
            payload = sample()
            payload["alert_data"]["alert"]["hitLog"] = [{"topic": topic, "ruleCode": "DEMO_A"}]
            rules = make_rules(self.directory, [(company, topic, "DEMO_A", "人工规则")], "isolation.csv")
            variants.append(normalize(payload, rules, received_at=NOW)["observations"][0])
        for item in variants:
            self.assertNotIn(item["occurrence_key"], keys)
            keys.add(item["occurrence_key"])

    def test_related_alerts_and_profile_code_never_supply_current_hits(self):
        payload = sample()
        payload["alert_data"]["alert"]["hitLog"] = []
        result = self.call(payload)
        self.assertEqual(result["observations"], [])
        self.assertEqual(result["issues"][0]["code"], "missing_hit_logs")

    def test_invalid_envelopes_or_mismatched_ids_produce_no_observations(self):
        cases = [[], "payload", {}, {**sample(), "app_code": True}, {**sample(), "alert_data": None}]
        for value in [True, -1, 1.5, [], "", "x" * 513]:
            cases.append({**sample(), "alert_id": value})
        for inner in [True, "102", None, [], ""]:
            payload = sample()
            payload["alert_data"]["alert"]["alertId"] = inner
            cases.append(payload)
        for payload in cases:
            with self.subTest(payload=payload):
                result = self.call(payload)
                self.assertEqual(result["status"], "skipped")
                self.assertEqual(result["observations"], [])
                self.assertTrue(result["issues"])

    def test_integer_and_string_ids_normalize_without_losing_leading_zeros(self):
        payload = sample()
        payload["alert_id"] = 101
        self.assertEqual(self.call(payload)["observations"][0]["alert_id"], "101")
        payload["alert_id"] = "0101"
        self.assertEqual(self.call(payload)["issues"][0]["code"], "alert_identity_mismatch")

    def test_invalid_unknown_hits_are_visible_and_valid_hits_survive(self):
        payload = sample()
        payload["alert_data"]["alert"]["hitLog"] = [
            None,
            {"topic": "demo_topic", "ruleCode": "待定"},
            {"topic": "demo_topic", "ruleCode": "MISSING"},
            {"topic": "demo_topic", "ruleCode": "DEMO_A"},
        ]
        result = self.call(payload)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(len(result["observations"]), 1)
        self.assertEqual(
            {i["code"] for i in result["issues"]}, {"invalid_hit", "invalid_rule_identity", "rule_not_found"}
        )
        self.assertTrue(all(i["location"] for i in result["issues"]))

    def test_ambiguous_csv_scope_requires_explicit_company(self):
        self.rules = make_rules(
            self.directory, [("a", "demo_topic", "DEMO_A", "规则A"), ("b", "demo_topic", "DEMO_A", "规则B")]
        )
        payload = sample()
        payload["alert_data"]["alert"]["hitLog"] = payload["alert_data"]["alert"]["hitLog"][:1]
        self.assertEqual(self.call(payload)["issues"][0]["code"], "rule_ambiguous")
        row = self.call(payload, company_code="b")["observations"][0]
        self.assertEqual((row["company_code"], row["company_scope_source"]), ("b", "explicit"))
        self.assertEqual(self.call(payload, company_code="missing")["observations"], [])

    def test_live_requires_explicit_company_and_does_not_guess_from_app_code(self):
        self.assertEqual(self.call(mode="live")["issues"][0]["code"], "company_scope_required")
        rows = self.call(mode="live", company_code="demo_company")["observations"]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(r["company_scope_source"] == "explicit" for r in rows))

    def test_missing_malformed_and_excessive_hit_lists(self):
        for hits, code in [
            (None, "missing_hit_logs"),
            ([], "missing_hit_logs"),
            ({}, "invalid_hit_logs"),
            ("[]", "invalid_hit_logs"),
            ([{}] * 1001, "too_many_hit_logs"),
        ]:
            payload = sample()
            payload["alert_data"]["alert"]["hitLog"] = hits
            result = self.call(payload)
            self.assertEqual(result["observations"], [])
            self.assertEqual(result["issues"][0]["code"], code)

    def test_invalid_caller_options_raise(self):
        for options in [
            {"received_at": datetime(2026, 1, 1)},
            {"received_at": "2026-01-01"},
            {"mode": "unknown"},
            {"mode": []},
            {"company_code": " "},
            {"company_code": False},
        ]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.call(**options)

    def test_provider_failure_is_not_silently_reported_as_missing_rule(self):
        rules = Mock()
        rules.get_rule.side_effect = OSError("injected source failure")
        with self.assertRaises(OSError):
            normalize(sample(), rules, received_at=NOW)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        with tempfile.TemporaryDirectory() as temporary:
            rules = make_rules(temporary)
            first = normalize(sample(), rules, received_at=NOW)
            repeated = sample()
            repeated["session_id"] = "second"
            second = normalize(repeated, rules, received_at=NOW + timedelta(minutes=1))
            print(
                json.dumps(
                    {
                        "mode": "demo",
                        "status": first["status"],
                        "input_hit_count": 3,
                        "unique_rule_count": len(first["observations"]),
                        "duplicate_hit_count": first["duplicate_hit_count"],
                        "repush_keys_unchanged": [r["occurrence_key"] for r in first["observations"]]
                        == [r["occurrence_key"] for r in second["observations"]],
                        "company_scope_source": [r["company_scope_source"] for r in first["observations"]],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    else:
        unittest.main(verbosity=2)
''',
}
for name, content in files.items():
    compile(content, name, "exec")
    target = root / name
    if target.is_symlink() or (target.exists() and (not target.is_file() or target.read_text(encoding="utf-8") != content)):
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
.venv/bin/python test/rule_refine/test_ingress.py
.venv/bin/python test/rule_refine/test_ingress.py --demo
SOC_STEP03A
