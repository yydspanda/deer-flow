"""Bundle the unchanged baseline and explicit-parameter variant for copy/paste."""

from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    command = """bash <<'SOC_EXPLICIT_MAX_INPUT'
set -euo pipefail
umask 077
probe_project="$HOME/sec_know_model"
probe_temp="$(mktemp -d "${TMPDIR:-/tmp}/sec-know-max-input.XXXXXX")"
trap 'rm -f "$probe_temp/probe.py" "$probe_temp/probe_with_max_input.py"; rmdir "$probe_temp"' EXIT
"""
    for name, marker in (
        ("probe.py", "SOC_BASELINE_PYTHON"),
        ("probe_with_max_input.py", "SOC_MAX_INPUT_PYTHON"),
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert marker not in source
        command += f"cat > \"$probe_temp/{name}\" <<'{marker}'\n{source}{marker}\n"
    command += """"$probe_project/.venv/bin/python" -B "$probe_temp/probe_with_max_input.py" --project-root "$probe_project"
SOC_EXPLICIT_MAX_INPUT
"""
    (root / "RUN-WITH-MAX-INPUT.sh").write_text(command, encoding="utf-8")


if __name__ == "__main__":
    main()
