"""Submit the prepared request once; subsequent invocations collect evidence only."""

import ast
import fcntl
import hashlib
import http.client
import json
import os
import re
import sqlite3
import subprocess
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

REQUEST_SHA = "283adc1f05877538b0f33658c25e92d90bfb462f4710fe424506cd229fd50572"
PREPARED_NAME = "ingress-one-ready-367kxcn7"
SOURCE_PINS = {
    "util/root_config.py": "8b107a4d5956d029959f487bfa177e2443906b5f02c96dd3008e6fc3ea70be06",
    "tasks/task_util.py": "3fb314523180b341e544c834dd20597595a740f9386c88fa8ceeeb91358aac89",
    "service/dependencies.py": "44ff159c1a6f0dda09a080fbc0da7216e4457086d66ad6c81524ee90ced97518",
    "model/db/crud/workflow_task.py": "3c0d0c54dbecda7f1d58527155e702dc563353b47fcac08de98251ec5b3e13c5",
}


class CheckError(ValueError):
    """Fixed diagnostic codes only; no request or credential values."""


def read(path, limit=2 * 1024 * 1024):
    with Path(path).open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise CheckError("file_over_budget")
    return raw


def encode(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, allow_nan=False
    ).encode("utf-8")


def save_once(path, value):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(encode(value))
        stream.flush()
        os.fsync(stream.fileno())
    directory_fd = os.open(Path(path).parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def prepared(directory):
    raw = read(directory / "request.json")
    meta = json.loads(read(directory / "selection.json", 65536))
    if (
        hashlib.sha256(raw).hexdigest() != REQUEST_SHA
        or meta.get("request_sha256") != REQUEST_SHA
    ):
        raise CheckError("prepared_request_changed")
    payload = json.loads(raw)
    if payload.get("app_code") != "zeus" or payload.get("flow_id") != "alert_agent":
        raise CheckError("unexpected_workflow")
    identity = payload.get("alert_id")
    if (
        not isinstance(identity, str)
        or not identity
        or str(payload["alert_data"]["alert"]["alertId"]) != identity
    ):
        raise CheckError("identity_mismatch")
    return raw, payload


def local_token(root):
    """Read only a literal dictionary in the known LOCAL branch; execute nothing."""
    tree = ast.parse(read(root / "nas/config.py", 512 * 1024).decode("utf-8-sig"))
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.If)
        and ast.dump(node.test)
        == ast.dump(ast.parse("rc.ENV == 'LOCAL'", mode="eval").body)
    ]
    if len(matches) != 1:
        raise CheckError("local_auth_branch_not_found")
    assignments = [
        node
        for node in matches[0].body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "APP_KEY_DICT"
    ]
    if len(assignments) != 1:
        raise CheckError("local_auth_dictionary_not_found")
    values = ast.literal_eval(assignments[0].value)
    token = values.get("ZUES") or values.get("test_key")
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 4096
        or not token.isascii()
        or any(char.isspace() for char in token)
    ):
        raise CheckError("local_auth_token_invalid")
    return token


def command(args):
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=8, check=False
    )
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        raise CheckError("local_process_inspection_failed")
    return result.stdout


def api_process(root, run=command):
    """Inspect only the API listener; broker consumers are deliberately unasserted."""
    pids = set(run(["lsof", "-nP", "-a", "-iTCP:8090", "-sTCP:LISTEN", "-t"]).split())
    if len(pids) != 1 or not next(iter(pids)).isdigit():
        raise CheckError("api_listener_not_unique")
    pid = next(iter(pids))
    cwd_lines = run(["lsof", "-a", "-p", pid, "-d", "cwd", "-Fn"]).splitlines()
    paths = [line[1:] for line in cwd_lines if line.startswith("n")]
    if len(paths) != 1 or Path(paths[0]).resolve() != root.resolve():
        raise CheckError("api_listener_project_mismatch")
    # Capture locally and discard everything except the profile check. Never print argv/env.
    text = run(["ps", "eww", "-p", pid, "-o", "command="])
    if not text.strip() or "run.py" not in text:
        raise CheckError("api_process_not_recognized")
    profiles = re.findall(r"(?:^|\s)env_profile=([^\s]*)", text)
    if len(profiles) > 1 or (profiles and profiles[0].upper() != "LOCAL"):
        raise CheckError("api_process_profile_not_local")
    return {
        "api_pid": int(pid),
        "api_project_matches": True,
        "api_profile_basis": "process_environment"
        if profiles
        else "reviewed_source_default",
        "api_profile": "LOCAL",
        "broker_worker_profile_verified": False,
        "callback_target_verified": False,
    }


