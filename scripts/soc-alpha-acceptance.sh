#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
OUTPUT_DIR="${SOC_ALPHA_ACCEPTANCE_OUTPUT_DIR:-$BACKEND_DIR/.deer-flow/soc-alpha-acceptance}"
PYTHON="${SOC_ALPHA_ACCEPTANCE_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
GENERATOR="$BACKEND_DIR/scripts/soc_alpha_acceptance.py"
KAFKA_IMAGE="${SOC_ALPHA_KAFKA_IMAGE:-docker.redpanda.com/redpandadata/redpanda:v24.3.18}"
KAFKA_PORT="${SOC_ALPHA_KAFKA_PORT:-19092}"
KAFKA_BOOTSTRAP_SERVERS="${SOC_ALPHA_KAFKA_BOOTSTRAP_SERVERS:-localhost:$KAFKA_PORT}"
KAFKA_CONTAINER=""
FRONTEND_PORT="${SOC_ALPHA_FRONTEND_PORT:-3100}"
FRONTEND_BASE_URL="${SOC_ALPHA_FRONTEND_BASE_URL:-http://127.0.0.1:$FRONTEND_PORT}"
FRONTEND_SERVER_PID=""

usage() {
  cat <<'EOF'
Usage: ./scripts/soc-alpha-acceptance.sh COMMAND

Commands:
  all       Reset artifacts and run core, Kafka, frontend, and final report.
  core      Run APT/EDR/HIDS through CLI, SQL, Gateway, feedback, audit, replay.
  kafka     Run APT/EDR/HIDS through a real local Kafka-compatible broker.
  frontend  Run focused SOC API and browser workflow regression tests.
  finalize  Merge existing component artifacts into alpha-acceptance-report.json.

Environment overrides:
  SOC_ALPHA_ACCEPTANCE_OUTPUT_DIR
  SOC_ALPHA_ACCEPTANCE_PYTHON
  SOC_ALPHA_KAFKA_BOOTSTRAP_SERVERS
  SOC_ALPHA_KAFKA_IMAGE
  SOC_ALPHA_KAFKA_PORT
  SOC_ALPHA_FRONTEND_BASE_URL
  SOC_ALPHA_FRONTEND_PORT
EOF
}

require_python() {
  if [[ ! -x "$PYTHON" ]]; then
    printf 'error: backend Python is unavailable: %s\n' "$PYTHON" >&2
    printf 'run the backend install first.\n' >&2
    return 2
  fi
}

prepare() {
  require_python || return $?
  "$PYTHON" "$GENERATOR" --output-dir "$OUTPUT_DIR" prepare
}

run_core() {
  require_python || return $?
  printf '[alpha/core] CLI + SQL + Gateway + feedback + audit + replay\n'
  "$PYTHON" "$GENERATOR" --output-dir "$OUTPUT_DIR" core
}

start_local_broker() {
  if [[ -n "${SOC_ALPHA_KAFKA_BOOTSTRAP_SERVERS:-}" ]]; then
    printf '[alpha/kafka] using configured broker %s\n' "$KAFKA_BOOTSTRAP_SERVERS"
    return 0
  fi
  if ! command -v docker >/dev/null 2>&1; then
    printf 'error: Docker CLI is unavailable. 请启动 Docker Desktop，等待 Engine Ready，并确认 WSL Integration 已开启。\n' >&2
    return 2
  fi
  if ! docker info >/dev/null 2>&1; then
    printf 'error: Docker daemon is unavailable. 请启动 Docker Desktop，等待 Engine Ready，并确认 WSL Integration 已开启。\n' >&2
    return 2
  fi

  KAFKA_CONTAINER="deer-flow-soc-alpha-redpanda-$$"
  printf '[alpha/kafka] starting ephemeral Redpanda %s on %s\n' "$KAFKA_CONTAINER" "$KAFKA_BOOTSTRAP_SERVERS"
  docker run --rm --detach \
    --name "$KAFKA_CONTAINER" \
    --publish "127.0.0.1:$KAFKA_PORT:9092" \
    "$KAFKA_IMAGE" \
    redpanda start \
    --mode dev-container \
    --smp 1 \
    --memory 512M \
    --reserve-memory 0M \
    --node-id 0 \
    --kafka-addr PLAINTEXT://0.0.0.0:9092 \
    --advertise-kafka-addr "PLAINTEXT://localhost:$KAFKA_PORT" >/dev/null
}

