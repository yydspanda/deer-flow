"""Explicitly continue Pattern accumulation from fixed, already-completed Runs."""

from collections import Counter

from soc_agent.contracts import AnalysisRunStatus, ProcessingJobStatus
from soc_agent.contracts.corpus_experiments import CorpusPatternBackfillCommand, CorpusRoundSelection
from soc_agent.core.access_control import require_actor_roles
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.demo.corpus_experiment_runtime import accumulate_corpus_run
from soc_agent.demo.corpus_experiments import continuous_accumulation_scope
from soc_agent.utils.hashing import stable_hash


class SocCorpusPatternContinuationService:
    """No Runtime dispatch, report mutation, candidate approval, or model calls."""

    def __init__(self, *, coordinator, profiles):
        self._coordinator = coordinator
        self._profiles = profiles

    def accumulate(self, round_id: str, command: CorpusPatternBackfillCommand, *, context, dry_run: bool = False) -> dict:
        require_actor_roles(context, {"soc_admin"}, operation="continuing corpus Pattern accumulation")
        with self._coordinator.repository.mutation_transaction() as repository:
            repository.lock_memory_governance()
            store = repository.corpus_experiments()
            round_ = store.get_round(round_id)
            if round_ is None:
                raise ValueError("round not found")
            experiment = store.get_experiment(round_.experiment_id)
            if experiment is None:
                raise CorpusExperimentConflict("round experiment no longer exists")
            if round_.version != command.expected_round_version:
                raise CorpusExperimentConflict("轮次 version 已变化，请刷新后重新提交接续积累")
            if round_.purpose == "facts":
                raise ValueError("事实预处理轮次没有研判结论，不能接续经验积累")
            if round_.learning_generation != store.current_learning_generation(round_.experiment_id):
                raise CorpusExperimentConflict("旧学习代次已被重新配置重跑替代，不能接续到当前代次")
            self._require_quiescent(store, round_.experiment_id)
            jobs = store.round_jobs_for_alerts(round_id, list(command.source_runs))
            if set(jobs) != set(command.source_runs):
                raise ValueError("来源告警不是指定轮次成员 (member)")
            current = {
                member.alert_id: (member, job) for member, job in store.batch_jobs(round_.experiment_id, CorpusRoundSelection(batch=round_.selection.batch, scope=round_.selection.scope, alert_ids=list(command.source_runs)), limit=100)
            }
            admitted, items = [], []
            for alert_id, run_id in command.source_runs.items():
                job = jobs[alert_id]
                member, latest = current.get(alert_id, (None, None))
                reason = None
                if member is None or latest is None or latest.job_id != job.job_id:
                    reason = "newer_batch_result_exists"
                elif job.status != ProcessingJobStatus.COMPLETED or job.run_id != run_id:
                    reason = "source_result_not_current"
                if reason:
                    items.append({"alert_id": alert_id, "run_id": run_id, "status": "skipped", "reason": reason})
                    continue
                run = repository.get_run(run_id)
                if run is None or run.alert_id != alert_id or run.input_hash != member.payload_hash or stable_hash(run.input_payload) != member.payload_hash:
                    raise CorpusExperimentConflict("saved Run identity does not match the fixed member")
                if run.status not in {AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW}:
                    raise CorpusExperimentConflict("saved Run is not a completed analysis result")
                tenants = [source.tenant_id for source in (run.normalized_alert, run.llm_analysis_request) if source is not None]
                if not tenants or any(tenant != experiment.tenant_id for tenant in tenants):
                    raise CorpusExperimentConflict("saved Run tenant does not match the experiment")
                admitted.append((member, run))
            projected_round = round_.model_copy(update={"pattern_accumulation_scope": continuous_accumulation_scope(store, round_.experiment_id, round_.learning_generation)})
            for member, run in admitted:
                item = {"alert_id": member.alert_id, "run_id": run.run_id}
                if dry_run:
                    items.append({**item, "status": "eligible"})
                    continue
                outcome = accumulate_corpus_run(repository=repository, profiles=self._profiles, round_=projected_round, member=member, run=run, context=context)
                items.append(
                    {
                        **item,
                        "status": "accumulated" if outcome.observation_id else "skipped",
                        "reason": outcome.pattern_reason,
                        "observation_id": outcome.observation_id,
                        "candidate_id": outcome.candidate_id,
                        "support_count": outcome.summary.get("pattern_support_count"),
                    }
                )
            return {"round_id": round_id, "experiment_id": round_.experiment_id, "dry_run": dry_run, "counts": dict(Counter(item["status"] for item in items)), "items": items, "model_calls": 0}

    @staticmethod
    def _require_quiescent(store, experiment_id):
        if any(store.batch_has_active_jobs(experiment_id, batch) for batch in ("learning", "validation")):
            raise CorpusExperimentConflict("请先暂停本实验并等待所有活动任务结束 (quiescent)")
        offset = 0
        while True:
            rounds = store.list_rounds(experiment_id=experiment_id, limit=100, offset=offset)
            for round_ in rounds:
                if round_.state == "running" or store.eligible_job_ids(round_.round_id, limit=1):
                    raise CorpusExperimentConflict("请先暂停本实验并等待单条运行或审核任务结束 (quiescent)")
            if len(rounds) < 100:
                return
            offset += len(rounds)
