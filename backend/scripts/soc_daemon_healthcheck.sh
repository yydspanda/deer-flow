#!/usr/bin/env sh
# SOC Kafka daemon readiness healthcheck.
#
# Intended Docker healthcheck:
#   sh backend/scripts/soc_daemon_healthcheck.sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BACKEND_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
cd "$BACKEND_DIR"

SOC_DAEMON_PYTHON=${SOC_DAEMON_PYTHON:-./.venv/bin/python}
if [ ! -x "$SOC_DAEMON_PYTHON" ]; then
  SOC_DAEMON_PYTHON=${SOC_DAEMON_PYTHON_FALLBACK:-python}
fi

set -- daemon status

case "$(printf '%s' "${SOC_DAEMON_HEALTHCHECK_DATABASE:-true}" | tr '[:upper:]' '[:lower:]')" in
  0|false|no|off)
    set -- "$@" --skip-database-check
    ;;
esac

case "$(printf '%s' "${SOC_DAEMON_HEALTHCHECK_BROKER:-true}" | tr '[:upper:]' '[:lower:]')" in
  1|true|yes|on)
    set -- "$@" --check-broker
    ;;
esac

exec env PYTHONPATH="${PYTHONPATH:-.}" "$SOC_DAEMON_PYTHON" -m soc_agent.cli "$@"
