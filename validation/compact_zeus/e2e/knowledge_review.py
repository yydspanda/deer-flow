"""Compile inert analyzer knowledge suggestions into one human-review package."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

CASE_REVIEW_SCHEMA_VERSION = "soc.validation.knowledge_candidate_case_review.v1"
PACKAGE_SCHEMA_VERSION = "soc.validation.knowledge_candidate_review_package.v1"

_TENANT_TERMS = (
    "pingan",
    "paic",
    "平安",
    "zeus",
    "天眼",
    "青藤",
    "内网",
    "d盘",
)
_GOVERNED_CONTEXT_TERMS = (
    "授权测试",
    "红队",
    "蓝队",
    "白帽",
    "护网",
    "维护窗口",
    "stg",
    "dev环境",
    "测试环境",
    "白名单",
)
_PROVIDER_TERMS = (
    "cmdb",
    "威胁情报",
    "资产查询",
    "资产归属",
    "安全标签",
    "接口查询",
    "tool调用",
    "mcp调用",
)
_ADAPTER_TERMS = (
    "字段映射",
    "字段语义",
    "解析规则",
    "schema",
    "adapter",
    "normalizer",
    "上游字段",
)
_POLICY_TERMS = (
    "处置策略",
    "抑制策略",
    "自动关闭",
    "封禁策略",
    "隔离策略",
    "响应策略",
)
_IPV4_RE = re.compile(r"(?<![0-9])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![0-9])")
_HASH_RE = re.compile(r"\b[a-f0-9]{32}(?:[a-f0-9]{8}|[a-f0-9]{32})?\b", re.IGNORECASE)
_DOMAIN_RE = re.compile(
    r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}\b",
    re.IGNORECASE,
)


def compile_case_knowledge_review(
    *,
    alert_id: str,
    run_id: str,
    source: Mapping[str, Any],
    analysis: Mapping[str, Any],
    grounding: Mapping[str, Any],
) -> dict[str, Any]:
    """Create a review-only projection for one analyzer result."""

    evidence_by_ref = {
        str(item.get("evidence_ref")): dict(item)
        for item in analysis.get("evidence") or []
        if isinstance(item, Mapping) and item.get("evidence_ref")
    }
    reasoning_by_ref = {
        str(item.get("reasoning_id")): dict(item)
        for item in analysis.get("reasoning") or []
        if isinstance(item, Mapping) and item.get("reasoning_id")
    }
    evidence_grounding = {
        str(item.get("evidence_ref")): str(item.get("status") or "missing")
        for item in grounding.get("items") or []
        if isinstance(item, Mapping) and item.get("evidence_ref")
    }
    reasoning_grounding = {
        str(item.get("reasoning_id")): str(item.get("status") or "missing")
        for item in grounding.get("reasoning_items") or []
        if isinstance(item, Mapping) and item.get("reasoning_id")
    }

    candidates: list[dict[str, Any]] = []
    for raw_candidate in analysis.get("knowledge_candidates") or []:
        if not isinstance(raw_candidate, Mapping):
            continue
        candidate = dict(raw_candidate)
        evidence_refs = [str(item) for item in candidate.get("evidence_refs") or []]
        reasoning_refs = [str(item) for item in candidate.get("reasoning_refs") or []]
        evidence_statuses = {
            ref: evidence_grounding.get(ref, "missing") for ref in evidence_refs
        }
        reasoning_statuses = {
            ref: reasoning_grounding.get(ref, "missing") for ref in reasoning_refs
        }
        support_grounded = bool(evidence_refs and reasoning_refs) and all(
            status == "grounded"
            for status in (*evidence_statuses.values(), *reasoning_statuses.values())
        )
        recommended_destination, classification_reasons = _recommend_destination(
            candidate
        )
        candidates.append(
            {
                "candidate_id": candidate.get("candidate_id"),
                "statement": candidate.get("statement"),
                "model_destination_hint": candidate.get("destination_hint"),
                "model_scope_hint": candidate.get("scope_hint"),
                "recommended_destination": recommended_destination,
                "classification_reasons": classification_reasons,
                "rationale": candidate.get("rationale"),
                "support_status": "grounded" if support_grounded else "unresolved",
                "evidence_support": [
                    {
                        "evidence_ref": ref,
                        "grounding_status": evidence_statuses[ref],
                        "fact": evidence_by_ref.get(ref),
                    }
                    for ref in evidence_refs
                ],
                "reasoning_support": [
                    {
                        "reasoning_ref": ref,
                        "grounding_status": reasoning_statuses[ref],
                        "reasoning": reasoning_by_ref.get(ref),
                    }
                    for ref in reasoning_refs
                ],
                "review_status": "pending_review",
                "memory_write_performed": False,
                "skill_change_performed": False,
                "decision_impact": "none",
            }
        )

    return {
        "schema_version": CASE_REVIEW_SCHEMA_VERSION,
        "alert_id": alert_id,
        "run_id": run_id,
        "source": dict(source),
        "candidate_count": len(candidates),
        "grounded_candidate_count": sum(
            item["support_status"] == "grounded" for item in candidates
        ),
        "policy": {
            "human_review_required": True,
            "auto_write_memory": False,
            "auto_modify_skill": False,
            "auto_modify_adapter_or_policy": False,
            "runtime_decision_impact": "none",
        },
        "candidates": candidates,
    }


def compile_knowledge_review_package(
    case_reviews: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Deduplicate exact statements while retaining every alert occurrence."""

    grouped: dict[str, dict[str, Any]] = {}
    raw_count = 0
    for case_review in case_reviews:
        for candidate_value in case_review.get("candidates") or []:
            if not isinstance(candidate_value, Mapping):
                continue
            raw_count += 1
            candidate = dict(candidate_value)
            statement = str(candidate.get("statement") or "").strip()
            normalized = _normalize_statement(statement)
            digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            group = grouped.setdefault(
                digest,
                {
                    "review_candidate_id": f"KR-{digest[:12].upper()}",
                    "statement": statement,
                    "statement_sha256": digest,
                    "model_destination_hints": set(),
                    "model_scope_hints": set(),
                    "recommended_destinations": set(),
                    "classification_reasons": set(),
                    "occurrences": [],
                },
            )
            group["model_destination_hints"].add(
                str(candidate.get("model_destination_hint") or "")
            )
            group["model_scope_hints"].add(str(candidate.get("model_scope_hint") or ""))
            group["recommended_destinations"].add(
                str(candidate.get("recommended_destination") or "reject_or_verify")
            )
            group["classification_reasons"].update(
                str(item) for item in candidate.get("classification_reasons") or []
            )
            group["occurrences"].append(
                {
                    "alert_id": case_review.get("alert_id"),
                    "run_id": case_review.get("run_id"),
                    "source": case_review.get("source"),
                    "candidate_id": candidate.get("candidate_id"),
                    "support_status": candidate.get("support_status"),
                    "evidence_support": candidate.get("evidence_support"),
                    "reasoning_support": candidate.get("reasoning_support"),
                    "rationale": candidate.get("rationale"),
                }
            )

    review_candidates: list[dict[str, Any]] = []
    for digest, group in sorted(grouped.items()):
        destinations = sorted(
            item for item in group["recommended_destinations"] if item
        )
        final_destination = (
            destinations[0] if len(destinations) == 1 else "reject_or_verify"
        )
        occurrences = group["occurrences"]
        review_candidates.append(
            {
                "review_candidate_id": group["review_candidate_id"],
                "statement": group["statement"],
                "statement_sha256": digest,
                "occurrence_count": len(occurrences),
                "distinct_alert_count": len(
                    {str(item.get("alert_id")) for item in occurrences}
                ),
                "all_support_grounded": all(
                    item.get("support_status") == "grounded" for item in occurrences
                ),
                "model_destination_hints": sorted(
                    item for item in group["model_destination_hints"] if item
                ),
                "model_scope_hints": sorted(
                    item for item in group["model_scope_hints"] if item
                ),
                "recommended_destination": final_destination,
                "classification_reasons": sorted(group["classification_reasons"]),
                "occurrences": occurrences,
                "review": {
                    "status": "pending_review",
                    "reviewer": None,
                    "reviewed_at": None,
                    "decision": None,
                    "target_id": None,
                    "reason": None,
                },
                "memory_write_performed": False,
                "skill_change_performed": False,
                "decision_impact": "none",
            }
        )

    destinations = Counter(
        str(item["recommended_destination"]) for item in review_candidates
    )
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "pending_human_review" if review_candidates else "no_candidates",
        "policy": {
            "candidate_is_not_memory": True,
            "candidate_is_not_current_alert_evidence": True,
            "candidate_is_not_a_runtime_decision": True,
            "no_automatic_write_or_activation": True,
            "exact_statement_deduplication_only": True,
        },
        "summary": {
            "case_count": len(case_reviews),
            "raw_candidate_count": raw_count,
            "review_candidate_count": len(review_candidates),
            "grounded_review_candidate_count": sum(
                item["all_support_grounded"] for item in review_candidates
            ),
            "destination_counts": dict(sorted(destinations.items())),
        },
        "candidates": review_candidates,
    }


