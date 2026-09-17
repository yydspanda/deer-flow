"""Validate and render reviewer-owned business lessons for confirmed Memory."""

from __future__ import annotations

from soc_agent.contracts import (
    SocMemoryApplicabilitySpec,
    SocMemoryBusinessLesson,
    SocMemoryCandidateReviewCommand,
)
from soc_agent.contracts.schemas import SocMemoryReuseCondition

_MEMORY_FACET_LABELS = {
    "attack_behavior_family": "攻击行为类型",
    "behavior_component": "行为特征",
    "behavior_component_core": "核心行为",
    "behavior_component_strong": "强行为特征",
    "behavior_component_weak": "弱行为特征",
    "behavior_fingerprint": "行为指纹",
    "behavior_strength": "行为强度",
    "category": "告警类别",
    "detection_key": "检测键",
    "detection_signature": "检测签名",
    "entity": "关联实体",
    "environment": "运行环境",
    "network_service": "目标网络服务",
    "product": "安全产品",
    "role_entity": "角色实体",
    "rule_code": "规则编码",
    "rule_name": "规则名称",
    "scenario_key": "安全场景",
    "service_uri": "服务地址",
    "severity": "严重级别",
    "source_system": "来源系统",
    "source_type": "告警来源类型",
    "vulnerability_id": "漏洞标识",
}
_MEMORY_FACET_VALUE_LABELS = {
    ("attack_behavior_family", "command_and_control"): "命令与控制",
    ("attack_behavior_family", "denial_of_service"): "拒绝服务",
    ("attack_behavior_family", "proxy_tunnel_activity"): "代理或隧道活动",
    ("attack_behavior_family", "vulnerability_exploitation"): "漏洞利用",
    ("behavior_strength", "strong"): "强特征",
    ("behavior_strength", "weak_only"): "仅弱特征",
    ("environment", "dev"): "开发环境",
    ("environment", "dev-corpus-eval"): "DEV 告警演练",
    ("environment", "local"): "本地环境",
    ("environment", "prd"): "生产环境",
    ("environment", "stg"): "预发布环境",
    ("source_type", "edr"): "终端检测与响应",
    ("source_type", "hids"): "主机入侵检测",
    ("source_type", "ndr"): "网络检测与响应",
    ("source_type", "nids"): "网络入侵检测",
}


def memory_lesson_applicability_conditions(
    applicability: SocMemoryApplicabilitySpec,
) -> list[str]:
    """Render the machine-owned applicability scope for a human lesson."""

    conditions = [_memory_applicability_condition(key, values) for key, values in sorted(applicability.required_facets.items()) if not (key == "behavior_fingerprint" and applicability.selected_behavior_components is not None)]
    if applicability.selected_behavior_components is not None:
        conditions.append("直接复用时，以下已审核核心行为必须逐项全部满足：" + "；".join(applicability.selected_behavior_components))
    for item in applicability.reuse_conditions:
        conditions.append("仅直接复用结论时：" + _memory_applicability_condition(item.facet_key, item.values))
    if applicability.minimum_optional_matches:
        conditions.append(f"还必须至少匹配 {applicability.minimum_optional_matches} 组经审核的可选条件。")
    return conditions


def memory_lesson_invalidation_conditions(
    model_conditions: list[str],
) -> list[str]:
    """Add deterministic invalidation floors before model-authored details."""

    baseline = [
        "直接复用条件不满足时，不直接沿用结论；符合相关经验检索条件时，仍可比较差异后供模型参考。",
        "当前告警出现与已审核业务结论冲突的新证据或攻击影响时，必须重新研判。",
    ]
    normalized = [" ".join(str(value).split()) for value in model_conditions if str(value).strip()]
    return list(dict.fromkeys([*baseline, *normalized]))


def _memory_applicability_condition(
    key: str,
    values: list[str],
) -> str:
    label = _MEMORY_FACET_LABELS.get(key, "系统匹配条件")
    rendered_values = ", ".join(_memory_facet_value_label(key, value) for value in values)
    return f"必须匹配「{label}（{key}）」：{rendered_values}"


def _memory_facet_value_label(key: str, value: str) -> str:
    normalized = str(value).strip()
    label = _MEMORY_FACET_VALUE_LABELS.get((key, normalized.casefold()))
    if label is None:
        return normalized
    return f"{label}（{normalized}）"


