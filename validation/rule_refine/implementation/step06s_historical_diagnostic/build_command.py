"""Build one copyable offline command; include reviewed pure sources, no clients."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
STEPS = {
    "contracts": "step01_contracts",
    "rule_config_source": "step03d_preparation",
    "materials": "step06c_task_source",
    "analysis": "step06l_fenced_json",
    "filter_evaluation": "step05a_filter_evaluation",
    "csv_config_source": "step03d_preparation",
}


def contents():
    files = {
        "inventory": HERE.parent / "step06o_ingress_readiness/materials_check.py",
        "historical_materials": HERE.parent / "step06r_historical_review/materials.py",
        "historical_review": HERE.parent / "step06r_historical_review/review.py",
        "historical_checks": HERE / "checks.py",
        "log_audit": HERE / "log_audit.py",
        "diagnostic_runner": HERE / "runner.py",
    }
    sources = {
        name: (
            HERE.parent
            / step
            / "payload/my_workflows/zeus/rule_refine"
            / (name + ".py")
        ).read_text(encoding="utf-8")
        for name, step in STEPS.items()
    }
    pins = json.loads(
        (HERE.parent / "step06m_apply_parser/current-pins.json").read_text(
            encoding="utf-8"
        )
    )
    for name, source in sources.items():
        relative = "my_workflows/zeus/rule_refine/" + name + ".py"
        if hashlib.sha256(source.encode("utf-8")).hexdigest() != pins[relative]:
            raise ValueError("staged_source_changed: " + name)
    return {
        "files": {
            name: path.read_text(encoding="utf-8") for name, path in files.items()
        },
        "sources": sources,
    }


def build():
    packet = contents()
    for name, source in {**packet["files"], **packet["sources"]}.items():
        compile(source, name, "exec")
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    loader = '''import base64, hashlib, json, sys, types, zlib
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), 256 * 1024 + 1)
if (len(raw) > 256 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("诊断工具校验失败；未调用模型。")
packet = json.loads(raw)
order = ("inventory", "historical_materials", "historical_review", "historical_checks", "log_audit", "diagnostic_runner")
if set(packet["files"]) != set(order):
    raise SystemExit("诊断工具内容不匹配；未调用模型。")
for name in order:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(packet["files"][name], name, "exec"), module.__dict__)
raise SystemExit(sys.modules["diagnostic_runner"].main(packet["sources"]))
'''.replace("__PAYLOAD__", payload).replace(
        "__DIGEST__", hashlib.sha256(raw).hexdigest()
    )
    command = (
        "bash <<'SOC_DIAGNOSE_HISTORICAL_REVIEW'\nset -euo pipefail\ncd \"$HOME/sec_know_model\"\n"
        'soc_report_dir="$HOME/sec-rule-refine-data/historical-rule-review-tz8x5cs4"\n'
        'echo "离线诊断已保存回答和日志省略原因；不调用模型、不修改应用。"\n'
        ".venv/bin/python -B - \"$soc_report_dir\" <<'PY_HISTORICAL_DIAGNOSE'\n"
        + loader
        + "PY_HISTORICAL_DIAGNOSE\nSOC_DIAGNOSE_HISTORICAL_REVIEW\n"
    )
    (HERE / "DIAGNOSE-HISTORICAL-REVIEW.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