stop_local_broker() {
  if [[ -n "$KAFKA_CONTAINER" ]]; then
    docker stop "$KAFKA_CONTAINER" >/dev/null 2>&1 || true
    KAFKA_CONTAINER=""
  fi
}

write_kafka_status() {
  local apt_status="$1"
  local edr_status="$2"
  local hids_status="$3"
  local status="passed"
  if (( apt_status != 0 || edr_status != 0 || hids_status != 0 )); then
    status="failed"
  fi
  printf '{\n  "schema_version": "soc.alpha_kafka_status.v1",\n  "status": "%s",\n  "bootstrap_servers": "%s",\n  "exit_codes": {"apt": %d, "edr": %d, "hids": %d}\n}\n' \
    "$status" "$KAFKA_BOOTSTRAP_SERVERS" "$apt_status" "$edr_status" "$hids_status" \
    >"$OUTPUT_DIR/kafka/status.json"
}

run_kafka_sample() {
  local scenario="$1"
  local sample="$2"
  local include_dead_letter="$3"
  local output="$OUTPUT_DIR/kafka/$scenario.json"
  local topic_suffix=".alpha.$$.$scenario"
  local args=(
    "$PYTHON"
    "$BACKEND_DIR/scripts/soc_kafka_smoke.py"
    --bootstrap-servers "$KAFKA_BOOTSTRAP_SERVERS"
    --database-url "sqlite+pysqlite:///$OUTPUT_DIR/soc_alpha_kafka.db"
    --sample "$sample"
    --topic-suffix "$topic_suffix"
  )
  if [[ "$include_dead_letter" == "true" ]]; then
    args+=(--include-dead-letter)
  fi
  "${args[@]}" >"$output" 2>>"$OUTPUT_DIR/kafka/kafka.log"
}

run_kafka() {
  require_python || return $?
  mkdir -p "$OUTPUT_DIR/kafka"
  : >"$OUTPUT_DIR/kafka/kafka.log"
  start_local_broker || {
    write_kafka_status 2 2 2
    return 2
  }
  trap stop_local_broker EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  printf '[alpha/kafka] APT strict envelope -> process -> commit -> DLQ\n'
  run_kafka_sample "apt" "$BACKEND_DIR/samples/alerts/pingan_legacy_apt.json" true
  local apt_status=$?
  printf '[alpha/kafka] EDR strict envelope -> process -> commit\n'
  run_kafka_sample "edr" "$BACKEND_DIR/samples/alerts/pingan_legacy_edr.json" false
  local edr_status=$?
  printf '[alpha/kafka] HIDS strict envelope -> process -> commit\n'
  run_kafka_sample "hids" "$BACKEND_DIR/samples/alerts/pingan_legacy_hids.json" false
  local hids_status=$?

  write_kafka_status "$apt_status" "$edr_status" "$hids_status"
  stop_local_broker
  trap - EXIT INT TERM
  if (( apt_status != 0 || edr_status != 0 || hids_status != 0 )); then
    return 1
  fi
}

write_frontend_status() {
  local unit_status="$1"
  local browser_status="$2"
  local check_status="$3"
  local status="passed"
  if (( unit_status != 0 || browser_status != 0 || check_status != 0 )); then
    status="failed"
  fi
  printf '{\n  "schema_version": "soc.alpha_frontend_status.v1",\n  "status": "%s",\n  "exit_codes": {"api_unit": %d, "browser": %d, "check": %d},\n  "coverage": ["queue/context render", "close/correct", "approval integrity", "memory review", "disposition outcome/sample", "normalization actions"]\n}\n' \
    "$status" "$unit_status" "$browser_status" "$check_status" \
    >"$OUTPUT_DIR/frontend/status.json"
}

