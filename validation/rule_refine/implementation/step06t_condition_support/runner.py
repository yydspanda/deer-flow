"""Inspect saved condition support and preview a larger complete-message budget."""

import io
import json
import os
import sys
import tempfile
import warnings
from pathlib import Path

import budget_preview
import condition_support
import diagnostic_runner
import historical_materials
import inventory


def preview(root, csv_path, original_context, loaded, material_source, max_input_kib):
    raw = inventory.bounded_read(
        root / "nas/original" / historical_materials.SAMPLE, 64 * 1024 * 1024
    )
    if diagnostic_runner.digest(raw) != historical_materials.SAMPLE_SHA:
        raise ValueError("historical_file_changed")
    if (
        diagnostic_runner.digest(inventory.bounded_read(csv_path, 16 * 1024 * 1024))
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
    if historical_materials.build_context(frame, rules, inventory) != original_context:
        raise ValueError("saved_context_not_reproduced")
    context, summary = budget_preview.build_preview(
        frame,
        rules,
        inventory,
        material_source,
        analysis=loaded.analysis,
        review=diagnostic_runner.historical_review,
        max_input_kib=max_input_kib,
    )
    request = (
        budget_preview.build_request(
            context, loaded.analysis, diagnostic_runner.historical_review, max_input_kib
        )
        if context["samples"]
        else None
    )
    return context, request, summary


def private_json(folder, name, value):
    fd = os.open(folder / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def main(packet):
    summary = {
        "check": "historical_condition_support_and_budget_preview",
        "execution_mode": "offline",
        "model_calls": 0,
        "redis_calls": 0,
        "database_calls": 0,
        "business_alerts_submitted": 0,
        "application_code_updated": False,
        "original_report_unchanged": True,
        "automatic_apply": False,
    }
    try:
        folder = Path(sys.argv[1]).expanduser().resolve()
        max_input_kib = int(sys.argv[2])
        if not 64 <= max_input_kib <= 512:
            raise ValueError("input_budget_out_of_range")
        old_check, context, loaded = diagnostic_runner.saved_response_check(
            folder, packet["sources"]
        )
        if not old_check["saved_result_reproduced"]:
            raise ValueError("saved_result_not_reproduced")
        raw_answer = inventory.bounded_read(folder / "assistant-content.txt", 16384)
        if diagnostic_runner.digest(raw_answer) != old_check["response_sha256"]:
            raise ValueError("saved_response_hash_mismatch")
        answer = raw_answer.decode("utf-8")
        value = json.loads(
            loaded.analysis._json_document(answer),
            object_pairs_hook=loaded.analysis._unique_object,
            parse_constant=loaded.analysis._invalid_constant,
        )
        draft = loaded.analysis.AnalysisDraft.model_validate(value)
        summary.update(
            response_sha256=old_check["response_sha256"],
            prompt_sha256=old_check["prompt_sha256"],
            original_failure_stage=old_check["validation_stage"],
            condition_support=condition_support.explain(
                context, draft, loaded.analysis
            ),
        )
    except Exception as error:
        summary.update(
            status="diagnostic_stopped", failure=diagnostic_runner.stop_reason(error)
        )
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1
    new_context = request = None
    try:
        new_context, request, summary["budget_preview"] = preview(
            Path.cwd(),
            Path.home() / "sec-rule-refine-data/rules.csv",
            context,
            loaded,
            packet["files"]["historical_materials"],
            max_input_kib,
        )
    except Exception as error:
        summary["budget_preview"] = diagnostic_runner.stop_reason(error)
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(exist_ok=True, mode=0o700)
    output = Path(tempfile.mkdtemp(prefix="historical-conditions-", dir=parent))
    summary.update(status="diagnosed", output_dir=str(output))
    if new_context is not None and request is not None:
        private_json(output, "preview-context.json", new_context)
        private_json(output, "preview-request.json", request)
    private_json(output, "diagnostic.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0
