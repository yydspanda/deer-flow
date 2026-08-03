#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
PYTHON="$BACKEND_DIR/.venv/bin/python"
VALIDATION_ROOT="$BACKEND_DIR/.deer-flow/soc-runtime-validation"
HISTORY_ROOT="$BACKEND_DIR/.deer-flow/soc-runtime-validation-history"
MODEL_NAME="${SOC_VALIDATION_MODEL:-deepseek-v4-flash}"
CHECKPOINT_D_ALERT_ID="${SOC_CHECKPOINT_D_ALERT_ID:-1965449}"
CHECKPOINT_D_EVIDENCE_MODE="${SOC_VALIDATION_SENSITIVE_EVIDENCE_MODE:-full}"
CHECKPOINT_D_CORPUS="$ROOT_DIR/validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"

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
  checkpoint-d Run corpus inventory, sample D1-D5, and full-corpus D6 routing audit.
  checkpoint-d-live
               Run live D7 structured analyzer-output review using SOC_VALIDATION_MODEL.
  checkpoint-d-grounding
               Run deterministic D8 evidence grounding against the saved D7 artifact.
  checkpoint-d-decision
               Run deterministic D9 Decision Policy review against saved D5/D7/D8 artifacts.
  checkpoint-d-cross-source
               Run D10 representative cross-source live-model Runtime replay using SOC_VALIDATION_MODEL.
  checkpoint-d-full-corpus
               Run D11 full-corpus two-pass deterministic Runtime compatibility and stability review.
  finalize     Rebuild manifests and RUN-INDEX.md from current artifacts.
  snapshot     Copy the current ignored artifact tree to a timestamped local backup.
  all          Run core, live, evaluations, and finalize in order.

The artifact tree contains real-alert-derived local data and is gitignored.
The live, checkpoint-d-live, and checkpoint-d-cross-source commands call the configured model and may incur model cost.
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
    if [[ ! -f "$ROOT_DIR/datas/legacy_demos/$sample.json" ]]; then
      printf 'error: validation sample missing: datas/legacy_demos/%s.json\n' "$sample" >&2
      exit 2
    fi
  done
}

require_checkpoint_d_corpus() {
  if [[ ! -f "$CHECKPOINT_D_CORPUS" ]]; then
    printf 'error: Checkpoint D corpus missing: %s\n' "$CHECKPOINT_D_CORPUS" >&2
    printf 'build it with validation/compact_zeus/corpus/build_alert_validation_corpus.py first.\n' >&2
    exit 2
  fi
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
  printf '[core] regenerating Steps 01-05 from datas/legacy_demos/*.json\n'
  run_backend_module scripts.generate_soc_normalization_maintenance_validation
}

run_checkpoint_d() {
  local checkpoint_dir="$ROOT_DIR/validation/compact_zeus/checkpoint_d"
  printf '[checkpoint-d] D0 corpus inventory\n'
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_corpus_inventory.py"
  printf '[checkpoint-d] D1-D5 sample alert: %s\n' "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_normalization_review.py" --alert-id "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_entity_extraction_review.py" --alert-id "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_fact_reconstruction_review.py" --alert-id "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_bounded_analysis_input_review.py" --alert-id "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_skill_context_review.py" --alert-id "$CHECKPOINT_D_ALERT_ID"
  printf '[checkpoint-d] D6 full-corpus skill routing coverage\n'
  "$PYTHON" "$checkpoint_dir/build_checkpoint_d_skill_route_coverage.py"
}

run_checkpoint_d_live() {
  local checkpoint_dir="$ROOT_DIR/validation/compact_zeus/checkpoint_d"
  local d5_artifact="$VALIDATION_ROOT/checkpoint-d/step-d5-skill-context/$CHECKPOINT_D_ALERT_ID.skill-context.json"
  if [[ ! -f "$d5_artifact" ]]; then
    printf 'error: Checkpoint D5 artifact missing: %s\n' "$d5_artifact" >&2
    printf 'run ./scripts/soc-runtime-validation.sh checkpoint-d first.\n' >&2
    exit 2
  fi
  printf '[checkpoint-d-live] D7 analyzer output: alert=%s model=%s\n' \
    "$CHECKPOINT_D_ALERT_ID" "$MODEL_NAME"
  "$PYTHON" \
    "$checkpoint_dir/build_checkpoint_d_analyzer_output_review.py" \
    --alert-id "$CHECKPOINT_D_ALERT_ID" \
    --model-name "$MODEL_NAME"
}

run_checkpoint_d_grounding() {
  local checkpoint_dir="$ROOT_DIR/validation/compact_zeus/checkpoint_d"
  local d5_artifact="$VALIDATION_ROOT/checkpoint-d/step-d5-skill-context/$CHECKPOINT_D_ALERT_ID.skill-context.json"
  local d7_artifact="$VALIDATION_ROOT/checkpoint-d/step-d7-analyzer-output/$CHECKPOINT_D_ALERT_ID.analyzer-output.json"
  if [[ ! -f "$d5_artifact" ]]; then
    printf 'error: Checkpoint D5 artifact missing: %s\n' "$d5_artifact" >&2
    printf 'run ./scripts/soc-runtime-validation.sh checkpoint-d first.\n' >&2
    exit 2
  fi
  if [[ ! -f "$d7_artifact" ]]; then
    printf 'error: Checkpoint D7 artifact missing: %s\n' "$d7_artifact" >&2
    printf 'run ./scripts/soc-runtime-validation.sh checkpoint-d-live first.\n' >&2
    exit 2
  fi
  printf '[checkpoint-d-grounding] D8 evidence grounding: alert=%s\n' \
    "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" \
    "$checkpoint_dir/build_checkpoint_d_evidence_grounding_review.py" \
    --alert-id "$CHECKPOINT_D_ALERT_ID"
}

