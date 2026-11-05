"""Install reviewed legacy queue edits locally; never import the application."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import warnings

import launcher_transforms
import proxy_transforms
import routing_transforms
import scoped_proxy
import scoped_python

HERE = Path(__file__).resolve().parent
MODULES = (routing_transforms, launcher_transforms, proxy_transforms)
SCOPED_MODULES = (scoped_proxy, scoped_python)
SCOPED_PATHS = {
    "llm_service/litellm_proxy_config.prd.yaml",
    "llm_service/openai_completion.py",
    "my_workflows/zeus/flows/alert_controller.py",
    "test/test_all_models.py",
}
INSTALLER_VERSION = "pub-queue-reviewed-regions-stg-qwen-v3"
PATHS = sorted({p for module in MODULES for p in module.SUPPORTED_PATHS})
LOCAL_CONFIG = "config/rule_refine.analysis.local.json"
PUB = "Pub_DeepSeek_V4_Flash"
RETIRED = ("DeepSeek_V32", "DeepSeek_V4_Flash_My")


class InstallError(ValueError):
    """Only fixed error codes and relative filenames may enter public diagnostics."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def transform(path, data):
    for module in MODULES:
        if path in module.SUPPORTED_PATHS:
            return module.transforms(path, data.decode("utf-8")).encode("utf-8")
    raise InstallError("unsupported_path:" + path)


def reviewed_transform(path, data, manifest=None):
    """Whole-file checks stay primary; four known drift points permit bounded edits."""
    if manifest is None:
        manifest = json.loads((HERE / "source-pins.json").read_text(encoding="utf-8"))
    pairs = manifest["files"].get(path, {})
    current = digest(data)
    if current in pairs:
        result = transform(path, data)
        if digest(result) != pairs[current]:
            raise InstallError("transformed_hash_mismatch:" + path)
        return result, {"state": "before", "validation": "whole_file_sha256"}
    if current in pairs.values():
        return data, {"state": "after", "validation": "whole_file_sha256"}
    if path in SCOPED_PATHS:
        contracts = json.loads(
            (HERE / "source-contracts.json").read_text(encoding="utf-8")
        )
        module = scoped_proxy if path.startswith("llm_service/") else scoped_python
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                warnings.simplefilter("ignore", DeprecationWarning)
                result, proof = module.apply(path, data, contracts[path])
        except (ValueError, SyntaxError, KeyError, UnicodeError):
            raise InstallError("target_region_check_failed:" + path) from None
        if proof.get("state") not in {"before", "after"}:
            raise InstallError("invalid_target_region_check:" + path)
        if (result == data) != (proof["state"] == "after"):
            raise InstallError("target_region_state_mismatch:" + path)
        preserved = proof.get(
            "outside_target_bytes_preserved", proof.get("non_target_bytes_preserved")
        )
        if preserved is not True:
            raise InstallError("unverified_target_region_boundary:" + path)
        return result, dict(
            proof,
            validation="reviewed_target_regions",
            entire_file_reviewed=False,
            outside_target_bytes_preserved=True,
        )
    raise InstallError("unreviewed_source:" + path + ":" + current)


def verify_scoped_after_receipt(root, parent, current):
    """Absence of retired keys alone is not proof of a previous successful install."""
    for directory in parent.glob("queue-pub27-*"):
        if directory.is_symlink() or not directory.is_dir():
            continue
        try:
            record = json.loads(read_regular(directory, "result.json")[0])
            hashes = json.loads(read_regular(directory, "file-hashes.json")[0])
            if (
                record.get("check") == "sec_know_pub_queue_consolidation"
                and record.get("installer_version") == INSTALLER_VERSION
                and record.get("project_root") == str(root)
                and record.get("status") in {"installed", "already_installed"}
                and record.get("installed_state_verified") is True
                and all(
                    hashes.get(name, {}).get("after") == digest(data)
                    for name, data in current.items()
                )
            ):
                return
        except (OSError, ValueError, AttributeError, TypeError):
            continue
    raise InstallError("scoped_after_receipt_missing")


def rollback_sources(root, written, before, after, modes, work):
    failed = []
    for name in reversed(written):
        try:
            if read_regular(root, name) != (after[name], modes[name]):
                raise InstallError("changed_during_rollback")
            replace_file(root / name, before[name], modes[name])
        except Exception:
            failed.append(name)
    if failed:
        raise InstallError("rollback_requires_review:" + str(work)) from None


def read_regular(root, name):
    path = root / name
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise InstallError("symlink_not_allowed:" + name)
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise InstallError("regular_single_link_file_required:" + name)
    if metadata.st_size > 4 * 1024 * 1024:
        raise InstallError("source_too_large:" + name)
    return path.read_bytes(), stat.S_IMODE(metadata.st_mode)


def no_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InstallError("duplicate_json_key")
        result[key] = value
    return result


