bash <<'SOC_STEP06E_CHECK'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
import ast
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

root = Path.cwd()
baseline = {
    "tasks/task.py": "b5b04f118e6ff051d73acde39b9f6366da24a488ee1fb130ba0646c8a47c1341",
    "config/celery_config.py": "3a02e7492e2d59799eb87c71a4a4c67f956f40bbdaea2be53c4f69b546c41cef",
    "local_run.sh": "2bde4b5acacaf81eff23d30c89f5efde2df8ae234daeba59b67200dc4a470bb1",
    "local_run.detector.sh": "d2dcf3f4ab1771db19cdaaf7fe0f5993e3271c30a0bc6f1d552bf6ad0ebc769f",
}


def option(tokens, names):
    for index, token in enumerate(tokens):
        for name in names:
            value = None
            if token == name and index + 1 < len(tokens):
                value = tokens[index + 1]
            elif token.startswith(name + "="):
                value = token[len(name) + 1:]
            if value is not None:
                return value if re.fullmatch(r"[A-Za-z0-9_.,:-]{1,200}", value) else "dynamic_or_redacted"
    return None


def command_summary(command):
    try:
        tokens = shlex.split(command, comments=True)
    except ValueError:
        return None
    if not any(Path(token).name in {"celery", "celery.exe"} for token in tokens):
        return None
    role = "worker" if "worker" in tokens else "beat" if "beat" in tokens else None
    if role is None:
        return None
    return {
        "role": role,
        "app": option(tokens, ("-A", "--app")),
        "queues": option(tokens, ("-Q", "--queues")),
        "concurrency": option(tokens, ("-c", "--concurrency")),
        "pool": option(tokens, ("-P", "--pool")),
        "embedded_beat": "-B" in tokens or "--beat" in tokens,
    }


report = {
    "check": "rule_refine_background_layout",
    "read_only": True,
    "model_calls": 0,
    "database_calls": 0,
    "redis_calls": 0,
    "shell_env_profile": os.environ.get("env_profile", "LOCAL"),
    "files": [],
}
for name in (*baseline, "run_local.sh"):
    path = root / name
    item = {"file": name, "exists": path.is_file()}
    report["files"].append(item)
    if not item["exists"]:
        continue
    try:
        if path.stat().st_size > 1024 * 1024:
            raise ValueError("file_limit")
        raw = path.read_bytes()
        item["sha256"] = hashlib.sha256(raw).hexdigest()
        item["matches_source_zip"] = item["sha256"] == baseline[name] if name in baseline else None
        text = raw.decode("utf-8")
        if name.endswith(".sh"):
            item["celery_command_hints"] = [
                summary for line in text.replace("\\\n", " ").splitlines()
                if (summary := command_summary(line)) is not None
            ]
        else:
            tree = ast.parse(text)
            if name == "config/celery_config.py":
                for node in tree.body:
                    if not isinstance(node, ast.Assign):
                        continue
                    names = [target.id for target in node.targets if isinstance(target, ast.Name)]
                    if "imports" in names:
                        item["task_imports"] = ast.literal_eval(node.value)
                    if "task_queues" in names:
                        item["configured_queue_names"] = [
                            call.args[0].value for call in ast.walk(node.value)
                            if isinstance(call, ast.Call) and call.args
                            and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)
                            and isinstance(call.func, ast.Name) and call.func.id == "Queue"
                        ]
                    if "beat_schedule" in names and isinstance(node.value, ast.Dict):
                        item["beat_entry_names"] = [key.value for key in node.value.keys if isinstance(key, ast.Constant)]
            else:
                item["top_level_functions"] = [
                    node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
    except Exception as error:
        item["check_error_type"] = type(error).__name__

report["process_check"] = {"status": "not_checked", "celery_processes": []}
try:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,command="], capture_output=True, text=True, timeout=5, check=True
    )
    found = []
    for line in completed.stdout.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        summary = command_summary(fields[2])
        if summary is not None:
            found.append({"pid": int(fields[0]), "ppid": int(fields[1]), **summary})
    report["process_check"] = {
        "status": "checked",
        "visible_celery_process_count": len(found),
        "celery_processes": found[:30],
        "truncated": len(found) > 30,
        "project_ownership": "not_verified",
        "worker_connectivity": "not_tested",
    }
except Exception as error:
    report["process_check"].update(status="check_failed", error_type=type(error).__name__)

print(json.dumps(report, ensure_ascii=False, indent=2))
PY
SOC_STEP06E_CHECK
