bash <<'SOC_SURGE_DEV_HISTORY'
set -euo pipefail
umask 077
cd "$HOME/sec_know_model"
env env_profile=LOCAL .venv/bin/python -B test/rule_refine/check_surge_dev_history.py
SOC_SURGE_DEV_HISTORY
