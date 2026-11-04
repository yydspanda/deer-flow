"""Package explicit event-field usage checks over installed step07D runtime."""

import base64
import hashlib
import importlib.util
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "step07_surge_runtime"
PREVIOUS = HERE.parent / "step07d_exclusion_scope_fix"
MODULE = "my_workflows/zeus/rule_refine/"
TEST = "test/rule_refine/"
NEW_TESTS = (
    TEST + "test_surge_field_usage.py",
    TEST + "test_surge_event_fields.py",
    TEST + "test_surge_event_processor.py",
)
RUNTIME_FILES = (
    MODULE + "surge_ai_review.py",
    MODULE + "surge_processor.py",
    MODULE + "surge_field_usage.py",
)
REGRESSION_TESTS = (
    TEST + "test_surge_processor.py",
    TEST + "test_surge_runtime.py",
    TEST + "test_surge_integration.py",
    TEST + "test_surge_review_sampling.py",
    TEST + "test_surge_exclusion_scope.py",
    TEST + "test_surge_behavior_processor.py",
)
# Exact updates for privately staged expectations; shipped tests stay unchanged.
STAGED_EXPECTATIONS = {
    TEST + "test_surge_review_sampling.py": {
        "rule-surge-runtime-independent-generation-v2": (
            "rule-surge-runtime-independent-generation-v3",
            1,
        ),
        "rule-surge-runtime-review-v3": ("rule-surge-runtime-review-v4", 1),
    },
    TEST + "test_surge_exclusion_scope.py": {
        "rule-surge-runtime-independent-generation-v2": (
            "rule-surge-runtime-independent-generation-v3",
            1,
        ),
        "rule-surge-runtime-review-v3": ("rule-surge-runtime-review-v4", 2),
        "contract_references_expression_and_visible_field_names_only": (
            "contract_references_expression_visible_fields_and_reviewed_field_roles_only",
            2,
        ),
    },
    TEST + "test_surge_behavior_processor.py": {
        "format_fields_and_references_only": (
            "format_fields_references_and_reviewed_field_roles_only",
            1,
        ),
    },
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def baseline_builder():
    spec = importlib.util.spec_from_file_location(
        "event_field_fix_baseline", PREVIOUS / "build_command.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def previous_source(name, expected):
    """Resolve exact delivered bytes recursively, never relax the hash match."""
    previous = baseline_builder()
    packet = previous.contents()
    raw = (
        packet["files"][name]["content"].encode("utf-8")
        if name in packet["files"]
        else previous.previous_source(name, expected)
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
    if set(files) != set(NEW_TESTS) | set(RUNTIME_FILES):
        raise ValueError("event_field_payload_incomplete")
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
    for name, updates in STAGED_EXPECTATIONS.items():
        for old, (new, expected_count) in updates.items():
            if test_files[name].count(old) != expected_count:
                raise ValueError("unexpected_staged_test_expectation")
            test_files[name] = test_files[name].replace(old, new)
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
        "rule_refine_surge_runtime_install", "rule_refine_event_field_boundary_fix"
    )
    return source.replace(
        'prefix="surge-runtime-install-"', 'prefix="surge-event-field-fix-"'
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
installer = types.ModuleType("surge_event_field_fix_installer")
sys.modules[installer.__name__] = installer
exec(compile(bundle["installer"], "<event-field-fix-installer>", "exec"), installer.__dict__)
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
        'echo "修复事件字段与命中信息的边界；先执行离线回归，不调用模型，不重新补数。"\n'
        ".venv/bin/python -B - <<'PY_SURGE_EVENT_FIELD_FIX'\n"
        + runtime_source(packet)
        + "PY_SURGE_EVENT_FIELD_FIX\n"
    )


def build():
    packet = contents()
    command = (
        "bash <<'SOC_SURGE_EVENT_FIELD_FIX'\n"
        + install_body(packet)
        + "SOC_SURGE_EVENT_FIELD_FIX\n"
    )
    (HERE / "INSTALL-COMMAND.sh").write_text(command, encoding="utf-8")
    retest = (HERE.parent / "step07b_real_csv_acceptance/CHECK-REAL-CSV.sh").read_text(
        encoding="utf-8"
    )
    combined = (
        "bash <<'SOC_SURGE_EVENT_FIELD_INSTALL_AND_RETEST'\n"
        + install_body(packet)
        + 'echo "安装成功。接着用原真实CSV工具手工重测；新进程读取更新源码，无需先重启worker。"\n'
        + 'echo "预览后由你输入LIVE确认，最多2次SDK请求；原报告保留。"\n'
        + retest
        + "SOC_SURGE_EVENT_FIELD_INSTALL_AND_RETEST\n"
    )
    (HERE / "INSTALL-AND-RETEST.sh").write_text(combined, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