run_checkpoint_d_decision() {
  local checkpoint_dir="$ROOT_DIR/validation/compact_zeus/checkpoint_d"
  local d5_artifact="$VALIDATION_ROOT/checkpoint-d/step-d5-skill-context/$CHECKPOINT_D_ALERT_ID.skill-context.json"
  local d7_artifact="$VALIDATION_ROOT/checkpoint-d/step-d7-analyzer-output/$CHECKPOINT_D_ALERT_ID.analyzer-output.json"
  local d8_artifact="$VALIDATION_ROOT/checkpoint-d/step-d8-evidence-grounding/$CHECKPOINT_D_ALERT_ID.grounding.json"
  local artifact
  for artifact in "$d5_artifact" "$d7_artifact" "$d8_artifact"; do
    if [[ ! -f "$artifact" ]]; then
      printf 'error: Checkpoint D prerequisite missing: %s\n' "$artifact" >&2
      printf 'run checkpoint-d, checkpoint-d-live, and checkpoint-d-grounding first.\n' >&2
      exit 2
    fi
  done
  printf '[checkpoint-d-decision] D9 Decision Policy: alert=%s\n' \
    "$CHECKPOINT_D_ALERT_ID"
  "$PYTHON" \
    "$checkpoint_dir/build_checkpoint_d_decision_policy_review.py" \
    --alert-id "$CHECKPOINT_D_ALERT_ID"
}

run_checkpoint_d_cross_source() {
  local checkpoint_dir="$ROOT_DIR/validation/compact_zeus/checkpoint_d"
  local d0_artifact="$VALIDATION_ROOT/checkpoint-d/step-d0-corpus-inventory/corpus-inventory.json"
  require_checkpoint_d_corpus
  if [[ ! -f "$d0_artifact" ]]; then
    printf 'error: Checkpoint D0 artifact missing: %s\n' "$d0_artifact" >&2
    printf 'run ./scripts/soc-runtime-validation.sh checkpoint-d first.\n' >&2
    exit 2
  fi
  printf '[checkpoint-d-cross-source] D10 live-model representative Runtime replay: model=%s\n' \
    "$MODEL_NAME"
  "$PYTHON" \
    "$checkpoint_dir/build_checkpoint_d_cross_source_runtime_review.py" \
    --model-name "$MODEL_NAME"
}

run_checkpoint_d_full_corpus() {
  local checkpoint_dir="$ROOT_DIR/validation/compact_zeus/checkpoint_d"
  local d0_artifact="$VALIDATION_ROOT/checkpoint-d/step-d0-corpus-inventory/corpus-inventory.json"
  require_checkpoint_d_corpus
  if [[ ! -f "$d0_artifact" ]]; then
    printf 'error: Checkpoint D0 artifact missing: %s\n' "$d0_artifact" >&2
    printf 'run ./scripts/soc-runtime-validation.sh checkpoint-d first.\n' >&2
    exit 2
  fi
  printf '[checkpoint-d-full-corpus] D11 two-pass deterministic Runtime replay: evidence_mode=%s\n' \
    "$CHECKPOINT_D_EVIDENCE_MODE"
  "$PYTHON" \
    "$checkpoint_dir/build_checkpoint_d_full_corpus_runtime_review.py" \
    --sensitive-evidence-mode "$CHECKPOINT_D_EVIDENCE_MODE"
}

run_live() {
  printf '[live] model preflight: %s\n' "$MODEL_NAME"
  run_soc_json \
    "$VALIDATION_ROOT/step-06-live-llm/llm-status.json" \
    llm status --analyzer-mode llm --model-name "$MODEL_NAME" --pretty

  printf '[live] Step 06: bounded LLM analysis for apt-1965449\n'
  run_soc_json \
    "$VALIDATION_ROOT/step-06-live-llm/apt-1965449.step-06.json" \
    analyze "$ROOT_DIR/datas/legacy_demos/apt-1965449.json" \
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
      analyze "$ROOT_DIR/datas/legacy_demos/$sample.json" \
      --analyzer-mode llm --model-name "$MODEL_NAME" --pretty
  done

  printf '[live] Step 07: offline normalization mapping suggestions\n'
  run_soc_json \
    "$VALIDATION_ROOT/step-07-live-normalization-suggestion/apt-1965449.step-07.json" \
    normalize suggest "$ROOT_DIR/datas/legacy_demos/apt-1965449.json" \
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
      analyze "$ROOT_DIR/datas/legacy_demos/$sample.json" --analyzer-mode stub --pretty
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
    checkpoint-d)
      require_checkpoint_d_corpus
      run_checkpoint_d
      ;;
    checkpoint-d-live)
      run_checkpoint_d_live
      ;;
    checkpoint-d-grounding)
      run_checkpoint_d_grounding
      ;;
    checkpoint-d-decision)
      run_checkpoint_d_decision
      ;;
    checkpoint-d-cross-source)
      run_checkpoint_d_cross_source
      ;;
    checkpoint-d-full-corpus)
      run_checkpoint_d_full_corpus
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
