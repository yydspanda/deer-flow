#!/usr/bin/env sh
# Production-oriented SOC Kafka daemon entrypoint.
#
# Intended Docker command:
#   sh backend/scripts/soc_daemon_entrypoint.sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$BACKEND_DIR"

SOC_DAEMON_PYTHON=${SOC_DAEMON_PYTHON:-./.venv/bin/python}
if [ ! -x "$SOC_DAEMON_PYTHON" ]; then
  SOC_DAEMON_PYTHON=${SOC_DAEMON_PYTHON_FALLBACK:-python}
fi

SOC_KAFKA_ENABLED=${SOC_KAFKA_ENABLED:-true}
export SOC_KAFKA_ENABLED

case "$(printf '%s' "$SOC_KAFKA_ENABLED" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    ;;
  *)
    case "$(printf '%s' "${SOC_DAEMON_ALLOW_DISABLED:-false}" | tr '[:upper:]' '[:lower:]')" in
      1|true|yes|on)
        ;;
      *)
        echo "error: SOC_KAFKA_ENABLED must be true for production daemon; set SOC_DAEMON_ALLOW_DISABLED=true only for tests/local validation" >&2
        exit 2
        ;;
    esac
    ;;
esac

run_soc() {
  PYTHONPATH="${PYTHONPATH:-.}" "$SOC_DAEMON_PYTHON" -m soc_agent.cli "$@"
}

case "$(printf '%s' "${SOC_DAEMON_UPGRADE_DB:-false}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    run_soc db upgrade
    ;;
esac

case "$(printf '%s' "${SOC_DAEMON_PRESTART_STATUS_CHECK:-false}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    sh "$SCRIPT_DIR/soc_daemon_healthcheck.sh"
    ;;
esac

set -- daemon run \
  --idle-sleep-ms "${SOC_DAEMON_IDLE_SLEEP_MS:-1000}" \
  --error-backoff-ms "${SOC_DAEMON_ERROR_BACKOFF_MS:-1000}" \
  --max-consecutive-errors "${SOC_DAEMON_MAX_CONSECUTIVE_ERRORS:-3}"

if [ -n "${SOC_DAEMON_MAX_LOOPS:-}" ]; then
  set -- "$@" --max-loops "$SOC_DAEMON_MAX_LOOPS"
fi

if [ -n "${SOC_DAEMON_METRIC_JSONL:-}" ]; then
  set -- "$@" --metric-jsonl "$SOC_DAEMON_METRIC_JSONL"
fi

case "$(printf '%s' "${SOC_DAEMON_INCLUDE_RESULTS:-false}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    set -- "$@" --include-results
    ;;
esac

exec env PYTHONPATH="${PYTHONPATH:-.}" "$SOC_DAEMON_PYTHON" -m soc_agent.cli "$@"
