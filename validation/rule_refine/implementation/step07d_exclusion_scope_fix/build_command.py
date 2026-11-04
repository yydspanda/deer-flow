"""Package field-independent review improvements for installed step07C runtime."""

import base64
import hashlib
import importlib.util
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "step07_surge_runtime"
PREVIOUS = HERE.parent / "step07c_review_sampling_fix"
MODULE = "my_workflows/zeus/rule_refine/"
TEST = "test/rule_refine/"
NEW_TEST = TEST + "test_surge_exclusion_scope.py"
NEW_TESTS = (NEW_TEST, TEST + "test_surge_behavior_processor.py")
REGRESSION_TESTS = (
    TEST + "test_surge_processor.py",
    TEST + "test_surge_runtime.py",
    TEST + "test_surge_integration.py",
    TEST + "test_surge_review_sampling.py",
)
PROMPT_EXPECTATIONS = {
    "rule-surge-runtime-independent-generation-v1": "rule-surge-runtime-independent-generation-v2",
    "rule-surge-runtime-review-v2": "rule-surge-runtime-review-v3",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def baseline_builder():
    spec = importlib.util.spec_from_file_location(
        "exclusion_scope_fix_baseline", PREVIOUS / "build_command.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def previous_source(name, expected):
    """Resolve only the exact already-delivered step07C/step07 source bytes."""
    previous = baseline_builder()
    packet = previous.contents()
    if name in packet["files"]:
        raw = packet["files"][name]["content"].encode("utf-8")
    else:
        original = previous.baseline_builder()
        first = original.contents()
        raw = (
            first["files"][name]["content"].encode("utf-8")
            if name in first["files"]
            else original.find_source(name, expected).read_bytes()
        )
    if sha(raw) != expected:
        raise ValueError("previous_source_hash_mismatch")
    return raw


def contents():
    baseline = baseline_builder().contents()
    files = {}
    for source in sorted((HERE / "payload").rglob("*.py")):
        if "__pycache__" in source.parts:
            continue
        name = source.relative_to(HERE / "payload").as_posix()
        if not name.startswith((MODULE, TEST)):
            raise ValueError("unexpected_patch_path")
        raw = source.read_bytes()
        compile(raw, name, "exec")
        old = baseline["files"].get(name, {}).get("new_sha256")
        if old is None:
            old = baseline["dependencies"].get(name)
        files[name] = {
            "old_sha256": old,
            "new_sha256": sha(raw),
            "content": raw.decode("utf-8"),
        }
    if not set(NEW_TESTS).issubset(files) or not any(
        name.startswith(MODULE) for name in files
    ):
        raise ValueError("exclusion_scope_payload_incomplete")
    dependencies = {
        **baseline["dependencies"],
        **{
            name: entry["new_sha256"]
            for name, entry in baseline["files"].items()
            if name.startswith(MODULE)
        },
    }
    dependencies = {
        name: expected for name, expected in dependencies.items() if name not in files
    }
    # Existing tests are staged privately, not installed over operator fixtures.
    test_files = {
        **baseline["test_files"],
        **{
            name: entry["content"]
            for name, entry in baseline["files"].items()
            if name.startswith(TEST)
        },
    }
    test_files = {name: text for name, text in test_files.items() if name not in files}
    # The step07C fixture pins its then-current prompt versions. Update only those
    # two expectations in the private test stage; the delivered fixture and all
    # its behavioral assertions remain unchanged in the operator project.
    sampling_test = TEST + "test_surge_review_sampling.py"
    for old, new in PROMPT_EXPECTATIONS.items():
        if test_files[sampling_test].count(old) != 1:
            raise ValueError("unexpected_sampling_prompt_fixture")
        test_files[sampling_test] = test_files[sampling_test].replace(old, new)
    return {
        "require_evidence": False,
        "files": files,
        "dependencies": dependencies,
        "test_files": test_files,
        "tests": [*NEW_TESTS, *REGRESSION_TESTS],
    }


def installer_source():
    source = (BASE / "install.py").read_text(encoding="utf-8")
    source = source.replace(
        "rule_refine_surge_runtime_install", "rule_refine_exclusion_scope_fix"
    )
    return source.replace(
        'prefix="surge-runtime-install-"', 'prefix="surge-exclusion-scope-fix-"'
    )


def runtime_source(packet):
    raw = json.dumps(
        {"packet": packet, "installer": installer_source()},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    return (
        '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode("""__PAYLOAD__"""), __SIZE__ + 1)
if (len(raw) != __SIZE__ or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__SHA__"):
    raise SystemExit("修复包校验失败，项目未修改。")
bundle = json.loads(raw)
installer = types.ModuleType("surge_exclusion_scope_fix_installer")
sys.modules[installer.__name__] = installer
exec(compile(bundle["installer"], "<exclusion-scope-fix-installer>", "exec"), installer.__dict__)
try:
    result = installer.apply(Path.cwd(), Path.home() / "sec-rule-refine-data", bundle["packet"])
except Exception as error:
    result = {"status":"stopped", "error_type":type(error).__name__, "model_calls":0,
              "redis_calls":0, "business_database_calls":0}
    if isinstance(error, installer.InstallError):
        result.update(reason=str(error), file=error.relative_file, actual_sha256=error.actual_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps(result, ensure_ascii=False, indent=2))
'''.replace("__PAYLOAD__", payload)
        .replace("__SIZE__", str(len(raw)))
        .replace("__SHA__", sha(raw))
    )


def install_body(packet):
    return (
        "set -euo pipefail\numask 077\n"
        'cd "$HOME/sec_know_model"\n'
        'echo "优化建议生成与范围复核；先执行离线回归，不调用模型，不重新补数。"\n'
        ".venv/bin/python -B - <<'PY_SURGE_EXCLUSION_SCOPE_FIX'\n"
        + runtime_source(packet)
        + "PY_SURGE_EXCLUSION_SCOPE_FIX\n"
    )


def build():
    packet = contents()
    command = (
        "bash <<'SOC_SURGE_EXCLUSION_SCOPE_FIX'\n"
        + install_body(packet)
        + "SOC_SURGE_EXCLUSION_SCOPE_FIX\n"
    )
    (HERE / "INSTALL-COMMAND.sh").write_text(command, encoding="utf-8")
    retest = (HERE.parent / "step07b_real_csv_acceptance/CHECK-REAL-CSV.sh").read_text(
        encoding="utf-8"
    )
    combined = (
        "bash <<'SOC_SURGE_SCOPE_INSTALL_AND_RETEST'\n"
        + install_body(packet)
        + 'echo "安装成功。接着用原真实CSV工具手工重测；新进程读取更新源码，无需先重启worker。"\n'
        + 'echo "预览后由你输入LIVE确认，最多2次SDK请求；原报告保留。"\n'
        + retest
        + "SOC_SURGE_SCOPE_INSTALL_AND_RETEST\n"
    )
    (HERE / "INSTALL-AND-RETEST.sh").write_text(combined, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
