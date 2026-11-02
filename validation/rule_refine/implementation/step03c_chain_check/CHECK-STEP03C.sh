bash <<'SOC_STEP03C'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
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

root = Path.cwd()
package = types.ModuleType("_rule_refine_chain_check")
package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
sys.modules[package.__name__] = package
Source = importlib.import_module(package.__name__ + ".csv_config_source").CsvRuleConfigSource
normalize = importlib.import_module(package.__name__ + ".ingress").normalize_received_alert
RuleCounter = importlib.import_module(package.__name__ + ".counter").RedisRuleCounter
from util.redis_client import get_redis

source = Source(Path.home() / "sec-rule-refine-data/rules.csv")
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
    "company_scope_source": "csv_unique_match",
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
PY
SOC_STEP03C
