bash <<'SOC_PREPARE_ONE_ALERT'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python -B - <<'PY_PREPARE_ONE'
"""Template populated by build_command.py; never import project application code."""

import base64
import hashlib
import json
import os
import sys
import types
import warnings
import zlib
from pathlib import Path

PAYLOAD = """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"""
EXPECTED = "b037a921ff69baa25182b946865db8b6d4ce624a11c97fa94c3698f8cc191653"
ORDER = (
    "rule_config_source.py",
    "csv_config_source.py",
    "ingress.py",
    "materials_check.py",
    "request_schema.py",
    "prepare.py",
)


def load_tools(root):
    unpacker = zlib.decompressobj()
    data = unpacker.decompress(base64.b64decode(PAYLOAD), 256 * 1024 + 1)
    if (
        len(data) > 256 * 1024
        or not unpacker.eof
        or unpacker.unused_data
        or unpacker.unconsumed_tail
        or hashlib.sha256(data).hexdigest() != EXPECTED
    ):
        raise ValueError("embedded_tool_check_failed")
    packet = json.loads(data)
    files = packet["files"]
    if set(files) != set(ORDER) or set(packet["pure_pins"]) != set(ORDER[:3]):
        raise ValueError("embedded_tool_names_invalid")
    code = {name: compile(source, name, "exec") for name, source in files.items()}
    # Compare the three installed pure modules without importing the application.
    for name, digest in packet["pure_pins"].items():
        path = root / "my_workflows/zeus/rule_refine" / name
        with path.open("rb") as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024 or hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("installed_pure_module_changed")
    package = types.ModuleType("_one_alert_preparation")
    package.__path__ = []
    sys.modules[package.__name__] = package
    modules = {}
    for name in ORDER:
        qualified = package.__name__ + "." + name[:-3]
        module = types.ModuleType(qualified)
        module.__file__ = str(root / name)
        module.__package__ = package.__name__
        sys.modules[qualified] = module
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            exec(code[name], module.__dict__)
        modules[name] = module
    return modules, packet


def main():
    if any(
        os.environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        print(
            json.dumps(
                {
                    "status": "skipped_non_local",
                    "business_alerts_submitted": 0,
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return 0
    print(
        "准备一条历史告警：保留原ID及告警内容，仅保存本地请求；不提交、不调用模型。",
        flush=True,
    )
    try:
        modules, packet = load_tools(Path.cwd())
    except Exception as error:
        code = (
            str(error)
            if type(error) is ValueError
            and str(error)
            in {
                "embedded_tool_check_failed",
                "embedded_tool_names_invalid",
                "installed_pure_module_changed",
            }
            else "tool_loading_failed"
        )
        print(
            json.dumps(
                {
                    "status": "preparation_stopped",
                    "error_code": code,
                    "error_type": type(error).__name__,
                    "business_alerts_submitted": 0,
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return 1
    tool = modules["prepare.py"]
    try:
        result = tool.prepare(
            Path.cwd(),
            inventory=modules["materials_check.py"],
            csv_source=modules["csv_config_source.py"].CsvRuleConfigSource,
            normalize=modules["ingress.py"].normalize_received_alert,
            request_model=modules["request_schema.py"].WorkFlowTaskInput,
            reviewed_schema_source=packet["files"]["request_schema.py"],
            source_pins=packet["source_pins"],
        )
    except Exception as error:
        print(
            json.dumps(
                {
                    "status": "preparation_stopped",
                    "error_code": str(error)
                    if isinstance(error, tool.PreparationError)
                    else "preparation_failed",
                    "error_type": type(error).__name__,
                    "business_alerts_submitted": 0,
                    "model_calls": 0,
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

PY_PREPARE_ONE
SOC_PREPARE_ONE_ALERT