def config_update(data):
    text = data.decode("utf-8")
    value = json.loads(text, object_pairs_hook=no_duplicate_keys)
    if not isinstance(value, dict):
        raise InstallError("invalid_local_analysis_config")
    if value.get("model") not in RETIRED:
        return data
    pattern = r'("model"\s*:\s*")(?:DeepSeek_V32|DeepSeek_V4_Flash_My)(")'
    if len(re.findall(pattern, text)) != 1:
        raise InstallError("ambiguous_local_analysis_model")
    updated = re.sub(pattern, lambda match: match[1] + PUB + match[2], text)
    expected = dict(value, model=PUB)
    if json.loads(updated) != expected:
        raise InstallError("unexpected_local_analysis_change")
    return updated.encode("utf-8")


def write_private(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def replace_file(path, data, mode):
    descriptor, temporary = tempfile.mkstemp(prefix=".pub-queue-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def assignment(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                return node.value
    raise InstallError("missing_assignment:" + name)


def acceptance(sources, work):
    """Validate installed targets without importing any service module."""
    for name, data in sources.items():
        if name.endswith(".py"):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                warnings.simplefilter("ignore", DeprecationWarning)
                compile(data, name, "exec")
        if name.endswith(".sh"):
            checked = work / "syntax" / name
            write_private(checked, data)
            result = subprocess.run(
                ["bash", "-n", str(checked)], capture_output=True, check=False
            )
            if result.returncode:
                raise InstallError("shell_syntax_failed:" + name)
    queue = ast.literal_eval(
        assignment(sources["config/celery_config.py"], "MODEL_QUEUE_CONFIG")
    )
    if any(name in queue for name in RETIRED) or queue[PUB] != {
        "queue": PUB + "_queue",
        "concurrency": 27,
    }:
        raise InstallError("queue_catalog_mismatch")
    if queue.get("Qwen38_27B") != {"queue": "PingAnGPT_R_queue", "concurrency": 6}:
        raise InstallError("stg_queue_catalog_mismatch")
    policies = assignment(
        sources["config/model_limits.py"], "MODEL_POLICY_BY_MODEL_NAME"
    )
    policy = {
        ast.literal_eval(key): value
        for key, value in zip(policies.keys, policies.values)
    }
    public = {item.arg: ast.literal_eval(item.value) for item in policy[PUB].keywords}
    if (
        any(name in policy for name in RETIRED)
        or public.get("concurrency") != 27
        or public.get("semaphore_key") != PUB
    ):
        raise InstallError("model_capacity_mismatch")
    stg = {item.arg: ast.literal_eval(item.value) for item in policy["Qwen38_27B"].keywords}
    if stg.get("queue") != "PingAnGPT_R_queue" or stg.get("semaphore_key") != "PingAnGPT_R" or stg.get("concurrency") != 6:
        raise InstallError("stg_model_capacity_mismatch")
    for name in ("config/celery_task.conf", "config/celery_task.detector.conf"):
        text = sources[name].decode("utf-8")
        if any(model in text for model in RETIRED) or text.count("worker -c 27 ") != 1:
            raise InstallError("worker_configuration_mismatch:" + name)
    for name in ("local_run.sh", "local_run.detector.sh"):
        text = sources[name].decode("utf-8")
        if any(model in text for model in RETIRED):
            raise InstallError("retired_local_worker:" + name)
        pub_lines = [line for line in text.splitlines() if f"-Q {PUB}_queue " in line]
        if len(pub_lines) != 1 or " -c 2 " not in pub_lines[0]:
            raise InstallError("local_worker_capacity_mismatch:" + name)
    launch = sources["run.sh"].decode("utf-8")
    if (
        "python validation/verify_stg_distribution.py" in launch
        or "cleanup_priority9_tasks.py -e stg --delete" in launch
    ):
        raise InstallError("legacy_stg_automatic_mutation_remains")
    if LOCAL_CONFIG in sources:
        config_update(sources[LOCAL_CONFIG])
        if json.loads(sources[LOCAL_CONFIG]).get("model") in RETIRED:
            raise InstallError("retired_analysis_model_remains")


def run_regressions(before, work):
    snapshot = work / "regression-source"
    for path, data in before.items():
        write_private(snapshot / path, data)
    environment = dict(
        os.environ,
        SEC_KNOW_SOURCE_ROOT=str(snapshot),
        PYTHONNOUSERSITE="1",
        PYTHONDONTWRITEBYTECODE="1",
    )
    environment.pop("PYTHONPATH", None)
    process = subprocess.run(
        [
            sys.executable,
            "-B",
            "-W",
            "ignore::DeprecationWarning",
            "-m",
            "unittest",
            "discover",
            "-s",
            str(HERE),
            "-p",
            "test_[lpr]*.py",
        ],
        cwd=HERE,
        env=environment,
        capture_output=True,
        check=False,
    )
    output = process.stdout + process.stderr
    write_private(work / "offline-tests.log", output)
    matches = re.findall(rb"Ran (\d+) tests?", output)
    if (
        process.returncode
        or len(matches) != 1
        or b"skipped=" in output
        or int(matches[0]) == 0
    ):
        raise InstallError("offline_regression_failed")
    return int(matches[0])


def install(project_root, backup_parent, *, check_only=False):
    root = Path(project_root).expanduser().resolve(strict=True)
    parent = Path(backup_parent).expanduser().resolve()
    if parent == root or root in parent.parents:
        raise InstallError("backup_must_be_outside_project")
    manifest = json.loads((HERE / "source-pins.json").read_text(encoding="utf-8"))
    before, after, modes, states, source_checks = {}, {}, {}, [], {}
    for name in PATHS:
        data, modes[name] = read_regular(root, name)
        before[name] = data
        after[name], source_checks[name] = reviewed_transform(name, data, manifest)
        states.append(source_checks[name]["state"])
    if len(set(states)) != 1:
        raise InstallError("mixed_install_state_review_required")
    scoped_after = {
        name: before[name]
        for name, proof in source_checks.items()
        if proof["state"] == "after"
        and proof["validation"] == "reviewed_target_regions"
    }
    if scoped_after:
        verify_scoped_after_receipt(root, parent, scoped_after)
    config = root / LOCAL_CONFIG
    if config.exists() or config.is_symlink():
        before[LOCAL_CONFIG], modes[LOCAL_CONFIG] = read_regular(root, LOCAL_CONFIG)
        after[LOCAL_CONFIG] = config_update(before[LOCAL_CONFIG])
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    work = Path(tempfile.mkdtemp(prefix="queue-pub27-", dir=parent))
    tests_passed = 0
    if states[0] == "before":
        tests_passed = run_regressions(before, work)
    acceptance(after, work)
    changed = [name for name in before if before[name] != after[name]]
    # All paths and complete source versions must pass before the first mutation.
    for name in before:
        if read_regular(root, name) != (before[name], modes[name]):
            raise InstallError("source_changed_during_preflight:" + name)
    receipts = {
        name: {"before": digest(before[name]), "after": digest(after[name])}
        for name in before
    }
    write_private(
        work / "file-hashes.json", json.dumps(receipts, indent=2).encode("utf-8")
    )
    for name in changed:
        write_private(work / "backup" / name, before[name])
    written = []
    if not check_only:
        try:
            for name in changed:
                if read_regular(root, name) != (before[name], modes[name]):
                    raise InstallError("source_changed_during_install:" + name)
                replace_file(root / name, after[name], modes[name])
                written.append(name)
            for name in before:
                if read_regular(root, name) != (after[name], modes[name]):
                    raise InstallError("installed_state_mismatch:" + name)
        except Exception:
            rollback_sources(root, written, before, after, modes, work)
            raise InstallError("install_failed_rolled_back:" + str(work)) from None
    result = {
        "check": "sec_know_pub_queue_consolidation",
        "installer_version": INSTALLER_VERSION,
        "project_root": str(root),
        "status": "checked"
        if check_only
        else ("installed" if changed else "already_installed"),
        "files_changed": 0 if check_only else len(changed),
        "files_planned": len(changed),
        "changed_paths": changed,
        "tests_passed": tests_passed,
        "regression_tests_run_this_command": states[0] == "before",
        "installed_target_static_checks_passed": True,
        "installed_state_verified": not check_only,
        "source_validation": source_checks,
        "scoped_validation_files": [
            name
            for name, proof in source_checks.items()
            if proof["validation"] == "reviewed_target_regions"
        ],
        "backup_dir": str(work),
        "production_alert_model": PUB,
        "production_worker_concurrency": 27,
        "alert_shared_semaphore_capacity": 27,
        "local_worker_concurrency": 2,
        "stg_alert_model": "Qwen38_27B",
        "stg_alert_queue": "PingAnGPT_R_queue",
        "stg_alert_shared_semaphore_key": "PingAnGPT_R",
        "stg_alert_shared_capacity": 6,
        "production_monitor_disabled_in_launcher": b"export RULE_REFINE_MONITOR_ENABLED=0"
        in after["run.sh"],
        "stg_legacy_test_injection_and_auto_cleanup_disabled": True,
        "application_code_updated": bool(changed) and not check_only,
        "restart_required": bool(changed) and not check_only,
        "automatic_restart": False,
        "services_started": False,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "queue_tasks_deleted_or_migrated": 0,
        "old_queues_drained_verified": False,
        "running_production_verified": False,
    }
    try:
        write_private(
            work / "result.json",
            json.dumps(result, ensure_ascii=False, indent=2).encode("utf-8"),
        )
    except Exception:
        rollback_sources(root, written, before, after, modes, work)
        raise InstallError("receipt_write_failed_rolled_back:" + str(work)) from None
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root", type=Path, default=Path.home() / "sec_know_model"
    )
    parser.add_argument(
        "--backup-parent", type=Path, default=Path.home() / "sec-rule-refine-data"
    )
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        result = install(
            args.project_root, args.backup_parent, check_only=args.check_only
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "check": "sec_know_pub_queue_consolidation",
                    "status": "failed",
                    "error_type": type(error).__name__,
                    "error_code": str(error)
                    if isinstance(error, InstallError)
                    else "offline_install_or_source_check_failed",
                    "automatic_restart": False,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        "离线检查完成。安装未启动任何服务；发版时停止旧队列新投递，待旧任务全部结束后再切换启动配置。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(
        "This queue installer is retired; use the reviewed manual deployment. See README.md."
    )
