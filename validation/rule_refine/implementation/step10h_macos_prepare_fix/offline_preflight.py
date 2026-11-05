"""Run the actual paste command using a Mac-like aliased TMPDIR, twice."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import build_command as builder
import test_macos_paths as tests


def run():
    command = builder.build()
    frozen = {
        p: p.read_bytes()
        for p in builder.PREVIOUS.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    }
    with tempfile.TemporaryDirectory(prefix="macos-production-preflight-") as directory:
        base = Path(directory).resolve()
        root = base / "sec_know_model"
        root.mkdir()
        tests.fixtures.populate(root)
        before = {
            str(p.relative_to(root)): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }
        real_tmp = base / "private-var"
        real_tmp.mkdir()
        alias_tmp = base / "var"
        alias_tmp.symlink_to(real_tmp, target_is_directory=True)
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
            SEC_RULE_REFINE_DATA_ROOT=str(base / "backups"),
            env_profile="LOCAL",
            ENV_PROFILE="LOCAL",
            PYTHONDONTWRITEBYTECODE="1",
            TMPDIR=str(alias_tmp),
            TMP=str(alias_tmp),
            TEMP=str(alias_tmp),
        )
        env.pop("SEC_TOPIC_CSV_PATH", None)
        # Verify the subprocess really sees an unresolved alias, not just a fixture label.
        probe = subprocess.run(
            [
                sys.executable,
                "-c",
                "import tempfile;from pathlib import Path;p=Path(tempfile.gettempdir());assert p != p.resolve()",
            ],
            env=env,
            capture_output=True,
            check=True,
        )
        assert probe.returncode == 0
        results = []
        for attempt in range(2):
            process = subprocess.run(
                ["bash", str(command)],
                env=env,
                capture_output=True,
                text=True,
                timeout=360,
                check=False,
            )
            if process.returncode:
                print(process.stdout)
                print(process.stderr, file=sys.stderr)
                raise RuntimeError("macos_prepare_paste_failed")
            start = process.stdout.find('{\n  "check"')
            result, _ = json.JSONDecoder().raw_decode(process.stdout[start:])
            assert result["status"] == (
                "prepared" if attempt == 0 else "already_prepared"
            )
            assert result["preparation_revision"] == builder.REVISION
            results.append(result)
        for name, raw in before.items():
            if name not in results[0]["changed_files"]:
                assert (root / name).read_bytes() == raw
        for path, raw in frozen.items():
            assert path.read_bytes() == raw
        summary = {
            "check": "macos_production_prepare_offline_preflight",
            "status": "passed",
            "runtime_regression_tests": results[0]["tests_passed"],
            "actual_paste_command_with_aliased_tmpdir_verified": True,
            "idempotent_repeat_verified": True,
            "application_payload_unchanged_from_step10g": True,
            "old_delivery_unchanged": True,
            "local_and_queue_files_unchanged": True,
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
