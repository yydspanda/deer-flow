"""Quick validation uses the existing immutable rounds and shared job transactions."""

from collections import Counter
from copy import copy
from datetime import UTC, datetime

from soc_agent.contracts import ServiceRequestContext
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import Batch, CorpusQuickCommand, CorpusRound, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.demo.corpus_batches import CorpusBatchPlan
from soc_agent.demo.corpus_experiments import SocCorpusExperimentService, _eligible, _require_admin, _require_saved_options
from soc_agent.demo.corpus_round_comparison import job_result_row
from soc_agent.utils.hashing import stable_hash


class CorpusQuickValidation:
    def __init__(self, service: SocCorpusExperimentService, plan: CorpusBatchPlan):
        self.service = service
        self.plan = plan

    def experiment(self) -> str:
        existing = self.service.store.find_plan_experiment(self.plan.plan_id)
        return existing.experiment_id if existing else "EXP-QUICK-" + self.plan.plan_id[:32]

    def selection(self, batch: Batch, scope: str = "all", alert_id: str | None = None) -> CorpusRoundSelection:
        return CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else scope, alert_ids=[alert_id] if alert_id else [])

    def snapshot(self, batch: Batch, scope: str = "all", *, offset: int = 0, limit: int = 20, alert_ids: list[str] | None = None) -> dict:
        experiment_id = self.experiment()
        selection = self.selection(batch, scope)
        counts = Counter(self.service.store.batch_counts(experiment_id, selection))
        if alert_ids is not None and len(alert_ids) > 100:
            raise ValueError("at most 100 visible alert IDs")
        if alert_ids is not None:
            selection = selection.model_copy(update={"alert_ids": alert_ids})
        visible = self.service.store.batch_jobs(experiment_id, selection, offset=offset, limit=100 if alert_ids else limit)
        if self.service.store.get_experiment(experiment_id) is None:
            members = [m for m in self.plan.members if m.batch == batch and (batch == "learning" or scope == "all" or m.validation_tier == ("main" if scope == "reuse" else "supplementary"))]
            counts = Counter(remaining=len(members))
            visible = [(m, None) for m in members if m.alert_id in alert_ids] if alert_ids else [(m, None) for m in members[offset : offset + limit]]
        running, blocked_reason = self.service.store.batch_control_state(experiment_id, batch)
        items = []
        round_states = {}
        for member, job in visible:
            blocked = None
            if job:
                round_id = job.input_payload["round_id"]
                if round_id not in round_states:
                    round_states[round_id] = self.service.store.get_round(round_id)
                round_ = round_states[round_id]
                if round_ and round_.state == "blocked":
                    blocked = round_.state_reason or "configuration_changed"
            result = job_result_row(job) if job else {"status": "remaining", "job_id": None, "run_id": None, "summary": {}}
            items.append(
                {**result, "alert_id": member.alert_id, "manual_dispatch": bool(job and job.metadata.get("manual_dispatch")), "blocked_reason": blocked, "candidate_id": (job.result_payload or {}).get("candidate_id") if job else None}
            )
        active = sum(n for status, n in counts.items() if status in {"claimed", "prechecking", "analyzing", "projecting"})
        return {
            "experiment_id": experiment_id,
            "batch": batch,
            "scope": scope,
            "total": sum(counts.values()),
            "completed": counts["completed"],
            "failed": counts["failed"],
            "active": active,
            "remaining": counts["remaining"] + counts["queued"],
            "running": running,
            "manual_pending": self.service.store.has_manual_work(),
            "blocked_reason": blocked_reason,
            "pending_candidates": self.service.store.list_learning_candidates(experiment_id, limit=1).total,
            "items": items,
            **({"restart_token": self._restart_token(experiment_id)} if batch == "validation" else {}),
        }

    def _restart_token(self, experiment_id: str) -> str:
        latest = self.service.store.latest_round_identity(experiment_id, "validation")
        return stable_hash(["corpus-validation-restart-v1", experiment_id, latest[0] if latest else None])

    def _rounds(self, experiment_id: str, batch: Batch) -> list[CorpusRound]:
        result, offset = [], 0
        while True:
            page = self.service.store.list_rounds(experiment_id=experiment_id, limit=100, offset=offset)
            result.extend(r for r in page if r.selection.batch == batch)
            if len(page) < 100:
                return result
            offset += len(page)

    def command(self, command: CorpusQuickCommand, *, context: ServiceRequestContext, allow_options_override: bool = True, default_options: SocAnalysisExecutionOptions | None = None) -> dict:
        _require_admin(context)
        # Clone only the facade, binding every nested prepare/start/job mutation to
        # one DB transaction. This fences concurrent browser tabs and Gateway workers.
        try:
            with self.service.repository.mutation_transaction() as tx:
                tx.lock_memory_governance()
                bound = copy(self.service)
                bound.repository, bound.store, bound.jobs = tx, tx.corpus_experiments(), tx.processing_jobs()
                quick = CorpusQuickValidation(bound, self.plan)
                if command.action == "restart_validation":
                    return quick._restart_validation(command, context=context, allow_options_override=allow_options_override, default_options=default_options)
                if command.action != "pause" and not allow_options_override:
                    _require_saved_options(bound.store, quick.experiment(), command.batch, command.options, default_options)
                return quick._command(command, context=context)
        except CorpusExperimentConflict:
            if command.action == "restart_validation":
                # Rejected restarts are inert. In particular a stale browser token
                # cannot block, pause or otherwise change newer accepted work.
                raise
            # Failed admission rolls back all new work. Record configuration blocks
            # separately so a paused queue explains why explicit rerun is required.
            for round_ in self._rounds(self.experiment(), command.batch):
                if round_.state in {"prepared", "paused", "running"}:
                    problem = self.service.snapshot_problem(round_)
                    if problem:
                        self.service._block(round_.round_id, problem)
            raise

    def _restart_validation(self, command: CorpusQuickCommand, *, context: ServiceRequestContext, allow_options_override: bool, default_options: SocAnalysisExecutionOptions | None) -> dict:
        svc = self.service
        experiment_id = self.experiment()
        if context.idempotency_key != f"restart-validation-{command.restart_token}":
            raise ValueError("全部重跑需要与 restart_token 绑定的 Idempotency-Key")
        round_id = "ROUND-RESTART-" + stable_hash([experiment_id, command.restart_token])[:24].upper()
        existing = svc.store.get_round(round_id)
        if existing:
            if existing.experiment_id != experiment_id or existing.selection != self.selection("validation", "all") or existing.options != command.options:
                raise CorpusExperimentConflict("本次全部重跑已使用另一组配置提交，请刷新后再操作")
            return {"accepted": True}
        if command.restart_token != self._restart_token(experiment_id):
            raise CorpusExperimentConflict("第二批已有新的提交，请刷新后再重新配置并全部重跑")
        if not allow_options_override:
            _require_saved_options(svc.store, experiment_id, "validation", command.options, default_options)
        if svc.store.batch_has_active_jobs(experiment_id, "validation"):
            raise CorpusExperimentConflict("请先暂停第二批，并等待运行中的告警结束后再全部重跑")
        if svc.store.get_experiment(experiment_id) is None:
            svc.prepare(self.plan, experiment_id=experiment_id, name="告警快速验证", context=context)
        selection = self.selection("validation", "all")
        if not svc.store.list_members(experiment_id, selection=selection, limit=1).total:
            raise ValueError("当前第二批没有可验证的告警")
        usable = any(_eligible(record, datetime.now(UTC)) for record in svc.store.learning_memory_records(experiment_id))
        if not usable and svc.store.list_members(experiment_id, selection=self.selection("validation", "reuse"), limit=1).total:
            raise ValueError("请先点击“审核经验”，确认并开放第一批经验后再验证经验复用")
        # Keep every result, job, frozen option and Memory reference. Only dispatch
        # admission is retired, atomically with the replacement queue creation.
        for round_ in self._rounds(experiment_id, "validation"):
            svc.store.supersede_validation_round(round_.round_id, replacement_round_id=round_id, actor_id=context.actor.actor_id)
        svc.store.clear_batch_manual_intents(experiment_id, "validation", replacement_round_id=round_id)
        round_ = svc.create_round(
            CorpusRoundCreateCommand(
                experiment_id=experiment_id,
                selection=selection,
                options=command.options,
                purpose="full_flow" if command.options.tenant_policy_enabled else "memory",
                memory_mode="snapshot" if usable else "none",
                execution_limit=2_147_483_647,
                concurrency=svc._max_concurrency,
            ),
            context=context,
            round_identity=round_id,
        )
        svc.start(round_.round_id, context=context)
        return {"accepted": True}

    def _command(self, command: CorpusQuickCommand, *, context: ServiceRequestContext) -> dict:
        svc = self.service
        experiment_id = self.experiment()
        if svc.store.get_experiment(experiment_id) is None:
            svc.prepare(self.plan, experiment_id=experiment_id, name="告警快速验证", context=context)
        rounds = self._rounds(experiment_id, command.batch)
        if command.action == "pause":
            for round_ in rounds:
                if round_.state == "running":
                    svc.pause(round_.round_id, context=context)
            return {"accepted": True}
        selection = self.selection(command.batch, command.scope, command.alert_id)
        # Single alerts are independent of the current second-batch browser scope.
        if command.alert_id:
            selection = self.selection(command.batch, "all", command.alert_id)
        rows = svc.store.batch_jobs(experiment_id, selection)
        if not rows:
            raise ValueError("当前批次没有所选告警")
        if command.action == "rerun":
            if not context.idempotency_key:
                raise ValueError("重新运行需要 Idempotency-Key")
            round_id = "ROUND-" + stable_hash([context.actor.actor_id, context.idempotency_key])[:24].upper()
            existing = svc.store.get_round(round_id)
            if existing:
                if existing.experiment_id != experiment_id or existing.selection.alert_ids != [command.alert_id] or existing.options != command.options:
                    raise CorpusExperimentConflict("idempotency key already belongs to another command")
                return {"accepted": True}
        new_members = []
        start_rounds = set()
        for member, job in rows:
            if job and not job.status.is_terminal:
                if command.action == "rerun" and job.status.value == "queued" and svc.store.get_round(job.input_payload["round_id"]).state == "blocked":
                    new_members.append(member)
                    continue
                round_id = job.input_payload["round_id"]
                if command.alert_id:
                    svc.request_manual(round_id, member.alert_id, context=context)
                else:
                    start_rounds.add(round_id)
                continue
            if job and command.action != "rerun":
                continue  # Completed and failed work is retained; retry is explicit.
            new_members.append(member)
        for round_id in start_rounds:
            selected_ids = [m.alert_id for m, j in rows if j and j.input_payload["round_id"] == round_id]
            svc.store.set_dispatch_scope(round_id, selected_ids)
            # Capacity follows deployment settings; saved behavioral options stay fixed.
            svc.start(round_id, context=context, execution_limit=2_147_483_647, concurrency=svc._max_concurrency)
        if new_members:
            usable = any(_eligible(r, datetime.now(UTC)) for r in svc.store.learning_memory_records(experiment_id))
            if command.batch == "validation" and not usable and any(m.validation_tier == "main" for m in new_members):
                raise ValueError("请先点击“审核经验”，确认并开放第一批经验后再验证经验复用")
            # Fixed membership excludes previously submitted work across all rounds.
            round_ = svc.create_round(
                CorpusRoundCreateCommand(
                    experiment_id=experiment_id,
                    selection=self.selection(command.batch, "all").model_copy(update={"alert_ids": [m.alert_id for m in new_members]}),
                    options=command.options,
                    purpose="full_flow" if command.options.tenant_policy_enabled else "memory",
                    memory_mode="snapshot" if command.batch == "validation" and usable else "none",
                    execution_limit=2_147_483_647,
                    concurrency=svc._max_concurrency,
                ),
                context=context,
            )
            if command.alert_id:
                svc.request_manual(round_.round_id, command.alert_id, context=context)
            else:
                svc.start(round_.round_id, context=context)
        return {"accepted": True}
