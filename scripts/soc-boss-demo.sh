#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
DOCKER_DIR="$PROJECT_ROOT/docker"
MANIFEST_PATH="$BACKEND_DIR/.deer-flow/data/soc_boss_demo_manifest.json"
export DEER_FLOW_ROOT="${DEER_FLOW_ROOT:-$PROJECT_ROOT}"
COMPOSE=(
    docker compose
    -p deer-flow-dev
    -f "$DOCKER_DIR/docker-compose-dev.yaml"
    -f "$DOCKER_DIR/docker-compose.soc-boss-demo.yaml"
)

require_docker() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "Docker CLI is unavailable. 请先启动 Docker Desktop，等待 Engine Ready，并确认 WSL Integration 已开启。" >&2
        return 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo "Docker daemon is unavailable. 请先启动 Docker Desktop，等待 Engine Ready，并确认 WSL Integration 已开启。" >&2
        return 1
    fi
}

ensure_extensions_config() {
    local config_path="$PROJECT_ROOT/extensions_config.json"
    local example_path="$PROJECT_ROOT/extensions_config.example.json"

    if [ -f "$config_path" ]; then
        return
    fi
    if [ -f "$example_path" ]; then
        cp "$example_path" "$config_path"
        echo "Created extensions_config.json from extensions_config.example.json"
        return
    fi

    printf '%s\n' '{"mcpServers":{},"skills":{}}' >"$config_path"
    echo "Created minimal extensions_config.json"
}

ensure_soc_profile() {
    if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
        (
            cd "$BACKEND_DIR"
            .venv/bin/python -m soc_agent.cli agent install-profile --pretty
        )
    else
        (
            cd "$BACKEND_DIR"
            uv run python -m soc_agent.cli agent install-profile --pretty
        )
    fi
}

usage() {
    cat <<'EOF'
Usage: ./scripts/soc-boss-demo.sh COMMAND [SOC_DEMO_ARGS]

Commands:
  prepare   Seed the isolated Boss Demo SQLite database and save the manifest.
  start     Prepare data, then start Redis, Frontend, Gateway, and Nginx.
  status    Show container status and probe the public application endpoint.
  logs      Follow Gateway, Frontend, and Nginx logs.
  stop      Stop the Boss Demo Docker development stack.

Examples:
  ./scripts/soc-boss-demo.sh prepare --reset
  ./scripts/soc-boss-demo.sh start --reset
  ./scripts/soc-boss-demo.sh start --reset --analyzer-mode llm --model-name deepseek-v4-flash
EOF
}

run_soc_demo() {
    mkdir -p "$(dirname "$MANIFEST_PATH")"
    if [ -x "$BACKEND_DIR/.venv/bin/python" ]; then
        (
            cd "$BACKEND_DIR"
            .venv/bin/python -m soc_agent.cli demo boss \
                --web-base-url http://localhost:2026 \
                --pretty \
                "$@"
        )
    else
        (
            cd "$BACKEND_DIR"
            uv run python -m soc_agent.cli demo boss \
                --web-base-url http://localhost:2026 \
                --pretty \
                "$@"
        )
    fi
}

prepare() {
    local temp_manifest="${MANIFEST_PATH}.tmp"
    run_soc_demo "$@" | tee "$temp_manifest"
    mv "$temp_manifest" "$MANIFEST_PATH"
    echo
    echo "Boss Demo manifest: $MANIFEST_PATH"
}

start() {
    require_docker
    ensure_extensions_config
    ensure_soc_profile
    prepare "$@"
    "${COMPOSE[@]}" up --build -d --remove-orphans redis frontend gateway nginx
    echo
    echo "Boss Demo is starting: http://localhost:2026/workspace/soc/review"
    echo "Run './scripts/soc-boss-demo.sh status' to check readiness."
}

status() {
    require_docker
    "${COMPOSE[@]}" ps
    echo
    if curl -fsS --max-time 15 http://localhost:2026/workspace/soc/review >/dev/null; then
        echo "READY: http://localhost:2026/workspace/soc/review"
    else
        echo "NOT READY: inspect './scripts/soc-boss-demo.sh logs'"
        return 1
    fi
}

logs() {
    require_docker
    "${COMPOSE[@]}" logs -f --tail=200 gateway frontend nginx
}

stop() {
    require_docker
    "${COMPOSE[@]}" down
}

command="${1:-help}"
if [ "$#" -gt 0 ]; then
    shift
fi

case "$command" in
    prepare) prepare "$@" ;;
    start) start "$@" ;;
    status) status ;;
    logs) logs ;;
    stop) stop ;;
    help | -h | --help) usage ;;
    *)
        echo "Unknown command: $command" >&2
        usage >&2
        exit 2
        ;;
esac
