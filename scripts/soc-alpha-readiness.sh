#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
OUTPUT_DIR="${SOC_ALPHA_READINESS_OUTPUT_DIR:-$BACKEND_DIR/.deer-flow/soc-alpha-readiness}"
PYTHON="${SOC_ALPHA_READINESS_PYTHON:-$BACKEND_DIR/.venv/bin/python}"
GENERATOR="$BACKEND_DIR/scripts/soc_alpha_readiness.py"
ACCEPTANCE_SCRIPT="$PROJECT_ROOT/scripts/soc-alpha-acceptance.sh"
ACCEPTANCE_OUTPUT_DIR="$OUTPUT_DIR/soc-alpha-acceptance"

usage() {
  cat <<'EOF'
Usage: ./scripts/soc-alpha-readiness.sh COMMAND

Commands:
  all           Reset evidence, run acceptance and all release gates, then finalize.
  acceptance    Run the existing APT/EDR/HIDS Alpha acceptance package.
  backend       Run the complete SOC backend pytest suite.
  architecture  Run SOC architecture and migration-environment gates.
  finalize      Seal alpha-readiness-report.json from existing artifacts.

Environment overrides:
  SOC_ALPHA_READINESS_OUTPUT_DIR
  SOC_ALPHA_READINESS_PYTHON

Kafka/frontend overrides accepted by soc-alpha-acceptance.sh are inherited.
EOF
}

require_python() {
  if [[ ! -x "$PYTHON" ]]; then
    printf 'error: backend Python is unavailable: %s\n' "$PYTHON" >&2
    return 2
  fi
}

prepare() {
  require_python || return $?
  "$PYTHON" "$GENERATOR" --output-dir "$OUTPUT_DIR" prepare
}

run_acceptance() {
  mkdir -p "$OUTPUT_DIR"
  printf '[readiness/acceptance] APT + EDR + HIDS release acceptance\n'
  SOC_ALPHA_ACCEPTANCE_OUTPUT_DIR="$ACCEPTANCE_OUTPUT_DIR" \
    "$ACCEPTANCE_SCRIPT" all >"$OUTPUT_DIR/acceptance.log" 2>&1
}

record_gate() {
  local gate="$1"
  local exit_code="$2"
  local test_command="$3"
  local log_file="$4"
  "$PYTHON" "$GENERATOR" \
    --output-dir "$OUTPUT_DIR" \
    record-gate \
    --gate "$gate" \
    --exit-code "$exit_code" \
    --test-command "$test_command" \
    --log-file "$log_file" >/dev/null
}

run_backend() {
  require_python || return $?
  mkdir -p "$OUTPUT_DIR/tests"
  local log_file="$OUTPUT_DIR/tests/backend-soc.log"
  local test_command="cd backend && ./.venv/bin/python -m pytest -q tests/test_soc_*.py"
  printf '[readiness/backend] full SOC backend regression\n'
  (
    cd "$BACKEND_DIR" || exit 1
    "$PYTHON" -m pytest -q tests/test_soc_*.py
  ) >"$log_file" 2>&1
  local status=$?
  record_gate "backend-soc" "$status" "$test_command" "$log_file" || true
  return "$status"
}

run_architecture() {
  require_python || return $?
  mkdir -p "$OUTPUT_DIR/tests"
  local log_file="$OUTPUT_DIR/tests/architecture-migrations.log"
  local test_command="cd backend && ./.venv/bin/python -m pytest -q tests/architecture/test_soc_agent_boundaries.py tests/test_persistence_migrations_env.py"
  printf '[readiness/architecture] SOC import boundaries + migration environment\n'
  (
    cd "$BACKEND_DIR" || exit 1
    "$PYTHON" -m pytest -q \
      tests/architecture/test_soc_agent_boundaries.py \
      tests/test_persistence_migrations_env.py
  ) >"$log_file" 2>&1
  local status=$?
  record_gate "architecture-migrations" "$status" "$test_command" "$log_file" || true
  return "$status"
}

finalize() {
  require_python || return $?
  printf '[readiness/report] sealing owner-review candidate\n'
  "$PYTHON" "$GENERATOR" --output-dir "$OUTPUT_DIR" finalize
}

run_all() {
  local status=0
  prepare || return $?
  run_acceptance || status=1
  run_backend || status=1
  run_architecture || status=1
  finalize || status=1
  printf '[readiness] report: %s/alpha-readiness-report.json\n' "$OUTPUT_DIR"
  return "$status"
}

case "${1:-}" in
  all)
    run_all
    ;;
  acceptance)
    run_acceptance
    ;;
  backend)
    run_backend
    ;;
  architecture)
    run_architecture
    ;;
  finalize)
    finalize
    ;;
  *)
    usage
    exit 2
    ;;
esac
