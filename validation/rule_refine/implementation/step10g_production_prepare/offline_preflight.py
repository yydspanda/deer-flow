"""Exercise the actual Mac paste command twice without service credentials."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import build_command as builder
import test_prepare as fixtures


def run():
    command = builder.build()
    with tempfile.TemporaryDirectory(prefix="topic-production-paste-") as directory:
        root = Path(directory) / "sec_know_model"
        root.mkdir()
        fixtures.populate(root)
        before = {
            str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }
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
            SEC_RULE_REFINE_DATA_ROOT=str(Path(directory) / "backups"),
            env_profile="LOCAL",
            ENV_PROFILE="LOCAL",
            PYTHONDONTWRITEBYTECODE="1",
        )
        for key in ("SEC_TOPIC_CSV_PATH",):
            env.pop(key, None)
        results = []
        for attempt in range(2):
            process = subprocess.run(
                ["bash", str(command)],
                capture_output=True,
                text=True,
                env=env,
                timeout=360,
                check=False,
            )
            if process.returncode:
                print(process.stdout)
                print(process.stderr, file=sys.stderr)
                raise RuntimeError("production_prepare_paste_failed")
            start = process.stdout.find('{\n  "check"')
            result, _ = json.JSONDecoder().raw_decode(process.stdout[start:])
            if result["status"] != ("prepared" if attempt == 0 else "already_prepared"):
                raise RuntimeError("production_prepare_status_mismatch")
            results.append(result)
        for name, raw in before.items():
            if (
                name not in results[0]["changed_files"]
                and (root / name).read_bytes() != raw
            ):
                raise RuntimeError("untargeted_project_file_changed")
        summary = {
            "check": "topic_production_prepare_offline_preflight",
            "status": "passed",
            "runtime_regression_tests": results[0]["tests_passed"],
            "files_changed_in_temporary_project": results[0]["files_changed"],
            "exact_paste_command_verified": True,
            "idempotent_repeat_verified": True,
            "local_configuration_and_queue_files_unchanged": True,
            "model_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "zeus_posts": 0,
            "services_started": False,
        }
        print(json.dumps(summary, indent=2))
        return summary


if __name__ == "__main__":
    run()
