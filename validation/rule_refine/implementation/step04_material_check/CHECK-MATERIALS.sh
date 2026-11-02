bash <<'SOC_MATERIALS'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path

if os.environ.get("env_profile", "LOCAL").upper() != "LOCAL":
    raise SystemExit("本次只检查 LOCAL 数据库；当前环境不是 LOCAL，未连接数据库。")
path = Path("nas/data/sec_know_model.sqlite").resolve()
if not path.is_file():
    raise SystemExit("未找到项目本地数据库；未创建文件。请贴回此提示。")

report = {
    "check": "rule_refine_materials",
    "database_scope": "local_sqlite",
    "sample_basis": "latest_20_task_rows_not_unique_alerts",
    "read_only": True,
    "model_calls": 0,
}
counts = Counter()
states = Counter()
response_types = Counter()
alert_ids = set()
rule_pairs = set()
limit_bytes = 1024 * 1024

def parse_json(raw, size, field):
    if size is not None and size > limit_bytes:
        counts[field + "_too_large"] += 1
        return None
    try:
        return json.loads(raw) if raw is not None else None
    except (ValueError, TypeError, RecursionError):
        counts[field + "_invalid_json"] += 1
        return None

def text_value(value):
    if isinstance(value, str):
        value = value.strip()
        if 1 <= len(value) <= 512 and value != "待定":
            return value
    return None

def identifier(value):
    if type(value) is int and value >= 0:
        value = str(value)
    return text_value(value)

connection = None
try:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    connection.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + 5
    connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    rows = connection.execute("""
        SELECT alert_id, status,
               length(CAST(alert_data AS BLOB)),
               CASE WHEN length(CAST(alert_data AS BLOB)) <= ? THEN alert_data END,
               length(CAST(agent_response AS BLOB)),
               CASE WHEN length(CAST(agent_response AS BLOB)) <= ? THEN agent_response END
        FROM uas_app_workflow_task_status
        WHERE app_code = ? AND flow_id = ?
        ORDER BY created_date DESC, id DESC LIMIT 20
    """, (limit_bytes, limit_bytes, "zeus", "alert_agent")).fetchall()
    for outer_id, status, data_size, data_raw, response_size, response_raw in rows:
        state = status if status in {
            "created", "queening", "running", "successed", "failed", "update", "delete"
        } else "other"
        states[state] += 1
        outer_id = identifier(outer_id)
        if outer_id is not None:
            alert_ids.add(outer_id)
        data = parse_json(data_raw, data_size, "alert_data")
        response = parse_json(response_raw, response_size, "agent_response")
        response_types[type(response).__name__] += 1
        if isinstance(response, (dict, list, str)) and response:
            counts["rows_with_nonempty_agent_response"] += 1
        alert = data.get("alert") if isinstance(data, dict) else None
        if not isinstance(alert, dict):
            continue
        counts["rows_with_alert_object"] += 1
        if outer_id is not None and identifier(alert.get("alertId")) == outer_id:
            counts["rows_with_matching_alert_identity"] += 1
        hits = alert.get("hitLog")
        if not isinstance(hits, list):
            continue
        counts["rows_with_hit_log_list"] += 1
        valid_hits = [h for h in hits if isinstance(h, dict) and all(
            text_value(h.get(k)) is not None for k in ("topic", "ruleCode")
        )]
        if valid_hits:
            counts["rows_with_rule_identity"] += 1
        if any(isinstance(h.get("zeusRawLogs"), list) and h["zeusRawLogs"] for h in valid_hits):
            counts["rows_with_rule_and_nonempty_raw_logs"] += 1
        rule_pairs.update((text_value(h["topic"]), text_value(h["ruleCode"])) for h in valid_hits)
    report.update({
        "status": "checked" if rows else "no_local_samples",
        "task_rows": len(rows),
        "distinct_alert_ids": len(alert_ids),
        "distinct_topic_rule_pairs": len(rule_pairs),
        "task_status_counts": dict(states),
        "material_counts": dict(counts),
        "agent_response_json_types": dict(response_types),
    })
except sqlite3.Error as exc:
    raise SystemExit("只读检查未完成：" + type(exc).__name__ + "；" + str(exc)) from None
finally:
    if connection is not None:
        connection.close()
print(json.dumps(report, ensure_ascii=False, indent=2))
PY
SOC_MATERIALS