def render_knowledge_review_markdown(package: Mapping[str, Any]) -> str:
    summary = (
        package.get("summary") if isinstance(package.get("summary"), Mapping) else {}
    )
    lines = [
        "# PingAn Knowledge Candidate Review",
        "",
        f"- Status / 状态: `{package.get('status')}`",
        f"- Raw suggestions / 原始建议: `{summary.get('raw_candidate_count', 0)}`",
        f"- Review items / 去重审核项: `{summary.get('review_candidate_count', 0)}`",
        "- Boundary / 边界: 候选不是事实、Memory 或 Skill；审核前不影响任何 Runtime 结论。",
        "",
        "| ID | Candidate / 候选 | Suggested destination / 建议落点 | Support / 支撑 | Alerts / 告警数 | Review / 审核 |",
        "| --- | --- | --- | --- | ---: | --- |",
    ]
    for item in package.get("candidates") or []:
        if not isinstance(item, Mapping):
            continue
        lines.append(
            "| "
            + " | ".join(
                [
                    _markdown(item.get("review_candidate_id")),
                    _markdown(item.get("statement")),
                    _markdown(item.get("recommended_destination")),
                    "grounded" if item.get("all_support_grounded") else "unresolved",
                    str(item.get("distinct_alert_count") or 0),
                    "pending_review",
                ]
            )
            + " |"
        )
    lines.extend(
        [
            "",
            "## Review Rules / 审核规则",
            "",
            "- `general_skill`: 仅接收跨租户、可执行、可评测的通用研判方法。",
            "- `tenant_memory`: 仅接收有来源、范围和有效期的平安经验；确认后仍需单独激活检索。",
            "- `governed_context`: 授权测试、护网身份、维护窗口和安全标签等运营事实。",
            "- `provider_requirement` / `adapter_mapping` / `tenant_policy`: 进入对应工程台账，不写 Memory。",
            "- `reject_or_verify`: 单次 IOC、模型猜测、支撑未落地或落点冲突，默认拒绝或补证。",
            "",
        ]
    )
    return "\n".join(lines)


