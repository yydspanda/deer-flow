"""Operator-facing batch projection and recovery over durable execution history."""

import hashlib
import json
from collections import Counter, defaultdict
from copy import copy
from datetime import UTC, datetime

from soc_agent.contracts.corpus_experiments import Batch, CorpusBatchRetryCommand, CorpusRoundCreateCommand, CorpusRoundRetryCommand, CorpusRoundSelection
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.demo.corpus_experiment_timing import item_timing
from soc_agent.demo.corpus_experiments import _eligible, _require_admin, corpus_attempt_count, semantic_failed, semantic_retry_key
from soc_agent.utils.hashing import stable_hash

MEMORY_CONTINUATION_REASONS = {"memory_governance_changed"}


def batch_selection(batch: Batch):
    return CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else "all")


def result_row(service, round_, member, job, sequence_number):
    outcome = (job.result_payload or {}) if job else {}
    return {
        "alert_id": member.alert_id,
        "group_id": member.group_id,
        "rule_code": member.rule_code,
        "detection_key": member.detection_key,
        "payload_hash": member.payload_hash,
        "event_time": getattr(member, "event_time", getattr(member, "event_time_utc", None)),
        "validation_tier": member.validation_tier,
        "membership_reason": member.reason,
        "status": job.status.value if job else "remaining",
        "run_id": job.run_id if job else None,
        "job_id": job.job_id if job else None,
        "attempt_count": job.attempt_count if job else 0,
        "total_attempt_count": corpus_attempt_count(job) if job else 0,
        "job_timing": item_timing(round_, sequence_number=sequence_number, created_at=job.created_at, started_at=job.started_at, completed_at=job.completed_at) if job else None,
        "candidate_id": outcome.get("candidate_id"),
        "observation_id": outcome.get("observation_id"),
        "pattern_reason": outcome.get("pattern_reason"),
        "summary": outcome.get("summary", {}),
        "attempt_measurements": [
            measurement
            for key in ([job.idempotency_key, *(semantic_retry_key(job.idempotency_key, generation) for generation in range(1, job.metadata.get("semantic_retry", {}).get("generation", 0) + 1))] if job else [])
            for measurement in service.repository.journaled_run_measurements(
                alert_id=member.alert_id,
                input_hash=member.payload_hash,
                idempotency_key_hash=hashlib.sha256(json.dumps(key, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest(),
            )
        ],
        "snapshot_changed_during_run": outcome.get("snapshot_changed_during_run", False),
        "error_code": job.error_code if job else None,
        "error_message": job.error_message if job else None,
        "source_round_id": round_.round_id if round_ else None,
    }


def continue_memory_round(service, round_id, *, context, alert_ids=None, concurrency=None, semantic=False, automatic=False):
    """Create a fresh snapshot only for current pending/affected members; never rewrite history."""
    _require_admin(context)
    with service.repository.mutation_transaction() as tx:
        tx.lock_memory_governance()
        bound = copy(service)
        bound.repository, bound.store, bound.jobs = tx, tx.corpus_experiments(), tx.processing_jobs()
        parent = bound._round(round_id)
        if automatic and (
            parent.state != "blocked"
            or parent.state_reason not in MEMORY_CONTINUATION_REASONS
            or not parent.state_history
            or parent.state_history[-1].get("from") != "running"
            or parent.retest_provenance
            or parent.memory_continuation_depth >= 3
        ):
            return None
        if parent.superseded_by_round_id:
            raise CorpusExperimentConflict("该任务已被全部重跑替换，请刷新当前批次")
        reason = bound.snapshot_problem(parent)
        if reason not in MEMORY_CONTINUATION_REASONS:
            raise CorpusExperimentConflict(f"{reason or parent.state_reason or 'snapshot changed'}; 请重新配置并全部重跑")
        if bound.store.round_progress(round_id).active_count:
            if automatic:
                return None
            raise CorpusExperimentConflict("等待进行中的告警保存结果后再继续")
        if parent.state != "blocked":
            bound._block(round_id, reason)
            parent = bound._round(round_id)
        selected = set(alert_ids) if alert_ids is not None else None
        pending = bound.store.memory_continuation_alert_ids(parent, selected)
        previous_jobs = {}
        for offset in range(0, len(pending), 500):
            previous_jobs.update(bound.store.round_jobs_for_alerts(parent.round_id, pending[offset : offset + 500]))
        pending = [alert_id for alert_id in pending if corpus_attempt_count(previous_jobs[alert_id]) < 3]
        if not pending:
            return None
        # No reviewed records is a valid continuation after an operator revoked
        # the last one: fresh analysis proceeds without revoked Memory.
        usable = any(_eligible(record, datetime.now(UTC)) for record in bound.store.learning_memory_records(parent.experiment_id))
        options = parent.options.model_copy(update={"refresh_normalization": True}) if semantic else parent.options
        continuation_id = "ROUND-CONT-" + stable_hash([parent.round_id, pending, options.model_dump(), bound.store.governance_hash(bound.store.get_experiment(parent.experiment_id).tenant_id)])[:24].upper()
        child = bound.create_round(
            CorpusRoundCreateCommand(
                experiment_id=parent.experiment_id,
                selection=parent.selection.model_copy(update={"alert_ids": pending}),
                purpose=parent.purpose,
                options=options,
                memory_mode="snapshot" if parent.selection.batch == "validation" and usable else "none",
                parent_round_id=parent.round_id,
                concurrency=concurrency or parent.concurrency,
                execution_limit=2_147_483_647,
            ),
            context=context,
            round_identity=continuation_id,
            memory_continuation_depth=parent.memory_continuation_depth + 1,
        )
        bound.start(child.round_id, context=context)
        return child


class CorpusBatchOperations:
    def __init__(self, service, plan):
        self.service = service
        self.plan = plan

    def experiment_id(self):
        existing = self.service.store.find_plan_experiment(self.plan.plan_id)
        return existing.experiment_id if existing else "EXP-QUICK-" + self.plan.plan_id[:32]

    def _projection(self, batch):
        experiment_id = self.experiment_id()
        experiment = self.service.store.get_experiment(experiment_id)
        rows = self.service.store.batch_result_manifest(experiment_id, batch_selection(batch))
        if experiment is None:
            rows = [{"alert_id": member.alert_id, "job_id": None, "status": None, "round_id": None} for member in getattr(self.plan, "members", []) if member.batch == batch]
        sources = {row["round_id"] for row in rows if row["round_id"]}
        rounds = {key: self.service.store.get_round(key) for key in sorted(sources)}
        token = stable_hash(
            {
                "experiment": experiment.model_dump(mode="json") if experiment else None,
                "batch": batch,
                "generation": self.service.store.current_learning_generation(experiment_id),
                "rounds": {key: value.model_dump(mode="json") for key, value in rounds.items()},
                "items": rows,
            }
        )
        return experiment_id, experiment, rows, rounds, token

    def snapshot(self, batch: Batch, *, include_sources=False):
        experiment_id, experiment, rows, rounds, token = self._projection(batch)
        counts = Counter(row["status"] or "remaining" for row in rows)
        running, blocked_reason = self.service.store.batch_control_state(experiment_id, batch)
        active = sum(counts[key] for key in ("claimed", "prechecking", "analyzing", "projecting"))
        total = len(rows)
        learning = self.service.store.list_members(experiment_id, selection=batch_selection("learning"), limit=1).total
        validation = self.service.store.list_members(experiment_id, selection=batch_selection("validation"), limit=1).total
        if experiment is None:
            learning = sum(member.batch == "learning" for member in getattr(self.plan, "members", []))
            validation = sum(member.batch == "validation" for member in getattr(self.plan, "members", []))
        source_rounds = [
            round_.model_dump(mode="json")
            if include_sources
            else {
                "round_id": round_.round_id,
                "state": round_.state,
                "options": round_.options.model_dump(mode="json"),
                "config_hash": round_.config_hash,
                "learning_generation": round_.learning_generation,
                "frozen_memory_count": len(round_.memory_snapshot),
            }
            for round_ in rounds.values()
        ]
        metadata = experiment.model_dump(mode="json", exclude={"rebalance": {"fact_hashes", "learning_run_hashes", "learning_run_imports", "inherited_memory_ids"}}) if experiment else None
        result = {
            "schema_version": "soc.corpus_batch_status.v1",
            "batch": batch,
            "experiment_id": experiment_id,
            "plan_id": self.plan.plan_id,
            "learning_generation": self.service.store.current_learning_generation(experiment_id),
            "experiment": metadata,
            "source_identity": experiment.source_identity if experiment else getattr(self.plan, "source_identity", {}),
            "report_token": token,
            "report_mode": "memory_applicability",
            "total": total,
            "completed": counts["completed"],
            "failed": counts["failed"],
            "active": active,
            "remaining": counts["remaining"] + counts["queued"],
            "counts": dict(counts),
            "running": running,
            "blocked_reason": blocked_reason,
            "semantic_failed_count": sum(
                row["status"] == "completed"
                and (row.get("semantic_status") == "failed" or (row.get("semantic_status") is None and any(step.get("phase") == "normalization_assist" and step.get("status") == "failed" for step in row.get("phase_timings") or [])))
                for row in rows
            ),
            "memory_recovery_exhausted_count": sum(bool(row.get("snapshot_changed")) and row.get("attempt_count", 0) + (row.get("inherited_attempt_count") or 0) >= 3 for row in rows),
            "source_rounds": source_rounds,
            "stage_scope": {
                "batch_total": total,
                "learning_total": learning,
                "validation_total": validation,
                "stage_complete": total > 0 and counts["completed"] == total and not any(row.get("snapshot_changed") for row in rows),
            },
        }
        if getattr(self.plan, "schema_version", None) == "soc.corpus_group_time_split.v3":
            result["sampling_policy"] = {key: getattr(self.plan, key) for key in ("schema_version", "grouping_basis", "split_method", "max_learning_per_group")}
        return result

    def results(self, batch: Batch, *, offset=0, limit=100, report_token=None):
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("invalid batch result pagination")
        experiment_id, experiment, rows, rounds, token = self._projection(batch)
        if report_token is not None and token != report_token:
            raise CorpusExperimentConflict("批次结果已变化，请重新导出")
        items = []
        selected_rows = self.service.store.batch_result_jobs(experiment_id, batch_selection(batch), offset=offset, limit=limit)
        if experiment is None:
            selected_rows = [(member, None, 0) for member in getattr(self.plan, "members", []) if member.batch == batch][offset : offset + limit]
        for member, job, sequence in selected_rows:
            round_ = rounds[job.input_payload["round_id"]] if job else None
            items.append(result_row(self.service, round_, member, job, sequence))
        if self._projection(batch)[-1] != token:
            raise CorpusExperimentConflict("批次结果在读取期间发生变化，请重新导出")
        return {"total": len(rows), "offset": offset, "items": items, "report_token": token, "source_identity": experiment.source_identity if experiment else {}, "experiment_id": experiment_id, "batch": batch}

    def retry(self, batch: Batch, command: CorpusBatchRetryCommand, *, context):
        _require_admin(context)
        if command.concurrency is not None and command.concurrency > self.service.capacity.max_concurrency:
            raise ValueError("concurrency exceeds the saved server limit")
        concurrency = command.concurrency or self.service.capacity.max_concurrency
        with self.service.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            bound = copy(self.service)
            bound.repository, bound.store, bound.jobs = tx, tx.corpus_experiments(), tx.processing_jobs()
            experiment_id = self.experiment_id()
            if bound.store.batch_has_active_jobs(experiment_id, batch) or bound.store.batch_control_state(experiment_id, batch)[0]:
                raise CorpusExperimentConflict("请先暂停当前批次，并等待运行中的告警结束，再重试失败")
            groups = defaultdict(list)
            skipped = 0
            for member, job in bound.store.batch_jobs(experiment_id, batch_selection(batch)):
                if job and ((command.kind == "failed" and job.status.value == "failed") or (command.kind == "semantic_failed" and job.status.value == "completed" and semantic_failed(job))):
                    if corpus_attempt_count(job) >= 3 or (command.kind == "failed" and not (job.result_payload or {}).get("retryable")) or (command.kind == "semantic_failed" and not job.run_id):
                        skipped += 1
                        continue
                    groups[job.input_payload["round_id"]].append(member.alert_id)
            retried, sources = 0, []
            for round_id, alert_ids in groups.items():
                round_ = bound._round(round_id)
                if command.kind == "semantic_failed" and (round_.purpose == "facts" or round_.options.normalization_review_mode != "apply"):
                    raise ValueError("语义失败补跑需要该任务已开启语义核对")
                if bound.snapshot_problem(round_) in MEMORY_CONTINUATION_REASONS:
                    child = continue_memory_round(bound, round_id, context=context, alert_ids=alert_ids, concurrency=concurrency, semantic=command.kind == "semantic_failed")
                    if child:
                        retried += len(child.selection.alert_ids)
                        sources.append(child.round_id)
                else:
                    result = bound.retry_items(round_id, CorpusRoundRetryCommand(kind=command.kind, alert_ids=alert_ids, concurrency=concurrency, expected_round_version=round_.version), context=context)
                    retried += result["retried"]
                    skipped += result["not_retryable"]
                    sources.append(round_id)
            return {"batch": batch, "experiment_id": experiment_id, "retried": retried, "not_retryable": skipped, "source_round_ids": sources}
