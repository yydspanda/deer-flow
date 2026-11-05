"""Install the complete payload into a disposable reviewed-source fixture only."""

import hashlib
import importlib.util
import json
import tempfile
import types
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location(
    "operator_preflight_builder", HERE / "build_command.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def main():
    packet = BUILDER.contents()
    installer = types.ModuleType("operator_preflight_installer")
    exec(
        compile(BUILDER.installer_source(), "<operator-installer>", "exec"),
        installer.__dict__,
    )
    with tempfile.TemporaryDirectory(prefix="operator-full-preflight-") as directory:
        project = Path(directory) / "fixture"
        project.mkdir()
        for relative, raw in BUILDER.baseline().items():
            target = project / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        result = installer.apply(project, Path(directory) / "backups", packet)
        mismatches = [
            name
            for name, entry in packet["files"].items()
            if hashlib.sha256((project / name).read_bytes()).hexdigest()
            != entry["new_sha256"]
        ]
        if mismatches:
            raise RuntimeError("installed_payload_hash_mismatch")
    print(
        json.dumps(
            {
                "check": "rule_refine_operator_contract_offline_preflight",
                "status": "passed",
                "test_scope": "disposable_reviewed_production_source_fixture",
                "tests_passed": result["tests_passed"],
                "test_files": len(packet["tests"]),
                "files_changed_in_fixture": result["files_changed"],
                "source_zip_sha256": BUILDER.BASELINE_SHA256,
                "original_zip_modified": False,
                "application_code_updated": False,
                "model_calls": 0,
                "redis_calls": 0,
                "business_database_calls": 0,
                "services_started": False,
                "automatic_restart": False,
                "automatic_apply": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
