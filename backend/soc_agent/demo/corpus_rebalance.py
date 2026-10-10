"""Versioned corpus supplementation using saved facts and the existing job service."""

from datetime import UTC, datetime

from soc_agent.contracts import AnalysisRunStatus, ProcessingJobStatus
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_experiments import CorpusExperiment, CorpusExperimentRebalance, CorpusRoundCreateCommand, CorpusRoundSelection
from soc_agent.contracts.corpus_rebalance import CorpusRebalanceFact, CorpusRebalancePolicy
from soc_agent.demo.corpus_experiments import _eligible, _require_admin, _snapshot
from soc_agent.demo.corpus_rebalance_plan import build_corpus_rebalance_plan
from soc_agent.memory.facets import filter_learning_entity_facets
from soc_agent.utils.hashing import stable_hash


def project_rebalance_fact(run, profiles, *, environment):
    """Use the exact Pattern projection, never a verdict or the static index."""
    report = run.normalization_assistance
    alert = run.normalized_alert
    values = dict(alert_id=run.alert_id, payload_hash=run.input_hash, run_id=run.run_id, event_time=alert.event.event_time if alert else None, semantic_status=report.status if report else "unknown")
    if report is None or report.mode != "apply" or report.status not in {"applied", "partial", "unchanged"} or not values["event_time"]:
        return CorpusRebalanceFact(**values)
    profile = profiles.resolve_run(run)
    identity = profile.identity
    values.update(profile_id=identity.profile_id, profile_version=identity.profile_version, feature_schema_version=identity.feature_schema_version)
    facets = filter_learning_entity_facets(profile.project_run_facets(run), pattern_whitespace=True)
    facets["environment"] = [environment]
    # A detector label alone is not a semantic behavior group.
    if not facets.get("behavior_fingerprint"):
        return CorpusRebalanceFact(**values)
    try:
        signature = profile.build_pattern_signature(run, facets=facets)
        values["pattern_key"] = stable_hash({"dimension": signature.dimension.value, "value": signature.value})
        values["occurrence_key"] = profile.build_occurrence_key(run, signature=signature, facets=facets, observed_at=values["event_time"])
    except ValueError:
        pass
    return CorpusRebalanceFact(**values)


def independent_learning_run(run):
    """Do not turn a prior Memory answer, fallback or facts-only run into support."""
    return bool(
        run
        and run.status in {AnalysisRunStatus.SUCCESS, AnalysisRunStatus.NEEDS_REVIEW}
        and run.analysis is not None
        and run.direct_resolution is None
        and run.model_name not in {"stub", "not_invoked", "unknown"}
        and run.llm_analysis_request is not None
        and not any(item.kind.value == "confirmed_memory" for item in run.llm_analysis_request.context_catalog)
    )


def learning_run_hash(run):
    """Bind inherited evidence and decisions; billing and update timestamps may vary."""
    return stable_hash(
        run.model_dump(
            mode="json",
            include={
                "run_id",
                "alert_id",
                "input_hash",
                "status",
                "pipeline_version",
                "model_name",
                "prompt_version",
                "normalized_alert",
                "normalization_assistance",
                "llm_analysis_request",
                "analysis",
                "decision",
                "direct_resolution",
                "analysis_output_quality",
                "entities",
                "fact_reconstruction",
                "corrections",
                "role_adjudication_revisions",
                "role_adjudication_verification",
            },
        )
    )


def _fact_identity(fact):
    return fact.model_dump(mode="json", exclude={"run_id"})


def fact_identity_hash(fact):
    return stable_hash(_fact_identity(fact))


def _require_parent_review_complete(store, parent_id):
    # Global learning coordination can reuse a pending candidate from the old
    # scope. Finish that queue before creating a separate review scope.
    if store.list_learning_candidates(parent_id, limit=1).total:
        raise ValueError("旧实验仍有待审核经验，请先完成审核或取消沉淀，再建立新实验")


