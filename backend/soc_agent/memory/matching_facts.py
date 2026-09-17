"""Read-only matching explanations from frozen Runtime data, not model prose."""

from collections.abc import Callable, Sequence

from soc_agent.contracts import AnalysisMemoryUseMode, AnalysisRun, LLMAnalysisRequest

_FACET_LABELS = {
    "detection_key": "检测规则",
    "detection_signature": "规则与产品标识",
    "behavior_fingerprint": "行为标识",
    "behavior_strength": "行为特征强度",
    "behavior_component": "审核要求的行为",
    "environment": "数据使用范围",
    "role_entity": "角色实体",
    "entity": "关联实体",
}


def memory_matching_facts(run: AnalysisRun) -> list[str]:
    """Describe what was supplied/eligible/applied without changing authority."""
    if run.direct_resolution is not None:
        direct = run.direct_resolution
        if direct.source_kind == "memory":
            return [f"经验 {direct.source_id}@v{direct.source_version}：系统已精确匹配并直接复用审核结论；本次未调用主研判模型。"]
        return []
    request = run.llm_analysis_request
    if request is None:
        return []
    memories = [item for item in request.context_catalog if item.kind.value == "confirmed_memory"]
    facts = []
    for item in memories[:19]:
        prefix = f"经验 {item.source_id or item.context_ref}："
        comparison = item.memory_comparison
        if comparison is None:
            facts.append(prefix + "本次输入包含该经验，但未保存匹配比较，不能据此声称精确复用。")
            continue
        if comparison.use_mode is AnalysisMemoryUseMode.DIRECTIVE_APPLICABLE:
            mode = "匹配检查显示具备直接复用资格，但资格不等于已经执行复用，实际采用情况以决策记录为准。"
        elif comparison.use_mode is AnalysisMemoryUseMode.EXACT_CONTEXT:
            mode = "审核条件匹配，仅供模型参考，不承载直接复用指令。"
        else:
            mode = "仅供模型参考，不直接套用审核结论。"
        differences = []
        for label, values in (
            ("当前新增、旧经验未覆盖", comparison.uncovered_behavior_components),
            ("旧经验要求、当前未满足", comparison.missing_behavior_components),
        ):
            if values:
                differences.append(label + "：" + _values(values, lambda value: _behavior_label(value, request)))
        if comparison.missing_required_facet_keys:
            differences.append("必需条件未满足：" + _values(comparison.missing_required_facet_keys, lambda key: _FACET_LABELS.get(key, key)))
        if comparison.missing_reuse_conditions:
            values = [f"{_FACET_LABELS.get(condition.facet_key, condition.facet_key)}={_values(condition.values)}" for condition in comparison.missing_reuse_conditions]
            differences.append("直接复用的附加限制未满足：" + _values(values))
        if comparison.excluded_facet_hits:
            values = [f"{_FACET_LABELS.get(key, key)}={_values(values)}" for key, values in comparison.excluded_facet_hits.items()]
            differences.append("命中排除条件：" + _values(values))
        shared = "已命中条件：" + "、".join(_FACET_LABELS.get(key, key) for key in comparison.matched_required_facets) + "。" if comparison.matched_required_facets else ""
        detail = "；".join(differences) if differences else "比较记录未记录明确条件差异；不由文字推断额外复用权限。"
        facts.append(prefix + mode + shared + detail + "。以上是系统匹配事实，不是风险结论；差异的业务影响由模型结合当前证据判断。")
    if len(memories) > 19:
        facts.append(f"另有 {len(memories) - 19} 条经验，见本次运行的完整匹配比较。")
    return facts


def _values(values: Sequence[str], render: Callable[[str], str] = str) -> str:
    unique = list(dict.fromkeys(values))
    result = "、".join(render(value) for value in unique[:4])
    if len(unique) > 4:
        result += f"（另有 {len(unique) - 4} 项，见完整匹配比较）"
    return result


def _behavior_label(value: str, request: LLMAnalysisRequest) -> str:
    if value.startswith("detected_behavior:"):
        identity, _, binding = value.rpartition("@")
        if ":id:" in identity:
            identifier = identity.rsplit(":id:", 1)[1]
            names = {item.name for item in request.canonical_entities.detections if (item.detector_id or "").casefold() == identifier}
            name = next(iter(names)) if len(names) == 1 else "检测"
            if binding.startswith("service:"):
                return f"{name}（{identifier}），目标服务 {binding.removeprefix('service:').upper()}"
    for prefix, label in (("network_service:", "目标服务"), ("protocol:", "协议"), ("technique:", "攻击技术")):
        if value.startswith(prefix):
            return label + " " + value.removeprefix(prefix).upper()
    return value
