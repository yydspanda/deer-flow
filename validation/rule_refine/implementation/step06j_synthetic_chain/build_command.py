"""Build a complete copy/paste command; embedded tools are checksummed before use."""

import base64
import hashlib
import json
from pathlib import Path
import textwrap
import zlib

HERE = Path(__file__).resolve().parent
raw = json.dumps(
    {
        name: (HERE / name).read_text(encoding="utf-8")
        for name in ("runner.py", "probe.py", "pins.json")
    },
    ensure_ascii=False,
    separators=(",", ":"),
).encode("utf-8")
payload = "\n".join(
    textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode(), 100)
)
command = '''bash <<'SOC_STEP06J'
set -euo pipefail
umask 077
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_CHAIN'
import base64, hashlib, json, os, subprocess, sys, tempfile, zlib
from pathlib import Path

payload = """
PAYLOAD
"""
raw = zlib.decompress(base64.b64decode("".join(payload.split()), validate=True))
if hashlib.sha256(raw).hexdigest() != "DIGEST":
    raise SystemExit("停止：命令内容不完整或校验失败；未连接服务。")
files = json.loads(raw)
if set(files) != {"runner.py", "probe.py", "pins.json"}:
    raise SystemExit("停止：工具文件清单异常。")
for name, content in files.items():
    if name.endswith(".py"):
        compile(content, name, "exec")
parent = Path.home() / "sec-rule-refine-data"
parent.mkdir(mode=0o700, parents=True, exist_ok=True)
folder = Path(tempfile.mkdtemp(prefix="synthetic-chain-check-", dir=parent))
for name, content in files.items():
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
print("验证工具已就绪：" + str(folder), flush=True)
raise SystemExit(subprocess.call([sys.executable, "-B", str(folder / "runner.py")]))
PY_CHAIN
SOC_STEP06J
'''
(HERE / "CHECK-SYNTHETIC-CHAIN.sh").write_text(
    command.replace("PAYLOAD", payload).replace(
        "DIGEST", hashlib.sha256(raw).hexdigest()
    ),
    encoding="utf-8",
)
print(
    "Built CHECK-SYNTHETIC-CHAIN.sh; embedded sha256=" + hashlib.sha256(raw).hexdigest()
)
