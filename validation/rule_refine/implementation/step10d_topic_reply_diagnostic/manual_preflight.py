"""Execute the delivered shell with synthetic preparation and declined LIVE."""

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
    "model": "synthetic-model",
    "budget": types.SimpleNamespace(input_token_budget=209715),
    "case": {"current_samples": [{"alert_id": "synthetic-1"}], "history_samples": [],
             "execute_type": {"status": "resolved", "values": [3]}},
    "preview": {"selection": {"current_count": 1}, "request": {"input_bytes": 100, "estimated_input_tokens": 90}},
}
def forbidden(*args, **kwargs):
    raise RuntimeError("live_execution_forbidden")
def cancel(prompt):
    Path("cancelled.json").write_text(json.dumps({"prompt": prompt, "tool_hashes": prepared.get("tool_hashes", {})}), encoding="utf-8")
    return "NO"
def trace(frame, event, arg):
    if event == "call" and frame.f_code.co_name == "main" and frame.f_globals.get("__name__") == "runner":
        frame.f_globals["validate_sources"] = lambda root: None
        frame.f_globals["prepare"] = lambda *args, **kwargs: prepared
        frame.f_globals["ask"] = cancel
        frame.f_globals["execute"] = forbidden
        sys.setprofile(None)
def guard(event, args):
    if event.startswith("socket.") or event in {"sqlite3.connect", "subprocess.Popen", "os.system", "os.exec"}:
        raise RuntimeError("external_operation_forbidden")
sys.addaudithook(guard)
sys.setprofile(trace)
source = sys.stdin.read()
sys.argv = ["-"]
exec(compile(source, "<diagnostic-shell>", "exec"), {"__name__": "__main__"})
"""


def run():
    command = builder.HERE / "DIAGNOSE-TOPIC-REPLY.sh"
    with tempfile.TemporaryDirectory(prefix="topic-diagnostic-shell-") as temporary:
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
        env = {
            **os.environ,
            "SEC_KNOW_MODEL_ROOT": str(root),
            "env_profile": "LOCAL",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = subprocess.run(
            ["bash", str(command)],
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        if result.returncode:
            raise AssertionError((result.stdout, result.stderr))
        receipt = json.loads((root / "cancelled.json").read_text(encoding="utf-8"))
        assert set(receipt["tool_hashes"]) == set(builder.ORDER)
        assert "诊断调用" in receipt["prompt"]
        assert '"max_zeus_posts": 0' in result.stdout
        assert '"ai_contract": "topic-condition-tree-v1"' in result.stdout
        assert not (root / "nas").exists()
    summary = {
        "check": "topic_reply_diagnostic_shell_cancel",
        "status": "passed",
        "actual_shell_executed": True,
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "zeus_posts": 0,
    }
    print(json.dumps(summary))
    return summary


if __name__ == "__main__":
    run()
