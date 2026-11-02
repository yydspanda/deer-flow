"""Install diagnostic-only instrumentation after the existing LIVE confirmation."""

import json
from pathlib import Path

import diagnostics
import runner

original_execute = runner.execute


def execute(prepared, output):
    diagnostics.install(prepared, output.parent)
    return original_execute(prepared, output)


runner.execute = execute
code = runner.main()
check = Path(__file__).with_name("output-check.json")
if check.is_file():
    print("\n模型输出校验详情：", flush=True)
    print(check.read_text(encoding="utf-8"), flush=True)
else:
    print(
        json.dumps(
            {"output_check_saved": False, "model_request_attempted": "see_run_summary"}
        )
    )
raise SystemExit(code)
