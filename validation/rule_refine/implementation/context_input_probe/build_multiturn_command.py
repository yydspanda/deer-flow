"""Bundle the unchanged transport helper and full-history multi-turn probe."""

from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    command = """bash <<'SOC_MULTITURN_CONTEXT'
set -euo pipefail
umask 077
probe_project="$HOME/sec_know_model"
probe_temp="$(mktemp -d "${TMPDIR:-/tmp}/sec-know-multiturn.XXXXXX")"
trap 'rm -f "$probe_temp/probe.py" "$probe_temp/probe_multiturn.py"; rmdir "$probe_temp"' EXIT
"""
    for name, marker in (
        ("probe.py", "SOC_MULTITURN_BASELINE_PYTHON"),
        ("probe_multiturn.py", "SOC_MULTITURN_PROBE_PYTHON"),
    ):
        source = (root / name).read_text(encoding="utf-8")
        assert marker not in source
        command += f"cat > \"$probe_temp/{name}\" <<'{marker}'\n{source}{marker}\n"
    command += """"$probe_project/.venv/bin/python" -B "$probe_temp/probe_multiturn.py" --project-root "$probe_project"
SOC_MULTITURN_CONTEXT
"""
    (root / "RUN-MULTITURN.sh").write_text(command, encoding="utf-8")


if __name__ == "__main__":
    main()