class CorpusRebalanceService:
    """Keep old rounds immutable; preparation never approves or publishes Memory."""

    def __init__(self, service, profiles):
        self.service, self.profiles = service, profiles
        self.repository, self.store = service.repository, service.store

    def _members(self, experiment_id):
        result = []
        for batch in ("learning", "validation"):
            offset = 0
            while True:
                page = self.store.list_members(experiment_id, selection=CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else "all"), offset=offset, limit=500)
                result.extend(page.items)
                offset += len(page.items)
                if offset >= page.total:
                    break
                if not page.items:
                    raise ValueError("experiment pagination changed")
        return sorted(result, key=lambda member: member.sequence_number)

    def prepare_facts(self, parent_id, *, context, options=None):
        _require_admin(context)
        if not context.idempotency_key:
            raise ValueError("事实预处理需要 Idempotency-Key")
        parent = self.store.get_experiment(parent_id)
        if parent is None or parent.fact_source_experiment_id:
            raise ValueError("请选择已有学习实验作为来源")
        self.store.require_quiescent(parent_id)
        _require_parent_review_complete(self.store, parent_id)
        options = options or SocAnalysisExecutionOptions(normalization_review_mode="apply")
        if options.normalization_review_mode != "apply" or options.tenant_policy_enabled:
            raise ValueError("事实预处理需要开启语义核对并关闭企业策略")
        inventory_id = "EXP-FACTS-" + stable_hash([parent_id, context.idempotency_key])[:24].upper()
        inventory = CorpusExperiment(
            experiment_id=inventory_id,
            name="补充学习：语义事实预处理",
            plan_id=stable_hash([parent.plan_id, "facts", inventory_id]),
            tenant_id=parent.tenant_id,
            environment=parent.environment,
            source_identity=parent.source_identity,
            created_by=context.actor.actor_id,
            created_at=datetime.now(UTC),
            fact_source_experiment_id=parent_id,
        )
        members = self._members(parent_id)
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            tx.corpus_experiments().prepare(inventory, members)
        rounds = []
        for batch in ("learning", "validation"):
            if not any(member.batch == batch for member in members):
                continue
            round_ = self.service.create_round(
                CorpusRoundCreateCommand(
                    experiment_id=inventory_id,
                    selection=CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else "all"),
                    purpose="facts",
                    memory_mode="none",
                    options=options,
                    execution_limit=2_147_483_647,
                    concurrency=self.service._max_concurrency,
                ),
                context=context.model_copy(update={"idempotency_key": f"{context.idempotency_key}:{batch}"}),
            )
            rounds.append(round_)
        return {"experiment_id": inventory_id, "parent_experiment_id": parent_id, "rounds": [r.model_dump(mode="json") for r in rounds]}

    def plan(self, parent_id, facts_experiment_id, *, context, policy=None):
        _require_admin(context)
        parent = self.store.get_experiment(parent_id)
        inventory = self.store.get_experiment(facts_experiment_id)
        if parent is None or inventory is None or inventory.fact_source_experiment_id != parent_id:
            raise ValueError("事实预处理实验与学习来源不一致")
        self.store.require_quiescent(parent_id)
        self.store.require_quiescent(facts_experiment_id)
        _require_parent_review_complete(self.store, parent_id)
        members = self._members(parent_id)
        facts = []
        checked_rounds = set()
        for batch in ("learning", "validation"):
            offset = 0
            while True:
                rows = self.store.batch_jobs(facts_experiment_id, CorpusRoundSelection(batch=batch, scope="reuse" if batch == "learning" else "all"), limit=500, offset=offset)
                for member, job in rows:
                    if job is None or not job.status.is_terminal:
                        raise ValueError("请完整完成事实预处理后再确定新名单")
                    round_id = job.metadata.get("round_id")
                    if round_id not in checked_rounds:
                        round_ = self.store.get_round(round_id)
                        if round_ is None or round_.purpose != "facts" or self.service.snapshot_problem(round_):
                            raise ValueError("事实预处理配置已变化，请按当前配置重新预处理")
                        checked_rounds.add(round_id)
                    value = ((job.result_payload or {}).get("summary") or {}).get("rebalance_fact")
                    if job.status == ProcessingJobStatus.COMPLETED and value:
                        fact = CorpusRebalanceFact.model_validate(value)
                        if fact.alert_id != member.alert_id or fact.payload_hash != member.payload_hash or fact.run_id != job.run_id:
                            raise ValueError("事实结果与固定任务身份不一致")
                        facts.append(fact)
                offset += len(rows)
                if len(rows) < 500:
                    break
        memories = sorted([_snapshot(record) for record in self.store.learning_memory_records(parent_id) if record.tenant_id == parent.tenant_id and _eligible(record, datetime.now(UTC))], key=lambda item: (item.memory_id, item.version))
        plan = build_corpus_rebalance_plan(parent_experiment=parent, members=members, facts=facts, policy=policy or CorpusRebalancePolicy(), inherited_memory_refs=memories)
        self.store.require_quiescent(parent_id)
        self.store.require_quiescent(facts_experiment_id)
        return plan

    def apply(self, plan, facts_experiment_id, *, experiment_id, name, context):
        _require_admin(context)
        if plan.blockers:
            raise ValueError("补样名单仍有阻断项，请先处理后重新生成")
        # Rebuild from server-owned immutable job facts; a client hash is not authority.
        current = self.plan(plan.parent_experiment_id, facts_experiment_id, context=context, policy=plan.policy)
        if current.model_dump(mode="json") != plan.model_dump(mode="json"):
            raise ValueError("补样名单或经验状态已变化，请重新生成计划")
        parent = self.store.get_experiment(plan.parent_experiment_id)
        fact_by_alert = {fact.alert_id: fact for fact in plan.facts}
        imports = {}
        import_hashes = {}
        original = [member for member in self._members(parent.experiment_id) if member.batch == "learning"]
        for offset in range(0, len(original), 500):
            for member, job in self.store.batch_jobs(parent.experiment_id, CorpusRoundSelection(batch="learning"), limit=500, offset=offset):
                if job is None or job.status != ProcessingJobStatus.COMPLETED or not job.run_id:
                    continue
                run = self.repository.get_run(job.run_id)
                if not independent_learning_run(run) or self.repository.list_memory_uses(run_id=run.run_id, limit=1):
                    continue
                fact = fact_by_alert.get(member.alert_id)
                fact_run = self.repository.get_run(fact.run_id) if fact and fact.run_id else None
                if fact_run is None or not run.normalization_assistance or not fact_run.normalization_assistance or run.normalization_assistance.request_hash != fact_run.normalization_assistance.request_hash:
                    continue
                if fact and _fact_identity(project_rebalance_fact(run, self.profiles, environment=parent.environment)) == _fact_identity(fact):
                    imports[member.alert_id] = run.run_id
                    import_hashes[member.alert_id] = learning_run_hash(run)
        experiment = CorpusExperiment(
            experiment_id=experiment_id,
            name=name,
            plan_id=plan.plan_id,
            tenant_id=parent.tenant_id,
            environment=parent.environment,
            source_identity=parent.source_identity,
            created_by=context.actor.actor_id,
            created_at=datetime.now(UTC),
            rebalance=CorpusExperimentRebalance(
                parent_experiment_id=parent.experiment_id,
                plan_hash=plan.plan_id,
                inherited_memory_ids=[ref.memory_id for ref in plan.inherited_memory_refs],
                learning_run_imports=imports,
                learning_run_hashes=import_hashes,
                facts_experiment_id=facts_experiment_id,
                fact_hashes={fact.alert_id: fact_identity_hash(fact) for fact in plan.facts if fact.pattern_key},
                creation_request_hash=stable_hash([context.actor.actor_id, context.idempotency_key]) if context.idempotency_key else None,
            ),
        )
        with self.repository.mutation_transaction() as tx:
            tx.lock_memory_governance()
            store = tx.corpus_experiments()
            store.require_quiescent(parent.experiment_id)
            store.require_quiescent(facts_experiment_id)
            _require_parent_review_complete(store, parent.experiment_id)
            if experiment.rebalance.creation_request_hash:
                offset = 0
                while True:
                    existing = store.list_experiments(limit=500, offset=offset)
                    if any(item.rebalance and item.rebalance.creation_request_hash == experiment.rebalance.creation_request_hash and item.experiment_id != experiment_id for item in existing):
                        raise ValueError("Idempotency-Key 已用于另一个补样实验")
                    offset += len(existing)
                    if len(existing) < 500:
                        break
            latest = sorted(
                [_snapshot(record) for record in store.learning_memory_records(parent.experiment_id) if record.tenant_id == parent.tenant_id and _eligible(record, datetime.now(UTC))], key=lambda item: (item.memory_id, item.version)
            )
            if latest != plan.inherited_memory_refs:
                raise ValueError("审核经验已变化，请重新生成补样名单")
            store.prepare(experiment, plan.members)
        return {"experiment": self.store.get_experiment(experiment_id).model_dump(mode="json"), "imported_learning_runs": len(imports), "inherited_memories": len(plan.inherited_memory_refs), "counts": plan.counts}
