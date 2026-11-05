bash <<'SOC_RULE_RELEASE_DISABLE'
set -euo pipefail
umask 077
release_project="$HOME/sec_know_model"
release_python="$release_project/.venv/bin/python"
if [ ! -x "$release_python" ]; then release_python=python3; fi
echo '关闭生产启动脚本中的规则监测；备份原文件，不启动或重启服务。'
"$release_python" -B - --project-root "$release_project" <<'SOC_RELEASE_DISABLE_PYTHON'
"""Disable rule monitoring in the reviewed deployment launcher, without launching it."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

BASE_SHA256 = "125020a4f1bc5934d35bcdea1ed9648fbc78d87244c92519abe915f393d8a8d7"
BLOCK = (
    b"\n\n# BEGIN RULE_REFINE_RELEASE_DISABLED\n"
    b"# Keep rule-refinement monitoring off for this production/STG release.\n"
    b"export RULE_REFINE_MONITOR_ENABLED=0\n"
    b"# END RULE_REFINE_RELEASE_DISABLED\n"
)
BACKUP_NAME = "run.sh.before-rule-refine-release-disable"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def patched_bytes(original):
    first, separator, rest = original.partition(b"\n")
    if first != b"#!/bin/bash" or not separator:
        raise ValueError("unreviewed_run_sh")
    if digest(original) == BASE_SHA256:
        return first + separator + BLOCK + rest
    if rest.startswith(BLOCK):
        restored = first + separator + rest[len(BLOCK) :]
        if digest(restored) == BASE_SHA256:
            return original
    raise ValueError("unreviewed_run_sh")


def read_regular(path):
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise ValueError("regular_unlinked_file_required:" + path.name)
    if metadata.st_size > 1024 * 1024:
        raise ValueError("file_too_large:" + path.name)
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode)


def install(project_root):
    root = Path(project_root).expanduser().resolve(strict=True)
    target = root / "run.sh"
    original, mode = read_regular(target)
    changed = patched_bytes(original)
    backup = root / BACKUP_NAME
    baseline = original if changed != original else original.replace(BLOCK, b"", 1)
    if backup.exists() or backup.is_symlink():
        saved, _ = read_regular(backup)
        if saved != baseline:
            raise ValueError("backup_conflicts")
    else:
        descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(baseline)
            stream.flush()
            os.fsync(stream.fileno())
    if changed != original:
        descriptor, temporary = tempfile.mkstemp(
            prefix=".run.sh.rule-refine-", dir=root
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(changed)
                stream.flush()
                os.fsync(stream.fileno())
                os.fchmod(stream.fileno(), mode)
            current, current_mode = read_regular(target)
            if current != original or current_mode != mode:
                raise ValueError("run_sh_changed_during_install")
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    installed, installed_mode = read_regular(target)
    if installed != changed or installed_mode != mode:
        raise ValueError("installed_state_mismatch")
    return {
        "check": "rule_refine_release_disable",
        "status": "installed" if changed != original else "already_installed",
        "files_changed": int(changed != original),
        "file": str(target),
        "backup_file": str(backup),
        "before_sha256": digest(original),
        "after_sha256": digest(installed),
        "installed_state_verified": True,
        "disabled_variable": "RULE_REFINE_MONITOR_ENABLED=0",
        "scope": "processes_started_by_reviewed_run_sh",
        "launch_script_updated": changed != original,
        "application_code_updated": False,
        "local_run_unchanged": True,
        "configuration_files_modified": False,
        "services_started": False,
        "automatic_restart": False,
        "running_production_verified": False,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path.home() / "sec_know_model"
    )
    args = parser.parse_args()
    try:
        result = install(args.project_root)
    except (OSError, ValueError) as error:
        print(
            json.dumps(
                {
                    "check": "rule_refine_release_disable",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error)
                    if isinstance(error, ValueError)
                    else "file_operation_failed",
                    "automatic_restart": False,
                    "next_step": "review_run_sh_diff_do_not_overwrite_whole_file",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "完成。请把修改后的 run.sh 随今晚代码发布；当前服务未重启，本地测试配置未修改。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
SOC_RELEASE_DISABLE_PYTHON
SOC_RULE_RELEASE_DISABLE
