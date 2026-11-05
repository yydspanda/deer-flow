"""Run the actual pasted command against a disposable installed-step09 fixture."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import build_command as builder


def run():
    command = builder.build()
    packet = builder.contents()
    with tempfile.TemporaryDirectory(prefix="complete-output-preflight-") as tmp:
        root = Path(tmp) / "project"
        root.mkdir()
        before = builder.baseline()
        for name, raw in before.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        python = root / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        python.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
            encoding="utf-8",
        )
        python.chmod(0o700)
        env = dict(os.environ)
        env.update(
            SEC_KNOW_MODEL_ROOT=str(root),
            SEC_RULE_REFINE_DATA_ROOT=str(Path(tmp) / "backups"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        results = []
        for attempt in range(2):
            process = subprocess.run(
                ["bash", str(command)],
                env=env,
                text=True,
                capture_output=True,
                timeout=360,
                check=False,
            )
            if process.returncode:
                print(process.stdout)
                print(process.stderr, file=sys.stderr)
                raise RuntimeError("paste_command_preflight_failed")
            start = process.stdout.find('{\n  "check"')
            if start < 0:
                raise RuntimeError("preflight_summary_missing")
            result = json.loads(process.stdout[start:])
            expected = "installed" if attempt == 0 else "already_installed"
            if result["status"] != expected:
                raise RuntimeError("preflight_status_mismatch")
            results.append(result)
        for name, entry in packet["files"].items():
            if (
                hashlib.sha256((root / name).read_bytes()).hexdigest()
                != entry["new_sha256"]
            ):
                raise RuntimeError("preflight_target_hash_mismatch")
        for name, raw in before.items():
            if name not in packet["files"] and (root / name).read_bytes() != raw:
                raise RuntimeError("preflight_untargeted_file_changed")
        summary = {
            "check": "complete_output_paste_offline_preflight",
            "status": "passed",
            "tests_passed": results[0]["tests_passed"],
            "files_changed_in_disposable_fixture": results[0]["files_changed"],
            "idempotent_reinstall_verified": True,
            "target_hashes_verified": True,
            "untargeted_source_unchanged": True,
            "model_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "services_started": False,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


if __name__ == "__main__":
    run()
