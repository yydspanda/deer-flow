bash <<'SOC_ENABLE_MONITOR'
set -euo pipefail
cd "$HOME/sec_know_model"
export RULE_REFINE_MONITOR_ENABLED=1
export RULE_REFINE_CSV_PATH="$HOME/sec-rule-refine-data/rules.csv"
export RULE_REFINE_NAMESPACE="sec-know-rule-monitor-mac-zhangjianming627-v1"

.venv/bin/python - <<'PYCHECK'
import json
import os
import socket
import sys

client = None
try:
    import util.root_config as rc
    from my_workflows.zeus.rule_refine.runtime_monitor import _build_monitor

    port = int(rc.SERVER_PORT)
    with socket.socket() as probe:
        probe.settimeout(1)
        if probe.connect_ex(("127.0.0.1", port)) == 0:
            raise SystemExit(f"停止：端口 {port} 仍有服务运行。请先停止原来的 run.py，再执行本命令。")
    monitor = _build_monitor()
    client = monitor.counter.client
    if not client.ping():
        raise RuntimeError("Redis ping failed")
    print(json.dumps({
        "status": "preflight_passed",
        "env_profile": rc.ENV,
        "api_port": port,
        "csv_rules": monitor.rules.count,
        "rule_provider": "csv_snapshot",
        "monitor_enable_setting": True,
        "namespace": os.environ["RULE_REFINE_NAMESPACE"],
        "redis_ping": True,
        "business_alerts_submitted": 0,
    }, ensure_ascii=False, indent=2), flush=True)
except Exception as error:
    print("预检失败：" + type(error).__name__ + "。先贴回这行，不会启动接口。", file=sys.stderr)
    raise SystemExit(1)
finally:
    if client is not None:
        client.close()
PYCHECK

exec .venv/bin/python run.py
SOC_ENABLE_MONITOR
