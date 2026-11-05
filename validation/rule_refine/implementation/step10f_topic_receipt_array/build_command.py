"""Accept single-item batch receipts without resending or rewriting old deliveries."""

import importlib.util
import json
from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step10e_topic_advisory"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PREV = load("topic_receipt_previous_builder", PREVIOUS / "build_command.py")
BASE, COMPACT = PREV.BASE, PREV.COMPACT
MODULE, TEST = PREV.MODULE, PREV.TEST
CONTRACT = "single-rule-batch-receipt-v1"
EXPECTED_RUNTIME = {MODULE + "topic_delivery.py"}


@lru_cache(maxsize=1)
def _baseline_snapshot():
    before = PREV.baseline()
    before.update(
        {
            name: entry["content"].encode("utf-8")
            for name, entry in PREV.contents()["files"].items()
        }
    )
    return tuple(before.items())


def baseline():
    return dict(_baseline_snapshot())


def contents():
    before = baseline()
    files = {}
    for path in sorted((HERE / "payload").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        name = path.relative_to(HERE / "payload").as_posix()
        if (
            name not in EXPECTED_RUNTIME
            and name != TEST + "test_topic_receipt_array.py"
        ):
            raise ValueError("unexpected_topic_receipt_patch_path")
        raw = path.read_bytes()
        compile(raw, name, "exec")
        files[name] = {
            "old_sha256": BASE.sha(before[name]) if name in before else None,
            "new_sha256": BASE.sha(raw),
            "content": raw.decode("utf-8"),
        }
    if set(files) != EXPECTED_RUNTIME | {TEST + "test_topic_receipt_array.py"}:
        raise ValueError("topic_receipt_payload_missing")
    packet = PREV.contents()
    packet.update(
        source_baseline="step10e_topic_advisory_installed_sources",
        topic_receipt_contract_version=CONTRACT,
        delivery_profile="topic_single_receipt_incremental",
        files=files,
        dependencies={
            n: BASE.sha(raw)
            for n, raw in before.items()
            if n not in files and not n.startswith(TEST)
        },
        test_files={
            n: raw.decode("utf-8")
            for n, raw in before.items()
            if n.startswith(TEST) and n not in files
        },
        tests=sorted(set(packet["tests"]) | {TEST + "test_topic_receipt_array.py"}),
    )
    return packet


def installer_source():
    source = PREV.installer_source()
    source = BASE._replace(
        source,
        "def validate_packet(packet):\n",
        "def validate_packet(packet):\n"
        f'    if packet.get("topic_receipt_contract_version") != "{CONTRACT}":\n'
        '        raise InstallError("unexpected_topic_receipt_contract")\n',
    )
    source = source.replace(
        "rule_refine_topic_advisory_install", "rule_refine_topic_receipt_install"
    ).replace("topic-advisory-install-", "topic-receipt-install-")
    source = BASE._replace(
        source,
        f'"topic_advisory_prompt_version": "{PREV.PROMPT_VERSION}",',
        f'"topic_advisory_prompt_version": "{PREV.PROMPT_VERSION}",\n'
        f'            "topic_receipt_contract_version": "{CONTRACT}",',
    )
    compile(source, "<topic-receipt-installer>", "exec")
    return source


def build():
    packet, before = contents(), baseline()
    source = installer_source()
    command = COMPACT.packtext(COMPACT.make_bundle(packet, before, source))
    command = command.replace("SOC_OPERATORS", "SOC_TOPIC_RECEIPT_FIX").replace(
        "PY_OPERATORS", "PY_TOPIC_RECEIPT_FIX"
    )
    command = command.replace(
        '"check":"rule_refine_operator_contract_install"',
        '"check":"rule_refine_topic_receipt_install"',
    )
    target = HERE / "INSTALL-TOPIC-RECEIPT-FIX.sh"
    target.write_text(command, encoding="utf-8")
    installed = {
        name: BASE.sha(raw) for name, raw in before.items() if not name.startswith(TEST)
    }
    installed.update(
        {
            name: item["new_sha256"]
            for name, item in packet["files"].items()
            if not name.startswith(TEST)
        }
    )
    enable_builder = load(
        "topic_receipt_enable_builder", PREV.PREV.ORIGINAL / "build_enable.py"
    )
    enable = enable_builder.make_command(installed, source)
    (HERE / "ENABLE-TOPIC-FILTER.sh").write_text(enable, encoding="utf-8")
    manifest = {
        "source_baseline": packet["source_baseline"],
        "topic_receipt_contract_version": CONTRACT,
        "files": {
            name: {k: item[k] for k in ("old_sha256", "new_sha256")}
            for name, item in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "command_sha256": BASE.sha(command.encode()),
        "enable_command_sha256": BASE.sha(enable.encode()),
        "replays_previous_job": False,
        "model_calls": 0,
        "zeus_posts": 0,
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    build()
