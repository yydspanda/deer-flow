"""Exercise the existing background tick with isolated Redis and SQLite materials."""

import copy
import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import base_probe

save, synthetic_data = base_probe.save, base_probe.synthetic_data


class SyntheticRuleSource(base_probe.SyntheticRuleSource):
    def list_rules(self):
        return [copy.deepcopy(self.rule)]


class _RecordedProcessor:
    def __init__(self, processor):
        self.processor, self.outcomes = processor, []

    def __getattr__(self, name):
        return getattr(self.processor, name)

    def process(self, signal):
        result = self.processor.process(signal)
        self.outcomes.append(copy.deepcopy(result))
        return result


def _database(path, rows):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("""CREATE TABLE uas_app_workflow_task_status (
            id TEXT PRIMARY KEY, app_code TEXT, flow_id TEXT, alert_id TEXT,
            status TEXT, created_date TEXT, alert_data JSON, agent_response JSON)""")
        database.executemany(
            "INSERT INTO uas_app_workflow_task_status VALUES (?,?,?,?,?,?,?,?)",
            [(row["id"], row["app_code"], row["flow_id"], row["alert_id"],
              "synthetic", datetime.now(timezone.utc).isoformat(),
              json.dumps(row["alert_data"], ensure_ascii=False), None) for row in rows],
        )


def _audit(analysis):
    transport = analysis.get("model_transport", {})
    return {
        "analysis_status": analysis.get("status"),
        "analysis_error_code": analysis.get("error_code"),
        "model_error_code": transport.get("error_code"),
        "model_error_type": transport.get("error_type"),
        "model_calls": analysis.get("model_calls"),
        **{key: transport.get(key) for key in (
            "sdk_request_calls", "requested_model", "returned_model", "transport_mode",
            "finish_reason", "usage", "elapsed_ms", "upstream_attempts",
        )},
    }


