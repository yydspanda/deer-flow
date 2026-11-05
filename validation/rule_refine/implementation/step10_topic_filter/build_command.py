"""Build a topic-filter delta over the installed operator and complete-output fixes."""

import importlib.util
import json
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step09a_complete_output"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREV = load("topic_delivery_previous", PREVIOUS / "build_command.py")
BASE, COMPACT = PREV.BASE, PREV.COMPACT
MODULE, TEST, CLIENT = BASE.MODULE, BASE.TEST, BASE.CLIENT
CONFIG = "config/celery_config.py"
CONTRACT = "topic-filter-delivery-v1"


def baseline():
    """Reproduce the installed Step09a tip, not the earlier ZIP or Step09 state."""
    before = PREV.baseline()
    before.update(
        {
            name: entry["content"].encode("utf-8")
            for name, entry in PREV.contents()["files"].items()
        }
    )
    # This explicitly scoped configuration contains no addresses or credentials.
    # PREV.baseline() has already checked the complete private archive's digest.
    with zipfile.ZipFile(BASE.ARCHIVE) as archive:
        before[CONFIG] = archive.read(CONFIG)
    return before


def allowed(name):
    return (
        name.endswith(".py")
        and ".." not in Path(name).parts
        and "__pycache__" not in Path(name).parts
        and (name.startswith((MODULE, TEST)) or name in {CLIENT, CONFIG})
    )


def contents():
    before = baseline()
    files = {}
    for path in sorted((HERE / "payload").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = path.relative_to(HERE / "payload").as_posix()
        if not allowed(name):
            raise ValueError("unexpected_topic_patch_path")
        raw = path.read_bytes()
        compile(raw, name, "exec")
        if raw == before.get(name):
            raise ValueError("unchanged_topic_payload_file")
        files[name] = {
            "old_sha256": BASE.sha(before[name]) if name in before else None,
            "new_sha256": BASE.sha(raw),
            "content": raw.decode("utf-8"),
        }
    if not any(name.startswith(MODULE + "topic_") for name in files):
        raise ValueError("topic_runtime_payload_missing")
    tests = sorted(
        set(PREV.contents()["tests"])
        | {name for name in files if name.startswith(TEST + "test_")}
    )
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": "step09a_complete_output_installed_sources",
        "operator_contract_version": "small-rule-operators-v2",
        "output_contract_version": "complete-advisory-json-v1",
        "topic_contract_version": CONTRACT,
        "delivery_profile": "topic_filter_incremental",
        "files": files,
        "dependencies": {
            name: BASE.sha(raw)
            for name, raw in before.items()
            if name not in files and not name.startswith(TEST)
        },
        "test_files": {
            name: raw.decode("utf-8")
            for name, raw in before.items()
            if name.startswith(TEST) and name not in files
        },
        "tests": tests,
    }


def installer_source():
    source = PREV.installer_source()
    source = BASE._replace(
        source,
        'or relative == "my_workflows/zeus/flows/disposition_tools/small_rule_sync_client.py"',
        'or relative == "my_workflows/zeus/flows/disposition_tools/small_rule_sync_client.py"\n'
        '                or relative == "config/celery_config.py"',
    )
    source = BASE._replace(
        source,
        "def validate_packet(packet):\n",
        "def validate_packet(packet):\n"
        f'    if packet.get("topic_contract_version") != "{CONTRACT}":\n'
        '        raise InstallError("unexpected_topic_contract")\n',
    )
    source = BASE._replace(
        source,
        '"background_service.py": 4,',
        '"background_service.py": 4,\n            "celery_config.py": 5,',
    )
    source = source.replace(
        "rule_refine_complete_output_install", "rule_refine_topic_filter_install"
    ).replace("complete-output-install-", "topic-filter-install-")
    source = BASE._replace(
        source,
        '"output_contract_version": "complete-advisory-json-v1",',
        '"output_contract_version": "complete-advisory-json-v1",\n'
        f'            "topic_contract_version": "{CONTRACT}",',
    )
    source = BASE._replace(
        source,
        '"configuration_files_modified": False,',
        '"configuration_files_modified": any(name.startswith("config/") for name in changed),\n'
        '            "monitor_enabled_by_install": False,\n'
        '            "launch_script_updated": False,',
    )
    # Shared installer lock prevents racing with already delivered installations.
    # Installing these Python sources neither activates monitoring nor submits rules.
    compile(source, "<topic-filter-installer>", "exec")
    return source


def build():
    packet = contents()
    before = baseline()
    command = COMPACT.packtext(COMPACT.make_bundle(packet, before, installer_source()))
    command = command.replace("SOC_OPERATORS", "SOC_TOPIC_FILTER").replace(
        "PY_OPERATORS", "PY_TOPIC_FILTER"
    )
    command = command.replace(
        '"check":"rule_refine_operator_contract_install"',
        '"check":"rule_refine_topic_filter_install"',
    )
    target = HERE / "INSTALL-TOPIC-FILTER.sh"
    target.write_text(command, encoding="utf-8")
    installed_hashes = {
        name: BASE.sha(raw) for name, raw in before.items() if not name.startswith(TEST)
    }
    installed_hashes.update(
        {
            name: entry["new_sha256"]
            for name, entry in packet["files"].items()
            if not name.startswith(TEST)
        }
    )
    enable_builder = load("topic_enable_builder", HERE / "build_enable.py")
    enable_command = enable_builder.make_command(installed_hashes, installer_source())
    (HERE / "ENABLE-TOPIC-FILTER.sh").write_text(enable_command, encoding="utf-8")
    manual_builder = load("topic_manual_builder", HERE / "build_manual.py")
    manual_command = manual_builder.make_command()
    (HERE / "CHECK-TOPIC-REAL-CSV.sh").write_text(manual_command, encoding="utf-8")
    manifest = {
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": packet["source_baseline"],
        "topic_contract_version": CONTRACT,
        "files": {
            name: {key: entry[key] for key in ("old_sha256", "new_sha256")}
            for name, entry in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "command_sha256": BASE.sha(command.encode("utf-8")),
        "enable_command_sha256": BASE.sha(enable_command.encode("utf-8")),
        "manual_command_sha256": BASE.sha(manual_command.encode("utf-8")),
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    target = build()
    print(json.dumps({"command": str(target), "bytes": target.stat().st_size}))
