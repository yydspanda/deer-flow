bash <<'SOC_READ_GENERATE_ERROR'
set -euo pipefail
cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"
.venv/bin/python -B - <<'PY_READ_GENERATE_ERROR'
import hashlib
import json
import os
import re
from pathlib import Path

default = (
    Path.home() / "sec-rule-refine-data/surge-real-csv-runtime-nt34ideo/jobs"
    / "36ae2550484c7f81ad6b333bbdf69f510da43515c18ad39a5b79b7531f9c9c82"
)
folder = Path(os.environ.get("SEC_RULE_REFINE_DIAG_JOB", str(default))).expanduser()
snapshots = {}

def read(name):
    path = folder / "generate" / name
    with path.open("rb") as stream:
        raw = stream.read(40 * 1024 * 1024 + 1)
    if len(raw) > 40 * 1024 * 1024:
        raise SystemExit("文件过大，停止读取：" + name)
    snapshots[path] = hashlib.sha256(raw).hexdigest()
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SystemExit("文件结构不符：" + name)
    return value

parsed = read("parsed.json")
response = read("response.json")
request = read("request.json")
content = response.get("content")
transport = response.get("transport") or {}
context = request.get("context") or {}
info = {
    "check": "rule_refine_saved_generation_error",
    "read_only": True,
    "model_calls": 0,
    "phase": "generate",
    "status": parsed.get("status"),
    "error_code": parsed.get("error_code"),
    "error_path": parsed.get("error_path"),
    "prompt_version": request.get("prompt_version", context.get("prompt_version")),
    "operator_contract_version": context.get("operator_contract_version"),
    "response_request_hash_matches": response.get("request_sha256") == parsed.get("request_sha256"),
    "finish_reason": transport.get("finish_reason"),
    "usage": transport.get("usage"),
    "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest()
        if isinstance(content, str) else None,
}
print(json.dumps(info, ensure_ascii=False, indent=2))
print("\n模型最终回答（只读展示；不输出请求原始日志或独立推理字段）：")
if isinstance(content, str):
    # Some providers inline reasoning in content. Hide it in this diagnostic;
    # leave the saved response unchanged.
    shown = re.sub(
        r"<(think|analysis)\b[^>]*>.*?(?:</\1\s*>|\Z)",
        "[内嵌推理已隐藏]", content, flags=re.IGNORECASE | re.DOTALL,
    )
    print(shown[:24000])
    if len(shown) > 24000:
        print("[显示超过24000字符，后文未打印；保存原文未修改]")
else:
    print("content不是字符串：" + type(content).__name__)

unchanged = all(hashlib.sha256(path.read_bytes()).hexdigest() == digest
                for path, digest in snapshots.items())
print(json.dumps({"saved_files_unchanged": unchanged, "files_written": 0},
                 ensure_ascii=False))
if not unchanged:
    raise SystemExit("读取期间文件发生变化，请勿以此作为一致快照。")
PY_READ_GENERATE_ERROR
SOC_READ_GENERATE_ERROR
