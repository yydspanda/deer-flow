#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
DOCKER_DIR="$PROJECT_ROOT/docker"
RUNTIME_DIR="$BACKEND_DIR/.deer-flow/soc-validation/memory-dev-web"
DATABASE_PATH="$RUNTIME_DIR/soc-memory-dev.sqlite"
CORPUS_PATH="$PROJECT_ROOT/validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
DATABASE_URL="sqlite:///$DATABASE_PATH"
WORKBENCH_URL="http://localhost:2026/workspace/soc/memory-validation"
export DEER_FLOW_ROOT="${DEER_FLOW_ROOT:-$PROJECT_ROOT}"
COMPOSE=(
    docker compose
    -p deer-flow-dev
    -f "$DOCKER_DIR/docker-compose-dev.yaml"
    -f "$DOCKER_DIR/docker-compose.soc-memory-dev.yaml"
)

export SOC_DATABASE_URL="$DATABASE_URL"
export SOC_DEV_MEMORY_CORPUS_PATH="$CORPUS_PATH"
export SOC_DEV_MEMORY_WORKBENCH_ENABLED=true
export SOC_ANALYZER_MODE=llm
export SOC_MEMORY_ENVIRONMENT=dev
export SOC_AUTOMATION_ENVIRONMENT=dev
export SOC_TENANT_POLICY_ENABLED=false
export SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS=false

require_source() {
    if [ ! -f "$CORPUS_PATH" ]; then
        echo "Missing local SOC validation corpus: $CORPUS_PATH" >&2
        return 1
    fi
}

require_docker() {
    if ! command -v docker >/dev/null 2>&1 || ! docker info >/dev/null 2>&1; then
        echo "Docker is unavailable. Start Docker Desktop and wait for the Engine to become ready." >&2
        return 1
    fi
}

upgrade_database() {
    mkdir -p "$RUNTIME_DIR"
    (
        cd "$BACKEND_DIR"
        .venv/bin/python -m soc_agent.cli db upgrade \
            --database-url "$DATABASE_URL"
    )
}

start() {
    require_docker
    require_source
    upgrade_database
    "${COMPOSE[@]}" up --no-build -d --remove-orphans redis frontend gateway nginx
    echo
    echo "SOC Memory DEV workbench: $WORKBENCH_URL"
    echo "Database: $DATABASE_PATH"
}

rebuild() {
    require_docker
    require_source
    upgrade_database
    "${COMPOSE[@]}" up --build -d --remove-orphans redis frontend gateway nginx
    echo
    echo "SOC Memory DEV workbench: $WORKBENCH_URL"
    echo "Database: $DATABASE_PATH"
}

status() {
    require_docker
    "${COMPOSE[@]}" ps
    echo
    echo "Database: $DATABASE_PATH"
    if curl -fsS --max-time 15 "$WORKBENCH_URL" >/dev/null; then
        echo "READY: $WORKBENCH_URL"
    else
        echo "NOT READY: inspect logs/gateway.log and logs/frontend.log" >&2
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

reset() {
    stop
    rm -f "$DATABASE_PATH"
    echo "Reset SOC Memory DEV database: $DATABASE_PATH"
}

usage() {
    cat <<EOF
Usage: ./scripts/soc-memory-dev.sh COMMAND

Commands:
  start   Migrate SQLite and start the persistent Docker DEV stack.
  rebuild Rebuild Docker images, then start the persistent DEV stack.
  status  Probe the browser workbench.
  logs    Follow Gateway and Frontend logs.
  stop    Stop this checkout's DEV services.
  reset   Stop services and delete only the isolated workbench database.

Open: $WORKBENCH_URL
EOF
}

case "${1:-help}" in
    start) start ;;
    rebuild) rebuild ;;
    status) status ;;
    logs) logs ;;
    stop) stop ;;
    reset) reset ;;
    help | -h | --help) usage ;;
    *)
        echo "Unknown command: $1" >&2
        usage >&2
        exit 2
        ;;
esac
