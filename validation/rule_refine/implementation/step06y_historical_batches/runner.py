"""Prepare all historical requests, confirm once, then execute sequentially."""

import copy
import hashlib
import io
import json
import os
import sys
import tempfile
import warnings
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import batch_materials
import batch_report
import context_budget
import historical_adapter
import historical_materials
import historical_review
import inventory
import single_runner


CONFIG_FILES = (
    "config/rule_refine.analysis.local.json",
    "config/rule_refine.limits.local.json",
)


def _hash_file(path, *, optional=False):
    try:
        return hashlib.sha256(inventory.bounded_read(path, 1024 * 1024)).hexdigest()
    except FileNotFoundError:
        if optional:
            return None
        raise


def configuration_snapshot(root, pins):
    result = {name: _hash_file(root / name) for name in pins}
    for name in CONFIG_FILES:
        result[name] = _hash_file(
            root / name, optional=name.endswith("limits.local.json")
        )
    return result


def runtime(root, loaded, settings):
    """Resolve existing LOCAL alias and capacity policy without a model call."""
    model = settings.get("model", "DeepSeek_V32")
    if (
        not isinstance(model, str)
        or not 1 <= len(model) <= 128
        or model != model.strip()
        or not model.isprintable()
    ):
        raise ValueError("invalid_model")
    sys.path.insert(0, str(root))
    import util.root_config as rc
    from nas.config import APP_KEY_DICT
    from config.model_limits import get_model_policy

    endpoint = urlsplit(rc.LITELLM_OPENAI_SERVER)
    if (
        rc.ENV != "LOCAL"
        or endpoint.scheme not in {"http", "https"}
        or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
        or endpoint.username
        or endpoint.password
        or endpoint.query
        or endpoint.fragment
    ):
        raise ValueError("local_model_proxy_required")
    policy = get_model_policy(model)
    key = APP_KEY_DICT.get("litellm_key")
    if policy is None or not isinstance(key, str) or not key:
        raise ValueError("model_configuration_missing")
    return SimpleNamespace(
        model=model,
        key=key,
        endpoint=rc.LITELLM_OPENAI_SERVER,
        rc=rc,
        policy=policy,
    )


