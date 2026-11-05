"""Build the topic condition-tree contract over the frozen installed Step10b release."""

import importlib.util
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step10b_topic_expression_review"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREV = load("topic_tree_previous_builder", PREVIOUS / "build_command.py")
BASE, COMPACT = PREV.BASE, PREV.COMPACT
MODULE, TEST = PREV.MODULE, PREV.TEST
ORIGINAL = HERE.parent / "step10_topic_filter"
CONTRACT = "topic-condition-tree-v1"
EXPECTED_RUNTIME = {
    MODULE + "topic_tree_ai.py",
    MODULE + "topic_processor.py",
    MODULE + "topic_tree.py",
    MODULE + "topic_background.py",
}


@lru_cache(maxsize=1)
def _baseline_snapshot():
    """Read the frozen previous release once per builder invocation."""
    before = PREV.baseline()
    before.update(
        {
            name: entry["content"].encode("utf-8")
            for name, entry in PREV.contents()["files"].items()
        }
    )
    return tuple(before.items())


def baseline():
    """Return a fresh mapping so callers cannot mutate the frozen source snapshot."""
    return dict(_baseline_snapshot())


def contents():
    before = baseline()
    files = {}
    for path in sorted((HERE / "payload").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = path.relative_to(HERE / "payload").as_posix()
        if name not in EXPECTED_RUNTIME and not name.startswith(
            TEST + "test_topic_tree"
        ):
            raise ValueError("unexpected_topic_tree_patch_path")
        raw = path.read_bytes()
        compile(raw, name, "exec")
        if raw == before.get(name):
            raise ValueError("unchanged_topic_tree_payload_file")
        files[name] = {
            "old_sha256": BASE.sha(before[name]) if name in before else None,
            "new_sha256": BASE.sha(raw),
            "content": raw.decode("utf-8"),
        }
    if not EXPECTED_RUNTIME.issubset(files):
        raise ValueError("topic_tree_runtime_payload_missing")
    tests = sorted(
        set(PREV.contents()["tests"])
        | {name for name in files if name.startswith(TEST + "test_")}
    )
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": "step10b_topic_expression_review_installed_sources",
        "operator_contract_version": "small-rule-operators-v2",
        "output_contract_version": "complete-advisory-json-v1",
        "topic_contract_version": PREV.PREV.PREV.CONTRACT,
        "topic_json_contract_version": PREV.PREV.CONTRACT,
        "topic_expression_contract_version": PREV.CONTRACT,
        "topic_tree_contract_version": CONTRACT,
        "delivery_profile": "topic_tree_incremental",
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
        f'    if packet.get("topic_tree_contract_version") != "{CONTRACT}":\n'
        '        raise InstallError("unexpected_topic_tree_contract")\n',
    )
    source = source.replace(
        "rule_refine_topic_expression_fix_install", "rule_refine_topic_tree_install"
    ).replace("topic-expression-fix-install-", "topic-tree-install-")
    source = BASE._replace(
        source,
        f'"topic_expression_contract_version": "{PREV.CONTRACT}",',
        f'"topic_expression_contract_version": "{PREV.CONTRACT}",\n'
        f'            "topic_tree_contract_version": "{CONTRACT}",',
    )
    compile(source, "<topic-tree-fix-installer>", "exec")
    return source


def build():
    packet, before = contents(), baseline()
    source = installer_source()
    command = COMPACT.packtext(COMPACT.make_bundle(packet, before, source))
    command = command.replace("SOC_OPERATORS", "SOC_TOPIC_TREE_FIX").replace(
        "PY_OPERATORS", "PY_TOPIC_TREE_FIX"
    )
    command = command.replace(
        '"check":"rule_refine_operator_contract_install"',
        '"check":"rule_refine_topic_tree_install"',
    )
    target = HERE / "INSTALL-TOPIC-CONDITION-TREE.sh"
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
    enable_builder = load("topic_tree_enable_builder", ORIGINAL / "build_enable.py")
    enable_command = enable_builder.make_command(installed, source)
    (HERE / "ENABLE-TOPIC-FILTER.sh").write_text(enable_command, encoding="utf-8")
    manual_builder = load("topic_tree_manual_builder", HERE / "build_manual.py")
    manual_command = manual_builder.make_command()
    (HERE / "CHECK-TOPIC-REAL-CSV.sh").write_text(manual_command, encoding="utf-8")
    manifest = {
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": packet["source_baseline"],
        "topic_tree_contract_version": CONTRACT,
        "files": {
            name: {key: entry[key] for key in ("old_sha256", "new_sha256")}
            for name, entry in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "command_sha256": BASE.sha(command.encode("utf-8")),
        "enable_command_sha256": BASE.sha(enable_command.encode("utf-8")),
        "manual_command": "CHECK-TOPIC-REAL-CSV.sh",
        "manual_command_sha256": BASE.sha(manual_command.encode("utf-8")),
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    result = build()
    print(json.dumps({"command": str(result), "bytes": result.stat().st_size}))
