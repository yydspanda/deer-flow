"""DEV batch orchestration around the existing Runtime, jobs and Memory services."""

import logging
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import Event, Thread
from uuid import uuid4

from soc_agent.contracts import ActorContext, ActorType, EntrySurface, ProcessingJobStatus, ServiceRequestContext, SocMemoryRecord, SocProcessingJobSubmission
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusExecutionOutcome, CorpusExperiment, CorpusExperimentMember, CorpusMemorySnapshotEntry, CorpusRound, CorpusRoundCreateCommand
from soc_agent.db import SqlAlchemyAlertRepository
from soc_agent.db.corpus_experiments import CorpusExperimentConflict
from soc_agent.db.jobs import ProcessingJobConflictError
from soc_agent.demo.corpus_round_comparison import comparison_results_hash, comparison_side, job_result_row
from soc_agent.utils.hashing import stable_hash

logger = logging.getLogger(__name__)
CORPUS_WORKLOAD = "corpus_experiment"
CORPUS_QUEUE = "deepseek-v4-flash"


def _require_saved_options(store, experiment_id: str, batch: str, submitted: SocAnalysisExecutionOptions, defaults: SocAnalysisExecutionOptions | None) -> None:
    # Call only under the admission transaction's governance lock. Otherwise a
    # remote request can publish an older setting after the owner saves a new one.
    saved = store.latest_run_options(experiment_id, batch) or defaults or SocAnalysisExecutionOptions()
    if submitted != saved:
        raise PermissionError("运行配置仅限部署本机修改；请刷新后沿用已保存配置运行。")


class CorpusExecutionError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, run_id: str | None = None, block_round: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.run_id = run_id
        self.block_round = block_round


