"""Build a copyable, read-only saved-answer diagnostic for the internal Mac."""

from pathlib import Path

HERE = Path(__file__).resolve().parent
PREFIX = """bash <<'SOC_DIAGNOSE_SAVED_REPLY'
# 复制整个文件到内网 Mac 终端执行；不需要传入新的脚本文件。
set -euo pipefail
cd "$HOME/sec_know_model"
read -r -p '报告目录（回车使用 historical-rule-batches-y00ewc9u）：' soc_report_dir </dev/tty
soc_report_dir="${soc_report_dir:-$HOME/sec-rule-refine-data/historical-rule-batches-y00ewc9u}"
printf '%s\\n' '离线检查保存的回答格式；不调用模型、不修改应用或旧报告。'
.venv/bin/python -B - "$soc_report_dir" <<'PY_SAVED_REPLY_DIAGNOSTIC'
"""
SUFFIX = "PY_SAVED_REPLY_DIAGNOSTIC\nSOC_DIAGNOSE_SAVED_REPLY\n"


def build():
    source = (HERE / "diagnose_saved_reply.py").read_text(encoding="utf-8")
    compile(source, "diagnose_saved_reply.py", "exec")
    return PREFIX + source.rstrip() + "\n" + SUFFIX


if __name__ == "__main__":
    (HERE / "DIAGNOSE-SAVED-REPLY.sh").write_text(build(), encoding="utf-8")
