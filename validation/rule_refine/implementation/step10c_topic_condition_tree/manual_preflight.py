"""Run the exact manual Shell wrapper with a synthetic preview and cancelled consent."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import build_command as builder

SHIM = """import json, sys, types
from pathlib import Path
prepared = {
    "root": Path.cwd(), "profile": "LOCAL", "model": "synthetic-model",
    "namespace": "synthetic-topic", "zeus_system_url": "https://synthetic.invalid",
    "zeus_url": "https://synthetic.invalid", "submission_url": "https://synthetic.invalid/public/syncSmallRule",
    "zeus_target": {"base_url": "https://synthetic.invalid", "endpoint": "https://synthetic.invalid/public/syncSmallRule", "source": "static_root_config"},
    "budget": types.SimpleNamespace(input_token_budget=209715),
    "case": {"current_samples": [{"alert_id": "synthetic-1"}], "history_samples": [],
             "execute_type": {"status": "resolved", "values": [3]}},
    "preview": {"selection": {"current_count": 1}, "request": {"input_bytes": 100, "estimated_input_tokens": 90}},
}
def forbidden(*args, **kwargs):
    raise RuntimeError("live_execution_forbidden_in_manual_preflight")
def cancel(prompt):
    Path("cancelled.json").write_text(json.dumps({"prompt": prompt, "tool_hashes": prepared.get("tool_hashes", {})}), encoding="utf-8")
    return "NO"
def trace(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "main" and frame.f_globals.get("__name__") == "runner":
        frame.f_globals["prepare"] = lambda *args, **kwargs: prepared
        frame.f_globals["ask"] = cancel
        frame.f_globals["execute"] = forbidden
        sys.setprofile(None)
def guard(event, args):
    if event.startswith("socket.") or event in {"sqlite3.connect", "subprocess.Popen", "os.system", "os.exec"}:
        raise RuntimeError("external_operation_forbidden_in_manual_preflight")
sys.addaudithook(guard)
sys.setprofile(trace)
source = sys.stdin.read()
sys.argv = ["-"]
exec(compile(source, "<actual-manual-command>", "exec"), {"__name__": "__main__"})
"""


def run():
    builder.build()
    command = builder.HERE / "CHECK-TOPIC-REAL-CSV.sh"
    with tempfile.TemporaryDirectory(
        prefix="topic-manual-paste-preflight-"
    ) as temporary:
        root = Path(temporary) / "project"
        root.mkdir()
        shim = root / "synthetic_preview_only.py"
        shim.write_text(SHIM, encoding="utf-8")
        python = root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text(
            "#!/bin/sh\nexec "
            + shlex.quote(sys.executable)
            + " "
            + shlex.quote(str(shim))
            + ' "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o700)
        env = dict(os.environ)
        env.update(
            SEC_KNOW_MODEL_ROOT=str(root),
            env_profile="LOCAL",
            ENV_PROFILE="LOCAL",
            PYTHONDONTWRITEBYTECODE="1",
        )
        process = subprocess.run(
            ["bash", str(command)],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if process.returncode:
            print(process.stdout)
            print(process.stderr, file=sys.stderr)
            raise RuntimeError("topic_manual_paste_cancel_failed")
        receipt = json.loads((root / "cancelled.json").read_text(encoding="utf-8"))
        if "创建ZEUS小规则" not in receipt["prompt"]:
            raise RuntimeError("topic_manual_creation_consent_missing")
        if set(receipt["tool_hashes"]) != {
            "surge_materials",
            "restricted_dataframe_pickle",
            "materials",
            "runner",
        }:
            raise RuntimeError("topic_manual_embedded_sources_missing")
        if (
            "实际创建小规则" not in process.stdout
            or '"max_zeus_posts": 1' not in process.stdout
            or '"ai_contract": "topic-condition-tree-v1"' not in process.stdout
        ):
            raise RuntimeError("topic_manual_preview_scope_missing")
        summary = {
            "check": "topic_filter_manual_paste_cancel_preflight",
            "status": "passed",
            "fixture": "synthetic_preview_with_declined_live_confirmation",
            "actual_shell_wrapper_executed": True,
            "embedded_modules_verified": True,
            "cancelled_without_execution": True,
            "model_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "zeus_callback_calls": 0,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


if __name__ == "__main__":
    run()
