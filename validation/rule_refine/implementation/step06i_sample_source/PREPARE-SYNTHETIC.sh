#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
import copy
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

from my_workflows.zeus.rule_refine.materials import build_rule_materials
from my_workflows.zeus.rule_refine.analysis import build_analysis_request

configuration = {
    "topic": "demo-http",
    "expression": "A",
    "A": {"name": "path", "operator": "contain", "value": "/"},
    "description": "人工演示规则：HTTP路径包含斜线即触发；仅用于隔离链路测试。",
}
digest = hashlib.sha256(json.dumps(configuration, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
rule = {
    "company_code": "demo-company",
    "topic": "demo-http",
    "rule_code": "DEMO_RULE_REFINE_HTTP_HEALTH",
    "rule_name": "人工演示：HTTP健康探测集中触发",
    "rule_config_version": "synthetic-v1:sha256:" + digest,
    "source": {"kind": "synthetic_fixture"},
    "configuration": configuration,
}
rows, payloads = [], []
for number in range(3):
    alert_id = f"DEMO-healthcase-v1-{number}"
    data = {"alert": {
        "alertId": alert_id,
        "hitLog": [{"topic": rule["topic"], "ruleCode": rule["rule_code"], "zeusRawLogs": [{
            "host": "synthetic.invalid", "path": "/health", "method": "GET",
            "user_agent": "RuleRefineSyntheticProbe/1.0", "source_ip": "192.0.2.10",
            "scenario_note": "人工构造的周期健康探测；正常业务属性仍需运营确认。",
        }]}],
    }}
    rows.append({
        "id": "task-" + alert_id, "app_code": "zeus", "flow_id": "alert_agent",
        "alert_id": alert_id, "alert_data": copy.deepcopy(data), "agent_response": None,
    })
    payloads.append({
        "app_code": "zeus", "flow_id": "alert_agent", "session_id": "session-" + alert_id,
        "alert_id": alert_id, "alert_data": copy.deepcopy(data),
    })
case = {"rule": rule, "rows": rows, "payloads": payloads}

class SyntheticRuleSource:
    def get_rule(self, rule_code, *, company_code=None, topic=None):
        if rule_code != rule["rule_code"] or topic != rule["topic"]:
            raise ValueError("人工规则身份不匹配")
        if company_code is not None and company_code != rule["company_code"]:
            raise ValueError("人工规则企业不匹配")
        return copy.deepcopy(rule)

signal = {
    "schema_version": "sec_know.rule_burst_signal.v1",
    "mode": "demo", "namespace": "preview-only", "signal_id": "preview-only",
    "source_app": "zeus", "company_code": rule["company_code"],
    "topic": rule["topic"], "rule_code": rule["rule_code"],
    "current_count": 3,
    "window_start": "2026-01-01T00:00:00+00:00",
    "window_end": "2026-01-01T01:00:00+00:00",
    "time_basis": "provided_received_at",
    "sample_alert_ids": [row["alert_id"] for row in rows],
}
materials = build_rule_materials(signal, SyntheticRuleSource(), rows, source_kind="synthetic_fixture")
request = build_analysis_request(materials)
if materials["status"] != "ready" or len(materials["samples"]) != 3 or request is None:
    sys.exit("停止：人工材料校验未通过，未保存文件。")

parent = Path.home() / "sec-rule-refine-data"
parent.mkdir(mode=0o700, parents=True, exist_ok=True)
path = parent / "synthetic-healthcase-v1.json"
raw = (json.dumps(case, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
fd, temporary = tempfile.mkstemp(prefix=".synthetic-case-", dir=parent)
try:
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    except FileExistsError:
        if path.read_bytes() != raw:
            sys.exit("停止：同名文件内容不同，原文件已保留：" + str(path))
finally:
    Path(temporary).unlink(missing_ok=True)
print(json.dumps({
    "status": "ready", "material_source": "synthetic_fixture",
    "sample_count": 3, "planned_submissions_with_repush": 4,
    "model_calls": 0, "database_calls": 0, "redis_calls": 0,
    "file": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
}, ensure_ascii=False, indent=2))
PY
