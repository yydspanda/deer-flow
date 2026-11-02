"""Package only the standalone historical-review tool; never install app changes."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def contents():
    pins = json.loads(
        (HERE.parent / "step06w_unified_limits/current-pins.json").read_text(
            encoding="utf-8"
        )
    )
    names = {
        "contracts",
        "rule_config_source",
        "csv_config_source",
        "materials",
        "analysis",
        "filter_evaluation",
        "model_adapter",
        "limits",
        "condition_values",
    }
    pins = {
        name: value
        for name, value in pins.items()
        if (
            name.startswith("my_workflows/zeus/rule_refine/")
            and Path(name).stem in names
        )
        or name
        in {
            "util/model_semaphore.py",
            "config/model_limits.py",
            "config/circuit_breaker.py",
        }
    }
    pins["util/root_config.py"] = (
        "8b107a4d5956d029959f487bfa177e2443906b5f02c96dd3008e6fc3ea70be06"
    )
    files = {
        "historical_materials": (HERE / "materials.py").read_text(encoding="utf-8"),
        "historical_review": (HERE / "review.py").read_text(encoding="utf-8"),
        "historical_runner": (HERE / "runner.py").read_text(encoding="utf-8"),
        "inventory": (
            HERE.parent / "step06o_ingress_readiness/materials_check.py"
        ).read_text(encoding="utf-8"),
    }
    return {"files": files, "pins": pins}


def build():
    packet = contents()
    for name, source in packet["files"].items():
        compile(source, name, "exec")
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    loader = '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), 256 * 1024 + 1)
if (len(raw) > 256 * 1024 or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("工具校验失败，未调用模型。")
packet = json.loads(raw)
order = ("inventory", "historical_review", "historical_materials", "historical_runner")
if set(packet["files"]) != set(order):
    raise SystemExit("工具目录不匹配，未调用模型。")
compiled = {name: compile(source, name, "exec") for name, source in packet["files"].items()}
for name in order:
    module = types.ModuleType(name)
    module.__file__ = str(Path.cwd() / (name + ".py"))
    sys.modules[name] = module
    exec(compiled[name], module.__dict__)
tool_hashes = {name: hashlib.sha256(source.encode("utf-8")).hexdigest() for name, source in packet["files"].items()}
raise SystemExit(sys.modules["historical_runner"].main(packet["pins"], tool_hashes=tool_hashes))
'''.replace("__PAYLOAD__", payload).replace(
        "__DIGEST__", hashlib.sha256(raw).hexdigest()
    )
    command = (
        "bash <<'SOC_REVIEW_HISTORICAL_RULE'\nset -euo pipefail\numask 077\ncd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_HISTORICAL_REVIEW'\n"
        + loader
        + "PY_HISTORICAL_REVIEW\nSOC_REVIEW_HISTORICAL_RULE\n"
    )
    (HERE / "REVIEW-HISTORICAL-RULE.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
