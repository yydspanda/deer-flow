"""Package a small complete-output fix over the installed step09 sources."""

import importlib.util
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
PREVIOUS = HERE.parent / "step09_operator_contract"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = load("complete_output_base_builder", PREVIOUS / "build_command.py")
COMPACT = load("complete_output_compact", PREVIOUS / "compact_command.py")
MODULE, TEST = BASE.MODULE, BASE.TEST


def baseline():
    before = BASE.baseline()
    prior = COMPACT.inline_packet(BASE.contents())
    before.update(
        {name: item["content"].encode("utf-8") for name, item in prior["files"].items()}
    )
    return before


def contents():
    before = baseline()
    prior = COMPACT.inline_packet(BASE.contents())
    files = {}
    for path in sorted((HERE / "payload").rglob("*.py")):
        name = path.relative_to(HERE / "payload").as_posix()
        if "__pycache__" in path.parts:
            continue
        if not name.startswith((MODULE, TEST)) or ".." in Path(name).parts:
            raise ValueError("unexpected_complete_output_path")
        raw = path.read_bytes()
        compile(raw, name, "exec")
        if raw == before.get(name):
            raise ValueError("unchanged_complete_output_file")
        files[name] = {
            "old_sha256": BASE.sha(before[name]) if name in before else None,
            "new_sha256": BASE.sha(raw),
            "content": raw.decode("utf-8"),
        }
    expected = {MODULE + "surge_ai_review.py", MODULE + "surge_processor.py"}
    if {name for name in files if not name.startswith(TEST)} != expected:
        raise ValueError("complete_output_runtime_scope_mismatch")
    tests = sorted(
        set(prior["tests"])
        | {name for name in files if name.startswith(TEST + "test_")}
    )
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": "step09_inline_installed_sources",
        "operator_contract_version": "small-rule-operators-v2",
        "output_contract_version": "complete-advisory-json-v1",
        "delivery_profile": "complete_output_incremental",
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
    source = BASE.installer_source()
    source = source.replace(
        "rule_refine_operator_contract_install", "rule_refine_complete_output_install"
    ).replace("operator-contract-install-", "complete-output-install-")
    source = source.replace(
        '"operator_contract_version": "small-rule-operators-v2",',
        '"operator_contract_version": "small-rule-operators-v2",\n'
        '            "output_contract_version": "complete-advisory-json-v1",',
    )
    # Use the same lock as step09 so concurrent installers cannot race.
    compile(source, "<complete-output-installer>", "exec")
    return source


def build():
    packet = contents()
    bundle = COMPACT.make_bundle(packet, baseline(), installer_source())
    command = (
        COMPACT.packtext(bundle)
        .replace("SOC_OPERATORS", "SOC_COMPLETE_OUTPUT")
        .replace("PY_OPERATORS", "PY_COMPLETE_OUTPUT")
    )
    command = command.replace(
        '"check":"rule_refine_operator_contract_install"',
        '"check":"rule_refine_complete_output_install"',
    )
    target = HERE / "FIX-COMPLETE-OUTPUT.sh"
    target.write_text(command, encoding="utf-8")
    real_csv = (
        HERE.parent / "step07b_real_csv_acceptance/CHECK-REAL-CSV.sh"
    ).read_text(encoding="utf-8")
    old_cd = 'cd "$HOME/sec_know_model"'
    if real_csv.count(old_cd) != 1:
        raise ValueError("real_csv_project_directory_changed")
    real_csv = real_csv.replace(
        old_cd, 'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"'
    )
    combined = (
        "bash <<'SOC_FIX_OUTPUT_AND_RETEST'\nset -euo pipefail\n"
        + command
        + 'echo "修复安装完成。接着用原真实CSV新建一次生成与复核；预览后输入LIVE才调用模型。"\n'
        + real_csv
        + "SOC_FIX_OUTPUT_AND_RETEST\n"
    )
    combined_path = HERE / "FIX-AND-RETEST.sh"
    combined_path.write_text(combined, encoding="utf-8")
    manifest = {
        "baseline_zip_sha256": BASE.BASELINE_SHA256,
        "source_baseline": packet["source_baseline"],
        "output_contract_version": packet["output_contract_version"],
        "files": {
            name: {key: item[key] for key in ("old_sha256", "new_sha256")}
            for name, item in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "command_sha256": BASE.sha(command.encode("utf-8")),
        "combined_command_sha256": BASE.sha(combined.encode("utf-8")),
    }
    (HERE / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return target


if __name__ == "__main__":
    path = build()
    print(json.dumps({"command": str(path), "bytes": path.stat().st_size}))
