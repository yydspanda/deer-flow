bash <<'SOC_STEP03D'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYUPDATE'
import hashlib
import os
import stat
import tempfile
from pathlib import Path

patches = [
    ('my_workflows/zeus/rule_refine/rule_config_source.py', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941', [
        (0, 0, r'''"""规则配置查询契约；CSV 与未来 ZEUS API 适配器共享，不绑定传输方式。"""

from typing import Protocol


class RuleConfigError(ValueError):
    pass


class RuleNotFoundError(RuleConfigError):
    pass


class AmbiguousRuleError(RuleConfigError):
    pass


class RuleConfigSource(Protocol):
    def get_rule(self, rule_code: str, *, company_code: str | None = None, topic: str | None = None) -> dict:
        """返回唯一匹配；零条/多条分别抛出上述异常，来源故障原样传播。

        结果包含 company_code、topic、rule_code、rule_name、rule_config_version
        和 source.kind；适配器保留原始配置及可核验来源，不冒充已查询 ZEUS。
        """
        ...


def validate_rule_match(rule, *, rule_code, topic, company_code=None):
    """在形成观察前校验提供器返回，错误响应不能混入其他公司或规则。"""
    if not isinstance(rule, dict):
        raise RuleConfigError("规则查询结果必须为对象")
    for key in ("company_code", "topic", "rule_code", "rule_config_version"):
        value = rule.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise RuleConfigError("规则查询结果的身份或版本字段无效：" + key)
    if rule["topic"] != topic or rule["rule_code"] != rule_code:
        raise RuleConfigError("规则查询结果与请求身份不一致")
    if company_code is not None and rule["company_code"] != company_code:
        raise RuleConfigError("规则查询结果超出指定企业范围")
    if not isinstance(rule.get("rule_name"), str):
        raise RuleConfigError("规则查询结果缺少规则名称")
    source = rule.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("kind"), str) or not source["kind"].strip():
        raise RuleConfigError("规则查询结果缺少配置来源")
'''),
    ]),
    ('my_workflows/zeus/rule_refine/csv_config_source.py', '37712dc7adb83e7b4d2adc9b09c4ad2e0f51e139a3dd85ceac7988b20142c1bd', '46c9e527b214160e5ba92d20a188ac092a18ebbf164839cbf42fd8872b30d807', [
        (11, 21, r'''# 保留既有异常导入名；直接执行本文件的 CLI 也继续可用。
if __package__:
    from .rule_config_source import AmbiguousRuleError as AmbiguousRuleError
    from .rule_config_source import RuleConfigError as CsvRuleConfigError
    from .rule_config_source import RuleNotFoundError as RuleNotFoundError
else:
    from rule_config_source import AmbiguousRuleError as AmbiguousRuleError
    from rule_config_source import RuleConfigError as CsvRuleConfigError
    from rule_config_source import RuleNotFoundError as RuleNotFoundError
'''),
        (49, 50, r'''        value = json.loads(
            raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant, parse_float=_finite_float
        )
'''),
        (69, 70, r'''            term["standard_field_config"] = _parse_json(
                term["standard_field_config"], dict, term_location + ".standard_field_config", nullable=True
            )
'''),
        (71, 72, r'''    result["standard_field_config"] = _parse_json(
        row["standard_field_config"], dict, location + ".standard_field_config", nullable=True
    )
'''),
        (121, 122, r'''        return [
            {key: entry[key] for key in ("company_code", "topic", "rule_code", "rule_name")} for entry in self._entries
        ]
'''),
        (129, 130, r'''        matches = [
            entry
            for entry in self._by_code.get(rule_code, [])
            if (company_code is None or entry["company_code"] == company_code)
            and (topic is None or entry["topic"] == topic)
        ]
'''),
        (151, 152, r'''            summary["selected_rule"] = {
                key: rule[key] for key in ("rule_code", "rule_name", "topic", "rule_config_version")
            }
'''),
    ]),
    ('my_workflows/zeus/rule_refine/ingress.py', '5e1e8d195788855bc02d257eeb55a9c5552d2b6782c5dd5ad7045a3fa7926788', '3a7eac234cdb2bd24d2bbc2beb5b52a9ee26fa3199dd72e9b0f301679cb7889a', [
        (6, 7, r'''from .rule_config_source import AmbiguousRuleError, RuleConfigSource, RuleNotFoundError, validate_rule_match
'''),
        (25, 27, r'''def normalize_received_alert(payload, rules: RuleConfigSource, *, received_at, mode="demo", company_code=None):
    """按可信配置目录唯一匹配规则归属；显式公司只接受调用上下文，不读取请求或资产公司。"""
'''),
        (47, 50, r''''''),
        (111, 111, r'''        validate_rule_match(rule, rule_code=rule_code, topic=topic, company_code=company_code)
'''),
        (117, 118, r'''                "company_scope_source": ("explicit" if company_code is not None else "rule_config_unique_match"),
'''),
    ]),
    ('my_workflows/zeus/rule_refine/counter.py', '1fbf0e5908b42a2c7753c2b0d4d51e61624b39213deb06c86d9a9280534cde06', '2b05b81ee7f28034989b4866f5eed5dc2dc5de8a010b9152792d9715e02b95c2', [
        (65, 66, r'''        if self.mode == "live" and observation.get("company_scope_source") not in (
            "explicit",
            "csv_unique_match",
            "rule_config_unique_match",
        ):
'''),
        (118, 120, r'''                    # Keep the WATCH connection reserved until its write transaction.
                    # A separate pipeline batches reads from the same Redis primary;
                    # any intervening change still invalidates the original WATCH.
                    with self.client.pipeline(transaction=False) as read:
                        for key in keys:
                            read.type(key)
                        read.hgetall(keys[1])
                        read.get(keys[2])
                        read.zscore(keys[0], alert_id)
                        if self.mode == "live":
                            read.time()
                        snapshot = read.execute(raise_on_error=False)
                    for actual, kind in zip(snapshot[:3], ("zset", "hash", "string")):
                        if isinstance(actual, Exception):
                            raise actual
                        if _decoded(actual) not in ("none", kind):
'''),
        (121, 121, r'''                    for value in snapshot[3:]:
                        if isinstance(value, Exception):
                            raise value
                    state = {_decoded(k): int(v) for k, v in snapshot[3].items()}
                    raw, score = snapshot[4:6]
'''),
        (123, 124, r'''                        seconds, micros = snapshot[6]
'''),
        (125, 127, r''''''),
        (131, 132, r''''''),
        (133, 135, r'''                    with self.client.pipeline(transaction=False) as read:
                        read.zcount(keys[0], f"({now - w}", now)
                        read.zcount(keys[0], f"({now - 2 * w}", now - w)
                        current, previous = read.execute()
'''),
    ]),
    ('test/rule_refine/test_csv_config_source.py', '1983db5590cb0031026727e632ace2c6a3533131bec27d2893126388f1481792', 'a65d5e498d5be2e6ef70f7518f6cd847dddd96788da1273d18e599cfe198e167', [
        (5, 6, r'''import importlib
'''),
        (9, 9, r'''import types
'''),
        (12, 17, r'''source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_csv_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + ".csv_config_source")
'''),
        (34, 35, r'''        "terms": encode(
            [
                {
                    "term_code": "demo_term",
                    "config": encode({"A": {"value": " example.invalid "}}),
                    "standard_field_config": encode({"host": "source_host"}),
                    "extra": "保留",
                }
            ]
        ),
'''),
        (86, 87, r'''        self.assertEqual(
            result["source"],
            {
                "kind": "csv_snapshot",
                "file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "file_name": path.name,
                "data_record_1based": 1,
            },
        )
'''),
        (101, 102, r'''        for scope, expected in (
            ({"company_code": "demo", "topic": "demo_topic"}, rows[0]),
            ({"company_code": "other"}, rows[1]),
            ({"topic": "other_topic"}, rows[2]),
        ):
'''),
        (104, 105, r'''        for code, scope in (
            ("missing", {}),
            ("demo_rule", {}),
            (" DEMO_RULE", {}),
            ("DEMO_RULE ", {}),
            ("DEMO_RULE", {"company_code": "Demo"}),
            ("DEMO_RULE", {"topic": "demo_topic "}),
            ("DEMO_RULE", {"company_code": "other", "topic": "other_topic"}),
        ):
'''),
        (108, 109, r'''        for code, scope in (
            ("", {}),
            (None, {}),
            (123, {}),
            ("DEMO_RULE", {"company_code": ""}),
            ("DEMO_RULE", {"topic": " \t "}),
        ):
'''),
        (114, 115, r'''        for rows, scope in (
            ([sample(), {**sample(), "company_code": "other"}], {}),
            ([sample(), {**sample(), "topic": "other"}], {"company_code": "demo"}),
            ([sample(), sample()], {"company_code": "demo", "topic": "demo_topic"}),
        ):
'''),
        (136, 138, r'''        cases = [
            ("terms", value)
            for value in (
                "",
                "{}",
                "null",
                "[1]",
                "[null]",
                "NaN",
                '[{"config":"{}","config":"{}"}]',
                '[{"config":"{}","extra":NaN}]',
            )
        ]
        cases += [
            ("config", value)
            for value in (
                "",
                None,
                {},
                [],
                "[]",
                "null",
                "true",
                '{"x":1,"x":2}',
                '{"x":NaN}',
                '{"x":Infinity}',
                '{"x":-Infinity}',
                '{"x":1e400}',
                '{"x":-1e400}',
            )
        ]
'''),
        (139, 140, r'''            cases += [
                (key, value)
                for value in ("[", "[]", "true", '{"x":1,"x":2}', '{"x":NaN}', '{"x":1e400}', '{"x":-1e400}')
            ]
'''),
        (170, 171, r'''        path = self.write(
            [{**sample(), "rule_code": "OTHER_RULE"}, row], fields=list(reversed(row)), name="reordered.csv"
        )
'''),
    ]),
    ('test/rule_refine/test_ingress.py', 'ecd61b84e1dc999ebde35560be94241cce4be442a761d9232d38ad3ed9b5582c', 'e9423d7a3cc74e994cf5bc15c34967669abebd8ad66317580dcbfd7bbf42a8d8', [
        (20, 20, r'''common_module = importlib.import_module(package.__name__ + ".rule_config_source")
'''),
        (84, 85, r'''            self.assertEqual(item["company_scope_source"], "rule_config_unique_match")
'''),
        (178, 180, r'''    def test_live_uses_unique_csv_scope_not_payload_company_fields(self):
        payload = sample()
        payload.update(app_code="another_system", company_code="untrusted_company")
        payload["alert_data"]["alert"]["company_code"] = "untrusted_company"
        payload["alert_data"]["alert"]["asset"] = {"company_code": "asset_company"}
        result = self.call(payload, mode="live")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["issues"], [])
        self.assertEqual(len(result["observations"]), 2)
        for row in result["observations"]:
            self.assertEqual(row["mode"], "live")
            self.assertEqual(row["source_app"], "another_system")
            self.assertEqual(row["company_code"], "demo_company")
            self.assertEqual(row["company_scope_source"], "rule_config_unique_match")

    def test_live_unknown_and_ambiguous_rules_skip_without_guessing_company(self):
        self.rules = make_rules(
            self.directory,
            [
                ("a", "demo_topic", "DEMO_A", "规则A"),
                ("b", "demo_topic", "DEMO_A", "规则B"),
                ("c", "demo_topic", "DEMO_B", "规则C"),
            ],
        )
        payload = sample()
        payload.update(app_code="a", company_code="a")
        payload["alert_data"]["alert"]["asset"] = {"company_code": "a"}
        payload["alert_data"]["alert"]["hitLog"].append({"topic": "demo_topic", "ruleCode": "MISSING"})
        result = self.call(payload, mode="live")
        self.assertEqual(result["status"], "partial")
        self.assertEqual({issue["code"] for issue in result["issues"]}, {"rule_ambiguous", "rule_not_found"})
        self.assertEqual(len(result["observations"]), 1)
        row = result["observations"][0]
        self.assertEqual(
            (row["rule_code"], row["company_code"], row["company_scope_source"]),
            ("DEMO_B", "c", "rule_config_unique_match"),
        )
        payload["alert_data"]["alert"]["hitLog"] = payload["alert_data"]["alert"]["hitLog"][:1]
        self.assertEqual(self.call(payload, mode="live")["status"], "skipped")

    def test_live_explicit_scope_is_preserved_without_cross_company_fallback(self):
'''),
        (183, 183, r'''        result = self.call(mode="live", company_code="another_company")
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["observations"], [])
        self.assertTrue(all(issue["code"] == "rule_not_found" for issue in result["issues"]))
'''),
        (209, 209, r'''
    def test_provider_can_change_without_changing_identity_or_normalization(self):
        csv_rows = self.call(mode="live")["observations"]
        source = self.rules

        class MockRuleApi:
            def get_rule(self, rule_code, *, company_code=None, topic=None):
                row = source.get_rule(rule_code, company_code=company_code, topic=topic)
                row["source"] = {"kind": "mock_rule_api"}
                return row

        api_rows = normalize(sample(), MockRuleApi(), received_at=NOW, mode="live")["observations"]
        self.assertEqual([r["occurrence_key"] for r in api_rows], [r["occurrence_key"] for r in csv_rows])
        for csv_row, api_row in zip(csv_rows, api_rows):
            self.assertEqual(csv_row["rule_source"]["kind"], "csv_snapshot")
            self.assertEqual(api_row["rule_source"]["kind"], "mock_rule_api")
            self.assertEqual(api_row["company_scope_source"], "rule_config_unique_match")
            self.assertEqual(api_row["company_code"], csv_row["company_code"])
        self.assertIs(csv_module.CsvRuleConfigError, common_module.RuleConfigError)
        self.assertIs(csv_module.RuleNotFoundError, common_module.RuleNotFoundError)
        self.assertIs(csv_module.AmbiguousRuleError, common_module.AmbiguousRuleError)
        for error, code in [
            (common_module.RuleNotFoundError, "rule_not_found"),
            (common_module.AmbiguousRuleError, "rule_ambiguous"),
        ]:
            provider = Mock()
            provider.get_rule.side_effect = error("injected")
            result = normalize(sample(), provider, received_at=NOW, mode="live")
            self.assertEqual(result["status"], "skipped")
            self.assertTrue(all(issue["code"] == code for issue in result["issues"]))

    def test_malformed_or_mismatched_provider_result_never_creates_observations(self):
        original = self.rules.get_rule("DEMO_A", topic="demo_topic")
        cases = [None, [], {}, {**original, "topic": "other_topic"}, {**original, "rule_code": "DEMO_B"}]
        for field, value in [
            ("company_code", ""),
            ("company_code", True),
            ("company_code", "different_company"),
            ("rule_config_version", " "),
            ("rule_config_version", None),
            ("source", None),
            ("source", {}),
            ("source", {"kind": " "}),
            ("rule_name", None),
        ]:
            cases.append({**original, field: value})
        for row in cases:
            provider = Mock()
            provider.get_rule.return_value = row
            with self.subTest(row=row), self.assertRaises(common_module.RuleConfigError):
                normalize(sample(), provider, received_at=NOW, mode="live", company_code="demo_company")
'''),
    ]),
    ('test/rule_refine/test_counter.py', 'a420200201d835c28be68113cd6faaa5dc10ef6e9835d370f15dbccf2cbd59ac', 'b2e2c825c19d0dd8372aaf9f1113b4d2e98942edd30d17f1958286f6dee3d9f2', [
        (208, 208, r'''            is_write = pipe.explicit_transaction
'''),
        (209, 210, r'''            if is_write and not lost:
'''),
        (287, 288, r'''            if pipe.explicit_transaction and not injected:
'''),
        (310, 311, r'''            if pipe.explicit_transaction and not injected:
'''),
        (329, 331, r'''            if pipe.explicit_transaction:
                attempts += 1
                self.redis.hset(keys[1], "test_collision", attempts)
'''),
        (344, 345, r'''        execute = Pipeline.execute
        write_attempts = 0

        def disconnect_on_write(pipe, *args, **kwargs):
            nonlocal write_attempts
            if pipe.explicit_transaction:
                write_attempts += 1
                raise failure
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=disconnect_on_write):
'''),
        (349, 350, r'''        self.assertEqual(write_attempts, 1)
'''),
        (352, 352, r'''
    def test_read_batch_failure_preserves_existing_counts_and_state(self):
        self.send("a", 0)
        self.send("b", 1)
        keys = self.counter._keys(observation())
        before = (self.redis.zrange(keys[0], 0, -1, withscores=True), self.redis.hgetall(keys[1]))
        execute = Pipeline.execute
        failure = ConnectionError("injected read batch failure")
        writes = 0

        def fail_read(pipe, *args, **kwargs):
            nonlocal writes
            if not pipe.transaction and not pipe.explicit_transaction:
                raise failure
            if pipe.explicit_transaction:
                writes += 1
            return execute(pipe, *args, **kwargs)

        with patch.object(Pipeline, "execute", new=fail_read):
            with self.assertRaises(ConnectionError) as raised:
                self.send("c", 2)
        self.assertIs(raised.exception, failure)
        self.assertEqual(writes, 0)
        after = (self.redis.zrange(keys[0], 0, -1, withscores=True), self.redis.hgetall(keys[1]))
        self.assertEqual(after, before)
        self.assertIsNone(self.counter.pending(observation()))

    def test_change_after_read_batch_forces_reread_before_counting(self):
        self.send("a", 0)
        self.send("b", 1)
        execute = Pipeline.execute
        racing = []
        injected = False

        def race_after_read(pipe, *args, **kwargs):
            nonlocal injected
            is_read = not pipe.transaction and not pipe.explicit_transaction
            result = execute(pipe, *args, **kwargs)
            if is_read and not injected:
                injected = True
                racing.append(self.counter.observe(observation("c", 2)))
            return result

        with patch.object(Pipeline, "execute", new=race_after_read):
            result = self.send("c", 2)
        self.assertTrue(racing[0]["triggered"])
        self.assertFalse(result["added"])
        self.assertFalse(result["triggered"])
        self.assertEqual(result["current_count"], 3)
        self.assertEqual(result["pending_signal"], racing[0]["pending_signal"])
        self.assertEqual(self.redis.zcard(self.counter._keys(observation())[0]), 3)

    def test_live_accepts_provider_scope_and_rejects_unknown_or_empty_scope(self):
        for scope in ("explicit", "csv_unique_match", "rule_config_unique_match"):
            with self.subTest(scope=scope):
                counter = self.make_counter(mode="live", namespace=uuid4().hex, minimum_count=1)
                item = observation(mode="live", company_scope_source=scope)
                self.keys.update(counter._keys(item))
                result = counter.observe(item)
                self.assertTrue(result["added"])
                self.assertTrue(result["triggered"])
                self.assertEqual(result["pending_signal"]["company_code"], item["company_code"])
                self.assertFalse(counter.observe(item)["added"])
                for change in (
                    {"company_scope_source": "unverified"},
                    {"company_scope_source": None},
                    {"company_code": ""},
                ):
                    with self.subTest(change=change), self.assertRaises(ValueError):
                        counter.observe({**item, **change})
'''),
    ]),
    ('test/rule_refine/check_chain.py', 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', '2d0958c0aac6b25e695c2f321faa59209a5a0f8cfc2cd5e381fb47221ff21cd7', [
        (0, 0, r'''"""15 次人工接收串联检查；随机 Redis 键，精确清理，不调用模型。"""

import argparse
import importlib
import json
import statistics
import sys
import time
import types
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root))
package = types.ModuleType("_rule_refine_chain_check")
package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
sys.modules[package.__name__] = package
Source = importlib.import_module(package.__name__ + ".csv_config_source").CsvRuleConfigSource
normalize = importlib.import_module(package.__name__ + ".ingress").normalize_received_alert
RuleCounter = importlib.import_module(package.__name__ + ".counter").RedisRuleCounter
get_redis = importlib.import_module("util.redis_client").get_redis

parser = argparse.ArgumentParser(description="规则监测人工串联检查")
parser.add_argument("--csv", default=str(Path.home() / "sec-rule-refine-data/rules.csv"))
args = parser.parse_args()
source = Source(args.csv)
catalog = source.list_rules()
identities = Counter((row["topic"], row["rule_code"]) for row in catalog)
selected = next((row for row in catalog if identities[row["topic"], row["rule_code"]] == 1), None)
if selected is None:
    raise SystemExit("停止：CSV 中没有可唯一匹配的规则。")
connection = get_redis()
counter = RuleCounter(connection, mode="demo", minimum_count=5, dedup_seconds=10800)
run = uuid4().hex
keys = set()
results, count_times, chain_times = [], [], []
output = {
    "mode": "demo",
    "csv_rules": source.count,
    "csv_company_count": len({row["company_code"] for row in catalog}),
    "ambiguous_topic_rule_pairs": sum(count > 1 for count in identities.values()),
    "rule_provider": "csv_snapshot",
    "company_scope_source": "rule_config_unique_match",
    "model_calls": 0,
    "real_ingress_connected": False,
}
print("开始串联检查：12 条人工告警 + 3 次重发；不调用模型。", flush=True)
try:
    connection.ping()
    for position, number in enumerate([*range(12), 0, 1, 2], 1):
        identity = f"RULE-REFINE-CHECK-{run}-{number}"
        hit = {"topic": selected["topic"], "ruleCode": selected["rule_code"]}
        payload = {
            "app_code": "zeus",
            "flow_id": "alert_agent",
            "session_id": uuid4().hex,
            "alert_id": identity,
            "alert_data": {"alert": {"alertId": identity, "hitLog": [hit, dict(hit)]}},
        }
        start = time.perf_counter()
        normalized = normalize(payload, source, received_at=datetime.now(timezone.utc), mode="demo")
        if normalized["status"] != "ready" or len(normalized["observations"]) != 1:
            raise RuntimeError("规则解析结果不符合预期")
        observation = normalized["observations"][0]
        keys.update(counter._keys(observation))
        before_count = time.perf_counter()
        result = counter.observe(observation)
        end = time.perf_counter()
        count_times.append((end - before_count) * 1000)
        chain_times.append((end - start) * 1000)
        results.append(result)
        if position % 5 == 0:
            print(f"已完成 {position}/15 次接收检查", flush=True)
    new_signals = sum(item["triggered"] for item in results)
    repeated_added = sum(item["added"] for item in results[12:])
    passed = (
        sum(item["added"] for item in results) == 12
        and results[-1]["current_count"] == 12
        and new_signals == 1
        and repeated_added == 0
        and all(item["pending_signal"] for item in results[4:])
        and len({item["pending_signal"]["signal_id"] for item in results[4:]}) == 1
    )

    def timing(values):
        return {
            "calls": len(values),
            "mean_ms": round(statistics.mean(values), 2),
            "median_ms": round(statistics.median(values), 2),
            "max_ms": round(max(values), 2),
        }

    output.update(
        status="passed" if passed else "failed",
        submitted=len(results),
        unique_count=results[-1]["current_count"],
        new_signal_count=new_signals,
        duplicate_added_count=repeated_added,
        count_call_ms=timing(count_times),
        parse_and_count_ms=timing(chain_times),
    )
except Exception as error:
    output.update(status="failed", error_type=type(error).__name__, completed_calls=len(results))
finally:
    failed_cleanup = []
    for key in sorted(keys):
        try:
            connection.delete(key)
        except Exception:
            failed_cleanup.append(key)
    output["cleanup_ok"] = not failed_cleanup
    if failed_cleanup:
        output["cleanup_failed_keys"] = failed_cleanup
print(json.dumps(output, ensure_ascii=False, indent=2))
if output["status"] != "passed" or not output["cleanup_ok"]:
    raise SystemExit(1)
'''),
    ]),
]
prepared = []
for name, old_hash, new_hash, changes in patches:
    target = Path(name)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SystemExit("停止：目标不是普通文件：" + name)
    original = target.read_bytes() if target.exists() else b""
    digest = hashlib.sha256(original).hexdigest()
    if digest == new_hash:
        print("内容一致：" + name)
        continue
    if digest != old_hash:
        raise SystemExit("停止：文件版本不同，未覆盖：" + name)
    lines = original.decode("utf-8").splitlines(keepends=True)
    for start, end, replacement in reversed(changes):
        lines[start:end] = [replacement]
    updated = "".join(lines).encode("utf-8")
    if hashlib.sha256(updated).hexdigest() != new_hash:
        raise SystemExit("停止：更新结果校验失败：" + name)
    compile(updated, name, "exec")
    backup = target.with_name(target.name + ".before-step03d")
    if original and (backup.is_symlink() or (backup.exists() and backup.read_bytes() != original)):
        raise SystemExit("停止：旧版副本内容不一致：" + str(backup))
    prepared.append((target, backup, original, updated))
for target, backup, original, updated in prepared:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    if original and not backup.exists():
        with backup.open("xb") as stream:
            stream.write(original)
    descriptor, temporary = tempfile.mkstemp(prefix=".rule-step03d-", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("已更新：" + str(target))
PYUPDATE
.venv/bin/python test/rule_refine/test_csv_config_source.py
.venv/bin/python test/rule_refine/test_ingress.py
.venv/bin/python test/rule_refine/test_counter.py
.venv/bin/python test/rule_refine/check_chain.py
SOC_STEP03D
