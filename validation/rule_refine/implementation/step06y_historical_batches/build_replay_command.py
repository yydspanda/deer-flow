"""Package a saved-answer replay into one copyable command, with no app install."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = ("context_budget", "review", "diagnose_saved_reply", "replay_saved_reply")


def build():
    sources = {
        name: (HERE / (name + ".py")).read_text(encoding="utf-8") for name in ORDER
    }
    for name, source in sources.items():
        compile(source, name, "exec")
    raw = json.dumps(sources, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    runtime = (
        '''import base64, hashlib, json, sys, types, zlib
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), __SIZE__ + 1)
if (len(raw) != __SIZE__ or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("离线工具校验失败；未调用模型、未修改原报告。")
sources = json.loads(raw)
order = __ORDER__
if set(sources) != set(order):
    raise SystemExit("离线工具模块不完整。")
compiled = {name: compile(source, name, "exec") for name, source in sources.items()}
for name in order:
    module = types.ModuleType(name)
    module.__file__ = "<embedded-" + name + ">"
    module.__source_sha256__ = hashlib.sha256(sources[name].encode("utf-8")).hexdigest()
    sys.modules[name] = module
    exec(compiled[name], module.__dict__)
raise SystemExit(sys.modules["replay_saved_reply"].main())
'''.replace("__PAYLOAD__", payload)
        .replace("__SIZE__", str(len(raw)))
        .replace("__DIGEST__", hashlib.sha256(raw).hexdigest())
        .replace("__ORDER__", repr(ORDER))
    )
    command = (
        "bash <<'SOC_REPLAY_SAVED_CERTAINTY'\n"
        'set -euo pipefail\numask 077\ncd "$HOME/sec_know_model"\n'
        "read -r -p '报告目录（回车使用 historical-rule-batches-y00ewc9u）：' soc_report_dir </dev/tty\n"
        'soc_report_dir="${soc_report_dir:-$HOME/sec-rule-refine-data/historical-rule-batches-y00ewc9u}"\n'
        "printf '%s\\n' '使用保存回答重新校验；不调用模型，结果另存，原报告保留。'\n"
        ".venv/bin/python -B - \"$soc_report_dir\" <<'PY_REPLAY_CERTAINTY'\n"
        + runtime
        + "PY_REPLAY_CERTAINTY\nSOC_REPLAY_SAVED_CERTAINTY\n"
    )
    (HERE / "REPLAY-SAVED-REPLY.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