class SocCorpusExperimentService:
    """Thin coordinator; it never builds prompts or publishes/approves Memory."""

    def __init__(
        self,
        *,
        repository: SqlAlchemyAlertRepository,
        execute: Callable[[CorpusRound, CorpusExperimentMember, ServiceRequestContext], CorpusExecutionOutcome],
        configuration_provider: Callable[[SocAnalysisExecutionOptions], dict],
        max_concurrency: int = 3,
        lease_seconds: int = 120,
    ):
        self.repository = repository
        self.store = repository.corpus_experiments()
        self.jobs = repository.processing_jobs()
        self._execute = execute
        self._configuration_provider = configuration_provider
        self._max_concurrency = max_concurrency
        self._lease_seconds = lease_seconds

    def prepare(self, plan, *, experiment_id: str, name: str, context: ServiceRequestContext) -> CorpusExperiment:
        _require_admin(context)
        experiment = CorpusExperiment(
            experiment_id=experiment_id, name=name, plan_id=plan.plan_id, tenant_id="pingan", environment="dev-corpus-eval", source_identity=plan.source_identity, created_by=context.actor.actor_id, created_at=datetime.now(UTC)
        )
        members = [
            CorpusExperimentMember(
                alert_id=m.alert_id,
                source_index=m.source_index,
                payload_hash=m.payload_hash,
                group_id=m.group_id,
                sequence_number=i,
                position_in_group=m.position_in_group,
                batch=m.batch,
                validation_tier=m.validation_tier,
                event_time=m.event_time_utc,
                rule_code=m.rule_code,
                detection_key=m.detection_key,
                reason=m.reason,
            )
            for i, m in enumerate(plan.members)
        ]
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            tx.corpus_experiments().prepare(experiment, members)
        return self.store.get_experiment(experiment_id)

    def create_round(
        self,
        command: CorpusRoundCreateCommand,
        *,
        context: ServiceRequestContext,
        allow_options_override: bool = True,
        default_options: SocAnalysisExecutionOptions | None = None,
        current_plan_id: str | None = None,
    ) -> CorpusRound:
        _require_admin(context)
        if command.concurrency > self._max_concurrency:
            raise ValueError(f"concurrency exceeds the server limit {self._max_concurrency}")
        command_hash = stable_hash(command.model_dump(mode="json"))
        round_id = "ROUND-" + (stable_hash([context.actor.actor_id, context.idempotency_key])[:24].upper() if context.idempotency_key else uuid4().hex[:16].upper())
        config = self._configuration_provider(command.options)
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            existing = store.get_round(round_id)
            if existing is not None:
                if existing.creation_command_hash != command_hash:
                    raise CorpusExperimentConflict("idempotency key already belongs to a different round command")
                return existing
            experiment = store.get_experiment(command.experiment_id)
            if experiment is None:
                raise ValueError("experiment not found; prepare the fixed dataset first")
            if not allow_options_override:
                if current_plan_id is not None and experiment.plan_id != current_plan_id:
                    raise PermissionError("仅能按当前语料的已保存配置运行；请刷新后重试。")
                # Match the configuration read/quick entry's canonical experiment.
                # A caller-created fork must not reset or retain its own authority.
                canonical = store.find_plan_experiment(experiment.plan_id)
                if canonical is None:
                    raise PermissionError("当前语料配置不可用；请刷新后重试。")
                _require_saved_options(store, canonical.experiment_id, command.selection.batch, command.options, default_options)
            parent = None
            if command.parent_round_id:
                parent = store.get_round(command.parent_round_id)
                if parent is None or parent.experiment_id != experiment.experiment_id:
                    raise ValueError("retest parent round must belong to this experiment")
                if parent.selection.batch != command.selection.batch:
                    raise ValueError("retest parent must belong to the same batch")
                if parent.state == "running" or store.round_progress(parent.round_id).active_count:
                    raise ValueError("pause the parent round and wait for active jobs before preparing a retest")
            if provenance := command.retest_provenance:
                parent = store.get_round(provenance.parent_round_id)
                if parent is None or parent.experiment_id != experiment.experiment_id or command.parent_round_id != parent.round_id:
                    raise ValueError("retest parent must belong to this experiment")
                if provenance.source_identity != experiment.source_identity or parent.selection.batch != command.selection.batch:
                    raise ValueError("retest dataset or batch differs from the fixed source")
                if provenance.selection_hash != stable_hash(command.selection.model_dump(mode="json")):
                    raise ValueError("retest selection differs from the fixed plan")
            snapshot = []
            if command.selection.batch == "validation" and command.memory_mode == "snapshot":
                snapshot = [_snapshot(record) for record in store.learning_memory_records(experiment.experiment_id) if record.tenant_id == experiment.tenant_id and _eligible(record, datetime.now(UTC))]
                if not snapshot:
                    raise ValueError("no usable reviewed first-batch Memory; review learning candidates or explicitly select a no-Memory baseline")
            round_ = CorpusRound(
                round_id=round_id,
                creation_command_hash=command_hash,
                experiment_id=experiment.experiment_id,
                selection=command.selection,
                purpose=command.purpose,
                options=command.options,
                config_hash=stable_hash(config),
                config_snapshot=config,
                memory_snapshot=snapshot,
                memory_mode=command.memory_mode,
                governance_hash=store.governance_hash(experiment.tenant_id) if command.selection.batch == "validation" and command.memory_mode == "snapshot" else None,
                parent_round_id=command.parent_round_id,
                retest_provenance=command.retest_provenance,
                execution_limit=command.execution_limit,
                concurrency=command.concurrency,
                created_by=context.actor.actor_id,
                created_at=datetime.now(UTC),
            )
            store.create_round(round_)
            offset = 0
            captured_results = []
            while True:
                page = store.list_members(experiment.experiment_id, selection=command.selection, offset=offset, limit=500)
                if page.total == 0:
                    raise ValueError("no alerts match the selected batch and filters")
                submissions = []
                parent_jobs = store.round_jobs_for_alerts(parent.round_id, [member.alert_id for member in page.items]) if parent else {}
                for member in page.items:
                    # Job input is a reference to fixed corpus data, never a second raw dataset.
                    key = f"corpus:{round_.round_id}:{member.alert_id}"
                    metadata = {"round_id": round_.round_id, "batch": command.selection.batch, "config_hash": round_.config_hash, "no_external_actions": True}
                    if parent:
                        previous_job = parent_jobs.get(member.alert_id)
                        previous_result = comparison_side(job_result_row(previous_job)) if previous_job else None
                        metadata["comparison_baseline"] = {"round_id": parent.round_id, "config_hash": parent.config_hash, "captured_at": round_.created_at.isoformat(), "result": previous_result}
                        if previous_result:
                            captured_results.append({"alert_id": member.alert_id, **previous_result})
                    submissions.append(
                        SocProcessingJobSubmission(
                            workload_kind=CORPUS_WORKLOAD,
                            queue_name=CORPUS_QUEUE,
                            tenant_id=experiment.tenant_id,
                            alert_id=member.alert_id,
                            detection_key=member.detection_key,
                            concurrency_key=corpus_alert_concurrency_key(experiment.tenant_id, experiment.environment, member.alert_id),
                            idempotency_key=key,
                            input_payload={"experiment_id": experiment.experiment_id, "round_id": round_.round_id, "alert_id": member.alert_id, "payload_hash": member.payload_hash},
                            metadata=metadata,
                        )
                    )
                jobs = tx.processing_jobs().submit_many(submissions)
                store.attach_jobs(round_.round_id, [(member.alert_id, job.job_id, offset + index) for index, (member, (job, _)) in enumerate(zip(page.items, jobs, strict=True))])
                offset += len(page.items)
                if offset >= page.total:
                    break
            if command.retest_provenance and command.retest_provenance.selected_results_hash and comparison_results_hash(captured_results) != command.retest_provenance.selected_results_hash:
                raise ValueError("report results changed; export the parent round again before preparing a retest")
            return round_

    def start(self, round_id: str, *, context: ServiceRequestContext, execution_limit: int | None = None, concurrency: int | None = None) -> CorpusRound:
        _require_admin(context)
        if concurrency is not None and not 1 <= concurrency <= self._max_concurrency:
            raise ValueError("concurrency exceeds the server limit")
        round_ = self._round(round_id)
        if round_.state == "blocked":
            raise CorpusExperimentConflict("snapshot changed; create a new round")
        reason = self.snapshot_problem(round_)
        if reason:
            self._block(round_id, reason)
            raise CorpusExperimentConflict(f"{reason}; create a new round")
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            return tx.corpus_experiments().set_round_state(round_id, expected_version=round_.version, state="running", reason=None, execution_limit=execution_limit, concurrency=concurrency, actor_id=context.actor.actor_id)

    def pause(self, round_id: str, *, context: ServiceRequestContext) -> CorpusRound:
        _require_admin(context)
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            round_ = store.get_round(round_id)
            if round_ is None:
                raise ValueError("round not found")
            return store.set_round_state(round_id, expected_version=round_.version, state="paused", reason="operator_requested", actor_id=context.actor.actor_id)

    def snapshot_problem(self, round_: CorpusRound) -> str | None:
        try:
            config = self._configuration_provider(round_.options)
        except ValueError as exc:
            return f"configuration_invalid: {exc}"
        if stable_hash(config) != round_.config_hash:
            return "configuration_changed"
        experiment = self.store.get_experiment(round_.experiment_id)
        if experiment is None:
            return "experiment_missing"
        if round_.governance_hash is not None and self.store.governance_hash(experiment.tenant_id) != round_.governance_hash:
            return "memory_governance_changed"
        now = datetime.now(UTC)
        for entry in round_.memory_snapshot:
            record = self.repository.get_memory_record(entry.memory_id)
            if record is None or _snapshot(record) != entry or not _eligible(record, now):
                return "memory_snapshot_changed_or_expired"
        return None

    def request_manual(self, round_id: str, alert_id: str, *, context: ServiceRequestContext) -> None:
        _require_admin(context)
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            round_ = store.get_round(round_id)
            if round_ is None:
                raise ValueError("round not found")
            if round_.state == "blocked" or self.snapshot_problem(round_):
                raise CorpusExperimentConflict("运行设置或已审核经验发生变化，请显式重新运行")
            queued = store.request_manual(round_id, alert_id, actor_id=context.actor.actor_id)
            if queued and round_.state == "completed":
                store.set_round_state(round_id, expected_version=round_.version, state="paused", reason="manual_requested", actor_id=context.actor.actor_id)

    def execute_one(self, round_id: str) -> bool:
        round_ = self._round(round_id)
        if round_.state not in {"prepared", "running", "paused"}:
            return False
        problem = self.snapshot_problem(round_)
        if problem:
            self._block(round_id, problem)
            return False
        worker_id = f"corpus-{uuid4().hex}"
        # Short DB transaction fences pause/claim and the per-round concurrency budget.
        # No HTTP/model request executes under this governance/claim lock.
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            if store.active_job_count() >= self._max_concurrency:
                return False
            ids = store.eligible_job_ids(round_id)
            job = tx.processing_jobs().claim_next(queue_name=CORPUS_QUEUE, workload_kind=CORPUS_WORKLOAD, worker_id=worker_id, lease_seconds=self._lease_seconds, job_ids=ids)
        if job is None:
            self._finish_if_drained(round_id)
            return False
        member = self.store.get_member(round_.experiment_id, job.alert_id)
        request_context = ServiceRequestContext(actor=ActorContext(actor_id=round_.created_by, actor_type=ActorType.SERVICE, surface=EntrySurface.DAEMON, roles=["soc_batch_runner", "soc_admin"]), idempotency_key=job.idempotency_key)
        try:
            job = self.jobs.transition(job.job_id, worker_id=worker_id, expected_status=job.status, target_status=ProcessingJobStatus.PRECHECKING, event_type="corpus_precheck")
            problem = self.snapshot_problem(round_)
            if problem:
                raise CorpusExecutionError(problem, block_round=True)
            if member is None:
                raise CorpusExecutionError("fixed experiment member missing", block_round=True)
            if job.attempt_count > 3:
                raise CorpusExecutionError("recovery attempt budget exhausted")
            job = self.jobs.transition(job.job_id, worker_id=worker_id, expected_status=job.status, target_status=ProcessingJobStatus.ANALYZING, event_type="corpus_runtime_started")
            with self._heartbeat(job.job_id, worker_id):
                outcome = self._execute(round_, member, request_context)
            job = self.jobs.transition(job.job_id, worker_id=worker_id, expected_status=job.status, target_status=ProcessingJobStatus.PROJECTING, event_type="corpus_runtime_saved", run_id=outcome.run_id)
            problem = self.snapshot_problem(round_)
            if problem:
                outcome = outcome.model_copy(update={"snapshot_changed_during_run": True})
                self._block(round_id, problem)
            self.jobs.transition(job.job_id, worker_id=worker_id, expected_status=job.status, target_status=ProcessingJobStatus.COMPLETED, event_type="corpus_completed", run_id=outcome.run_id, result_payload=outcome.model_dump(mode="json"))
        except ProcessingJobConflictError:
            # Another owner recovered the expired lease. Do not overwrite its state.
            logger.exception("Corpus lease lost for %s", job.job_id)
        except Exception as exc:
            controlled = exc if isinstance(exc, CorpusExecutionError) else CorpusExecutionError(str(exc))
            current = self.jobs.get(job.job_id)
            if current is not None and not current.status.is_terminal:
                failure_result = {"retryable": controlled.retryable, "stage": current.status.value}
                if controlled.run_id:
                    # A downstream Pattern failure must not rewrite saved analysis.
                    saved_run = self.repository.get_run(controlled.run_id)
                    failure_result["summary"] = {"analysis_status": saved_run.status.value if saved_run is not None else "failed", "measurements": self.repository.get_run_measurements(controlled.run_id)}
                self.jobs.transition(
                    job.job_id,
                    worker_id=worker_id,
                    expected_status=current.status,
                    target_status=ProcessingJobStatus.FAILED,
                    event_type="corpus_failed",
                    run_id=controlled.run_id,
                    error_code=type(exc).__name__,
                    error_message=str(exc)[:2000],
                    result_payload=failure_result,
                )
            if controlled.block_round:
                self._block(round_id, str(controlled)[:2000])
            logger.exception("Corpus execution failed for %s", job.job_id)
        self._finish_if_drained(round_id)
        return True

    def recover(self) -> list[str]:
        return self.jobs.recover_expired_leases(queue_name=CORPUS_QUEUE, workload_kind=CORPUS_WORKLOAD)

    def retry_failed(self, round_id: str, *, context: ServiceRequestContext) -> dict[str, int]:
        _require_admin(context)
        round_ = self._round(round_id)
        reason = self.snapshot_problem(round_)
        if reason or round_.state == "blocked":
            raise CorpusExperimentConflict("snapshot changed; create a new round")
        retried = skipped = offset = 0
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            while True:
                page = store.list_round_items(round_id, offset=offset, limit=500)
                for item in page.items:
                    if item.job.status != ProcessingJobStatus.FAILED:
                        continue
                    if not (item.job.result_payload or {}).get("retryable") or item.job.attempt_count >= 3:
                        skipped += 1
                        continue
                    tx.processing_jobs().retry_failed(item.job_id, expected_version=item.job.version, actor_id=context.actor.actor_id)
                    retried += 1
                offset += len(page.items)
                if offset >= page.total:
                    break
            if retried:
                store.set_round_state(round_id, expected_version=round_.version, state="running", reason="retry_failed", actor_id=context.actor.actor_id)
        return {"retried": retried, "not_retryable": skipped}

    def _round(self, round_id: str) -> CorpusRound:
        round_ = self.store.get_round(round_id)
        if round_ is None:
            raise ValueError("round not found")
        return round_

    def _block(self, round_id: str, reason: str):
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            round_ = store.get_round(round_id)
            if round_ is not None and round_.state != "blocked":
                store.set_round_state(round_id, expected_version=round_.version, state="blocked", reason=reason)

    def _finish_if_drained(self, round_id: str):
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            progress = store.round_progress(round_id)
            if progress.round.state == "running" and not progress.active_count and not store.dispatch_pending_count(round_id):
                store.set_round_state(round_id, expected_version=progress.round.version, state="completed", reason="execution_limit_reached" if progress.admitted_count < progress.selected_count else "selected_scope_completed")

    @contextmanager
    def _heartbeat(self, job_id: str, worker_id: str):
        stop = Event()
        errors = []

        def renew():
            while not stop.wait(max(1, self._lease_seconds / 3)):
                try:
                    self.jobs.renew_lease(job_id, worker_id=worker_id, expected_status=ProcessingJobStatus.ANALYZING, lease_seconds=self._lease_seconds)
                except Exception as exc:
                    errors.append(exc)
                    return

        thread = Thread(target=renew, name="soc-corpus-lease", daemon=True)
        thread.start()
        try:
            yield
            if errors:
                raise ProcessingJobConflictError("corpus lease renewal failed") from errors[0]
        finally:
            stop.set()
            thread.join()


def corpus_alert_concurrency_key(tenant_id: str, environment: str, alert_id: str) -> str:
    return "corpus-alert:" + stable_hash([tenant_id, environment, alert_id])


def _require_admin(context: ServiceRequestContext):
    if "soc_admin" not in context.actor.roles:
        raise PermissionError("corpus experiments require an administrator")


def _snapshot(record: SocMemoryRecord) -> CorpusMemorySnapshotEntry:
    return CorpusMemorySnapshotEntry(memory_id=record.memory_id, version=record.version, content_hash=record.content_hash, facets_hash=record.facets_hash, record_hash=stable_hash(record.model_dump(mode="json")))


def _eligible(record: SocMemoryRecord, now: datetime) -> bool:
    if record.status.value != "confirmed" or not record.retrieval_enabled or record.validity.valid_from > now:
        return False
    return all(value is None or value > now for value in (record.validity.valid_until, record.retrieval_valid_until, record.retrieval_review_due_at))
