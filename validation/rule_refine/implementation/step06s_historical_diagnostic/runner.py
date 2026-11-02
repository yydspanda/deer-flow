"""Offline diagnosis of saved step6R artifacts; no application imports or clients."""

import hashlib
import io
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path
from types import ModuleType, SimpleNamespace
from uuid import uuid4

import historical_checks
import historical_materials
import historical_review
import inventory
import log_audit

PURE_MODULES = (
    "contracts",
    "rule_config_source",
    "materials",
    "analysis",
    "filter_evaluation",
    "csv_config_source",
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path, limit=256 * 1024):
    return json.loads(
        inventory.bounded_read(path, limit),
        object_pairs_hook=historical_materials._unique,
        parse_constant=historical_materials._constant,
    )


def load_pure(sources, recorded_pins):
    """Use exactly the reviewed historical sources, independent of installed code."""
    if set(sources) != set(PURE_MODULES):
        raise ValueError("diagnostic_sources_mismatch")
    for name in PURE_MODULES:
        relative = "my_workflows/zeus/rule_refine/" + name + ".py"
        if digest(sources[name].encode("utf-8")) != recorded_pins.get(relative):
            raise ValueError("recorded_source_version_mismatch")
    package = ModuleType("_historical_diagnostic_" + uuid4().hex)
    package.__path__ = []
    sys.modules[package.__name__] = package
    loaded = {}
    for name in PURE_MODULES:
        module = ModuleType(package.__name__ + "." + name)
        module.__package__ = package.__name__
        sys.modules[module.__name__] = module
        exec(compile(sources[name], "<reviewed-" + name + ">", "exec"), module.__dict__)
        loaded[name] = module
    return SimpleNamespace(**loaded)


def saved_response_check(folder, sources):
    pins = read_json(folder / "source-pins.json")
    loaded = load_pure(sources, pins)
    context = read_json(folder / "context.json")
    request = read_json(folder / "request.json")
    previous = read_json(folder / "analysis.json")
    raw = inventory.bounded_read(folder / "assistant-content.txt", 16384)
    if previous.get("response_sha256") != digest(raw):
        raise ValueError("saved_response_hash_mismatch")
    expected = historical_review.build_request(context, loaded.analysis)
    if (
        request != expected
        or previous.get("prompt_sha256") != expected["prompt_sha256"]
    ):
        raise ValueError("saved_prompt_mismatch")
    diagnostic = historical_checks.diagnose(
        context,
        raw.decode("utf-8"),
        analysis=loaded.analysis,
        evaluator=loaded.filter_evaluation.evaluate_filter,
        review=historical_review,
    )
    diagnostic.update(
        response_sha256=digest(raw),
        prompt_sha256=expected["prompt_sha256"],
        source_versions_verified=True,
        saved_result_reproduced=(
            diagnostic["format_valid"] == previous.get("format_valid")
            and diagnostic["analysis_status"] == previous.get("status")
        ),
    )
    return diagnostic, context, loaded


def check_log_coverage(root, csv_path, context, loaded):
    raw = inventory.bounded_read(
        root / "nas/original" / historical_materials.SAMPLE, 64 * 1024 * 1024
    )
    if digest(raw) != historical_materials.SAMPLE_SHA:
        raise ValueError("historical_file_changed")
    if (
        digest(inventory.bounded_read(csv_path, 16 * 1024 * 1024))
        != historical_materials.CSV_SHA
    ):
        raise ValueError("csv_snapshot_changed")
    rules = loaded.csv_config_source.CsvRuleConfigSource(csv_path)
    if rules.file_sha256 != historical_materials.CSV_SHA:
        raise ValueError("csv_snapshot_changed")
    import pandas as pd

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        frame = inventory.RestrictedDataFrameUnpickler(io.BytesIO(raw)).load()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("dataframe_required")
    rebuilt = historical_materials.build_context(frame, rules, inventory)
    if rebuilt != context:
        raise ValueError("saved_context_not_reproduced")
    result = log_audit.audit_logs(frame, historical_materials, inventory)
    result.update(status="checked", saved_context_reproduced=True)
    result["limits"]["model_selection_recomputed"] = True
    result["model_visible_alerts"] = rebuilt["coverage"]["included_alerts"]
    result["model_visible_logs"] = rebuilt["coverage"]["included_logs"]
    result["eligible_alerts_not_selected"] = rebuilt["coverage"]["omitted_alerts"]
    return result


SAFE_CODES = {
    "diagnostic_sources_mismatch",
    "recorded_source_version_mismatch",
    "saved_response_hash_mismatch",
    "saved_prompt_mismatch",
    "diagnostic_validator_disagreement",
    "historical_file_changed",
    "csv_snapshot_changed",
    "dataframe_required",
    "saved_context_not_reproduced",
}


def stop_reason(error):
    return {
        "status": "unavailable",
        "error_type": type(error).__name__,
        "error_code": str(error)
        if str(error) in SAFE_CODES
        else "local_evidence_check_failed",
    }


def main(sources):
    report = Path(sys.argv[1]).expanduser().resolve()
    summary = {
        "check": "historical_rule_review_diagnostic",
        "execution_mode": "offline_replay",
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "business_alerts_submitted": 0,
        "automatic_apply": False,
        "application_code_updated": False,
        "original_report_unchanged": True,
    }
    try:
        diagnostic, context, loaded = saved_response_check(report, sources)
        summary["response_check"] = diagnostic
    except Exception as error:
        summary.update(status="diagnostic_stopped", response_check=stop_reason(error))
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    try:
        summary["log_coverage_check"] = check_log_coverage(
            Path.cwd(), Path.home() / "sec-rule-refine-data/rules.csv", context, loaded
        )
    except Exception as error:
        # Preserve the useful response diagnosis even if the PKL is no longer here.
        summary["log_coverage_check"] = stop_reason(error)
    summary["status"] = "diagnosed"
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix="historical-review-diagnostic-", dir=parent))
    summary["output_dir"] = str(output)
    fd = os.open(
        output / "diagnostic.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(summary, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
