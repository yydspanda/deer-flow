"""Package the review-sampling fix against the already delivered step07 runtime."""

import base64
import hashlib
import importlib.util
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "step07_surge_runtime"
MODULE = "my_workflows/zeus/rule_refine/"
TEST = "test/rule_refine/"
NEW_TEST = TEST + "test_surge_review_sampling.py"
REGRESSION_TESTS = (
    TEST + "test_surge_processor.py",
    TEST + "test_surge_runtime.py",
    TEST + "test_surge_integration.py",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def baseline_builder():
    spec = importlib.util.spec_from_file_location(
        "review_sampling_fix_baseline", BASE / "build_command.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    return builder


def contents():
    builder = baseline_builder()
    baseline = builder.contents()
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
    if NEW_TEST not in files or not any(name.startswith(MODULE) for name in files):
        raise ValueError("review_sampling_payload_incomplete")
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
    # All test fixtures are staged privately, never installed over old tests.
    test_files = {
        **baseline["test_files"],
        **{
            name: entry["content"]
            for name, entry in baseline["files"].items()
            if name.startswith(TEST)
        },
    }
    test_files = {name: text for name, text in test_files.items() if name not in files}
    return {
        "require_evidence": False,
        "files": files,
        "dependencies": dependencies,
        "test_files": test_files,
        "tests": [NEW_TEST, *REGRESSION_TESTS],
    }


def installer_source():
    source = (BASE / "install.py").read_text(encoding="utf-8")
    source = source.replace(
        "rule_refine_surge_runtime_install", "rule_refine_review_sampling_fix"
    )
    return source.replace(
        'prefix="surge-runtime-install-"', 'prefix="surge-review-sampling-fix-"'
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
installer = types.ModuleType("surge_review_sampling_fix_installer")
sys.modules[installer.__name__] = installer
exec(compile(bundle["installer"], "<review-sampling-fix-installer>", "exec"), installer.__dict__)
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


def build():
    command = (
        "bash <<'SOC_SURGE_REVIEW_SAMPLING_FIX'\nset -euo pipefail\numask 077\n"
        'cd "$HOME/sec_know_model"\n'
        'echo "修复AI复核取样；先执行离线回归，不调用模型，不重新补数。"\n'
        ".venv/bin/python -B - <<'PY_SURGE_REVIEW_SAMPLING_FIX'\n"
        + runtime_source(contents())
        + "PY_SURGE_REVIEW_SAMPLING_FIX\nSOC_SURGE_REVIEW_SAMPLING_FIX\n"
    )
    (HERE / "INSTALL-COMMAND.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
