#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
VALIDATION_ROOT="$BACKEND_DIR/.deer-flow/soc-runtime-validation"
HISTORY_ROOT="$BACKEND_DIR/.deer-flow/soc-runtime-validation-history"
MODEL_NAME="${SOC_VALIDATION_MODEL:-deepseek-v4-pro}"

SAMPLES=(
  "apt-1965449"
  "apt-2025642"
  "apt-2026494"
  "edr-1965810"
  "hids-1965448"
)

usage() {
  cat <<'EOF'
Usage: ./scripts/soc-runtime-validation.sh COMMAND

Commands:
  core         Regenerate deterministic Steps 01-05.
  live         Run live LLM Steps 06-09 using SOC_VALIDATION_MODEL.
  evaluations  Run deterministic replay, correlation, and governance Steps 10-12.
  finalize     Rebuild manifests and RUN-INDEX.md from current artifacts.
  snapshot     Copy the current ignored artifact tree to a timestamped local backup.
  all          Run core, live, evaluations, and finalize in order.

The artifact tree contains real-alert-derived local data and is gitignored.
The live command calls the configured model and may incur model cost.
EOF
}

require_python() {
  if [[ ! -x "$PYTHON" ]]; then
    printf 'error: backend virtualenv missing: %s\n' "$PYTHON" >&2
    printf 'run the repository backend install first.\n' >&2
    exit 2
  fi
}

require_samples() {
  local sample
  for sample in "${SAMPLES[@]}"; do
    if [[ ! -f "$ROOT_DIR/datas/$sample.json" ]]; then
      printf 'error: validation sample missing: datas/%s.json\n' "$sample" >&2
      exit 2
    fi
  done
}

run_backend_module() {
  (
    cd "$BACKEND_DIR"
    PYTHONPATH=. "$PYTHON" -m "$@"
  )
}

run_soc_json() {
  local output="$1"
  shift
  local temporary="${output}.tmp"
  mkdir -p "$(dirname "$output")"
  if run_backend_module soc_agent.cli "$@" >"$temporary"; then
    mv "$temporary" "$output"
  else
    local status=$?
    mv "$temporary" "${output}.failed"
    printf 'error: command failed (%s); partial output: %s.failed\n' "$status" "$output" >&2
    return "$status"
  fi
}

run_soc_json_allow_pending() {
  local output="$1"
  shift
  local temporary="${output}.tmp"
  mkdir -p "$(dirname "$output")"
  local status=0
  if run_backend_module soc_agent.cli "$@" >"$temporary"; then
    status=0
  else
    status=$?
  fi
  mv "$temporary" "$output"
  if [[ "$status" -ne 0 && "$status" -ne 1 ]]; then
    printf 'error: command failed (%s): %s\n' "$status" "$output" >&2
    return "$status"
  fi
  return 0
}

run_core() {
  printf '[core] regenerating Steps 01-05 from datas/*.json\n'
  run_backend_module scripts.generate_soc_normalization_maintenance_validation
}

