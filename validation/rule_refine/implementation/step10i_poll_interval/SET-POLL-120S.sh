bash <<'SOC_TOPIC_POLL_120S'
set -euo pipefail
umask 077
cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"
.venv/bin/python -B - <<'PY_TOPIC_POLL_120S'
import ast
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path.cwd().resolve()
CONFIG = "config/celery_config.py"
TEST = "test/rule_refine/test_topic_beat.py"
MARKER = "# Rule refinement: reuse the existing LOCAL periodic worker and beat."
REFERENCE = '''
import os as _rule_refine_os
if all(_rule_refine_os.environ.get(key, "LOCAL").upper() == "LOCAL" for key in ("env_profile", "ENV_PROFILE")):
    beat_schedule["rule_refine_pending"] = {
        "task": "tasks.task.rule_refine_tick",
        "schedule": 120.0,
        "options": {"queue": "periodic_queue", "expires": 25},
    }
elif rc.ENV == "PRD":
    from pathlib import Path as _RuleRefinePath
    from my_workflows.zeus.rule_refine.topic_settings import topic_enabled as _topic_enabled
    if _topic_enabled(_RuleRefinePath(__file__).resolve().parents[1], _rule_refine_os.environ):
        beat_schedule["rule_refine_pending"] = {
            "task": "tasks.task.rule_refine_tick",
            "schedule": 120.0,
            "options": {"queue": "periodic_queue", "expires": 25},
        }
'''


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def patch_schedule(raw, count):
    tree = ast.parse(raw)
    lines = raw.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    replacements = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        pairs = {
            key.value: value for key, value in zip(node.keys, node.values)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        task = pairs.get("task")
        if not isinstance(task, ast.Constant) or task.value != "tasks.task.rule_refine_tick":
            continue
        if set(pairs) != {"task", "schedule", "options"} or len(node.keys) != 3:
            raise ValueError("unexpected_tick_definition")
        if ast.literal_eval(pairs["options"]) != {"queue": "periodic_queue", "expires": 25}:
            raise ValueError("unexpected_tick_options")
        value = pairs["schedule"]
        if (not isinstance(value, ast.Constant) or type(value.value) not in (int, float)
                or value.value not in (30, 120)):
            raise ValueError("unexpected_poll_interval")
        start = offsets[value.lineno - 1] + value.col_offset
        end = offsets[value.end_lineno - 1] + value.end_col_offset
        replacements.append((start, end))
    if len(replacements) != count:
        raise ValueError("unexpected_tick_definition_count")
    for start, end in sorted(replacements, reverse=True):
        raw = raw[:start] + b"120.0" + raw[end:]
    ast.parse(raw)
    return raw


def replace(path, raw, mode):
    fd, name = tempfile.mkstemp(prefix=".poll-120s-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, mode)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def install():
    paths = {name: ROOT / name for name in (CONFIG, TEST)}
    if any(path.is_symlink() or not path.is_file() for path in paths.values()):
        raise ValueError("source_file_missing_or_symlink")
    before = {name: path.read_bytes() for name, path in paths.items()}
    after = {CONFIG: patch_schedule(before[CONFIG], 2), TEST: patch_schedule(before[TEST], 1)}
    source = after[CONFIG].decode("utf-8")
    if source.count(MARKER) != 1:
        raise ValueError("registration_marker_changed")
    registration = source.split(MARKER, 1)[1]
    if ast.dump(ast.parse(registration)) != ast.dump(ast.parse(REFERENCE)):
        raise ValueError("registration_structure_changed")
    # Run the existing isolated tests against staged source, without importing Celery.
    with tempfile.TemporaryDirectory(prefix="topic-poll-offline-") as temp:
        stage = Path(temp).resolve()
        for name, raw in after.items():
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        check = subprocess.run(
            [sys.executable, "-B", str(stage / TEST)], cwd=stage,
            capture_output=True, text=True, timeout=60,
        )
        if check.returncode:
            raise ValueError("offline_regression_failed")
    changed = [name for name in paths if before[name] != after[name]]
    backup = None
    written = []
    modes = {name: path.stat().st_mode & 0o777 for name, path in paths.items()}
    if changed:
        data = Path(os.environ.get("SEC_RULE_REFINE_DATA_ROOT", str(Path.home() / "sec-rule-refine-data")))
        data.mkdir(parents=True, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix="topic-poll-120s-", dir=data))
        for name in changed:
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            saved.write_bytes(before[name])
            os.chmod(saved, 0o600)
        if any(path.read_bytes() != before[name] for name, path in paths.items()):
            raise ValueError("source_changed_during_check")
        try:
            for name in changed:
                replace(paths[name], after[name], modes[name])
                written.append(name)
            if any(paths[name].read_bytes() != after[name] for name in paths):
                raise ValueError("installed_state_mismatch")
        except BaseException:
            for name in reversed(written):
                replace(paths[name], before[name], modes[name])
            raise
    return {
        "check": "rule_refine_poll_interval_120s",
        "status": "installed" if changed else "already_installed",
        "poll_interval_seconds": 120,
        "profiles": ["LOCAL", "PRD"], "stg_changed": False,
        "expires_seconds": 25, "offline_regression": "passed",
        "files_changed": len(changed), "installed_state_verified": True,
        "backup_dir": str(backup) if backup else None,
        "files": {name: {"before_sha256": digest(before[name]), "after_sha256": digest(after[name])} for name in paths},
        "beat_reload_required": True, "automatic_restart": False,
        "services_started": False, "model_calls": 0, "redis_calls": 0,
        "business_database_calls": 0, "zeus_posts": 0,
    }


if __name__ == "__main__":
    print("将LOCAL/PRD检查间隔改为120秒；先离线回归，再备份更新，不重启服务。", flush=True)
    try:
        result = install()
    except Exception as error:
        print(json.dumps({"check": "rule_refine_poll_interval_120s", "status": "failed",
                          "error_type": type(error).__name__, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("源码已准备好。随项目打包发布，并让Celery Beat重新加载；仅重启periodic worker不会更新调度。")
PY_TOPIC_POLL_120S
SOC_TOPIC_POLL_120S
