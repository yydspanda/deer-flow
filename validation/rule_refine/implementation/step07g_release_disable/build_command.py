"""Build the self-contained command without embedding private project source."""

from pathlib import Path


def command(source):
    marker = "SOC_RELEASE_DISABLE_PYTHON"
    assert marker not in source
    return (
        "bash <<'SOC_RULE_RELEASE_DISABLE'\n"
        "set -euo pipefail\n"
        "umask 077\n"
        'release_project="$HOME/sec_know_model"\n'
        'release_python="$release_project/.venv/bin/python"\n'
        'if [ ! -x "$release_python" ]; then release_python=python3; fi\n'
        "echo '关闭生产启动脚本中的规则监测；备份原文件，不启动或重启服务。'\n"
        f'"$release_python" -B - --project-root "$release_project" <<\'{marker}\'\n'
        + source
        + f"{marker}\nSOC_RULE_RELEASE_DISABLE\n"
    )


def main():
    root = Path(__file__).resolve().parent
    source = (root / "patch.py").read_text(encoding="utf-8")
    (root / "DISABLE-FOR-RELEASE.sh").write_text(command(source), encoding="utf-8")


if __name__ == "__main__":
    main()
