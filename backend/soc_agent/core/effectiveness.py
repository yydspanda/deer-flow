"""Product-effectiveness read service and deterministic rule guidance."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import Lock
from time import monotonic

from soc_agent.contracts import (
    SocBehaviorGroupEffectiveness,
    SocComputeEffectiveness,
    SocEffectivenessCoverage,
    SocEffectivenessScope,
    SocEffectivenessSnapshot,
    SocEffectivenessSummary,
    SocMemoryEffectiveness,
    SocMemoryEffectivenessAggregate,
    SocOperationsAvailability,
    SocRateMetric,
    SocRuleEffectiveness,
    SocRuleEffectivenessAggregate,
    SocRuleEffectivenessDetail,
    SocRuleEffectivenessSelector,
    SocRuleImprovementRecommendation,
    SocRuleOptimizationPolicy,
    SocRuleRecommendationKind,
    SocRuleRecommendationPriority,
)
from soc_agent.protocols import (
    SocEffectivenessRepository,
    SocEffectivenessRepositoryError,
)

from .errors import SocServiceNotFoundError, SocServiceNotImplementedError

_PRIORITY_ORDER = {
    SocRuleRecommendationPriority.HIGH: 0,
    SocRuleRecommendationPriority.MEDIUM: 1,
    SocRuleRecommendationPriority.LOW: 2,
    SocRuleRecommendationPriority.INFO: 3,
}


class SocEffectivenessService:
    """Compose honest metrics from persisted lineage without changing Runtime state."""

    def __init__(
        self,
        *,
        repository: SocEffectivenessRepository | None,
        policy: SocRuleOptimizationPolicy | None = None,
        database_error_code: str | None = None,
        clock: Callable[[], datetime] | None = None,
        cache_ttl_seconds: float = 30.0,
        monotonic_clock: Callable[[], float] | None = None,
    ) -> None:
        if cache_ttl_seconds < 0:
            raise ValueError("effectiveness cache_ttl_seconds must be non-negative")
        self._repository = repository
        self._policy = policy or SocRuleOptimizationPolicy()
        self._database_error_code = database_error_code
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cache_ttl_seconds = cache_ttl_seconds
        self._monotonic_clock = monotonic_clock or monotonic
        self._snapshot_cache: dict[
            tuple[int, str | None, str | None],
            tuple[float, SocEffectivenessSnapshot],
        ] = {}
        self._snapshot_cache_lock = Lock()

    def get_snapshot(
        self,
        *,
        window_days: int = 30,
        tenant_id: str | None = None,
        source_type: str | None = None,
    ) -> SocEffectivenessSnapshot:
        if window_days < 1 or window_days > 366:
            raise ValueError("effectiveness window_days must be 1-366")
        now, scope = self._scope(
            window_days=window_days,
            tenant_id=tenant_id,
            source_type=source_type,
        )
        if self._repository is None:
            return SocEffectivenessSnapshot(
                generated_at=now,
                availability=SocOperationsAvailability.NOT_CONFIGURED,
                scope=scope,
                recommendation_policy_version=self._policy.policy_version,
                error_code=self._database_error_code or "soc.effectiveness.database_not_configured",
            )
        if self._cache_ttl_seconds <= 0:
            return self._build_snapshot(now=now, scope=scope)

        cache_key = (window_days, tenant_id, source_type)
        with self._snapshot_cache_lock:
            current_tick = self._monotonic_clock()
            cached = self._snapshot_cache.get(cache_key)
            if cached is not None and cached[0] > current_tick:
                return cached[1]
            if cached is not None:
                self._snapshot_cache.pop(cache_key, None)

            snapshot = self._build_snapshot(now=now, scope=scope)
            if snapshot.availability is SocOperationsAvailability.AVAILABLE:
                self._store_cached_snapshot(
                    key=cache_key,
                    expires_at=current_tick + self._cache_ttl_seconds,
                    snapshot=snapshot,
                )
            return snapshot

    def _build_snapshot(
        self,
        *,
        now: datetime,
        scope: SocEffectivenessScope,
    ) -> SocEffectivenessSnapshot:
        assert self._repository is not None
        try:
            aggregates = self._repository.read_rule_aggregates(scope)
        except SocEffectivenessRepositoryError:
            return SocEffectivenessSnapshot(
                generated_at=now,
                availability=SocOperationsAvailability.UNAVAILABLE,
                scope=scope,
                recommendation_policy_version=self._policy.policy_version,
                error_code="soc.effectiveness.query_failed",
            )

        totals = _sum_aggregates(aggregates)
        coverage = _coverage(totals)
        summary = _summary(totals)
        compute = _compute(totals)
        rules = sorted(
            (_rule_effectiveness(item, self._policy) for item in aggregates),
            key=lambda item: (
                _PRIORITY_ORDER[item.recommendation.priority],
                -item.alert_count,
                item.detection_identity,
            ),
        )
        return SocEffectivenessSnapshot(
            generated_at=now,
            availability=SocOperationsAvailability.AVAILABLE,
            scope=scope,
            coverage=coverage,
            summary=summary,
            compute=compute,
            rules=rules,
            recommendation_policy_version=self._policy.policy_version,
            measurement_notes=[
                "每个 alert_id 只统计窗口内最新 Run；重跑与 replay 不重复扩大业务量。",
                "结论未被改判只表示当前没有高可信最终结果反驳 Effective Verdict；它包含尚未反馈的告警，不等于人工确认或研判正确。",
                "准确率、漏报率和转交质量只使用已形成高可信最终结论的告警；未标注告警不进入分母。",
                "自动化率按已实际应用的忽略类 disposition 计算；shadow proposal 不计为自动执行。",
                "Rule Code 是 PingAn 可选别名；通用聚合主键仍由租户、来源和 canonical detection identity 组成。",
                "模型算力仅统计 AnalysisRun 中可审计的 Runtime 调用；Memory 草稿等离线治理调用暂不混入告警成本。",
            ],
        )

    def _store_cached_snapshot(
        self,
        *,
        key: tuple[int, str | None, str | None],
        expires_at: float,
        snapshot: SocEffectivenessSnapshot,
    ) -> None:
        expired_keys = [cached_key for cached_key, (cached_expiry, _) in self._snapshot_cache.items() if cached_expiry <= self._monotonic_clock()]
        for expired_key in expired_keys:
            self._snapshot_cache.pop(expired_key, None)
        if len(self._snapshot_cache) >= 64:
            oldest_key = min(
                self._snapshot_cache,
                key=lambda cached_key: self._snapshot_cache[cached_key][0],
            )
            self._snapshot_cache.pop(oldest_key, None)
        self._snapshot_cache[key] = (expires_at, snapshot)

    def get_rule_detail(
        self,
        group_key: str,
        *,
        window_days: int = 30,
        tenant_id: str | None = None,
        source_type: str | None = None,
    ) -> SocRuleEffectivenessDetail:
        """Resolve one rule and expose its actual behavior/Memory relationships."""

        if self._repository is None:
            raise SocServiceNotImplementedError("SOC effectiveness repository is not configured")
        now, scope = self._scope(
            window_days=window_days,
            tenant_id=tenant_id,
            source_type=source_type,
        )
        try:
            aggregates = self._repository.read_rule_aggregates(scope)
        except SocEffectivenessRepositoryError as exc:
            raise SocServiceNotImplementedError("SOC effectiveness rule detail is unavailable") from exc
        selected_aggregate = next(
            (item for item in aggregates if _rule_group_key(item) == group_key),
            None,
        )
        if selected_aggregate is None:
            raise SocServiceNotFoundError(f"SOC effectiveness rule {group_key} not found")
        rule = _rule_effectiveness(selected_aggregate, self._policy)
        selector = _rule_selector(selected_aggregate)
        try:
            behavior_aggregates = self._repository.read_behavior_group_aggregates(
                scope,
                selector,
            )
            memory_aggregates = self._repository.read_memory_aggregates(
                scope,
                selector,
            )
        except SocEffectivenessRepositoryError as exc:
            raise SocServiceNotImplementedError("SOC effectiveness rule detail is unavailable") from exc
        return SocRuleEffectivenessDetail(
            generated_at=now,
            scope=scope,
            rule=rule,
            behavior_groups=[
                SocBehaviorGroupEffectiveness(
                    lineage_key=item.lineage_key,
                    behavior_label=item.behavior_label,
                    environment=item.environment,
                    data_class=item.data_class,
                    sample_count=item.sample_count,
                    distinct_alert_count=item.distinct_alert_count,
                    window_count=item.window_count,
                    verdict_counts=item.verdict_counts,
                    first_observed_at=item.first_observed_at,
                    last_observed_at=item.last_observed_at,
                    candidate_id=item.candidate_id,
                    candidate_status=item.candidate_status,
                    memory_id=item.memory_id,
                    memory_version=item.memory_version,
                    memory_status=item.memory_status,
                    retrieval_enabled=item.retrieval_enabled,
                )
                for item in behavior_aggregates
            ],
            memories=[_memory_effectiveness(item) for item in memory_aggregates],
        )

    def _scope(
        self,
        *,
        window_days: int,
        tenant_id: str | None,
        source_type: str | None,
    ) -> tuple[datetime, SocEffectivenessScope]:
        if window_days < 1 or window_days > 366:
            raise ValueError("effectiveness window_days must be 1-366")
        now = self._clock()
        if now.utcoffset() is None:
            raise ValueError("effectiveness clock must be timezone-aware")
        return now, SocEffectivenessScope(
            window_start=now - timedelta(days=window_days),
            window_end=now,
            tenant_id=tenant_id,
            source_type=source_type,
        )


def _sum_aggregates(
    aggregates: list[SocRuleEffectivenessAggregate],
) -> SocRuleEffectivenessAggregate:
    numeric_fields = {
        name
        for name, field in SocRuleEffectivenessAggregate.model_fields.items()
        if field.annotation is not str
        and name
        not in {
            "tenant_id",
            "source_type",
            "source_system",
            "detection_key",
            "rule_code",
            "rule_name",
        }
    }
    values = {name: sum(int(getattr(item, name)) for item in aggregates) for name in numeric_fields}
    return SocRuleEffectivenessAggregate(source_type="all", **values)


def _coverage(item: SocRuleEffectivenessAggregate) -> SocEffectivenessCoverage:
    return SocEffectivenessCoverage(
        total_alert_count=item.alert_count,
        completed_alert_count=item.completed_count,
        superseded_run_count=item.superseded_run_count,
        conclusion_maintained_alert_count=item.conclusion_maintained_count,
        labeled_alert_count=item.labeled_count,
        high_trust_labeled_alert_count=item.high_trust_labeled_count,
        conclusion_maintenance_rate=_rate(
            "operations.conclusion_maintenance_rate",
            item.conclusion_maintained_count,
            item.completed_count,
            "没有高可信最终结果反驳 Effective Verdict 的完成告警 / 全部完成告警",
            "反映结论尚未被改判的工作流状态；包含未反馈告警，不代表人工确认，也不能替代准确率。",
        ),
        label_coverage=_rate(
            "quality.label_coverage",
            item.labeled_count,
            item.completed_count,
            "已形成最终技术结论的完成告警 / 全部完成告警",
            "最终结果验证覆盖越低，准确率越不能代表全量生产效果。",
        ),
        high_trust_label_coverage=_rate(
            "quality.high_trust_label_coverage",
            item.high_trust_labeled_count,
            item.completed_count,
            "高可信人工或可信外部结论 / 全部完成告警",
            "只有高可信结论参与核心质量指标。",
        ),
    )


def _summary(item: SocRuleEffectivenessAggregate) -> SocEffectivenessSummary:
    return SocEffectivenessSummary(
        triage_accuracy=_rate(
            "quality.triage_accuracy",
            item.correct_count,
            item.high_trust_labeled_count,
            "Effective Verdict 与高可信最终技术结论一致 / 高可信已标注告警",
            "衡量系统最终研判结论是否正确，不把未标注告警视为正确。",
        ),
        detection_miss_rate=_rate(
            "quality.detection_miss_rate",
            item.detection_miss_count,
            item.final_risk_count,
            "最终确认为真实攻击、但 Effective Verdict 为误报 / 最终真实攻击",
            "衡量技术研判漏报；数值越低越好。",
        ),
        operational_miss_rate=_rate(
            "quality.operational_miss_rate",
            item.wrong_auto_ignore_count,
            item.final_risk_count,
            "最终确认为真实攻击、但系统已自动忽略 / 最终真实攻击",
            "这是自动化安全红线，比单纯自动化率更重要。",
        ),
        transfer_precision=_rate(
            "quality.transfer_precision",
            item.transferred_risk_count,
            item.labeled_transfer_count,
            "被转交且最终为真实攻击 / 有最终标签的转交告警",
            "报告中常称“转交检出率”，统计含义实际是转交精确率。",
        ),
        attack_transfer_recall=_rate(
            "quality.attack_transfer_recall",
            item.transferred_risk_count,
            item.final_risk_count,
            "被转交的真实攻击 / 全部最终真实攻击",
            "避免只提高转交精确率，却漏掉应升级的真实攻击。",
        ),
        auto_ignore_rate=_rate(
            "automation.auto_ignore_rate",
            item.auto_ignore_count,
            item.completed_count,
            "已实际自动应用忽略类处置 / 全部完成告警",
            "用户所称自动化率；必须和错误自动忽略率、最终结果验证覆盖一起阅读。",
        ),
        wrong_auto_ignore_rate=_rate(
            "automation.wrong_auto_ignore_rate",
            item.wrong_auto_ignore_count,
            item.labeled_auto_ignore_count,
            "自动忽略后最终确认为真实攻击 / 有最终标签的自动忽略告警",
            "衡量自动化是否错误放过风险；数值越低越好。",
        ),
        human_touch_rate=_rate(
            "operations.human_touch_rate",
            item.human_touch_count,
            item.completed_count,
            "发生人工最终修正或人工处置确认 / 全部完成告警",
            "用于衡量系统是否真正降低运营人工触达。",
        ),
    )


def _compute(item: SocRuleEffectivenessAggregate) -> SocComputeEffectiveness:
    average_tokens = item.total_tokens / item.token_measured_run_count if item.token_measured_run_count else None
    average_duration = item.total_duration_ms / item.duration_measured_run_count if item.duration_measured_run_count else None
    return SocComputeEffectiveness(
        run_count=item.alert_count,
        provider_run_count=item.provider_run_count,
        provider_call_count=item.provider_call_count,
        token_measured_run_count=item.token_measured_run_count,
        input_tokens=item.input_tokens,
        output_tokens=item.output_tokens,
        total_tokens=item.total_tokens,
        average_tokens_per_measured_run=average_tokens,
        duration_measured_run_count=item.duration_measured_run_count,
        average_total_duration_ms=average_duration,
        repair_run_count=item.repair_run_count,
        fallback_run_count=item.fallback_run_count,
        degraded_run_count=item.degraded_run_count,
        token_measurement_coverage=_rate(
            "compute.token_measurement_coverage",
            item.token_measured_run_count,
            item.provider_run_count,
            "具有 token usage 的模型 Run / 发生模型调用的 Run",
            "内网 Provider 不返回 usage 时会明确降低覆盖率，不会伪造 token。",
        ),
        repair_rate=_rate(
            "compute.repair_rate",
            item.repair_run_count,
            item.provider_run_count,
            "发生模型输出修复的 Run / 发生模型调用的 Run",
            "过高通常表示 Prompt 或输出契约不稳定。",
        ),
        fallback_rate=_rate(
            "compute.fallback_rate",
            item.fallback_run_count,
            item.provider_run_count,
            "退回确定性 fallback 的 Run / 发生模型调用的 Run",
            "过高会削弱 LLM 研判价值。",
        ),
        degraded_rate=_rate(
            "compute.degraded_rate",
            item.degraded_run_count,
            item.provider_run_count,
            "存在局部输出降级的 Run / 发生模型调用的 Run",
            "用于定位字段、Prompt、模型或解析器质量问题。",
        ),
    )


def _rule_effectiveness(
    item: SocRuleEffectivenessAggregate,
    policy: SocRuleOptimizationPolicy,
) -> SocRuleEffectiveness:
    identity = item.detection_key or item.rule_code or item.rule_name or f"unclassified:{item.source_type}"
    label_coverage = _divide(item.labeled_count, item.completed_count) or 0.0
    confirmed_risk_rate = _divide(item.final_risk_count, item.labeled_count)
    fp_rate = _divide(item.final_false_positive_count, item.labeled_count)
    triage_accuracy = _divide(item.correct_count, item.high_trust_labeled_count)
    miss_rate = _divide(item.detection_miss_count, item.final_risk_count)
    transfer_precision = _divide(item.transferred_risk_count, item.labeled_transfer_count)
    auto_ignore_rate = _divide(item.auto_ignore_count, item.completed_count) or 0.0
    average_duration = _divide(item.total_duration_ms, item.duration_measured_run_count)
    recommendation = _recommend_rule(
        item,
        policy,
        label_coverage=label_coverage,
        false_positive_rate=fp_rate,
        miss_rate=miss_rate,
    )
    return SocRuleEffectiveness(
        group_key=_rule_group_key(item),
        tenant_id=item.tenant_id,
        source_type=item.source_type,
        source_system=item.source_system,
        detection_identity=identity,
        detection_key=item.detection_key,
        rule_code=item.rule_code,
        rule_name=item.rule_name,
        alert_count=item.alert_count,
        completed_count=item.completed_count,
        labeled_count=item.labeled_count,
        high_trust_labeled_count=item.high_trust_labeled_count,
        label_coverage=label_coverage,
        final_risk_count=item.final_risk_count,
        final_false_positive_count=item.final_false_positive_count,
        confirmed_risk_rate=confirmed_risk_rate,
        false_positive_rate=fp_rate,
        triage_accuracy=triage_accuracy,
        miss_rate=miss_rate,
        transfer_precision=transfer_precision,
        auto_ignore_rate=auto_ignore_rate,
        wrong_auto_ignore_count=item.wrong_auto_ignore_count,
        provider_run_count=item.provider_run_count,
        provider_call_count=item.provider_call_count,
        total_tokens=item.total_tokens,
        average_total_duration_ms=average_duration,
        repair_run_count=item.repair_run_count,
        fallback_run_count=item.fallback_run_count,
        degraded_run_count=item.degraded_run_count,
        memory_context_use_count=item.memory_context_use_count,
        memory_directive_use_count=item.memory_directive_use_count,
        memory_contradiction_count=item.memory_contradiction_count,
        recommendation=recommendation,
    )


def _recommend_rule(
    item: SocRuleEffectivenessAggregate,
    policy: SocRuleOptimizationPolicy,
    *,
    label_coverage: float,
    false_positive_rate: float | None,
    miss_rate: float | None,
) -> SocRuleImprovementRecommendation:
    if item.labeled_count < policy.minimum_labeled_alerts or label_coverage < policy.minimum_label_coverage:
        return _recommendation(
            policy,
            SocRuleRecommendationKind.INSUFFICIENT_LABELS,
            SocRuleRecommendationPriority.INFO,
            "先补最终处置标签",
            [
                f"当前高可信标签 {item.high_trust_labeled_count}/{item.completed_count}。",
                "样本不足时不能把模型自报结论当成规则真实误报率。",
            ],
            "优先同步运营最终状态或执行独立抽样复核，再决定改规则、改模型或启用快速路径。",
            ["label_count_below_policy", "label_coverage_below_policy"],
        )
    if miss_rate is not None and miss_rate > policy.high_miss_rate:
        return _recommendation(
            policy,
            SocRuleRecommendationKind.DETECTION_GAP,
            SocRuleRecommendationPriority.HIGH,
            "优先修复漏报风险",
            [f"已标注真实攻击中的研判漏报率为 {miss_rate:.1%}。"],
            "复盘漏报样本的字段覆盖、场景 Skill、Memory 失效与决策策略；修复前不得为该组启用自动忽略快速路径。",
            ["miss_rate_above_policy"],
        )
    degraded_rate = _divide(item.degraded_run_count + item.fallback_run_count, item.provider_run_count)
    if degraded_rate is not None and degraded_rate > policy.high_degraded_rate:
        return _recommendation(
            policy,
            SocRuleRecommendationKind.IMPROVE_ADAPTER_OR_ENRICHMENT,
            SocRuleRecommendationPriority.HIGH,
            "先修输入或模型输出质量",
            [f"模型降级或 fallback 比例为 {degraded_rate:.1%}。"],
            "检查 Adapter 字段覆盖、上下文裁剪、Prompt 输出契约和必要只读证据，不要先用阈值掩盖质量问题。",
            ["degraded_or_fallback_rate_above_policy"],
        )
    risk_share = _divide(item.final_risk_count, item.labeled_count) or 0.0
    benign_share = _divide(item.final_false_positive_count, item.labeled_count) or 0.0
    if risk_share >= policy.mixed_outcome_floor and benign_share >= policy.mixed_outcome_floor:
        return _recommendation(
            policy,
            SocRuleRecommendationKind.RULE_SPLIT,
            SocRuleRecommendationPriority.MEDIUM,
            "同一规则需要拆分同类行为",
            [f"同组最终结论同时包含真实攻击 {risk_share:.1%} 与误报 {benign_share:.1%}。"],
            "按行为指纹、服务、协议、资产角色或授权上下文拆分规则/策略，不得仅凭同 Rule Code 统一忽略。",
            ["mixed_high_trust_outcomes"],
        )
    if false_positive_rate is not None and false_positive_rate >= policy.high_false_positive_rate:
        stability = max(risk_share, benign_share)
        if item.alert_count >= policy.high_volume_alert_count and stability >= policy.stable_outcome_rate and item.wrong_auto_ignore_count == 0 and item.provider_run_count > 0:
            return _recommendation(
                policy,
                SocRuleRecommendationKind.FAST_PATH_CANDIDATE,
                SocRuleRecommendationPriority.MEDIUM,
                "评估受治理快速路径",
                [
                    f"该组量级 {item.alert_count}，已标注误报率 {false_positive_rate:.1%}。",
                    f"当前已记录 {item.provider_call_count} 次模型调用、{item.total_tokens} tokens。",
                    "当前未观察到已标注的错误自动忽略。",
                ],
                "先收紧到精确行为指纹和已审核 Memory/Policy，再灰度采用去重、小模型或确定性快速路径，并保留抽样复核。",
                ["high_volume", "stable_false_positive_outcome", "model_compute_present"],
            )
        return _recommendation(
            policy,
            SocRuleRecommendationKind.UPSTREAM_RULE_TUNING,
            SocRuleRecommendationPriority.MEDIUM,
            "优化上游检测规则",
            [f"高可信已标注误报率为 {false_positive_rate:.1%}。"],
            "检查规则阈值、白名单、重复聚合和上游字段语义；保留真实攻击样本作为回归集。",
            ["false_positive_rate_above_policy"],
        )
    if risk_share >= 0.5:
        return _recommendation(
            policy,
            SocRuleRecommendationKind.KEEP_FULL_ANALYSIS,
            SocRuleRecommendationPriority.LOW,
            "保留完整研判",
            [f"已标注样本中真实攻击占 {risk_share:.1%}。"],
            "继续使用完整模型、Memory、租户知识和必要证据查询，不为节省算力降低风险覆盖。",
            ["material_risk_share"],
        )
    return _recommendation(
        policy,
        SocRuleRecommendationKind.MONITOR,
        SocRuleRecommendationPriority.INFO,
        "保持监控",
        ["当前质量与成本没有越过版本化优化阈值。"],
        "持续收集最终处置、模型成本和 Memory 反例，按周期重新评估。",
        ["within_policy_thresholds"],
    )


def _recommendation(
    policy: SocRuleOptimizationPolicy,
    kind: SocRuleRecommendationKind,
    priority: SocRuleRecommendationPriority,
    title: str,
    rationale: list[str],
    next_step: str,
    reason_codes: list[str],
) -> SocRuleImprovementRecommendation:
    return SocRuleImprovementRecommendation(
        kind=kind,
        priority=priority,
        title=title,
        rationale=rationale,
        suggested_next_step=next_step,
        reason_codes=reason_codes,
        policy_version=policy.policy_version,
    )


def _rate(
    metric_id: str,
    numerator: int,
    denominator: int,
    formula: str,
    interpretation: str,
) -> SocRateMetric:
    if denominator <= 0:
        return SocRateMetric(
            metric_id=metric_id,
            availability=SocOperationsAvailability.NOT_MEASURED,
            numerator=numerator,
            denominator=0,
            formula=formula,
            interpretation=interpretation,
        )
    return SocRateMetric(
        metric_id=metric_id,
        availability=SocOperationsAvailability.AVAILABLE,
        numerator=numerator,
        denominator=denominator,
        value=numerator / denominator,
        formula=formula,
        interpretation=interpretation,
    )


def _divide(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _rule_group_key(item: SocRuleEffectivenessAggregate) -> str:
    detection_identity = item.detection_key or item.rule_code or item.rule_name or f"unclassified:{item.source_type}"
    payload = json.dumps(
        [
            item.tenant_id,
            item.source_type,
            item.source_system,
            detection_identity,
        ],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _rule_selector(
    item: SocRuleEffectivenessAggregate,
) -> SocRuleEffectivenessSelector:
    return SocRuleEffectivenessSelector(
        tenant_id=item.tenant_id,
        source_type=item.source_type,
        source_system=item.source_system,
        detection_key=item.detection_key,
        rule_code=item.rule_code,
        rule_name=item.rule_name,
    )


def _memory_effectiveness(
    item: SocMemoryEffectivenessAggregate,
) -> SocMemoryEffectiveness:
    return SocMemoryEffectiveness(
        memory_id=item.memory_id,
        memory_version=item.memory_version,
        summary=item.summary,
        record_status=item.record_status,
        retrieval_enabled=item.retrieval_enabled,
        use_alert_count=item.use_alert_count,
        context_only_count=item.context_only_count,
        directive_count=item.directive_count,
        high_trust_feedback_count=item.high_trust_feedback_count,
        support_count=item.support_count,
        contradiction_count=item.contradiction_count,
        not_applicable_count=item.not_applicable_count,
        helpful_correction_count=item.helpful_correction_count,
        harmful_override_count=item.harmful_override_count,
        wrong_auto_ignore_count=item.wrong_auto_ignore_count,
        final_outcome_coverage=_rate(
            f"memory.{item.memory_id}.v{item.memory_version}.final_outcome_coverage",
            item.high_trust_feedback_count,
            item.use_alert_count,
            "具有高可信最终反馈的使用告警 / 使用该 Memory 的去重告警",
            "没有最终运营结论的使用只计入覆盖量，不用于判断 Memory 好坏。",
        ),
        directive_accuracy=_rate(
            f"memory.{item.memory_id}.v{item.memory_version}.directive_accuracy",
            item.directive_correct_count,
            item.directive_high_trust_feedback_count,
            "Memory 指令改判后与高可信最终结论一致 / 有高可信结论的指令使用",
            "仅衡量实际应用结论复用的 Run；context-only 不归因于 Memory。",
        ),
        source_rule_codes=item.source_rule_codes,
        actual_rule_codes=item.actual_rule_codes,
        last_use_at=item.last_use_at,
        last_feedback_at=item.last_feedback_at,
    )


__all__ = ["SocEffectivenessService"]
