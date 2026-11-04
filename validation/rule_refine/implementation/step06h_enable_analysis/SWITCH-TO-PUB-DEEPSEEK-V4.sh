#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/sec_know_model"

.venv/bin/python - <<'PY'
import json
import os
import tempfile
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from config.model_limits import get_model_policy

target = "Pub_DeepSeek_V4_Flash"
path = Path("config/rule_refine.analysis.local.json")
original = path.read_bytes()
data = json.loads(original)
if not isinstance(data, dict):
    raise SystemExit("分析配置不是JSON对象，未修改。")
previous = data.get("model", "DeepSeek_V32")

# 使用项目已有的模型策略，只读取配置，不连接Redis或请求模型。
get_model_policy(target)
try:
    opener = build_opener(ProxyHandler({}))
    with opener.open("http://127.0.0.1:8003/health", timeout=10) as response:
        health = json.load(response)
except Exception as error:
    raise SystemExit(
        "本机8003代理检查未通过（" + type(error).__name__
        + "）。请保持原start_proxy.sh运行后重试；配置未修改。"
    ) from None
route = health.get("upstreams", {}).get(target)
if not isinstance(route, dict):
    raise SystemExit("运行中的8003代理尚未加载目标模型路由；配置未修改。")

changed = previous != target
if changed:
    data["model"] = target
    fd, temporary = tempfile.mkstemp(prefix=".rule-model-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.read_bytes() != original:
            raise SystemExit("分析配置同时被修改，本次未覆盖，请重新执行。")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)

print(json.dumps({
    "status": "updated" if changed else "already_configured",
    "previous_model": previous,
    "model": target,
    "analysis_enabled": data.get("enabled", False),
    "adapter_route_present": True,
    "effective_on": "next_new_analysis_or_historical_run",
    "restart_required": False,
    "existing_results_rerun": False,
    "model_calls_this_command": 0,
}, ensure_ascii=False, indent=2))
PY
