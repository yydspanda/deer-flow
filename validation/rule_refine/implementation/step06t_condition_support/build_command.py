"""Package one offline command; preserve earlier review and diagnostic versions."""

import base64
import hashlib
import importlib.util
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def contents():
    path = HERE.parent / "step06s_historical_diagnostic/build_command.py"
    spec = importlib.util.spec_from_file_location("_previous_diagnostic_builder", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    packet = builder.contents()
    for name, filename in (
        ("condition_support", "support.py"),
        ("budget_preview", "budget_preview.py"),
        ("support_runner", "runner.py"),
    ):
        packet["files"][name] = (HERE / filename).read_text(encoding="utf-8")
    return packet


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
    raise SystemExit("离线工具校验失败；未调用模型。")
packet = json.loads(raw)
order = ("inventory", "historical_materials", "historical_review", "historical_checks", "log_audit", "diagnostic_runner", "condition_support", "budget_preview", "support_runner")
if set(packet["files"]) != set(order):
    raise SystemExit("离线工具内容不匹配；未调用模型。")
for name in order:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(packet["files"][name], name, "exec"), module.__dict__)
raise SystemExit(sys.modules["support_runner"].main(packet))
'''.replace("__PAYLOAD__", payload).replace(
        "__DIGEST__", hashlib.sha256(raw).hexdigest()
    )
    command = (
        "bash <<'SOC_CHECK_HISTORICAL_CONDITIONS'\nset -euo pipefail\ncd \"$HOME/sec_know_model\"\n"
        'soc_report_dir="$HOME/sec-rule-refine-data/historical-rule-review-tz8x5cs4"\n'
        "# 整次输入的文本预算，单位KiB；这里只做离线预览，不调用模型。\n"
        "soc_input_budget_kib=128\n"
        'echo "离线核对原回答条件，并预览完整日志取样；不调用模型、不修改应用。"\n'
        '.venv/bin/python -B - "$soc_report_dir" "$soc_input_budget_kib" <<\'PY_HISTORICAL_CONDITIONS\'\n'
        + loader
        + "PY_HISTORICAL_CONDITIONS\nSOC_CHECK_HISTORICAL_CONDITIONS\n"
    )
    (HERE / "CHECK-HISTORICAL-CONDITIONS.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
