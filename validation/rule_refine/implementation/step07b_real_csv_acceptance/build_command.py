"""Package source only for the real-CSV acceptance command, with no installation."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ("surge_materials", "restricted_dataframe_pickle", "materials", "runner")


def source_paths():
    return {
        "surge_materials": HERE.parent / "step06z_surge_recommendation/materials.py",
        "restricted_dataframe_pickle": HERE.parents[2]
        / "compact_zeus/shared/restricted_dataframe_pickle.py",
        "materials": HERE / "materials.py",
        "runner": HERE / "runner.py",
    }


def contents():
    return {
        "files": {
            name: path.read_text(encoding="utf-8")
            for name, path in source_paths().items()
        }
    }


def runtime_source(packet):
    if set(packet) != {"files"} or set(packet["files"]) != set(ORDER):
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
try:
    raw = decoder.decompress(base64.b64decode(payload), __SIZE__ + 1)
except Exception:
    raise SystemExit("工具内容损坏，未调用模型。") from None
if (len(raw) != __SIZE__ or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("工具校验失败，未调用模型。")
packet = json.loads(raw)
order = __ORDER__
if set(packet) != {"files"} or set(packet["files"]) != set(order):
    raise SystemExit("工具目录不匹配，未调用模型。")
compiled = {name: compile(source, name, "exec") for name, source in packet["files"].items()}
for name in order:
    module = types.ModuleType(name)
    module.__file__ = str(Path.cwd() / (name + ".py"))
    sys.modules[name] = module
    exec(compiled[name], module.__dict__)
tool_hashes = {name: hashlib.sha256(source.encode("utf-8")).hexdigest() for name, source in packet["files"].items()}
raise SystemExit(sys.modules["runner"].main(["--live"], tool_hashes=tool_hashes))
'''.replace("__PAYLOAD__", payload)
        .replace("__SIZE__", str(len(raw)))
        .replace("__DIGEST__", hashlib.sha256(raw).hexdigest())
        .replace("__ORDER__", repr(ORDER))
    )


def command(packet):
    return (
        "bash <<'SOC_REAL_CSV_ACCEPTANCE'\nset -euo pipefail\numask 077\n"
        'cd "$HOME/sec_know_model"\n'
        "env env_profile=LOCAL .venv/bin/python -B - <<'PY_REAL_CSV_ACCEPTANCE'\n"
        + runtime_source(packet)
        + "PY_REAL_CSV_ACCEPTANCE\nSOC_REAL_CSV_ACCEPTANCE\n"
    )


def build():
    value = command(contents())
    (HERE / "CHECK-REAL-CSV.sh").write_text(value, encoding="utf-8")
    return value


if __name__ == "__main__":
    build()