def prepare(root, pins):
    if any(
        os.environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        raise ValueError("local_only")
    snapshot = configuration_snapshot(root, pins)
    loaded = single_runner.modules(root, pins)
    configured_limits = loaded.limits.get_limits(root)
    limits = context_budget.from_limits(configured_limits)
    context_budget.install_compatibility(loaded)
    loaded.model_adapter = historical_adapter
    path = root / "nas/original/full_alert_2026_month_forth_sample_200.pkl"
    raw = inventory.bounded_read(path, 64 * 1024 * 1024)
    if hashlib.sha256(raw).hexdigest() != historical_materials.SAMPLE_SHA:
        raise ValueError("historical_file_changed")
    csv = Path.home() / "sec-rule-refine-data/rules.csv"
    if (
        hashlib.sha256(inventory.bounded_read(csv, 16 * 1024 * 1024)).hexdigest()
        != historical_materials.CSV_SHA
    ):
        raise ValueError("csv_snapshot_changed")
    rules = loaded.csv_config_source.CsvRuleConfigSource(csv)
    if rules.file_sha256 != historical_materials.CSV_SHA:
        raise ValueError("csv_changed_while_reading")
    import pandas as pd

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        frame = inventory.RestrictedDataFrameUnpickler(io.BytesIO(raw)).load()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("dataframe_required")
    plan = batch_materials.build_plan(
        frame, rules, inventory, analysis=loaded.analysis, limits=limits
    )
    if not plan["batches"]:
        raise single_runner.NoUsableSamples(plan["coverage"])
    settings = json.loads(
        inventory.bounded_read(root / CONFIG_FILES[0], 16384),
        object_pairs_hook=loaded.analysis._unique_object,
    )
    if not isinstance(settings, dict):
        raise ValueError("analysis_configuration_invalid")
    selected = runtime(root, loaded, settings)
    batches = []
    for index, context in enumerate(plan["batches"], 1):
        if context.get("batch_index") != index:
            raise ValueError("batch_index_mismatch")
        batch_report.identities(context)
        request = historical_review.build_request(
            context, loaded.analysis, limits=limits
        )
        batches.append(
            SimpleNamespace(
                **vars(selected),
                context=copy.deepcopy(context),
                request=request,
                loaded=loaded,
                limits=limits,
                csv=csv,
            )
        )
    prepared = SimpleNamespace(
        root=root,
        pins=pins,
        snapshot=snapshot,
        plan=plan,
        batches=batches,
        loaded=loaded,
        limits=limits,
        configured_limits=asdict(configured_limits),
        model=selected.model,
        csv=csv,
    )
    verify_configuration(prepared)
    return prepared


def verify_configuration(prepared):
    if configuration_snapshot(prepared.root, prepared.pins) != prepared.snapshot:
        raise ValueError("configuration_changed")
    if any(
        os.environ.get(key, "LOCAL").upper() != "LOCAL"
        for key in ("env_profile", "ENV_PROFILE")
    ):
        raise ValueError("configuration_changed")
    rule = prepared.plan["batches"][0]["rule"]
    fresh = prepared.loaded.csv_config_source.CsvRuleConfigSource(
        prepared.csv
    ).get_rule(
        rule["rule_code"], topic=rule["topic"], company_code=rule["company_code"]
    )
    if any(fresh.get(key) != value for key, value in rule.items()):
        raise ValueError("rule_configuration_changed")


def _atomic_save(directory, name, value):
    """Only progress files in this newly-created run directory are replaced."""
    raw = (
        value
        if isinstance(value, bytes)
        else json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")
    )
    fd, temporary = tempfile.mkstemp(prefix=".progress-", dir=directory)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, directory / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def stage(prepared, directory, tool_hashes):
    """Save every frozen request and its lineage before the first SDK invocation."""
    p = prepared
    for name, value in (
        ("plan.json", p.plan),
        ("coverage.json", p.plan["coverage"]),
        ("source-pins.json", p.pins),
        ("configuration-hashes.json", p.snapshot),
        ("tool-source-hashes.json", tool_hashes),
        ("limits.json", asdict(p.limits)),
        ("context-budget.json", p.limits.audit()),
        ("configured-limits.json", getattr(p, "configured_limits", {})),
        ("output-schema.json", p.loaded.analysis.AnalysisDraft.model_json_schema()),
    ):
        single_runner.save(directory, name, value)
    outcomes = []
    for index, batch in enumerate(p.batches, 1):
        child = directory / f"batch-{index:03d}"
        child.mkdir(mode=0o700)
        outcome = {
            "batch_index": index,
            "status": "not_started",
            "analysis_status": "not_started",
            "execution_started": False,
            "execution_finished": False,
            "sdk_request_calls": 0,
            "response_received": False,
            "input_bytes": batch.request["input_bytes"],
            "estimated_input_tokens": batch.request["estimated_input_tokens"],
            "input_token_budget": batch.request["input_token_budget"],
            "prompt_sha256": batch.request["prompt_sha256"],
            "directory": child.name,
        }
        outcomes.append(outcome)
        for name, value in (
            ("context.json", batch.context),
            ("request.json", batch.request),
            ("coverage.json", batch.context["coverage"]),
            (
                "source.json",
                {
                    "source": p.plan.get("source"),
                    "source_pins": p.pins,
                    "tool_hashes": tool_hashes,
                },
            ),
            (
                "audit.json",
                {
                    "batch_index": index,
                    "requested_model": batch.model,
                    "limits": asdict(batch.limits),
                    "prompt_sha256": batch.request["prompt_sha256"],
                    "configuration_hashes": p.snapshot,
                    "max_sdk_calls": 1,
                },
            ),
            ("outcome.json", outcome),
        ):
            single_runner.save(child, name, value)
    return outcomes


def execute_all(prepared, directory, *, tool_hashes=None, execute=None, verify=None):
    p = prepared
    execute = execute or single_runner.execute
    verify = verify or verify_configuration
    outcomes = stage(p, directory, tool_hashes or {})
    analyses = [{} for _ in p.batches]

    def publish(reason=None):
        summary = batch_report.summarize(
            p.plan,
            outcomes,
            model=p.model,
            limits=asdict(p.limits),
            output_dir=directory,
            stop_reason=reason,
        )
        summary["context_budget"] = p.limits.audit()
        _atomic_save(directory, "summary.json", summary)
        _atomic_save(
            directory,
            "REPORT.md",
            batch_report.report_text(p.plan, summary, analyses).encode("utf-8"),
        )
        return summary

    publish()
    stop_reason = None
    for index, batch in enumerate(p.batches):
        outcome = outcomes[index]
        child = directory / outcome["directory"]
        try:
            verify(p)
            # Refuse even an in-memory alteration of the confirmed frozen request.
            current = historical_review.build_request(
                batch.context, p.loaded.analysis, limits=p.limits
            )
            if (
                current != batch.request
                or batch.model != p.model
                or batch.limits != p.limits
            ):
                raise ValueError("frozen_request_changed")
        except Exception as error:
            outcome.update(
                status="blocked",
                error_type=type(error).__name__,
                error_code="pre_request_configuration_check_failed",
            )
            stop_reason = "pre_request_configuration_check_failed"
            _atomic_save(child, "outcome.json", outcome)
            break
        print(
            f"开始第 {index + 1}/{len(p.batches)} 批；本批最多1次SDK请求。", flush=True
        )
        outcome.update(execution_started=True)
        _atomic_save(child, "outcome.json", outcome)
        publish()
        try:
            result = execute(batch, child)
            outcome.update(result)
            outcome.update(
                batch_index=index + 1,
                directory=child.name,
                execution_started=True,
                execution_finished=True,
            )
            if outcome.get("sdk_request_calls") not in (0, 1, None):
                outcome.update(
                    status="blocked", error_code="unexpected_sdk_request_count"
                )
            if outcome["status"] in batch_report.VALID and (
                outcome.get("sdk_request_calls") != 1
                or outcome.get("format_valid") is not True
                or outcome.get("configuration_unchanged") is not True
            ):
                outcome.update(status="blocked", error_code="inconsistent_batch_result")
            path = child / "analysis.json"
            if path.exists():
                analyses[index] = json.loads(
                    inventory.bounded_read(path, p.loaded.limits.RECORD_BYTES)
                )
            try:
                verify(p)
            except Exception as error:
                outcome.update(
                    status="blocked",
                    error_code="post_request_configuration_check_failed",
                    error_type=type(error).__name__,
                )
        except (Exception, KeyboardInterrupt) as error:
            unknown = (child / "request-started.json").exists()
            calls = (
                outcome.get("sdk_request_calls")
                if outcome.get("execution_finished")
                else None
                if unknown
                else 0
            )
            outcome.update(
                status="interrupted"
                if isinstance(error, KeyboardInterrupt)
                else "failed",
                error_type=type(error).__name__,
                sdk_request_calls=calls,
                error_code="batch_execution_interrupted"
                if isinstance(error, KeyboardInterrupt)
                else "batch_execution_failed",
            )
        _atomic_save(child, "outcome.json", outcome)
        if outcome["status"] not in batch_report.VALID:
            stop_reason = outcome.get("error_code") or "batch_check_failed"
            break
        publish()
    return publish(stop_reason)


def terminal_summary(summary):
    return {
        "check": summary["check"],
        "status": summary["status"],
        "model": summary["requested_model"],
        "source": {
            key: summary["source_coverage"].get(key)
            for key in (
                "file_rows",
                "inspected_rows",
                "matching_unique_alerts",
                "eligible_unique_alerts",
                "eligible_logs",
                "invalid_rows",
                "invalid_logs",
                "excluded_conflicting_target_alerts",
            )
        },
        "planned_coverage": summary["planned_coverage"],
        "valid_result_coverage": summary["valid_result_coverage"],
        "all_planned_batches_valid": summary["all_planned_batches_valid"],
        "batch_counts": summary["batch_counts"],
        "context_budget": summary["context_budget"],
        "planned_estimated_input_tokens": summary["planned_estimated_input_tokens"],
        "reported_prompt_tokens": summary["reported_prompt_tokens"],
        "prompt_usage_missing_batches": summary["prompt_usage_missing_batches"],
        "sdk_request_calls": summary["sdk_request_calls"],
        "unknown_request_batches": summary["unknown_request_batches"],
        "stop_reason": summary["stop_reason"],
        "output_dir": summary["output_dir"],
    }


def main(pins, *, tool_hashes=None):
    try:
        p = prepare(Path.cwd(), pins)
    except Exception as error:
        safe_codes = {
            "local_only",
            "historical_file_changed",
            "csv_snapshot_changed",
            "csv_changed_while_reading",
            "dataframe_required",
            "invalid_model",
            "local_model_proxy_required",
            "model_configuration_missing",
            "analysis_configuration_invalid",
            "configuration_changed",
            "rule_configuration_changed",
            "no_usable_samples_within_budget",
        }
        report = {
            "check": "rule_refine_historical_batches",
            "status": "preparation_stopped",
            "error_type": type(error).__name__,
            "error_code": getattr(error, "code", "preparation_check_failed"),
            "coverage": getattr(error, "coverage", None),
            "model_calls": 0,
        }
        if str(error) in safe_codes:
            report["error_code"] = str(error)
        if isinstance(error, single_runner.ModuleChanged):
            report.update(
                error_code="module_changed",
                file=error.filename,
                actual_sha256=error.actual,
            )
        details = getattr(error, "details", None)
        if isinstance(details, dict):
            report["details"] = {
                key: details[key]
                for key in (
                    "required_input_bytes",
                    "required_estimated_tokens",
                    "input_token_budget",
                    "estimated_input_tokens",
                )
                if key in details
            }
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
        return 1
    print(
        json.dumps(
            {
                "status": "ready",
                "rule_code": p.plan["batches"][0]["rule"]["rule_code"],
                "model": p.model,
                "coverage": {
                    key: p.plan["coverage"].get(key)
                    for key in (
                        "file_rows",
                        "inspected_rows",
                        "matching_unique_alerts",
                        "eligible_unique_alerts",
                        "eligible_logs",
                        "invalid_rows",
                        "invalid_logs",
                        "excluded_conflicting_target_alerts",
                        "all_eligible_logs_planned",
                        "complete_source_coverage",
                    )
                },
                "planned_sdk_requests": len(p.batches),
                "total_input_bytes": sum(
                    batch.request["input_bytes"] for batch in p.batches
                ),
                "max_batch_input_bytes": max(
                    batch.request["input_bytes"] for batch in p.batches
                ),
                "batch_input_bytes": [
                    batch.request["input_bytes"] for batch in p.batches
                ],
                "context_budget": p.limits.audit(),
                "batch_estimated_input_tokens": [
                    batch.request["estimated_input_tokens"] for batch in p.batches
                ],
                "model_output_token_limit": None,
                "timeout_seconds": p.limits.timeout_seconds,
                "source_scope": "all_rows_of_this_small_file",
                "automatic_apply": False,
                "resume_supported": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )
    print(
        "本次按用户假定的1M上下文使用80万输入token估算预算；"
        "估算不是精确分词数，模型实际用量会另行记录。"
        f"将顺序分析以上全部批次，最多{len(p.batches)}次SDK请求；代理内部可能重试。"
        "本工具遇失败即停，不自动重试；重新执行会新建目录并重新分析全部批次。"
        "仅使用历史样本；不提交告警、不回调Zeus、不改规则。",
        flush=True,
    )
    print("确认执行全部计划批次请输入 LIVE，其他输入退出：", end="", flush=True)
    with open("/dev/tty", "r", encoding="utf-8") as terminal:
        if terminal.readline().strip() != "LIVE":
            print("已取消；未调用模型。")
            return 0
    parent = Path.home() / "sec-rule-refine-data"
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="historical-rule-batches-", dir=parent))
    print("本次报告目录：" + str(directory), flush=True)
    summary = execute_all(p, directory, tool_hashes=tool_hashes)
    print(
        json.dumps(terminal_summary(summary), ensure_ascii=False, indent=2), flush=True
    )
    return (
        0
        if summary["status"] == "passed"
        else 130
        if summary["status"] == "interrupted"
        else 1
    )