def run_check(directory, *, modules, redis_client, analyze, wrap_processor=lambda p: p, case=None):
    directory = Path(directory).expanduser()
    summary = {
        "check": "rule_refine_synthetic_background", "status": "failed", "mode": "live",
        "material_origin": "synthetic_fixture", "database_scope": "temporary_sqlite",
        "business_database_calls": 0, "database_reads": 0, "analysis_calls": 0,
        "model_calls": 0, "sdk_request_calls": 0, "automatic_apply": False,
        "real_ingress_connected": False, "celery_dispatch_tested": False,
        "background_tick_invocation": "manual_in_process", "counter_namespace_scope": "isolated_synthetic_test",
        "output_dir": str(directory.absolute()), "cleanup_ok": True,
    }
    if directory.is_symlink() or (directory.exists() and (not directory.is_dir() or any(directory.iterdir()))):
        return {**summary, "failure_stage": "output_directory_not_empty_or_invalid"}
    keys, counts, ticks, analysis, evaluation = set(), [], [], {}, {}
    claimed, database_path = False, None
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        directory = directory.resolve()
        os.chmod(directory, 0o700)
        save(directory, "probe-start.json", summary)
        claimed = True
        data = synthetic_data() if case is None else json.loads(modules.analysis._encode(case, 128 * 1024))
        if (not isinstance(data, dict) or set(data) != {"rule", "rows", "payloads"}
            or data["rule"].get("source", {}).get("kind") != "synthetic_fixture"
            or any(not isinstance(data[key], list) or len(data[key]) != 3 for key in ("rows", "payloads"))):
            raise ValueError("Three synthetic rows and requests are required")
        save(directory, "synthetic-input.json", data)
        save(directory, "rule.json", data["rule"])
        database_path = directory / "test-tasks.sqlite"
        _database(database_path, data["rows"])
        reader = modules.task_source.LocalWorkflowTaskSource(database_path)
        rules = SyntheticRuleSource(data["rule"])
        counter = modules.counter.RedisRuleCounter(
            redis_client, mode="live", namespace="synthetic-background-" + uuid4().hex,
            minimum_count=3, transaction_retries=3,
        )
        summary["namespace"] = counter.namespace
        requests = [*data["payloads"], copy.deepcopy(data["payloads"][0])]
        requests[-1]["session_id"] += "-repush"
        observations = []
        for request in requests:
            normalized = modules.ingress.normalize_received_alert(
                request, rules, received_at=datetime.now(timezone.utc), mode="live",
            )
            if normalized["status"] != "ready" or len(normalized["observations"]) != 1:
                raise ValueError("Synthetic observation is invalid")
            observation = normalized["observations"][0]
            keys.update(counter._keys(observation))
            observations.append(observation)
            counts.append(counter.observe(observation))
        save(directory, "observations.json", observations)
        signal = counter.pending(observations[-1])
        summary.update(submitted=len(counts), unique_count=counts[-1]["current_count"],
                       new_signal_count=sum(item["triggered"] for item in counts), duplicate_added=counts[-1]["added"])
        if not (len(keys) == 3 and sum(item["added"] for item in counts) == 3
                and summary["unique_count"] == 3 and summary["new_signal_count"] == 1
                and summary["duplicate_added"] is False and signal is not None
                and counts[2]["pending_signal"]["signal_id"] == counts[3]["pending_signal"]["signal_id"] == signal["signal_id"]
                and len(signal["sample_alert_ids"]) == 3):
            raise ValueError("Synthetic counting assertions failed")
        save(directory, "signal.json", signal)

        def load_rows(current_signal):
            summary["database_reads"] += 1
            return reader.load_task_rows(current_signal)

        def analyze_once(materials):
            if summary["analysis_calls"]:
                raise RuntimeError("Repeated model generation is not allowed")
            summary["analysis_calls"] += 1
            request = modules.analysis.build_analysis_request(materials)
            if request is None:
                raise ValueError("Synthetic materials cannot be analyzed")
            save(directory, "request.json", request)
            save(directory, "output-schema.json", modules.analysis.AnalysisDraft.model_json_schema())
            summary.update(model_calls=None, sdk_request_calls=None)
            result = analyze(copy.deepcopy(materials))
            analysis.update(copy.deepcopy(result))
            return result

        processor = modules.signal_processing.SignalProcessor(
            directory / "processing", load_rules=lambda: SyntheticRuleSource(data["rule"]),
            load_task_rows=load_rows, analyze=analyze_once,
            sink=modules.local_sink.LocalRecommendationSink(directory / "receipts"), source_kind="workflow_task_store",
        )
        tracked = _RecordedProcessor(processor)
        guarded = wrap_processor(tracked)
        def tick():
            return modules.background_tick.run_tick(directory / "scheduler", rules=rules, counter=counter, processor=guarded)
        ticks.append(tick())
        if ticks[0]["status"] == "acknowledged":
            ticks.append(tick())
        save(directory, "ticks.json", ticks)
        save(directory, "processing-outcomes.json", tracked.outcomes)
        first, processed = ticks[0], tracked.outcomes[0] if tracked.outcomes else {}
        summary.update(consumer_status=first["status"], processing_status=processed.get("status"),
                       processing_error_type=processed.get("error_type") or first.get("error_type"),
                       next_tick_status=ticks[1]["status"] if len(ticks) > 1 else None,
                       acknowledged=first.get("acknowledged"), processed_per_tick=[item["processed"] for item in ticks],
                       record_id=processed.get("record_id"))
        if summary["record_id"]:
            analysis = processor.store.load(summary["record_id"])["analysis"]
            delivery = processed["delivery"]
            attempt = processor.store.directory / "attempts" / summary["record_id"] / (delivery["attempt_id"] + ".request.json")
            evaluation = json.loads(attempt.read_text(encoding="utf-8"))["check"].get("evaluation", {})
            summary.update(delivery_status=delivery["status"], check_reason=delivery["check_reason"], sample_match_counts=evaluation.get("counts"))
        if analysis:
            summary.update(_audit(analysis))
        if (first["status"] == "acknowledged" and summary["next_tick_status"] == "idle"
                and summary["processed_per_tick"] == [1, 0] and summary["analysis_calls"] == 1
                and summary["sdk_request_calls"] == 1 and summary["database_reads"] == 1
                and summary.get("analysis_status") in {"proposal", "no_change", "insufficient_evidence", "needs_existing_condition_change"}
                and processed.get("status") in {"received", "not_applicable"}):
            summary["status"] = "passed"
    except Exception as error:
        summary.update(status="failed", error_type=type(error).__name__)
    finally:
        if analysis:
            summary.update(_audit(analysis))
        failed = []
        for key in sorted(keys):
            try:
                redis_client.delete(key)
                if redis_client.exists(key):
                    failed.append(key)
            except Exception:
                failed.append(key)
        summary.update(test_keys_tracked=len(keys), cleanup_ok=not failed)
        if failed:
            summary.update(status="failed", cleanup_failed_keys=failed)
        if database_path is not None:
            try:
                database_path.unlink(missing_ok=True)
                summary["temporary_database_removed"] = True
            except Exception as error:
                summary.update(status="failed", cleanup_ok=False, temporary_database_removed=False, database_cleanup_error=type(error).__name__)
        if claimed:
            try:
                save(directory, "counts.json", counts)
                report = base_probe.report_text(summary, analysis, evaluation).replace("手工调用既有消费函数", "手工调用现有run_tick后台轮询函数；使用独立测试命名空间及临时SQLite")
                save(directory, "REPORT.md", report, text=True)
                save(directory, "summary.json", summary)
            except Exception as error:
                summary.update(status="failed", artifact_error_type=type(error).__name__)
    return summary