def _recommend_destination(candidate: Mapping[str, Any]) -> tuple[str, list[str]]:
    statement = str(candidate.get("statement") or "")
    rationale = str(candidate.get("rationale") or "")
    scope = str(candidate.get("scope_hint") or "")
    hint = str(candidate.get("destination_hint") or "reject_or_verify")
    combined = f"{statement} {rationale}".casefold()

    if any(term.casefold() in combined for term in _GOVERNED_CONTEXT_TERMS):
        return "governed_context", ["authorized_or_time_bounded_operational_fact"]
    if any(term.casefold() in combined for term in _PROVIDER_TERMS):
        return "provider_requirement", ["requires_external_read_only_capability"]
    if any(term.casefold() in combined for term in _ADAPTER_TERMS):
        return "adapter_mapping", ["vendor_input_contract_or_parser_change"]
    if any(term.casefold() in combined for term in _POLICY_TERMS):
        return "tenant_policy", ["tenant_specific_response_policy"]
    if any(term.casefold() in combined for term in _TENANT_TERMS):
        return "tenant_memory", ["tenant_specific_operational_knowledge"]
    if scope == "event" or _contains_concrete_indicator(combined):
        return "reject_or_verify", ["event_specific_or_volatile_indicator"]
    if hint == "general_skill" and scope == "global":
        return "general_skill", ["model_proposed_cross_tenant_method"]
    if hint in {
        "tenant_memory",
        "governed_context",
        "provider_requirement",
        "adapter_mapping",
        "tenant_policy",
        "evaluation_fixture",
        "reject_or_verify",
    }:
        return hint, ["model_hint_retained_for_human_review"]
    return "reject_or_verify", ["destination_not_safely_determined"]


def _contains_concrete_indicator(value: str) -> bool:
    return bool(
        _IPV4_RE.search(value) or _HASH_RE.search(value) or _DOMAIN_RE.search(value)
    )


def _normalize_statement(value: str) -> str:
    return " ".join(value.casefold().split())


def _markdown(value: Any) -> str:
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


__all__ = [
    "CASE_REVIEW_SCHEMA_VERSION",
    "PACKAGE_SCHEMA_VERSION",
    "canonical_json_sha256",
    "compile_case_knowledge_review",
    "compile_knowledge_review_package",
    "render_knowledge_review_markdown",
]
