"""Build a tool-only terminal fix against the already delivered step07 bytes."""

import base64
import hashlib
import importlib.util
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = HERE.parent / "step07_surge_runtime"
TOOL = "test/rule_refine/check_surge_dev_live.py"
TEST = "test/rule_refine/test_surge_dev_terminal.py"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contents():
    spec = importlib.util.spec_from_file_location(
        "terminal_fix_baseline", BASE / "build_command.py"
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    baseline = builder.contents()
    files = {}
    for name in (TOOL, TEST):
        raw = (HERE / "payload" / name).read_bytes()
        files[name] = {
            "old_sha256": baseline["files"].get(name, {}).get("new_sha256"),
            "new_sha256": sha(raw),
            "content": raw.decode("utf-8"),
        }
    dependencies = {
        **baseline["dependencies"],
        **{
            name: entry["new_sha256"]
            for name, entry in baseline["files"].items()
            if name.startswith(builder.MODULE)
            or name.endswith("check_surge_dev_history.py")
        },
    }
    original_test = "test/rule_refine/test_surge_dev_live.py"
    return {
        "require_evidence": False,
        "files": files,
        "dependencies": dependencies,
        "test_files": {original_test: baseline["files"][original_test]["content"]},
        "tests": [TEST, original_test],
    }


def installer_source():
    source = (BASE / "install.py").read_text(encoding="utf-8")
    source = source.replace(
        "rule_refine_surge_runtime_install", "rule_refine_terminal_fix"
    )
    source = source.replace('"restart_required": True', '"restart_required": False')
    source = source.replace(
        "application_code_updated=True,",
        "application_code_updated=False,\n                verification_tool_updated=True,",
    )
    source = source.replace(
        'prefix="surge-runtime-install-"', 'prefix="surge-terminal-fix-"'
    )
    return source


def build():
    raw = json.dumps(
        {"packet": contents(), "installer": installer_source()},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    loader = '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode("""__PAYLOAD__"""), 2 * 1024 * 1024 + 1)
if (len(raw) > 2 * 1024 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__SHA__"):
    raise SystemExit("修复包校验失败，文件未修改。")
bundle = json.loads(raw)
installer = types.ModuleType("surge_terminal_fix_installer")
sys.modules[installer.__name__] = installer
exec(compile(bundle["installer"], "<terminal-fix-installer>", "exec"), installer.__dict__)
try:
    result = installer.apply(Path.cwd(), Path.home() / "sec-rule-refine-data", bundle["packet"])
except Exception as error:
    result = {"status":"stopped", "error_type":type(error).__name__, "model_calls":0}
    if isinstance(error, installer.InstallError):
        result.update(reason=str(error), file=error.relative_file, actual_sha256=error.actual_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps(result, ensure_ascii=False, indent=2))
'''.replace("__PAYLOAD__", payload).replace("__SHA__", sha(raw))
    command = (
        "bash <<'SOC_SURGE_TERMINAL_FIX'\nset -euo pipefail\numask 077\n"
        'cd "$HOME/sec_know_model"\n'
        'echo "修复验证工具的终端确认；先执行离线回归，不重新补数。"\n'
        ".venv/bin/python -B - <<'PY_SURGE_TERMINAL_FIX'\n"
        + loader
        + "PY_SURGE_TERMINAL_FIX\n"
        "env env_profile=LOCAL .venv/bin/python -B test/rule_refine/check_surge_dev_live.py --live\n"
        "SOC_SURGE_TERMINAL_FIX\n"
    )
    (HERE / "FIX-AND-CONTINUE.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