start_frontend_server() {
  if [[ -n "${SOC_ALPHA_FRONTEND_BASE_URL:-}" ]]; then
    printf '[alpha/frontend] using configured frontend %s\n' "$FRONTEND_BASE_URL"
    return 0
  fi

  printf '[alpha/frontend] starting isolated auth-disabled frontend on %s\n' "$FRONTEND_BASE_URL"
  (
    cd "$FRONTEND_DIR" || exit 1
    exec setsid env DEER_FLOW_AUTH_DISABLED=1 SKIP_ENV_VALIDATION=1 \
      pnpm exec next dev --turbo --hostname 127.0.0.1 --port "$FRONTEND_PORT"
  ) >"$OUTPUT_DIR/frontend/server.log" 2>&1 &
  FRONTEND_SERVER_PID=$!

  local attempt
  local http_status
  for attempt in $(seq 1 120); do
    if ! kill -0 "$FRONTEND_SERVER_PID" >/dev/null 2>&1; then
      printf 'error: frontend server exited before becoming ready; see %s\n' \
        "$OUTPUT_DIR/frontend/server.log" >&2
      return 1
    fi
    http_status="$(curl -sS -o /dev/null -w '%{http_code}' \
      "$FRONTEND_BASE_URL/workspace/soc/review" 2>/dev/null || true)"
    if [[ "$http_status" == "200" ]]; then
      return 0
    fi
    sleep 0.5
  done

  printf 'error: frontend server did not become ready; see %s\n' \
    "$OUTPUT_DIR/frontend/server.log" >&2
  return 1
}

stop_frontend_server() {
  if [[ -n "$FRONTEND_SERVER_PID" ]]; then
    kill -TERM -- "-$FRONTEND_SERVER_PID" >/dev/null 2>&1 || true
    local attempt
    for attempt in $(seq 1 20); do
      if ! kill -0 "$FRONTEND_SERVER_PID" >/dev/null 2>&1; then
        break
      fi
      sleep 0.1
    done
    kill -KILL -- "-$FRONTEND_SERVER_PID" >/dev/null 2>&1 || true
    wait "$FRONTEND_SERVER_PID" >/dev/null 2>&1 || true
    FRONTEND_SERVER_PID=""
  fi
}

run_frontend() {
  mkdir -p "$OUTPUT_DIR/frontend"
  printf '[alpha/frontend] full frontend regression including focused SOC API contracts\n'
  (
    cd "$FRONTEND_DIR"
    pnpm test
  ) >"$OUTPUT_DIR/frontend/api-unit.log" 2>&1
  local unit_status=$?

  printf '[alpha/frontend] focused SOC browser workflow regression\n'
  local browser_status=0
  start_frontend_server || {
    browser_status=$?
    stop_frontend_server
  }
  if (( browser_status == 0 )); then
    trap stop_frontend_server EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM
    (
      cd "$FRONTEND_DIR"
      PLAYWRIGHT_SKIP_WEB_SERVER=1 PLAYWRIGHT_BASE_URL="$FRONTEND_BASE_URL" \
        pnpm exec playwright test tests/e2e/soc-review.spec.ts
    ) >"$OUTPUT_DIR/frontend/browser.log" 2>&1
    browser_status=$?
  fi

  # Next.js writes dev type artifacts incrementally; check them before stopping the writer.
  printf '[alpha/frontend] lint + TypeScript contract check\n'
  (
    cd "$FRONTEND_DIR"
    pnpm check
  ) >"$OUTPUT_DIR/frontend/check.log" 2>&1
  local check_status=$?
  stop_frontend_server
  trap - EXIT INT TERM

  write_frontend_status "$unit_status" "$browser_status" "$check_status"
  if (( unit_status != 0 || browser_status != 0 || check_status != 0 )); then
    return 1
  fi
}

finalize() {
  require_python || return $?
  printf '[alpha/report] sealing versioned acceptance report\n'
  "$PYTHON" "$GENERATOR" --output-dir "$OUTPUT_DIR" finalize
}

run_all() {
  local status=0
  prepare || return $?
  run_core || status=1
  run_kafka || status=1
  run_frontend || status=1
  finalize || status=1
  printf '[alpha] report: %s/alpha-acceptance-report.json\n' "$OUTPUT_DIR"
  return "$status"
}

case "${1:-}" in
  all)
    run_all
    ;;
  core)
    run_core
    ;;
  kafka)
    run_kafka
    ;;
  frontend)
    run_frontend
    ;;
  finalize)
    finalize
    ;;
  *)
    usage
    exit 2
    ;;
esac