def rows(root, identity, task_id=None):
    database = root / "nas/data/sec_know_model.sqlite"
    if not database.is_file():
        raise CheckError("local_database_missing")
    with closing(
        sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=2)
    ) as db:
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 3
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        query = "SELECT id,status,failed_count,CASE WHEN length(CAST(alert_data AS BLOB)) <= 2097152 THEN alert_data ELSE NULL END,CASE WHEN agent_response IS NULL OR agent_response IN ('null','{}','') THEN 0 ELSE 1 END,CASE WHEN length(CAST(alert_data AS BLOB)) > 2097152 THEN 1 ELSE 0 END FROM uas_app_workflow_task_status WHERE app_code=? AND flow_id=? AND alert_id=?"
        parameters = ["zeus", "alert_agent", identity]
        if task_id:
            query += " AND id=?"
            parameters.append(task_id)
        query += " LIMIT 5"
        return db.execute(query, parameters).fetchall()


def log_cursor(root):
    path = root / "nas/log/info.log"
    try:
        st = path.stat()
        return {"inode": st.st_ino, "offset": st.st_size}
    except OSError:
        return None


def monitor_receipt(root, identity, cursor):
    report = {"status": "unconfirmed", "reason": "matching_new_log_not_found"}
    if not cursor:
        return {**report, "reason": "no_log_at_submission"}
    path = root / "nas/log/info.log"
    identity_json = json.dumps(
        ["zeus", "alert_agent", identity.strip()],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    key = "sha256:" + hashlib.sha256(identity_json.encode()).hexdigest()
    try:
        with path.open("rb") as stream:
            st = os.fstat(stream.fileno())
            if st.st_ino != cursor["inode"] or st.st_size < cursor["offset"]:
                return {**report, "reason": "log_rotated"}
            stream.seek(cursor["offset"])
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return {**report, "reason": "log_scan_budget_exceeded"}
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if "rule_refine_monitor " not in line:
                continue
            try:
                value = json.loads(line.split("rule_refine_monitor ", 1)[1])
                if value.get("request_key") != key:
                    continue
                integers = {
                    name: value[name]
                    for name in (
                        "rule_count",
                        "counted_rules",
                        "added_rules",
                        "duplicate_rules",
                        "new_signals",
                    )
                    if type(value.get(name)) is int
                }
                return {
                    "status": "matching_log_found",
                    "monitor_status": value.get("status")
                    if value.get("status")
                    in {"ready", "partial", "skipped", "degraded"}
                    else "unknown",
                    **integers,
                    "attribution": "same_alert_after_submission_not_exclusive_request_identity",
                }
            except (ValueError, AttributeError):
                continue
    except OSError:
        return {**report, "reason": "log_unavailable"}
    return report


def post_once(raw, token, connection_factory=http.client.HTTPConnection):
    conn = connection_factory("127.0.0.1", 8090, timeout=30)
    try:
        conn.request(
            "POST",
            "/workflow/task",
            body=raw,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token,
            },
        )
        response = conn.getresponse()
        report = {
            "http_status": response.status,
            "task_id": None,
            "post_outcome": "unconfirmed",
        }
        body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            return report
        if 200 <= response.status < 300:
            value = json.loads(body)
            task_id = value.get("id")
            if isinstance(task_id, str) and str(UUID(task_id)) == task_id:
                report.update(post_outcome="accepted", task_id=task_id)
        elif response.status in (401, 403, 422):
            report["post_outcome"] = "rejected_before_handler"
        # Do not output response bodies: errors can contain raw data or credentials.
        return report
    except Exception as error:
        return {
            "post_outcome": "unconfirmed",
            "task_id": None,
            "error_type": type(error).__name__,
        }
    finally:
        conn.close()


