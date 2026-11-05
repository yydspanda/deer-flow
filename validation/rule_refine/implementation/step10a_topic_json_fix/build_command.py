"""Build the bounded topic JSON repair over the frozen installed Step10 release."""

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step10_topic_filter"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREV = load("topic_json_previous_builder", PREVIOUS / "build_command.py")
BASE, COMPACT = PREV.BASE, PREV.COMPACT
MODULE, TEST = PREV.MODULE, PREV.TEST
CONTRACT = "topic-json-unique-keys-v1"
EXPECTED_RUNTIME = {
    MODULE + "topic_ai.py",
    MODULE + "topic_processor.py",
    MODULE + "topic_json.py",
}


def baseline():
    """Reproduce installed Step10 without modifying any frozen delivery file."""
    before = PREV.baseline()
    before.update(
        {
            name: entry["content"].encode("utf-8")
            for name, entry in PREV.contents()["files"].items()
        }
    )
    return before


def contents():
    before = baseline()
    files = {}
    for path in sorted((HERE / "payload").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = path.relative_to(HERE / "payload").as_posix()
        if name not in EXPECTED_RUNTIME and not name.startswith(
            TEST + "test_topic_json"
        ):
            raise ValueError("unexpected_topic_json_patch_path")
        raw = path.read_bytes()
        compile(raw, name, "exec")
        if raw == before.get(name):
            raise ValueError("unchanged_topic_json_payload_file")
        files[name] = {
            "old_sha256": BASE.sha(before[name]) if name in before else None,
            "new_sha256": BASE.sha(raw),
            "content": raw.decode("utf-8"),
        }
    if not EXPECTED_RUNTIME.issubset(files):
        raise ValueError("topic_json_runtime_payload_missing")
    tests = sorted(
        set(PREV.contents()["tests"])
        | {name for name in files if name.startswith(TEST + "test_")}
    )
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": "step10_topic_filter_installed_sources",
        "operator_contract_version": "small-rule-operators-v2",
        "output_contract_version": "complete-advisory-json-v1",
        "topic_contract_version": PREV.CONTRACT,
        "topic_json_contract_version": CONTRACT,
        "delivery_profile": "topic_json_incremental",
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
        "def validate_packet(packet):\n",
        "def validate_packet(packet):\n"
        f'    if packet.get("topic_json_contract_version") != "{CONTRACT}":\n'
        '        raise InstallError("unexpected_topic_json_contract")\n',
    )
    source = source.replace(
        "rule_refine_topic_filter_install", "rule_refine_topic_json_fix_install"
    ).replace("topic-filter-install-", "topic-json-fix-install-")
    source = BASE._replace(
        source,
        f'"topic_contract_version": "{PREV.CONTRACT}",',
        f'"topic_contract_version": "{PREV.CONTRACT}",\n'
        f'            "topic_json_contract_version": "{CONTRACT}",',
    )
    compile(source, "<topic-json-fix-installer>", "exec")
    return source


def build():
    packet, before = contents(), baseline()
    source = installer_source()
    command = COMPACT.packtext(COMPACT.make_bundle(packet, before, source))
    command = command.replace("SOC_OPERATORS", "SOC_TOPIC_JSON_FIX").replace(
        "PY_OPERATORS", "PY_TOPIC_JSON_FIX"
    )
    command = command.replace(
        '"check":"rule_refine_operator_contract_install"',
        '"check":"rule_refine_topic_json_fix_install"',
    )
    target = HERE / "INSTALL-TOPIC-JSON-FIX.sh"
    target.write_text(command, encoding="utf-8")
    # The previous activation tool pins source hashes. Regenerate only its source
    # manifest against this release; its behavior and configuration remain frozen.
    installed = {
        name: BASE.sha(raw) for name, raw in before.items() if not name.startswith(TEST)
    }
    installed.update(
        {
            name: entry["new_sha256"]
            for name, entry in packet["files"].items()
            if not name.startswith(TEST)
        }
    )
    enable_builder = load("topic_json_enable_builder", PREVIOUS / "build_enable.py")
    enable_command = enable_builder.make_command(installed, source)
    (HERE / "ENABLE-TOPIC-FILTER.sh").write_text(enable_command, encoding="utf-8")
    manifest = {
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": packet["source_baseline"],
        "topic_json_contract_version": CONTRACT,
        "files": {
            name: {key: entry[key] for key in ("old_sha256", "new_sha256")}
            for name, entry in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "command_sha256": BASE.sha(command.encode("utf-8")),
        "enable_command_sha256": BASE.sha(enable_command.encode("utf-8")),
        "manual_command": "../step10_topic_filter/CHECK-TOPIC-REAL-CSV.sh",
        "manual_command_sha256": BASE.sha(
            (PREVIOUS / "CHECK-TOPIC-REAL-CSV.sh").read_bytes()
        ),
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    result = build()
    print(json.dumps({"command": str(result), "bytes": result.stat().st_size}))
