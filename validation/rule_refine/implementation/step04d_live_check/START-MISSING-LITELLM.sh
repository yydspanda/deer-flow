bash <<'SOC_START_LITELLM'
set -euo pipefail
cd "$HOME/sec_know_model"

if lsof -nP -iTCP:4001 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "4001 已有监听进程，本次不重复启动。"
    lsof -nP -iTCP:4001 -sTCP:LISTEN
    exit 0
fi
if [ ! -x .venv/bin/litellm ]; then
    echo "未找到 .venv/bin/litellm，请贴回此提示。"
    exit 1
fi
if [ ! -f llm_service/litellm_proxy_config.local.yaml ]; then
    echo "未找到 LOCAL LiteLLM 配置，请贴回此提示。"
    exit 1
fi

echo "启动 4001 LiteLLM；请保持本终端打开。"
exec .venv/bin/litellm \
    --config "$PWD/llm_service/litellm_proxy_config.local.yaml" \
    --host 127.0.0.1 \
    --port 4001
SOC_START_LITELLM
