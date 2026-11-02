"""Build one copy/paste command from the two reviewed read-only probes."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
FILES = ("check.py", "materials_check.py")


def build():
    files = {name: (HERE / name).read_text(encoding="utf-8") for name in FILES}
    for name, source in files.items():
        compile(source, name, "exec")
    raw = json.dumps(files, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    digest = hashlib.sha256(raw).hexdigest()
    runner = '''import base64
import hashlib
import json
import os
import sys
import types
import zlib
from pathlib import Path

PAYLOAD = """__PAYLOAD__"""
EXPECTED = "__DIGEST__"

def load_tools():
    unpacker = zlib.decompressobj()
    data = unpacker.decompress(base64.b64decode(PAYLOAD), 256 * 1024 + 1)
    if (len(data) > 256 * 1024 or not unpacker.eof or unpacker.unused_data
            or unpacker.unconsumed_tail or hashlib.sha256(data).hexdigest() != EXPECTED):
        raise ValueError("embedded_tool_check_failed")
    files = json.loads(data)
    if set(files) != {"check.py", "materials_check.py"}:
        raise ValueError("embedded_tool_names_invalid")
    compiled = {name: compile(source, name, "exec") for name, source in files.items()}
    modules = {}
    for name, code in compiled.items():
        module = types.ModuleType("_rule_readonly_" + name[:-3])
        module.__file__ = str(Path.cwd() / name)
        sys.modules[module.__name__] = module
        exec(code, module.__dict__)
        modules[name] = module
    return modules

if any(os.environ.get(key, "LOCAL").upper() != "LOCAL" for key in ("env_profile", "ENV_PROFILE")):
    print(json.dumps({"check": "rule_refine_real_input_preparation", "status": "skipped_non_local",
                      "read_only": True, "business_alerts_submitted": 0, "model_calls": 0}, indent=2))
    raise SystemExit(0)
try:
    tools = load_tools()
except Exception as error:
    print(json.dumps({"status": "tool_preparation_failed", "error_type": type(error).__name__,
                      "model_calls": 0, "business_alerts_submitted": 0}, indent=2))
    raise SystemExit(1)

print("开始只读检查：本机接口、注入脚本结构及200条小样本；不提交告警、不调用模型。", flush=True)
root = Path.cwd()
try:
    ingress = tools["check.py"].check(root, os.environ)
    materials = tools["materials_check.py"].check_materials(root)
except Exception as error:
    print(json.dumps({"status": "inspection_failed", "error_type": type(error).__name__,
                      "model_calls": 0, "business_alerts_submitted": 0}, indent=2))
    raise SystemExit(1)
print(json.dumps({
    "check": "rule_refine_real_input_preparation",
    "status": "inspection_complete",
    "read_only": True,
    "business_alerts_submitted": 0,
    "celery_tasks_dispatched": 0,
    "model_calls": 0,
    "redis_calls": 0,
    "application_code_updated": False,
    "ingress": ingress,
    "historical_materials": materials,
}, ensure_ascii=False, indent=2))
'''.replace("__PAYLOAD__", payload).replace("__DIGEST__", digest)
    command = (
        "bash <<'SOC_CHECK_REAL_INPUTS'\nset -euo pipefail\ncd \"$HOME/sec_know_model\"\n"
        ".venv/bin/python -B - <<'PY_REAL_INPUTS'\n"
        + runner
        + "PY_REAL_INPUTS\nSOC_CHECK_REAL_INPUTS\n"
    )
    (HERE / "CHECK-REAL-INPUTS.sh").write_text(command, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
