"""Embed the reviewed export loaders and the topic-specific manual entry point."""

import importlib.util
from pathlib import Path

HERE = Path(__file__).resolve().parent


def make_command():
    path = HERE.parent / "step07b_real_csv_acceptance/build_command.py"
    spec = importlib.util.spec_from_file_location(
        "topic_tree_manual_export_builder", path
    )
    legacy = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(legacy)
    packet = legacy.contents()
    packet["files"]["runner"] = (HERE / "manual_runner.py").read_text(encoding="utf-8")
    command = legacy.command(packet)
    command = command.replace("SOC_REAL_CSV_ACCEPTANCE", "SOC_TOPIC_REAL_CSV").replace(
        "PY_REAL_CSV_ACCEPTANCE", "PY_TOPIC_REAL_CSV"
    )
    command = command.replace(
        'cd "$HOME/sec_know_model"',
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"',
    ).replace("env env_profile=LOCAL .venv/bin/python", ".venv/bin/python")
    return command.replace(
        ".venv/bin/python -B - <<'PY_TOPIC_REAL_CSV'",
        'echo "手工验证topic条件树建议：预览后输入LIVE，最多2次模型请求和1次真实ZEUS小规则提交；复核未通过不提交。"\n'
        "if [ -x .venv/bin/python ]; then SEC_TOPIC_PYTHON=.venv/bin/python; else SEC_TOPIC_PYTHON=python3; fi\n"
        "\"$SEC_TOPIC_PYTHON\" -B - <<'PY_TOPIC_REAL_CSV'",
    )
