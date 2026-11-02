"""Build one copy/paste command for offline numeric comparison replay."""

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
    spec = importlib.util.spec_from_file_location("_numeric_previous_builder", path)
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    packet = builder.contents()
    packet["files"].update(
        {
            "numeric_changes": (HERE / "changes.py").read_text(encoding="utf-8"),
            "numeric_runner": (HERE / "runner.py").read_text(encoding="utf-8"),
        }
    )
    packet["comparator_source"] = (HERE / "condition_values.py").read_text(
        encoding="utf-8"
    )
    return packet


def build():
    packet = contents()
    for name, source in {
        **packet["files"],
        **packet["sources"],
        "condition_values": packet["comparator_source"],
    }.items():
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
    raise SystemExit("工具完整性校验失败，未调用模型或修改应用。")
packet = json.loads(raw)
order = ("inventory", "historical_materials", "historical_review", "historical_checks", "log_audit", "diagnostic_runner", "numeric_changes", "numeric_runner")
if set(packet["files"]) != set(order):
    raise SystemExit("工具内容不匹配。")
for name in order:
    module = types.ModuleType(name)
    sys.modules[name] = module
    exec(compile(packet["files"][name], name, "exec"), module.__dict__)
raise SystemExit(sys.modules["numeric_runner"].main(packet))
'''.replace("__PAYLOAD__", payload).replace(
        "__DIGEST__", hashlib.sha256(raw).hexdigest()
    )
    command = (
        "bash <<'SOC_REPLAY_NUMERIC_EQUAL'\nset -euo pipefail\ncd \"$HOME/sec_know_model\"\n"
        'soc_report_dir="$HOME/sec-rule-refine-data/historical-rule-review-tz8x5cs4"\n'
        'echo "使用原回答离线复核数字等值条件；不调用模型、不修改应用或旧报告。"\n'
        ".venv/bin/python -B - \"$soc_report_dir\" <<'PY_NUMERIC_EQUAL'\n"
        + loader
        + "PY_NUMERIC_EQUAL\nSOC_REPLAY_NUMERIC_EQUAL\n"
    )
    (HERE / "REPLAY-NUMERIC-EQUAL.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
