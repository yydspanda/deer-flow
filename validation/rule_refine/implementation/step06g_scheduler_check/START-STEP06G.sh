#!/usr/bin/env bash
# 先在原 local_run.sh 终端按一次 Ctrl+C，等退出后再执行。
set -euo pipefail
cd "$HOME/sec_know_model"

.venv/bin/python - <<'PY'
import hashlib
import json
import shlex
import subprocess
import sys
from pathlib import Path

expected = {
    "local_run.sh": "2bde4b5acacaf81eff23d30c89f5efde2df8ae234daeba59b67200dc4a470bb1",
    "tasks/task.py": "4459e5d0c419d78cd38fe95442868e5e6aef3835c93cbbe88b8f5b78c5f2cd41",
    "config/celery_config.py": "64df22f908caf2533f0d6471a56c604bd88d6cce24890219e3f4ddb101670e7b",
    "my_workflows/zeus/rule_refine/background_service.py": "e132239d47a9a65b904dcd89dfb135e3934cdfedabc7bfa185144bda9b29e669",
    "my_workflows/zeus/rule_refine/background_tick.py": "827f2450a14c3adf4274a79cae906f0586b20885161cd6fe24de9e211268b070",
}
for name, digest in expected.items():
    path = Path(name)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        sys.exit("停止：文件版本不匹配，请贴回这一行：" + name)

def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result

try:
    config = json.loads(
        Path("config/rule_refine.analysis.local.json").read_text(encoding="utf-8"),
        object_pairs_hook=unique_object,
    )
except (OSError, ValueError):
    sys.exit("停止：无法读取独立分析配置，请贴回这一行。")
if not isinstance(config, dict) or config.get("enabled") is not False:
    sys.exit("停止：分析开关不是 false；本步先保持关闭，不启动后台。")

result = subprocess.run(
    ["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10
)
if result.returncode:
    sys.exit("停止：无法检查旧后台进程。")
remaining = []
for line in result.stdout.splitlines():
    parts = line.strip().split(None, 1)
    if len(parts) != 2 or "tasks.task" not in parts[1]:
        continue
    try:
        args = shlex.split(parts[1])
    except ValueError:
        remaining.append({"pid": parts[0], "role": "unconfirmed"})
        continue
    apps = []
    for index, arg in enumerate(args):
        if arg in {"-A", "--app"} and index + 1 < len(args):
            apps.append(args[index + 1])
        elif arg.startswith("--app="):
            apps.append(arg.split("=", 1)[1])
        elif arg.startswith("-A") and len(arg) > 2:
            apps.append(arg[2:])
    role = next((arg for arg in args if arg in {"worker", "beat", "flower"}), None)
    if role and set(apps) & {"tasks.task", "tasks.task.app"}:
        remaining.append({"pid": parts[0], "role": role})
if remaining:
    print(json.dumps({"remaining_processes": remaining}, ensure_ascii=False, indent=2))
    sys.exit("停止：还有同名后台进程。先等原 local_run.sh 完全退出；不要重复启动。")
print("启动前检查通过：自动分析关闭，未发现同名旧后台进程。", flush=True)
PY

exec env env_profile=LOCAL ENV_PROFILE=LOCAL bash local_run.sh
