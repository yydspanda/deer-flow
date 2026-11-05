"""Package the operator-contract migration over the reviewed production ZIP."""

import argparse
import base64
import hashlib
import json
import textwrap
import zipfile
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARCHIVE = HERE.parents[2] / "sec_know_model.zip"
BASE_INSTALLER = HERE.parent / "step07_surge_runtime/install.py"
BASELINE_SHA256 = "2a523efcc64df8cf5750ff2721f3a5bfd7658b7c1ba898ca9bee0e53185b4406"
MODULE = "my_workflows/zeus/rule_refine/"
TEST = "test/rule_refine/"
CLIENT = "my_workflows/zeus/flows/disposition_tools/small_rule_sync_client.py"
REGRESSION_TESTS = (
    "test_contracts.py",
    "test_analysis.py",
    "test_filter_evaluation.py",
    "test_numeric_values.py",
    "test_proposal_check.py",
    "test_local_sink.py",
    "test_saved_delivery.py",
    "test_signal_processing.py",
    "test_surge_runtime.py",
    "test_surge_processor.py",
    "test_surge_integration.py",
    "test_surge_review_sampling.py",
    "test_surge_exclusion_scope.py",
    "test_surge_behavior_processor.py",
    "test_surge_field_usage.py",
    "test_surge_event_fields.py",
    "test_surge_event_processor.py",
    "test_surge_repair_first.py",
    "test_surge_review_repair.py",
)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def allowed(relative):
    return (
        relative.endswith(".py")
        and ".." not in Path(relative).parts
        and "__pycache__" not in Path(relative).parts
        and (relative.startswith((MODULE, TEST)) or relative == CLIENT)
    )


def baseline():
    raw = ARCHIVE.read_bytes()
    if sha(raw) != BASELINE_SHA256:
        raise ValueError("production_baseline_zip_changed")
    with zipfile.ZipFile(ARCHIVE) as archive:
        return {
            name: archive.read(name) for name in archive.namelist() if allowed(name)
        }