def collect(root, directory, payload, *, polls=16, pause=time.sleep):
    started = json.loads(read(directory / "attempt.json", 65536))
    response_path = directory / "response.json"
    response = (
        json.loads(read(response_path, 65536))
        if response_path.exists()
        else {"post_outcome": "unconfirmed", "task_id": None}
    )
    task_id = response.get("task_id")
    evidence = {"status": "task_id_unconfirmed", "completion_verified": False}
    for index in range(polls if task_id else 1):
        try:
            found = rows(root, payload["alert_id"], task_id=task_id)
            if task_id and len(found) == 1:
                row = found[0]
                saved = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                matches = saved == payload["alert_data"]
                status = (
                    row[1]
                    if row[1]
                    in {
                        "created",
                        "queening",
                        "running",
                        "successed",
                        "failed",
                        "update",
                        "delete",
                    }
                    else "unknown"
                )
                evidence = {
                    "status": "exact_task_found",
                    "task_status": status,
                    "alert_data_matches_original": matches,
                    "agent_response_present": bool(row[4]),
                    "failed_count": row[2]
                    if type(row[2]) is int and 0 <= row[2] <= 1000000
                    else None,
                    "stored_data_over_budget": bool(row[5]),
                    "completion_verified": matches and status == "successed",
                }
                if not matches or status in {"successed", "failed"}:
                    break
            elif not task_id:
                evidence["same_alert_rows_now"] = len(found)
                evidence["attribution"] = "unconfirmed_do_not_resubmit"
        except (sqlite3.Error, OSError, ValueError) as error:
            evidence = {
                "status": "database_check_unavailable",
                "error_type": type(error).__name__,
                "completion_verified": False,
            }
            break
        if index + 1 < polls and task_id:
            if index % 5 == 0:
                print("已提交，正在只读查询任务状态……", flush=True)
            pause(2)
    return {
        "check": "rule_refine_one_alert_ingress",
        "request_sha256": REQUEST_SHA,
        "status": "collected",
        "http": response,
        "task": evidence,
        "monitor": monitor_receipt(
            root, payload["alert_id"], started.get("log_cursor")
        ),
        "submission_attempts_recorded": 1,
        "automatic_resubmission": False,
        "direct_model_calls_by_tool": 0,
        "workflow_model_calls": "not_measured",
        "zeus_callback_result": "not_verified",
        "broker_worker_profile_verified": False,
        "volume_analysis_trigger_proven": False,
        "automatic_apply": False,
        "output_dir": str(directory),
    }


def preflight(root, payload):
    for name, expected in SOURCE_PINS.items():
        if hashlib.sha256(read(root / name)).hexdigest() != expected:
            raise CheckError("reviewed_source_changed")
    process = api_process(root)
    if rows(root, payload["alert_id"]):
        raise CheckError("alert_already_in_local_task_store_no_submission")
    token = local_token(root)
    return token, process


def main():
    root = Path.cwd()
    if any(
        os.environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        raise CheckError("local_shell_required")
    parent = Path.home() / "sec-rule-refine-data"
    raw, payload = prepared(parent / PREPARED_NAME)
    directory = parent / ("ingress-one-submit-" + REQUEST_SHA[:16])
    directory.mkdir(mode=0o700, exist_ok=True)
    with os.fdopen(
        os.open(directory / "submission.lock", os.O_CREAT | os.O_RDWR, 0o600), "r+"
    ) as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CheckError("another_check_running") from None
        attempted = (directory / "attempt.json").exists()
        if not attempted:
            token, process = preflight(root, payload)
            print(
                json.dumps(
                    {
                        "status": "ready_for_one_submission",
                        "topic": "ptp-nids",
                        "rule_code": "RPAADM_002627",
                        "request_bytes": len(raw),
                        "request_sha256": REQUEST_SHA,
                        "process": process,
                        "configured_callback_profile_for_local": "STG",
                        "configured_local_and_stg_redis_are_distinct": True,
                        "output_dir": str(directory),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
            print(
                "将向本机8090提交这1条原始告警，进入现有队列及完整研判；会调用模型，并按接手worker的配置回调Zeus。",
                flush=True,
            )
            print(
                "LOCAL与STG使用不同Redis；LOCAL的Zeus回调地址配置与STG相同。原流程自身的重试仍会生效。",
                flush=True,
            )
            print(
                "若允许这次按现有测试链路执行，输入 LIVE；其他输入退出：",
                end="",
                flush=True,
            )
            with open("/dev/tty", "r", encoding="utf-8") as terminal:
                answer = terminal.readline().strip()
            if answer != "LIVE":
                print("已取消，未提交告警。")
                return
            # Recheck input before durable marker; marker precedes ANY POST bytes.
            raw, payload = prepared(parent / PREPARED_NAME)
            save_once(
                directory / "attempt.json",
                {
                    "request_sha256": REQUEST_SHA,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "phase": "post_started",
                    "log_cursor": log_cursor(root),
                    "preflight": process,
                },
            )
            response = post_once(raw, token)
            save_once(directory / "response.json", response)
            del token
        else:
            print("检测到本条已有提交标记；本次只查询状态，不再发送。", flush=True)
        result = collect(root, directory, payload)
        result["submission_attempts_this_command"] = 0 if attempted else 1
        # Retain separate collections; never overwrite earlier evidence.
        name = "collection-" + str(time.time_ns()) + ".json"
        save_once(directory / name, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "rule_refine_one_alert_ingress",
                    "status": "stopped",
                    "error_code": str(error)
                    if isinstance(error, CheckError)
                    else "execution_or_collection_failed",
                    "error_type": type(error).__name__,
                    "automatic_resubmission": False,
                },
                indent=2,
            )
        )
        raise SystemExit(1) from None
