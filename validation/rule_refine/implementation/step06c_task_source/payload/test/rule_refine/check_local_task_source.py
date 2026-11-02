"""只读检查 LOCAL 任务读取适配器；只输出数量，不输出告警或模型正文。"""

import importlib
import json
import os
import sqlite3
import sys
import time
import types
from collections import Counter
from contextlib import closing
from pathlib import Path

root = Path(__file__).resolve().parents[2]
package = types.ModuleType("_rule_refine_local_task_check")
package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
sys.modules[package.__name__] = package
source = importlib.import_module(package.__name__ + ".task_source")


def main():
    summary = {
        "check": "rule_refine_task_source",
        "database_scope": "local_sqlite",
        "sample_basis": "alert_ids_from_latest_20_task_rows_diagnostic_only",
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
    }
    if os.environ.get("env_profile", "LOCAL").upper() != "LOCAL":
        return {**summary, "status": "skipped_non_local"}
    path = root / "nas/data/sec_know_model.sqlite"
    if not path.is_file():
        return {**summary, "status": "database_missing_not_created"}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)) as db:
            db.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 5
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            latest = db.execute(
                "SELECT alert_id FROM uas_app_workflow_task_status WHERE app_code=? AND flow_id=? "
                "ORDER BY created_date DESC, id DESC LIMIT 20",
                ("zeus", "alert_agent"),
            ).fetchall()
        refs = list(dict.fromkeys(row[0] for row in latest))
        # 此处仅复用信号中的查询字段，不构造突增事件、不派发分析。
        rows = source.LocalWorkflowTaskSource(path).load_task_rows(
            {
                "schema_version": "sec_know.rule_burst_signal.v1",
                "mode": "replay",
                "source_app": "zeus",
                "sample_alert_ids": refs,
            }
        )
        states = {"created", "queening", "running", "successed", "failed", "update", "delete"}
        return {
            **summary,
            "status": "checked" if rows else "no_local_samples",
            "requested_alerts": len(refs),
            "task_rows": len(rows),
            "distinct_alerts": len({row["alert_id"] for row in rows}),
            "task_status_counts": dict(Counter(row["status"] if row["status"] in states else "other" for row in rows)),
            "unusable_alert_data_rows": sum(row["alert_data"] is None for row in rows),
            "omitted_response_rows": sum(row["agent_response_unavailable"] for row in rows),
        }
    except (sqlite3.Error, ValueError) as error:
        return {**summary, "status": "check_failed", "error_type": type(error).__name__}


if __name__ == "__main__":
    outcome = main()
    print(json.dumps(outcome, ensure_ascii=False, indent=2))
    raise SystemExit(1 if outcome["status"] == "check_failed" else 0)