def promote_memory_applicability_facets(
    applicability: SocMemoryApplicabilitySpec,
    promoted_facet_keys: list[str],
    promoted_facet_values: dict[str, list[str]] | None = None,
) -> SocMemoryApplicabilitySpec:
    """Add exact-reuse limits without changing the scope of model reference."""

    selected = promoted_facet_values or {}
    promoted = list(dict.fromkeys([*(str(key).strip() for key in promoted_facet_keys if str(key).strip()), *selected]))
    unknown = sorted(set(promoted) - set(applicability.optional_facets))
    if unknown:
        raise ValueError("promoted memory facets are not candidate optional facets: " + ", ".join(unknown))
    if set(promoted) & set(applicability.context_only_similarity_facet_keys):
        raise ValueError("context-only similarity facets must remain optional")
    if set(promoted) & {"behavior_component_core", "behavior_component_strong", "behavior_component_weak"}:
        raise ValueError("internal behavior aliases are not editable business limits")
    if not promoted:
        return applicability
    narrowed = {}
    for key in promoted:
        allowed = applicability.optional_facets[key]
        values = list(dict.fromkeys(selected.get(key, allowed)))
        if not values or not set(values) <= set(allowed):
            raise ValueError(f"promoted memory facet {key} must select candidate optional values")
        narrowed[key] = values
    conditions = {item.condition_key: item for item in applicability.reuse_conditions}
    for key, values in narrowed.items():
        groups: dict[str | None, list[str]] = {}
        for value in values:
            prefix = value.partition(":")[0].casefold() if key in {"entity", "role_entity", "behavior_component"} else None
            groups.setdefault(prefix, []).append(value)
        for prefix, items in groups.items():
            condition = SocMemoryReuseCondition(facet_key=key, value_prefix=prefix, values=items)
            previous = conditions.get(condition.condition_key)
            if previous and not {v.casefold() for v in condition.values} <= {v.casefold() for v in previous.values}:
                raise ValueError("reuse conditions cannot widen a saved candidate limit")
            conditions[condition.condition_key] = condition
    return SocMemoryApplicabilitySpec.model_validate(
        {
            **applicability.model_dump(),
            "reuse_conditions": [conditions[key] for key in sorted(conditions)],
            "policy_version": "soc.memory_applicability_policy.v4"
            if applicability.covered_behavior_components is not None
            else "soc.memory_applicability_policy.v3"
            if applicability.selected_behavior_components is not None
            else "soc.memory_applicability_policy.v2",
        }
    )


def resolve_memory_business_lesson(
    command: SocMemoryCandidateReviewCommand,
) -> tuple[SocMemoryBusinessLesson | None, str]:
    """Validate and return the reviewer-owned lesson for one confirmation."""

    if command.record_lesson is not None:
        return command.record_lesson, "reviewer_supplied"

    directive = command.decision_directive
    if directive is None:
        return None, "not_required"
    raise ValueError("decision-bearing Memory requires an explicit reviewed record_lesson")


def render_memory_business_lesson(lesson: SocMemoryBusinessLesson) -> str:
    """Render the typed lesson into bounded analyst/model-readable text."""

    sections = (
        *((("检测场景 / Detection scenario", [lesson.detection_scenario]),) if lesson.detection_scenario is not None else ()),
        *((("实际事件 / Observed event", [lesson.observed_event]),) if lesson.observed_event is not None else ()),
        ("审核结论 / Reviewed conclusion", [lesson.conclusion]),
        ("业务依据 / Business rationale", lesson.business_rationale),
        ("适用条件 / Applicability", lesson.applicability_conditions),
        ("泛化边界 / Generalization boundaries", lesson.generalization_boundaries),
        ("失效条件 / Invalidation conditions", lesson.invalidation_conditions),
        ("处置建议 / Handling guidance", lesson.handling_guidance),
    )
    lines: list[str] = []
    for heading, items in sections:
        lines.append(f"{heading}:")
        lines.extend(f"- {item}" for item in items)
    return "\n".join(lines)


__all__ = [
    "memory_lesson_applicability_conditions",
    "memory_lesson_invalidation_conditions",
    "promote_memory_applicability_facets",
    "render_memory_business_lesson",
    "resolve_memory_business_lesson",
]
