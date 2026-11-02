"""Build one offline installer compatible with the confirmed 6M and optional 6V."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMPL = HERE.parent
MODULE = "my_workflows/zeus/rule_refine/"
TEST = "test/rule_refine/"
POLICY = "config/rule_refine.limits.local.json"
DEFAULTS = {
    "input_kib": 128,
    "output_kib": 64,
    "timeout_seconds": 240,
    "retry_attempts": 3,
    "retry_delay_seconds": 60,
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def pins(step):
    return json.loads((IMPL / step / "current-pins.json").read_text(encoding="utf-8"))


def find_source(relative, expected):
    for path in sorted(IMPL.glob("step*/payload/" + relative)):
        if HERE in path.parents:
            continue
        if sha(path.read_bytes()) == expected:
            return path
    raise ValueError("reviewed_source_missing: " + relative)


def old_test_sources(relative):
    # Previously delivered standalone tests have no runtime authority; accept
    # their reviewed historical bytes or absence, while preserving unknown edits.
    paths = [
        p
        for p in sorted(IMPL.glob("step*/payload/" + relative))
        if HERE not in p.parents
    ]
    return paths


def contents():
    old_pins = pins("step06m_apply_parser")
    optional_pins = pins("step06v_apply_numeric_budget")
    files = {}
    for source in sorted((HERE / "payload").rglob("*")):
        if not source.is_file() or "__pycache__" in source.parts:
            continue
        relative = source.relative_to(HERE / "payload").as_posix()
        raw = source.read_bytes()
        if relative.endswith(".py"):
            compile(raw, relative, "exec")
        versions = set()
        if relative.startswith(TEST):
            versions.update(
                sha(path.read_bytes()) for path in old_test_sources(relative)
            )
            previous = None
        else:
            previous = old_pins.get(relative)
            optional = optional_pins.get(relative)
            if optional is not None:
                versions.add(optional)
        files[relative] = {
            "old_sha256": previous,
            "accepted_previous_sha256": sorted(versions),
            "new_sha256": sha(raw),
            "content": raw.decode("utf-8"),
        }
    raw = (json.dumps(DEFAULTS, indent=2) + "\n").encode("utf-8")
    files[POLICY] = {
        "old_sha256": None,
        "new_sha256": sha(raw),
        "content": raw.decode("utf-8"),
    }
    dependencies = {
        relative: expected
        for relative, expected in old_pins.items()
        if relative.startswith(MODULE) and relative not in files
    }
    for relative, expected in dependencies.items():
        find_source(relative, expected)
    tests = [
        name
        for name in files
        if name.startswith(TEST + "test_")
        and name.endswith(".py")
        and name != TEST + "test_task_source.py"
    ]
    return {
        "require_evidence": False,
        "files": files,
        "dependencies": dependencies,
        "test_files": {},
        "tests": sorted(tests),
        "evidence_expectations": {
            "response_sha256": "fb93fa57d6238a83094cd28d275eb59a42e156dc04617c93cd76482cbb84834b",
            "original_prompt_sha256": "7a8e9582b12890578e1cb3ce9593d30391343614ec2474273b5c362952a82484",
        },
    }


def build():
    packet = contents()
    installer = (HERE / "install.py").read_text(encoding="utf-8")
    raw = json.dumps(
        {"packet": packet, "installer": installer}, ensure_ascii=False, sort_keys=True
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
module = types.ModuleType("unified_limits_installer")
sys.modules[module.__name__] = module
exec(compile(bundle["installer"], "<unified-limits-installer>", "exec"), module.__dict__)
try:
    result = module.apply(Path.cwd(), Path(sys.argv[1]).expanduser(), bundle["packet"])
except Exception as error:
    result = {"status": "stopped", "error_type": type(error).__name__, "model_calls": 0,
              "redis_calls": 0, "database_calls": 0}
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
        "bash <<'SOC_UNIFIED_RULE_FIX'\nset -euo pipefail\numask 077\n"
        'cd "$HOME/sec_know_model"\n'
        'soc_install_data="$HOME/sec-rule-refine-data"\n'
        'echo "安装规则建议统一修复；先执行离线测试，不调用模型。"\n'
        ".venv/bin/python -B - \"$soc_install_data\" <<'PY_UNIFIED_RULE_FIX'\n"
        + loader
        + "PY_UNIFIED_RULE_FIX\nSOC_UNIFIED_RULE_FIX\n"
    )
    (HERE / "INSTALL-RULE-REFINE-FIX.sh").write_text(command, encoding="utf-8")
    current = pins("step06m_apply_parser")
    for relative, entry in packet["files"].items():
        if relative.startswith(MODULE):
            current[relative] = entry["new_sha256"]
    (HERE / "current-pins.json").write_text(
        json.dumps(current, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        name: {k: v for k, v in entry.items() if k != "content"}
        for name, entry in packet["files"].items()
    }
    (HERE / "install-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return command


if __name__ == "__main__":
    build()
