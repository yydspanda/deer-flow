#!/usr/bin/env bash
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

from my_workflows.zeus.rule_refine.local_settings import _object, configure_rule_monitor

root = Path.cwd()
path = root / "config/rule_refine.analysis.local.json"
original = path.read_bytes()
if len(original) > 16384:
    sys.exit("停止：分析配置过大。")
data = json.loads(original, object_pairs_hook=_object)
if not isinstance(data, dict) or set(data) - {"enabled", "model", "state_dir"}:
    sys.exit("停止：分析配置格式不符。")
if type(data.get("enabled")) is not bool:
    sys.exit("停止：enabled 必须是布尔值。")
print("当前自动分析：" + ("开启" if data["enabled"] else "关闭"), flush=True)
print("ENABLE：开启，有待办且材料齐全时自动调用模型；建议仅本地保存。")
print("DISABLE：关闭后续分析；已经开始的调用会继续结束。", flush=True)
print("请输入 ENABLE 或 DISABLE，其他输入取消：", end="", flush=True)
with open("/dev/tty", "r", encoding="utf-8") as tty:
    action = tty.readline().strip()
if action not in {"ENABLE", "DISABLE"}:
    sys.exit("已取消，配置未修改。")

enabled = action == "ENABLE"
if enabled:
    env = dict(os.environ)
    if any(env.get(key, "LOCAL").upper() != "LOCAL" for key in ("env_profile", "ENV_PROFILE")):
        sys.exit("停止：当前终端不是 LOCAL 环境，配置未修改。")
    model = data.get("model", "DeepSeek_V32")
    state_dir = data.get("state_dir", str(Path.home() / "sec-rule-refine-data/background"))
    if not isinstance(model, str) or not 1 <= len(model) <= 128 or model != model.strip() or not model.isprintable():
        sys.exit("停止：模型名称无效，配置未修改。")
    if not isinstance(state_dir, str) or not state_dir.strip():
        sys.exit("停止：结果目录无效，配置未修改。")
    monitor = configure_rule_monitor(root, env)
    if monitor["status"] != "configured" or not monitor["enabled"]:
        sys.exit("停止：监测配置未开启或无效，配置未修改。")
    from config.model_limits import get_model_policy
    from my_workflows.zeus.rule_refine.csv_config_source import CsvRuleConfigSource

    get_model_policy(model)
    rules = CsvRuleConfigSource(env["RULE_REFINE_CSV_PATH"])
    print(json.dumps({
        "configuration_check": "passed",
        "model": model,
        "rule_provider": "csv_snapshot",
        "csv_rules": len(rules.list_rules()),
        "namespace": env["RULE_REFINE_NAMESPACE"],
    }, ensure_ascii=False, indent=2), flush=True)

if path.read_bytes() != original:
    sys.exit("停止：配置在操作期间发生变化，请重新执行。")
data["enabled"] = enabled
fd, temporary = tempfile.mkstemp(prefix=".rule-analysis-", dir=path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
finally:
    Path(temporary).unlink(missing_ok=True)
print(json.dumps({
    "status": "enabled" if enabled else "disabled",
    "effective_on": "next_background_tick",
    "restart_required": False,
    "automatic_apply": False,
    "model_calls_this_command": 0,
}, ensure_ascii=False, indent=2))
PY