run_live() {
  printf '[live] model preflight: %s\n' "$MODEL_NAME"
  run_soc_json \
    "$VALIDATION_ROOT/step-06-live-llm/llm-status.json" \
    llm status --analyzer-mode llm --model-name "$MODEL_NAME" --pretty

  printf '[live] Step 06: bounded LLM analysis for apt-1965449\n'
  run_soc_json \
    "$VALIDATION_ROOT/step-06-live-llm/apt-1965449.step-06.json" \
    analyze "$ROOT_DIR/datas/apt-1965449.json" \
    --analyzer-mode llm --model-name "$MODEL_NAME" --pretty

  local runs_dir="$VALIDATION_ROOT/step-09-confidence-labeling/runs"
  mkdir -p "$runs_dir"
  cp \
    "$VALIDATION_ROOT/step-06-live-llm/apt-1965449.step-06.json" \
    "$runs_dir/apt-1965449.live.json"

  local sample
  for sample in "${SAMPLES[@]}"; do
    if [[ "$sample" == "apt-1965449" ]]; then
      continue
    fi
    printf '[live] Step 09 source run: %s\n' "$sample"
    run_soc_json \
      "$runs_dir/$sample.live.json" \
      analyze "$ROOT_DIR/datas/$sample.json" \
      --analyzer-mode llm --model-name "$MODEL_NAME" --pretty
  done

  printf '[live] Step 07: offline normalization mapping suggestions\n'
  run_soc_json \
    "$VALIDATION_ROOT/step-07-live-normalization-suggestion/apt-1965449.step-07.json" \
    normalize suggest "$ROOT_DIR/datas/apt-1965449.json" \
    --live-llm --model-name "$MODEL_NAME" --pretty

  printf '[live] Step 09: prepare a new pending label set without overwriting analyst truth\n'
  run_soc_json \
    "$VALIDATION_ROOT/step-09-confidence-labeling/label-set.rerun.pending.json" \
    eval labels prepare "$runs_dir" --glob '*.live.json' --pretty
  run_soc_json_allow_pending \
    "$VALIDATION_ROOT/step-09-confidence-labeling/validation.rerun.pending.json" \
    eval labels validate \
    "$VALIDATION_ROOT/step-09-confidence-labeling/label-set.rerun.pending.json" \
    --pretty

  run_finalize
}

run_evaluations() {
  local replay_dir="$VALIDATION_ROOT/step-10-five-sample-repair"
  local sample
  printf '[evaluations] Step 10: five-sample deterministic replay\n'
  for sample in "${SAMPLES[@]}"; do
    run_soc_json \
      "$replay_dir/$sample.deterministic.json" \
      analyze "$ROOT_DIR/datas/$sample.json" --analyzer-mode stub --pretty
  done

  printf '[evaluations] Step 10: main-orchestrator correlation bridge\n'
  run_soc_json \
    "$VALIDATION_ROOT/step-10-correlation-bridge/pingan-main.json" \
    eval pingan-main --pretty

  printf '[evaluations] Step 11: correlation baseline and deterministic replay diff\n'
  local baseline="$VALIDATION_ROOT/step-11-correlation-eval/correlation-baseline.json"
  run_soc_json "$baseline" eval correlation --pretty
  run_soc_json \
    "$VALIDATION_ROOT/step-11-correlation-eval/correlation-replay-diff.json" \
    eval correlation --baseline-json "$baseline" --pretty

  printf '[evaluations] Steps 11-12: governed-context lifecycle and authorization shadow\n'
  run_backend_module scripts.generate_soc_context_validation
  run_finalize
}

run_finalize() {
  printf '[finalize] rebuilding manifests and RUN-INDEX.md\n'
  run_backend_module scripts.generate_soc_runtime_validation_report
}

run_snapshot() {
  local timestamp
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$HISTORY_ROOT"
  if [[ ! -d "$VALIDATION_ROOT" ]]; then
    printf 'error: no validation tree to snapshot: %s\n' "$VALIDATION_ROOT" >&2
    exit 2
  fi
  cp -a "$VALIDATION_ROOT" "$HISTORY_ROOT/pre-rerun-$timestamp"
  printf 'snapshot: %s\n' "$HISTORY_ROOT/pre-rerun-$timestamp"
}

main() {
  require_python
  case "${1:-}" in
    core)
      require_samples
      run_core
      run_finalize
      ;;
    live)
      require_samples
      run_live
      ;;
    evaluations)
      require_samples
      run_evaluations
      ;;
    finalize)
      run_finalize
      ;;
    snapshot)
      run_snapshot
      ;;
    all)
      require_samples
      run_core
      run_live
      run_evaluations
      run_finalize
      ;;
    *)
      usage
      exit 2
      ;;
  esac
}

main "$@"
