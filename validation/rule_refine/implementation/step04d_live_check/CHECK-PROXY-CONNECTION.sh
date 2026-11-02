bash <<'SOC_PROXY_CHECK'
set -euo pipefail
cd "$HOME/sec_know_model"
for soc_port in 4001 8003; do
    echo "端口 ${soc_port} 的监听进程："
    lsof -nP -iTCP:"$soc_port" -sTCP:LISTEN || true
done
.venv/bin/python - <<'PY'
import importlib.metadata
import json
import socket

result = {"check": "local_model_proxy_connection", "model_calls": 0, "versions": {}, "connections": []}
for name in ("openai", "httpx", "httpcore"):
    try:
        result["versions"][name] = importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        result["versions"][name] = "not_installed"
for host, port in (("localhost", 4001), ("127.0.0.1", 4001), ("::1", 4001), ("127.0.0.1", 8003)):
    row = {"host": host, "port": port}
    try:
        with socket.create_connection((host, port), timeout=2) as connection:
            row.update(connected=True, peer=connection.getpeername()[0])
    except OSError as error:
        row.update(connected=False, error_type=type(error).__name__, errno=error.errno)
    result["connections"].append(row)
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
SOC_PROXY_CHECK
