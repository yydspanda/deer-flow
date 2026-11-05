"""Package an isolated diagnostic; never rebuild or alter delivered installers."""

import ast
import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step10c_topic_condition_tree"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_files():
    previous = load("topic_diagnostic_baseline", PREVIOUS / "build_command.py")
    files = previous.baseline()
    files.update(
        {
            k: v["content"].encode("utf-8")
            for k, v in previous.contents()["files"].items()
        }
    )
    return files


def contents():
    legacy = load(
        "topic_diagnostic_export_builder",
        HERE.parent / "step07b_real_csv_acceptance/build_command.py",
    )
    files = legacy.contents()["files"]
    # Reuse only read-only preparation helpers. No submission/execution entry
    # point from the live creation tool is included in this command.
    source = (PREVIOUS / "manual_runner.py").read_text(encoding="utf-8")
    helpers = {
        "modules",
        "fingerprint",
        "static_zeus_target",
        "verify_runtime_target",
        "sqlite_rows",
        "material_loader",
        "prepare",
        "ask",
    }
    nodes = [
        node
        for node in ast.parse(source).body
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign))
        or isinstance(node, ast.FunctionDef)
        and node.name in helpers
    ]
    files["topic_materials"] = (
        "\n\n".join(ast.get_source_segment(source, node) for node in nodes) + "\n"
    )
    files["runner"] = (HERE / "runner.py").read_text(encoding="utf-8")
    reviewed = runtime_files()
    prefix = "my_workflows/zeus/rule_refine/"
    expected = {
        name: hashlib.sha256(raw).hexdigest()
        for name, raw in reviewed.items()
        if name.startswith(prefix) and name.endswith(".py")
    }
    files["diagnostic_contract"] = "EXPECTED_SOURCES = " + repr(expected) + "\n"
    return {"files": files}


ORDER = (
    "surge_materials",
    "restricted_dataframe_pickle",
    "materials",
    "topic_materials",
    "diagnostic_contract",
    "runner",
)


def load_runner():
    packet = contents()
    for name in ORDER:
        module = types.ModuleType(name)
        module.__file__ = str(HERE / (name + ".py"))
        sys.modules[name] = module
        exec(compile(packet["files"][name], module.__file__, "exec"), module.__dict__)
    return sys.modules["runner"]


def build():
    packet = contents()
    legacy = load(
        "topic_diagnostic_embedded_builder",
        HERE.parent / "step07b_real_csv_acceptance/build_command.py",
    )
    legacy.ORDER = ORDER
    shell = (
        "bash <<'SOC_TOPIC_DIAGNOSTIC'\nset -euo pipefail\numask 077\n"
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"\n'
        'echo "诊断topic模型回答：预览后输入LIVE，最多2次模型请求；不提交ZEUS，不修改源码或启动服务。"\n'
        "if [ -x .venv/bin/python ]; then SEC_TOPIC_PYTHON=.venv/bin/python; else SEC_TOPIC_PYTHON=python3; fi\n"
        "\"$SEC_TOPIC_PYTHON\" -B - <<'PY_TOPIC_DIAGNOSTIC'\n"
        + legacy.runtime_source(packet)
        + "PY_TOPIC_DIAGNOSTIC\nSOC_TOPIC_DIAGNOSTIC\n"
    )
    target = HERE / "DIAGNOSE-TOPIC-REPLY.sh"
    target.write_text(shell, encoding="utf-8")
    manifest = {
        "check": "topic_reply_diagnostic_delivery",
        "command_sha256": hashlib.sha256(shell.encode()).hexdigest(),
        "embedded_source_sha256": {
            name: hashlib.sha256(source.encode()).hexdigest()
            for name, source in packet["files"].items()
        },
        "application_code_updated": False,
        "max_sdk_requests": 2,
        "max_zeus_posts": 0,
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return shell


if __name__ == "__main__":
    build()
