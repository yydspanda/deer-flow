"""Build one copyable, standalone historical batch review command."""

import base64
import hashlib
import importlib.util
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = (
    "inventory",
    "context_budget",
    "historical_review",
    "historical_materials",
    "single_runner",
    "historical_adapter",
    "batch_materials",
    "batch_report",
    "batch_runner",
)


def contents():
    spec = importlib.util.spec_from_file_location(
        "_batch_source_builder",
        HERE.parent / "step06x_historical_review/build_command.py",
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    packet = builder.contents()
    files = packet["files"]
    files["single_runner"] = files.pop("historical_runner")
    for module, filename in (
        ("context_budget", "context_budget.py"),
        ("historical_review", "review.py"),
        ("historical_adapter", "adapter.py"),
        ("batch_materials", "materials.py"),
        ("batch_report", "report.py"),
        ("batch_runner", "runner.py"),
    ):
        files[module] = (HERE / filename).read_text(encoding="utf-8")
    return packet


def runtime_source(packet):
    if set(packet["files"]) != set(ORDER):
        raise ValueError("unexpected_tool_modules")
    for name, source in packet["files"].items():
        compile(source, name, "exec")
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    return (
        '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), __SIZE__ + 1)
if (len(raw) != __SIZE__ or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("工具校验失败，未调用模型。")
packet = json.loads(raw)
order = __ORDER__
if set(packet["files"]) != set(order):
    raise SystemExit("工具目录不匹配，未调用模型。")
compiled = {name: compile(source, name, "exec") for name, source in packet["files"].items()}
for name in order:
    module = types.ModuleType(name)
    module.__file__ = str(Path.cwd() / (name + ".py"))
    sys.modules[name] = module
    exec(compiled[name], module.__dict__)
tool_hashes = {name: hashlib.sha256(source.encode("utf-8")).hexdigest() for name, source in packet["files"].items()}
raise SystemExit(sys.modules["batch_runner"].main(packet["pins"], tool_hashes=tool_hashes))
'''.replace("__PAYLOAD__", payload)
        .replace("__SIZE__", str(len(raw)))
        .replace("__DIGEST__", hashlib.sha256(raw).hexdigest())
        .replace("__ORDER__", repr(ORDER))
    )


def build():
    command = (
        "bash <<'SOC_REVIEW_HISTORICAL_BATCHES'\n"
        'set -euo pipefail\numask 077\ncd "$HOME/sec_know_model"\n'
        ".venv/bin/python -B - <<'PY_HISTORICAL_BATCHES'\n"
        + runtime_source(contents())
        + "PY_HISTORICAL_BATCHES\nSOC_REVIEW_HISTORICAL_BATCHES\n"
    )
    (HERE / "REVIEW-HISTORICAL-BATCHES.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
