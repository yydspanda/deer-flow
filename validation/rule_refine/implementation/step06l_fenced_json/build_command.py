"""Build the complete zero-model-call copy/paste replay command."""

from pathlib import Path

here = Path(__file__).resolve().parent
source = (here / "replay.py").read_text(encoding="utf-8")
command = """bash <<'SOC_REPLAY_JSON'
set -euo pipefail
umask 077
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_REPLAY'
""" + source + """PY_REPLAY
SOC_REPLAY_JSON
"""
(here / "REPLAY-SAVED-ANSWER.sh").write_text(command, encoding="utf-8")
print("Built REPLAY-SAVED-ANSWER.sh")
