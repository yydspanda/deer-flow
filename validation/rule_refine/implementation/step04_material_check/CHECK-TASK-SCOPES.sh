bash <<'SOC_TASK_SCOPES'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
import json
import os
import sqlite3
import time
from pathlib import Path

if os.environ.get("env_profile", "LOCAL").upper() != "LOCAL":
    raise SystemExit("本次只检查 LOCAL 数据库，未连接远端。")
path = Path("nas/data/sec_know_model.sqlite").resolve()
if not path.is_file():
    raise SystemExit("本地数据库文件不存在，未创建文件。")

db = None
try:
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)
    db.execute("PRAGMA query_only=ON")
    deadline = time.monotonic() + 5
    db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
    db.execute("BEGIN")
    total = db.execute("SELECT COUNT(*) FROM uas_app_workflow_task_status").fetchone()[0]
    rows = db.execute("""
        SELECT app_code, flow_id, COUNT(*)
        FROM uas_app_workflow_task_status
        GROUP BY app_code, flow_id
        ORDER BY COUNT(*) DESC, app_code, flow_id LIMIT 20
    """).fetchall()
    print(json.dumps({
        "check": "local_task_scopes",
        "database_scope": "local_sqlite",
        "read_only": True,
        "model_calls": 0,
        "status": "empty_task_table" if total == 0 else "checked",
        "total_task_rows": total,
        "top_20_scopes": [
            {"app_code": app, "flow_id": flow, "task_rows": count}
            for app, flow, count in rows
        ],
    }, ensure_ascii=False, indent=2))
except sqlite3.Error as exc:
    raise SystemExit("只读检查未完成：" + str(exc)) from None
finally:
    if db is not None:
        db.close()
PY
SOC_TASK_SCOPES
