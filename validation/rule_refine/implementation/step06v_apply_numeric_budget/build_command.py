"""Package the reviewed app update and its pure dependency/test tree."""

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
OLD_FILES = {
    MODULE + "condition_values.py": None,
    MODULE + "filter_evaluation.py": "step05a_filter_evaluation/payload/",
    MODULE + "materials.py": "step06c_task_source/payload/",
    MODULE + "analysis.py": "step06l_fenced_json/payload/",
    TEST + "test_numeric_values.py": None,
    TEST + "test_materials.py": "step04a_materials/payload/",
    TEST + "test_filter_evaluation.py": "step05a_filter_evaluation/payload/",
    TEST + "test_analysis.py": "step06l_fenced_json/payload/",
    TEST + "test_proposal_check.py": "step05b_proposal_check/payload/",
}
DEPENDENCIES = {
    MODULE + "contracts.py": "step01_contracts/payload/",
    MODULE + "rule_config_source.py": "step03d_preparation/payload/",
    MODULE + "proposal_check.py": "step05b_proposal_check/payload/",
    MODULE + "saved_delivery.py": "step06a_saved_delivery/payload/",
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def contents():
    files = {}
    for relative, previous in OLD_FILES.items():
        raw = (HERE / "payload" / relative).read_bytes()
        compile(raw, relative, "exec")
        files[relative] = {
            "old_sha256": sha((IMPL / previous / relative).read_bytes())
            if previous
            else None,
            "new_sha256": sha(raw),
            "content": raw.decode("utf-8"),
        }
    dependencies = {
        relative: sha((IMPL / origin / relative).read_bytes())
        for relative, origin in DEPENDENCIES.items()
    }
    return {
        "files": files,
        "dependencies": dependencies,
        "tests": [
            TEST + name
            for name in (
                "test_numeric_values.py",
                "test_materials.py",
                "test_analysis.py",
                "test_filter_evaluation.py",
                "test_proposal_check.py",
            )
        ],
        "test_files": {},
        "evidence_expectations": {
            "response_sha256": "fb93fa57d6238a83094cd28d275eb59a42e156dc04617c93cd76482cbb84834b",
            "original_prompt_sha256": "7a8e9582b12890578e1cb3ce9593d30391343614ec2474273b5c362952a82484",
        },
    }


def build():
    packet = contents()
    install = (HERE / "install.py").read_text(encoding="utf-8")
    compile(install, "install", "exec")
    raw = json.dumps(
        {"packet": packet, "installer": install}, ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    loader = '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), 512 * 1024 + 1)
if (len(raw) > 512 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("安装包校验失败，项目未修改。")
bundle = json.loads(raw)
module = types.ModuleType("numeric_budget_installer")
sys.modules[module.__name__] = module
exec(compile(bundle["installer"], "<numeric-budget-installer>", "exec"), module.__dict__)
try:
    result = module.apply(Path.cwd(), Path(sys.argv[1]).expanduser(), bundle["packet"])
except Exception as error:
    result = {"status": "stopped", "error_type": type(error).__name__, "model_calls": 0,
              "redis_calls": 0, "database_calls": 0}
    # Installer messages contain fixed reasons and reviewed relative file names only.
    if isinstance(error, module.InstallError):
        result["reason"] = str(error)
        if error.relative_file is not None:
            result["file"] = error.relative_file
            result["actual_sha256"] = error.actual_sha256
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps(result, ensure_ascii=False, indent=2))
'''.replace("__PAYLOAD__", payload).replace("__DIGEST__", sha(raw))
    command = (
        "bash <<'SOC_APPLY_NUMERIC_BUDGET'\nset -euo pipefail\numask 077\ncd \"$HOME/sec_know_model\"\n"
        'soc_evidence_dir="$HOME/sec-rule-refine-data/historical-numeric-replay-188ctea2"\n'
        'echo "安装数字等值比较与完整请求预算；先运行隔离测试，不调用模型。"\n'
        ".venv/bin/python -B - \"$soc_evidence_dir\" <<'PY_APPLY_NUMERIC_BUDGET'\n"
        + loader
        + "PY_APPLY_NUMERIC_BUDGET\nSOC_APPLY_NUMERIC_BUDGET\n"
    )
    (HERE / "APPLY-NUMERIC-BUDGET-FIX.sh").write_text(command, encoding="utf-8")
    pins = json.loads(
        (IMPL / "step06m_apply_parser/current-pins.json").read_text(encoding="utf-8")
    )
    for relative, entry in packet["files"].items():
        if relative.startswith(MODULE):
            pins[relative] = entry["new_sha256"]
    (HERE / "current-pins.json").write_text(
        json.dumps(pins, indent=2) + "\n", encoding="utf-8"
    )
    (HERE / "install-manifest.json").write_text(
        json.dumps(
            {
                relative: {k: entry[k] for k in ("old_sha256", "new_sha256")}
                for relative, entry in packet["files"].items()
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return command


if __name__ == "__main__":
    build()
