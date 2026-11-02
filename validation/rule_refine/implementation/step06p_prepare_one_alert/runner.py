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

PAYLOAD = """__PAYLOAD__"""
EXPECTED = "__DIGEST__"
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