def collect(stage):
    """Copy only scoped changed sources; never copy the full private application."""
    stage = Path(stage)
    before = baseline()
    payload = HERE / "payload"
    changed = {}
    for path in sorted(stage.rglob("*.py")):
        relative = path.relative_to(stage).as_posix()
        if not allowed(relative):
            continue
        raw = path.read_bytes()
        if raw != before.get(relative):
            compile(raw, relative, "exec")
            changed[relative] = raw
    existing = {
        path.relative_to(payload).as_posix()
        for path in payload.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    if existing - set(changed):
        raise ValueError("stale_payload_requires_review")
    for relative, raw in changed.items():
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
    return sorted(changed)


def contents():
    before = baseline()
    files = {}
    for path in sorted((HERE / "payload").rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts:
            continue
        relative = path.relative_to(HERE / "payload").as_posix()
        if not allowed(relative):
            raise ValueError("unexpected_operator_patch_path")
        raw = path.read_bytes()
        compile(raw, relative, "exec")
        if raw == before.get(relative):
            raise ValueError("unchanged_source_in_patch")
        files[relative] = {
            "old_sha256": sha(before[relative]) if relative in before else None,
            "new_sha256": sha(raw),
            "content": raw.decode("utf-8"),
        }
    if not files or MODULE + "operators.py" not in files:
        raise ValueError("operator_payload_incomplete")
    dependencies = {
        relative: sha(raw)
        for relative, raw in before.items()
        if relative not in files and not relative.startswith(TEST)
    }
    test_files = {
        relative: raw.decode("utf-8")
        for relative, raw in before.items()
        if relative.startswith(TEST) and relative not in files
    }
    tests = sorted(
        {TEST + name for name in REGRESSION_TESTS}
        | {
            name
            for name in files
            if name.startswith(TEST + "test_") and name.endswith(".py")
        }
    )
    return {
        "require_evidence": False,
        "baseline_zip_sha256": BASELINE_SHA256,
        "operator_contract_version": "small-rule-operators-v2",
        "files": files,
        "dependencies": dependencies,
        "test_files": test_files,
        "tests": tests,
    }


def _replace(source, old, new):
    if source.count(old) != 1:
        raise ValueError("installer_template_changed")
    return source.replace(old, new)


def installer_source():
    source = BASE_INSTALLER.read_text(encoding="utf-8")
    source = _replace(
        source, "TEST_TIMEOUT_SECONDS = 180", "TEST_TIMEOUT_SECONDS = 300"
    )
    source = _replace(
        source, "or not 1 <= len(tests) <= 32", "or not 1 <= len(tests) <= 128"
    )
    source = _replace(
        source,
        '"check": "rule_refine_surge_runtime_install",',
        '"check": "rule_refine_operator_contract_install",\n'
        '            "operator_contract_version": "small-rule-operators-v2",\n'
        '            "source_baseline_sha256": packet["baseline_zip_sha256"],\n'
        '            "configuration_files_modified": False,\n'
        '            "services_started": False,\n'
        '            "zeus_callback_calls": 0,',
    )
    source = source.replace(
        ".rule-refine-surge-runtime-install.lock", ".rule-refine-operator-install.lock"
    )
    source = source.replace(
        'prefix="surge-runtime-install-"', 'prefix="operator-contract-install-"'
    )
    source = _replace(
        source, '"limits.py": 0,', '"operators.py": -1,\n            "limits.py": 0,'
    )
    scope_check = """def validate_packet(packet):
    # This installer may update only the reviewed operator consumers and tests.
    if packet.get("baseline_zip_sha256") != "__BASELINE__":
        raise InstallError("unexpected_operator_baseline")
    for group in ("files", "dependencies", "test_files"):
        for relative in packet.get(group, {}):
            if not relative.endswith(".py") or not (
                relative.startswith("my_workflows/zeus/rule_refine/")
                or relative.startswith("test/rule_refine/")
                or relative == "my_workflows/zeus/flows/disposition_tools/small_rule_sync_client.py"
            ):
                raise InstallError("unexpected_operator_patch_path")
""".replace("__BASELINE__", BASELINE_SHA256)
    source = _replace(source, "def validate_packet(packet):\n", scope_check)
    compile(source, "<operator-installer>", "exec")
    return source


def runtime_source(packet):
    raw = json.dumps(
        {"packet": packet, "installer": installer_source()},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    encoded = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    return (
        '''import base64, hashlib, json, os, sys, types, zlib
from pathlib import Path
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode("""__PAYLOAD__"""), __SIZE__ + 1)
if (len(raw) != __SIZE__ or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__SHA__"):
    raise SystemExit("操作符更新包校验失败，项目未修改。")
bundle = json.loads(raw)
installer = types.ModuleType("operator_contract_installer")
sys.modules[installer.__name__] = installer
exec(compile(bundle["installer"], "<operator-installer>", "exec"), installer.__dict__)
data_root = Path(os.environ.get("SEC_RULE_REFINE_DATA_ROOT", str(Path.home() / "sec-rule-refine-data")))
try:
    result = installer.apply(Path.cwd(), data_root, bundle["packet"])
except Exception as error:
    result = {"check":"rule_refine_operator_contract_install", "status":"failed",
              "error_type":type(error).__name__, "model_calls":0, "redis_calls":0,
              "business_database_calls":0, "automatic_restart":False}
    if isinstance(error, installer.InstallError):
        result.update(error_code=str(error), file=error.relative_file, actual_sha256=error.actual_sha256)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps(result, ensure_ascii=False, indent=2))
'''.replace("__PAYLOAD__", encoded)
        .replace("__SIZE__", str(len(raw)))
        .replace("__SHA__", sha(raw))
    )


def build():
    packet = contents()
    command = (
        "#!/usr/bin/env bash\nset -euo pipefail\numask 077\n"
        'cd "${SEC_KNOW_MODEL_ROOT:-$HOME/sec_know_model}"\n'
        'echo "统一规则操作符和提示词；先执行离线回归，通过后备份并更新源码，不启动服务。"\n'
        ".venv/bin/python -B - <<'PY_OPERATOR_CONTRACT'\n"
        + runtime_source(packet)
        + "PY_OPERATOR_CONTRACT\n"
    )
    target = HERE / "INSTALL-OPERATORS.sh"
    target.write_text(command, encoding="utf-8")
    target.chmod(0o755)
    summary = {
        "baseline_zip_sha256": BASELINE_SHA256,
        "operator_contract_version": packet["operator_contract_version"],
        "files": {
            name: {key: entry[key] for key in ("old_sha256", "new_sha256")}
            for name, entry in packet["files"].items()
        },
        "regression_test_files": packet["tests"],
        "installer_sha256": sha(command.encode("utf-8")),
    }
    (HERE / "manifest.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    return command


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage", type=Path, help="Collect changed reviewed sources before building"
    )
    args = parser.parse_args()
    if args.stage:
        collect(args.stage)
    build()
