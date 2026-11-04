"""Build a self-contained, readable Bash command for the internal Mac."""

from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    source = (root / "probe.py").read_text(encoding="utf-8")
    marker = "SOC_CONTEXT_PROBE_PYTHON"
    assert marker not in source
    command = (
        """bash <<'SOC_CONTEXT_INPUT_PROBE'
set -euo pipefail
umask 077
probe_project="$HOME/sec_know_model"
probe_script="$(mktemp "${TMPDIR:-/tmp}/sec-know-context-probe.XXXXXX")"
trap 'rm -f "$probe_script"' EXIT
cat > "$probe_script" <<'SOC_CONTEXT_PROBE_PYTHON'
"""
        + source
        + """SOC_CONTEXT_PROBE_PYTHON
"$probe_project/.venv/bin/python" -B "$probe_script" --project-root "$probe_project"
SOC_CONTEXT_INPUT_PROBE
"""
    )
    (root / "RUN-ONCE.sh").write_text(command, encoding="utf-8")


if __name__ == "__main__":
    main()
