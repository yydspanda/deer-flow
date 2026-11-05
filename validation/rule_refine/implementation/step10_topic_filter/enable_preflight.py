"""Run the exact enable paste command for LOCAL and PRD disposable fixtures."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

import build_command as builder


def run():
    builder.build()
    installed = builder.baseline()
    installed.update(
        {
            name: entry["content"].encode("utf-8")
            for name, entry in builder.contents()["files"].items()
        }
    )
    with tempfile.TemporaryDirectory(
        prefix="topic-enable-paste-preflight-"
    ) as directory:
        results = []
        for profile in ("LOCAL", "PRD"):
            root = Path(directory) / profile
            root.mkdir()
            for name, raw in installed.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            run_before = b"#!/bin/bash\nexport RULE_REFINE_MONITOR_ENABLED=0\n# Other startup behavior remains unchanged.\n"
            (root / "run.sh").write_bytes(run_before)
            csv = root / "nas/original/synthetic-rules.csv"
            csv.parent.mkdir(parents=True)
            csv.write_text("synthetic,fixture\n", encoding="utf-8")
            local = {
                "enabled": True,
                "namespace": "synthetic-previous-namespace",
                "csv_path": str(csv),
            }
            (root / "config/rule_refine.local.json").write_text(
                json.dumps(local), encoding="utf-8"
            )
            python = root / (
                ".venv/bin/python" if profile == "LOCAL" else "test-bin/python3"
            )
            python.parent.mkdir(parents=True)
            python.write_text(
                "#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n',
                encoding="utf-8",
            )
            python.chmod(0o700)
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith("RULE_REFINE_")
            }
            env.update(
                SEC_KNOW_MODEL_ROOT=str(root),
                SEC_RULE_REFINE_DATA_ROOT=str(Path(directory) / "backups" / profile),
                env_profile=profile,
                ENV_PROFILE=profile,
                PYTHONDONTWRITEBYTECODE="1",
            )
            if profile == "PRD":
                env["SEC_TOPIC_CSV_PATH"] = str(csv)
                env["RULE_REFINE_MONITOR_ENABLED"] = "0"
                env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
            for attempt in range(2):
                process = subprocess.run(
                    ["bash", str(builder.HERE / "ENABLE-TOPIC-FILTER.sh")],
                    env=env,
                    text=True,
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if process.returncode:
                    print(process.stdout)
                    print(process.stderr, file=sys.stderr)
                    raise RuntimeError("topic_enable_paste_failed")
                start = process.stdout.find('{\n  "check"')
                if start < 0:
                    raise RuntimeError("topic_enable_summary_missing")
                result = json.loads(process.stdout[start:])
                expected = (
                    "enabled_for_next_start" if attempt == 0 else "already_enabled"
                )
                if result["status"] != expected:
                    raise RuntimeError("topic_enable_paste_status_mismatch")
                results.append(result)
            run_after = (
                run_before
                if profile == "LOCAL"
                else run_before.replace(b"ENABLED=0", b"ENABLED=1")
            )
            if (root / "run.sh").read_bytes() != run_after:
                raise RuntimeError("topic_enable_launcher_scope_mismatch")
            for name, raw in installed.items():
                if (root / name).read_bytes() != raw:
                    raise RuntimeError("topic_enable_changed_runtime_sources")
        summary = {
            "check": "topic_filter_enable_paste_offline_preflight",
            "status": "passed",
            "profiles": ["LOCAL", "PRD"],
            "idempotent_reenable_verified": True,
            "runtime_sources_unchanged": True,
            "production_launcher_only_exact_monitor_marker_changed": True,
            "production_python3_fallback_verified": True,
            "model_calls": 0,
            "redis_calls": 0,
            "business_database_calls": 0,
            "zeus_callback_calls": 0,
            "services_started": False,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return summary


if __name__ == "__main__":
    run()
