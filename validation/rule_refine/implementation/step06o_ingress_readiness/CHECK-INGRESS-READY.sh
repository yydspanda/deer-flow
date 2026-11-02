bash <<'SOC_CHECK_INGRESS_READY'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_INGRESS_READY'
"""Read-only preparation for testing the existing LOCAL ingress; never submit alerts."""

import ast
import http.client
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path


def read_small(path, limit):
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("file_too_large")
    return raw.decode("utf-8")


def file_setting(root, name):
    try:
        data = json.loads(read_small(root / "config" / name, 16384))
        if not isinstance(data, dict) or type(data.get("enabled")) is not bool:
            return {"status": "invalid_or_missing_switch"}
        return {"status": "read", "enabled_on_disk": data["enabled"]}
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError):
        return {"status": "unreadable_or_invalid"}


def hook_on_disk(root):
    try:
        tree = ast.parse(read_small(root / "service/routers/workflows.py", 256 * 1024))
        entry = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "flow_task_router"
        )
        calls = [node for node in ast.walk(entry) if isinstance(node, ast.Call)]
        hook = [
            node.lineno
            for node in calls
            if isinstance(node.func, ast.Name)
            and node.func.id == "observe_workflow_request"
        ]
        create = [
            node.lineno
            for node in calls
            if isinstance(node.func, ast.Attribute)
            and node.func.attr == "create_work_flow_task"
        ]
        return {
            "status": "inspected",
            "hook_before_task_creation": bool(
                hook and create and min(hook) < min(create)
            ),
            "running_process_code_verified": False,
        }
    except (OSError, ValueError, SyntaxError, StopIteration):
        return {"status": "unavailable", "running_process_code_verified": False}


def api_schema(connection_factory=http.client.HTTPConnection):
    # Literal loopback target, no proxy, no redirects, no application/auth imports.
    conn = connection_factory("127.0.0.1", 8090, timeout=3)
    try:
        conn.request("GET", "/openapi.json", headers={"Accept": "application/json"})
        response = conn.getresponse()
        result = {"http_status": response.status, "workflow_task_documented": False}
        if response.status != 200:
            return {**result, "status": "schema_not_available"}
        raw = response.read(4 * 1024 * 1024 + 1)
        if len(raw) > 4 * 1024 * 1024:
            return {**result, "status": "schema_too_large"}
        data = json.loads(raw)
        operation = data.get("paths", {}).get("/workflow/task", {}).get("post")
        if not isinstance(operation, dict):
            return {**result, "status": "workflow_task_not_documented"}
        return {
            **result,
            "status": "schema_available",
            "workflow_task_documented": True,
            "business_post_sent": False,
            "authentication_tested": False,
        }
    except (OSError, http.client.HTTPException, ValueError, AttributeError):
        return {
            "status": "unavailable_or_invalid_response",
            "workflow_task_documented": False,
        }
    finally:
        conn.close()


def task_materials(root):
    path = root / "nas/data/sec_know_model.sqlite"
    if not path.is_file():
        return {"status": "database_missing_not_created"}
    try:
        with closing(
            sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            deadline = time.monotonic() + 3
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            rows = db.execute(
                "SELECT CASE WHEN alert_data IS NULL OR alert_data = 'null' THEN 0 ELSE 1 END "
                "FROM uas_app_workflow_task_status WHERE app_code=? AND flow_id=? "
                "ORDER BY created_date DESC, id DESC LIMIT 20",
                ("zeus", "alert_agent"),
            ).fetchall()
        return {
            "status": "task_rows_present" if rows else "no_local_samples",
            "sample_basis": "latest_20_zeus_alert_agent_task_rows",
            "task_rows": len(rows),
            "rows_with_non_null_alert_data": sum(row[0] for row in rows),
            "payload_contents_read": False,
            "payload_validity_verified": False,
        }
    except sqlite3.Error:
        return {"status": "database_read_failed"}


def check(root, environ, query_api=api_schema):
    result = {
        "check": "rule_refine_ingress_readiness",
        "read_only": True,
        "model_calls": 0,
        "redis_calls": 0,
        "business_alerts_submitted": 0,
        "celery_tasks_dispatched": 0,
        "application_code_updated": False,
        "real_ingress_acceptance": "not_tested",
        "celery_material_acceptance": "not_tested",
    }
    if any(
        environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        return {**result, "status": "skipped_non_local"}
    result.update(
        monitor_file=file_setting(root, "rule_refine.local.json"),
        analysis_file=file_setting(root, "rule_refine.analysis.local.json"),
        shell_monitor_override_present=any(
            key in environ
            for key in (
                "RULE_REFINE_MONITOR_ENABLED",
                "RULE_REFINE_CSV_PATH",
                "RULE_REFINE_NAMESPACE",
            )
        ),
        running_process_settings_verified=False,
        source_hook=hook_on_disk(root),
        api=query_api(),
        materials=task_materials(root),
    )
    result["status"] = "inspection_complete"
    result["next_step"] = (
        "select_existing_test_material"
        if result["materials"].get("rows_with_non_null_alert_data", 0)
        else "test_alert_material_required"
    )
    return result


if __name__ == "__main__":
    print(json.dumps(check(Path.cwd(), os.environ), ensure_ascii=False, indent=2))
PY_INGRESS_READY
SOC_CHECK_INGRESS_READY
