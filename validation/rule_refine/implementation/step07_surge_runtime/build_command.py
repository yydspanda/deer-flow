"""Build one offline surge-runtime installer against the installed 6W baseline."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPL = HERE.parent
BASELINE = IMPL / "step06w_unified_limits"
MODULE = "my_workflows/zeus/rule_refine/"
TEST = "test/rule_refine/"
POLICY = "config/rule_refine.surge.local.json"
DEFAULTS = {
    "enabled": True,
    "context_tokens": 262144,
    "input_fraction": 0.8,
    "timeout_seconds": 600,
    "operator_context": "",
}
REGRESSION_TESTS = (
    "test_pending_signal.py",
    "test_background_service.py",
    "test_background_tick.py",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def baseline_pins():
    return json.loads((BASELINE / "current-pins.json").read_text(encoding="utf-8"))


def find_source(relative, expected):
    for path in sorted(IMPL.glob("step*/payload/" + relative)):
        if HERE not in path.parents and sha(path.read_bytes()) == expected:
            return path
    raise ValueError("reviewed_source_missing: " + relative)


def contents():
    old = baseline_pins()
    files = {}
    for source in sorted((HERE / "payload").rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(HERE / "payload").as_posix()
        raw = source.read_bytes()
        if relative.endswith(".py"):
            compile(raw, relative, "exec")
        files[relative] = {
            "old_sha256": old.get(relative),
            "new_sha256": sha(raw),
            "content": raw.decode("utf-8"),
        }
    # Keep the defaults in the packet so the installer does not need to import
    # the application or read a model/credential configuration to activate it.
    raw = (json.dumps(DEFAULTS, indent=2) + "\n").encode("utf-8")
    files[POLICY] = {
        "old_sha256": None,
        "new_sha256": sha(raw),
        "content": raw.decode("utf-8"),
    }
    dependencies = {
        relative: expected
        for relative, expected in old.items()
        if relative.startswith(MODULE) and relative not in files
    }
    for relative, expected in dependencies.items():
        find_source(relative, expected)
    # The existing unittest fixtures import each other; stage them all, but do
    # not overwrite any of the operator's already-installed old test files.
    test_files = {
        source.relative_to(BASELINE / "payload").as_posix(): source.read_text(
            encoding="utf-8"
        )
        for source in sorted((BASELINE / "payload/test/rule_refine").glob("test_*.py"))
        if source.relative_to(BASELINE / "payload").as_posix() not in files
    }
    tests = sorted(
        relative
        for relative in files
        if relative.startswith(TEST + "test_surge") and relative.endswith(".py")
    ) + [TEST + name for name in REGRESSION_TESTS]
    return {
        "require_evidence": False,
        "files": files,
        "dependencies": dependencies,
        "test_files": test_files,
        "tests": tests,
    }


def build():
    packet = contents()
    raw = json.dumps(
        {
            "packet": packet,
            "installer": (HERE / "install.py").read_text(encoding="utf-8"),
        },
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    if len(raw) > 2 * 1024 * 1024:
        raise ValueError("installer_package_over_budget")
    encoded = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    loader = '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), 2 * 1024 * 1024 + 1)
if (len(raw) > 2 * 1024 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__SHA__"):
    raise SystemExit("安装包校验失败，项目未修改。")
bundle = json.loads(raw)
module = types.ModuleType("surge_runtime_installer")
sys.modules[module.__name__] = module
exec(compile(bundle["installer"], "<surge-runtime-installer>", "exec"), module.__dict__)
try:
    result = module.apply(Path.cwd(), Path(sys.argv[1]).expanduser(), bundle["packet"])
except Exception as error:
    result = {"status": "stopped", "error_type": type(error).__name__, "model_calls": 0,
              "redis_calls": 0, "business_database_calls": 0}
    if isinstance(error, module.InstallError):
        result["reason"] = str(error)
        if error.relative_file is not None:
            result["file"] = error.relative_file
            result["actual_sha256"] = error.actual_sha256
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps(result, ensure_ascii=False, indent=2))
'''.replace("__PAYLOAD__", encoded).replace("__SHA__", sha(raw))
    command = (
        "bash <<'SOC_SURGE_RUNTIME_INSTALL'\nset -euo pipefail\numask 077\n"
        'cd "$HOME/sec_know_model"\n'
        'soc_install_data="$HOME/sec-rule-refine-data"\n'
        'echo "安装突增分析与历史对照；先执行离线测试，不调用模型。"\n'
        ".venv/bin/python -B - \"$soc_install_data\" <<'PY_SURGE_RUNTIME_INSTALL'\n"
        + loader
        + "PY_SURGE_RUNTIME_INSTALL\nSOC_SURGE_RUNTIME_INSTALL\n"
    )
    (HERE / "INSTALL-COMMAND.sh").write_text(command, encoding="utf-8")
    current = baseline_pins()
    current.update(
        {
            name: entry["new_sha256"]
            for name, entry in packet["files"].items()
            if name.startswith(MODULE)
        }
    )
    (HERE / "current-pins.json").write_text(
        json.dumps(current, indent=2) + "\n", encoding="utf-8"
    )
    return command


if __name__ == "__main__":
    build()
