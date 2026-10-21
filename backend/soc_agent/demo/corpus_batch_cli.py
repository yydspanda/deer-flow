"""Batch-facing commands; durable execution identities remain in exported evidence."""

import time
from collections import Counter, defaultdict
from datetime import UTC, datetime

from soc_agent.demo.corpus_experiment_reports import export_report


def compact_status(state):
    return {
        "batch": state["batch"],
        "selected_count": state["total"],
        "completed_count": state["completed"],
        "failed_count": state["failed"],
        "active_count": state["active"],
        "remaining_count": state["remaining"],
        "semantic_failed_count": state.get("semantic_failed_count", 0),
        "memory_recovery_exhausted_count": state.get("memory_recovery_exhausted_count", 0),
        "running": state["running"],
        "blocked_reason": state.get("blocked_reason"),
    }


def watch_batch(request, emit, batch, *, watch=False):
    previous = None
    while True:
        state = request("GET", f"/batches/{batch}")
        display = compact_status(state)
        if display != previous:
            emit(display)
            previous = display
        if not watch or (not state["running"] and not state["active"]):
            return state
        time.sleep(2)


def read_batch_results(request, batch):
    first = request("GET", f"/batches/{batch}", params={"include_sources": "true"})
    if first["running"] or first["active"]:
        raise ValueError("请先按批次 pause，等待 active_count=0，再导出报告")
    token = first["report_token"]
    offset, rows = 0, []
    while True:
        page = request("GET", f"/batches/{batch}/results", params={"offset": offset, "limit": 100, "report_token": token})
        if page.get("report_token") != token or page["total"] != first["total"]:
            raise ValueError("导出期间批次结果发生变化，未写入报告，请重试")
        rows.extend(page["items"])
        offset += len(page["items"])
        if offset >= first["total"]:
            break
        if not page["items"]:
            raise ValueError("批次报告分页提前结束，未写入报告")
    if len(rows) != first["total"] or len({row["alert_id"] for row in rows}) != len(rows):
        raise ValueError("批次报告成员重复或缺失，未写入报告")
    final = request("GET", f"/batches/{batch}")
    if final["report_token"] != token or final["running"] or final["active"]:
        raise ValueError("导出期间批次结果发生变化，未写入报告，请重试")
    return first, rows


def export_batch(request, batch, output):
    first, rows = read_batch_results(request, batch)
    report = {
        "schema_version": "soc.corpus_batch_report.v1",
        "batch": batch,
        "experiment_id": first["experiment_id"],
        "exported_at": datetime.now(UTC).isoformat(),
        "report_token": first["report_token"],
        "source_rounds": first["source_rounds"],
        "source_identity": first["source_identity"],
        "progress": {
            "selected_count": first["total"],
            "completed_count": first["completed"],
            "failed_count": first["failed"],
            "active_count": first["active"],
            "counts": dict(Counter(row["status"] for row in rows)),
        },
        "rows": rows,
        **{key: first[key] for key in ("experiment", "stage_scope", "report_mode", "sampling_policy") if key in first},
    }
    export_report(report, output.expanduser().resolve())


def accumulate_batch(request, emit, batch, *, dry_run):
    state, rows = read_batch_results(request, batch)
    sources = {source["round_id"]: source for source in state["source_rounds"]}
    groups = defaultdict(list)
    for row in rows:
        if row["status"] == "completed" and row.get("run_id"):
            groups[row["source_round_id"]].append(row)
    counts, reasons, processed = Counter(), Counter(), 0
    for source_id, members in groups.items():
        for offset in range(0, len(members), 100):
            current = request("GET", f"/batches/{batch}")
            if current["report_token"] != state["report_token"] or current["running"] or current["active"]:
                raise ValueError("当前批次结果已变化，接续已停止；再次执行不会重复增加同一告警的样本数")
            chunk = members[offset : offset + 100]
            result = request(
                "POST",
                f"/rounds/{source_id}/accumulate",
                params={"dry_run": dry_run},
                json={"expected_round_version": sources[source_id]["version"], "source_runs": {row["alert_id"]: row["run_id"] for row in chunk}},
            )
            processed += len(chunk)
            counts.update(result["counts"])
            reasons.update(item.get("reason", "unknown") for item in result.get("items", []) if item.get("status") == "skipped")
            emit({"batch": batch, "dry_run": dry_run, "processed": processed, "counts": dict(counts), "skipped_reasons": dict(reasons), "model_calls": 0})
    if not groups:
        emit({"batch": batch, "processed": 0, "counts": {}, "model_calls": 0})


def dispatch_batch(args, request, emit):
    batch, command = args.batch, args.command
    if command == "status":
        watch_batch(request, emit, batch, watch=args.watch)
    elif command == "export":
        export_batch(request, batch, args.output_dir)
        print(f"报告已保存：{args.output_dir}", flush=True)
    elif command == "accumulate":
        accumulate_batch(request, emit, batch, dry_run=args.dry_run)
    elif command in {"retry", "retry-failed"}:
        if getattr(args, "alert", []):
            raise ValueError("--batch 补跑当前批次该类失败；定点诊断请使用 --round --alert")
        body = {"kind": args.kind if command == "retry" else "failed"}
        if getattr(args, "concurrency", None) is not None:
            body["concurrency"] = args.concurrency
        result = request("POST", f"/batches/{batch}/retry", json=body)
        emit({key: value for key, value in result.items() if key not in {"rounds", "source_rounds", "round_ids", "source_round_ids"}})
    elif command == "pause":
        request("POST", "/quick-validation", json={"batch": batch, "scope": "all", "action": "pause"})
        emit({"batch": batch, "action": "pause", "message": "已请求暂停；status --watch 可等待进行中的告警完成。"})
    elif command == "resume":
        if args.limit is not None or args.restore_full_scope:
            raise ValueError("--batch 会接续当前批次剩余任务，不接受 --limit 或 --restore-full-scope")
        if args.concurrency is not None:
            request("POST", "/experiments/concurrency", json={"max_concurrency": args.concurrency})
        config = request("GET", "/experiments/configuration", params={"batch": batch})
        options = config.get("saved_options") or config["defaults"]
        request("POST", "/quick-validation", json={"batch": batch, "scope": "all", "action": "start", "options": options})
        emit({"batch": batch, "action": "resume", "message": "已提交继续，沿用当前批次保存设置。"})
    else:
        raise ValueError(f"不支持的批次操作：{command}")
