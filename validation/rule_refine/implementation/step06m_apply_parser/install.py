"""Install the verified two-file parser fix after isolated, service-free tests."""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from changes import PATCHES

DEPENDENCIES = {
    "my_workflows/zeus/rule_refine/contracts.py": "59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a",
    "my_workflows/zeus/rule_refine/rule_config_source.py": "dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941",
    "my_workflows/zeus/rule_refine/materials.py": "5d06b99874b667f8c9edd1d8a2e2840fa25c1f663eca55e20d24f967239f0e33",
    "test/rule_refine/test_materials.py": "3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76",
}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def save(path, raw):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def atomic_replace(path, raw, expected):
    if path.is_symlink() or digest(path.read_bytes()) != expected:
        raise ValueError("文件执行期间发生变化，未覆盖：" + path.name)
    mode = stat.S_IMODE(path.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix=".parser-fix-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        if path.is_symlink() or digest(path.read_bytes()) != expected:
            raise ValueError("文件执行期间发生变化，未覆盖：" + path.name)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def run_checks(files):
    with tempfile.TemporaryDirectory(prefix="parser-test-") as temporary:
        stage = Path(temporary)
        for relative, raw in files.items():
            save(stage / relative, raw)
        result = subprocess.run(
            [sys.executable, "-B", str(stage / "test/rule_refine/test_analysis.py")],
            cwd=stage,
            capture_output=True,
            text=True,
            timeout=60,
        )
        count = re.search(r"Ran (\d+) tests? in", result.stderr)
        if result.returncode or count is None or int(count.group(1)) != 18:
            print(result.stdout + result.stderr, file=sys.stderr)
            raise ValueError("隔离测试未通过，项目文件未修改")
    return 18


def apply(root, evidence_directory):
    root = Path(root).resolve()
    evidence_raw = (Path(evidence_directory) / "summary.json").read_bytes()
    evidence = json.loads(evidence_raw)
    expected = {
        "check": "saved_model_reply_revalidation",
        "status": "passed",
        "execution_mode": "offline_replay",
        "response_origin": "saved_model_response",
        "analysis_status": "proposal",
        "delivery_status": "received",
        "check_reason": "local_checks_passed",
        "validation_error": None,
        "response_sha256": "10dc131e79722ae54191572a0518cd9523b2e8b597c01c5b2b92c788faa69000",
        "parser_sha256": PATCHES[0]["new"],
    }
    if any(evidence.get(k) != v for k, v in expected.items()):
        raise ValueError("离线验证证据不匹配，未安装")
    if any(
        type(evidence.get(k)) is not int or evidence[k] != 0
        for k in ("model_calls", "redis_calls", "database_calls")
    ):
        raise ValueError("离线验证调用计数不匹配，未安装")
    if (
        evidence.get("application_code_updated") is not False
        or evidence.get("automatic_apply") is not False
        or evidence.get("original_report_unchanged") is not True
    ):
        raise ValueError("离线验证状态不匹配，未安装")
    files, originals = {}, {}
    for relative, expected_hash in DEPENDENCIES.items():
        raw = (root / relative).read_bytes()
        if digest(raw) != expected_hash:
            raise ValueError("依赖版本不匹配，未安装：" + relative)
        files[relative] = raw
    for patch in PATCHES:
        path = root / patch["path"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("目标不是普通文件：" + patch["path"])
        raw = path.read_bytes()
        originals[patch["path"]] = raw
        current = digest(raw)
        if current not in {patch["old"], patch["new"]}:
            raise ValueError("文件存在其他修改，未覆盖：" + patch["path"])
        if current == patch["old"]:
            text = raw.decode("utf-8")
            for before, after in patch["changes"]:
                if text.count(before) != 1:
                    raise ValueError("修复位置不唯一，未安装")
                text = text.replace(before, after, 1)
            raw = text.encode("utf-8")
        if digest(raw) != patch["new"]:
            raise ValueError("修复摘要不匹配，未安装")
        compile(raw, str(path), "exec")
        files[patch["path"]] = raw
    test_count = run_checks(files)
    report = {
        "status": "already_installed",
        "tests_passed": test_count,
        "application_code_updated": True,
        "restart_required": True,
        "automatic_apply": False,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "parser_sha256": PATCHES[0]["new"],
        "test_sha256": PATCHES[1]["new"],
    }
    changed = [p for p in reversed(PATCHES) if originals[p["path"]] != files[p["path"]]]
    if not changed:
        return report
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix="parser-fix-", dir=parent))
    for patch in changed:
        save(backup / "before" / patch["path"], originals[patch["path"]])
    save(
        backup / "manifest.json",
        json.dumps(
            {
                "evidence_sha256": digest(evidence_raw),
                "evidence_directory": str(evidence_directory),
                "files": [{k: p[k] for k in ("path", "old", "new")} for p in changed],
                "tests_passed": test_count,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
    )
    try:
        for patch in changed:
            atomic_replace(
                root / patch["path"],
                files[patch["path"]],
                digest(originals[patch["path"]]),
            )
    except BaseException:
        # The runtime source is the final commit point. Restore the test if it did
        # not commit; never overwrite an unrelated concurrent edit.
        if digest((root / PATCHES[0]["path"]).read_bytes()) != PATCHES[0]["new"]:
            test_path = root / PATCHES[1]["path"]
            if (
                digest(test_path.read_bytes()) == PATCHES[1]["new"]
                and originals[PATCHES[1]["path"]] != files[PATCHES[1]["path"]]
            ):
                atomic_replace(
                    test_path, originals[PATCHES[1]["path"]], PATCHES[1]["new"]
                )
        raise
    report.update(status="installed", backup_dir=str(backup))
    return report


if __name__ == "__main__":
    try:
        result = apply(
            Path.cwd(), Path.home() / "sec-rule-refine-data/fenced-json-replay-6nth7amy"
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        result = {
            "status": "stopped",
            "error_type": type(error).__name__,
            "model_calls": 0,
            "redis_calls": 0,
            "database_calls": 0,
        }
        if isinstance(error, ValueError) and str(error).startswith(
            (
                "离线验证",
                "依赖版本",
                "目标不是",
                "文件存在",
                "修复",
                "隔离测试",
                "文件执行",
            )
        ):
            result["reason"] = str(error)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(1)
