"""Compare typed scopes, not lesson wording or a retrieval top-K sample."""

from collections.abc import Iterator
from datetime import datetime

from soc_agent.contracts import SocMemoryApplicabilitySpec, SocMemoryCandidate, SocMemoryCandidateCreateCommand, SocMemoryRecord, SocMemoryRecordStatus, Verdict
from soc_agent.contracts.memory_governance import MemoryGovernancePreview, MemoryScopeDifference, RelatedGovernedMemory
from soc_agent.memory.profiles import SocMemoryProfileRegistry
from soc_agent.protocols import MemoryRecordRepository
from soc_agent.utils.hashing import stable_hash


def normalized_facets(values: dict[str, list[str]]) -> dict[str, list[str]]:
    return {key.casefold(): sorted({v.strip().casefold() for v in items}) for key, items in sorted(values.items())}


def scope_identity(item: SocMemoryCandidate | SocMemoryCandidateCreateCommand) -> str | None:
    spec = item.applicability
    if spec is None or not spec.required_facets.get("behavior_fingerprint"):
        return None
    return stable_hash(
        {
            "tenant_scope": item.tenant_scope,
            "tenant_id": item.tenant_id,
            "profile": [spec.profile_id, spec.profile_version, spec.feature_schema_version],
            "required": normalized_facets(spec.required_facets),
            "optional": normalized_facets(spec.optional_facets) if spec.minimum_optional_matches else {},
            "minimum_optional_matches": spec.minimum_optional_matches,
            "minimum_strong_anchor_matches": spec.minimum_strong_anchor_matches,
            "excluded": normalized_facets(spec.excluded_facets),
            "data_class": item.metadata.get("data_class"),
        }
    )


def scope_relation(left: SocMemoryApplicabilitySpec | None, right: SocMemoryApplicabilitySpec | None, registry: SocMemoryProfileRegistry) -> str:
    if left is None or right is None:
        return "unknown"
    if (left.profile_id, left.profile_version, left.feature_schema_version) != (right.profile_id, right.profile_version, right.feature_schema_version):
        return "disjoint"
    a, b = normalized_facets(left.required_facets), normalized_facets(right.required_facets)
    ax, bx = normalized_facets(left.excluded_facets), normalized_facets(right.excluded_facets)
    profile = registry.get(left.profile_id)
    # Only a Profile can promise a facet has one value per alert. Entity lists cannot.
    exclusive = getattr(profile, "exclusive_scope_facet_keys", frozenset())
    for key in set(a) | set(b):
        av, bv = set(a.get(key, [])), set(b.get(key, []))
        if (av and av <= set(bx.get(key, []))) or (bv and bv <= set(ax.get(key, []))):
            return "disjoint"
        if key in exclusive and av and bv and not av & bv:
            return "disjoint"
    if a == b and ax == bx and left.minimum_optional_matches == right.minimum_optional_matches and left.minimum_strong_anchor_matches == right.minimum_strong_anchor_matches:
        if not left.minimum_optional_matches or normalized_facets(left.optional_facets) == normalized_facets(right.optional_facets):
            return "same"
    return "overlap"


def governed_records(repository: MemoryRecordRepository, tenant_id: str | None, *, enabled_only: bool = False) -> Iterator[SocMemoryRecord]:
    offset = 0
    while True:
        page = repository.list_memory_records(status=SocMemoryRecordStatus.CONFIRMED, retrieval_enabled=True if enabled_only else None, limit=200, offset=offset)
        for record in page:
            if tenant_id is None or record.tenant_id in {None, tenant_id}:
                yield record
        if len(page) < 200:
            return
        offset += len(page)


def validity_overlaps(record: SocMemoryRecord, *, valid_from: datetime, valid_until: datetime | None) -> bool:
    ends = [value for value in (record.validity.valid_until, record.retrieval_valid_until, record.retrieval_review_due_at) if value is not None]
    end = min(ends) if ends else None
    return (end is None or end > valid_from) and (valid_until is None or record.validity.valid_from < valid_until)


def assessed_verdict(record: SocMemoryRecord) -> Verdict | None:
    return record.reviewed_verdict or (record.decision_directive.target_verdict if record.decision_directive else None)


def preview_governance(candidate: SocMemoryCandidate, records: list[SocMemoryRecord], *, registry: SocMemoryProfileRegistry, reviewer_verdict: Verdict | None, used_ids: set[str] | None = None) -> MemoryGovernancePreview:
    related = []
    used_ids = used_ids or set()
    for record in records:
        if record.source_candidate_id == candidate.candidate_id:
            continue
        relation = scope_relation(candidate.applicability, record.applicability, registry)
        shared = any(set(candidate.facets.get(key, [])) & set(record.facets.get(key, [])) for key in ("behavior_fingerprint", "detection_key"))
        if record.memory_id not in used_ids and not shared:
            continue
        verdict = assessed_verdict(record)
        conclusion_relation = "undetermined" if reviewer_verdict in {None, Verdict.UNKNOWN, Verdict.SUSPICIOUS} or verdict in {None, Verdict.UNKNOWN, Verdict.SUSPICIOUS} else "agrees" if verdict == reviewer_verdict else "differs"
        a = candidate.applicability.required_facets if candidate.applicability else {}
        b = record.applicability.required_facets if record.applicability else {}
        differences = [
            MemoryScopeDifference(facet=key, candidate_values=a.get(key, []), memory_values=b.get(key, [])) for key in sorted(set(a) | set(b)) if normalized_facets({key: a.get(key, [])}) != normalized_facets({key: b.get(key, [])})
        ]
        related.append(
            RelatedGovernedMemory(
                memory_id=record.memory_id,
                version=record.version,
                summary=record.summary[:1000],
                conclusion=(record.business_lesson.conclusion if record.business_lesson else record.summary)[:2000],
                reviewed_verdict=verdict,
                scope_relation=relation,
                conclusion_relation=conclusion_relation,
                retrieved_in_source_run=record.memory_id in used_ids,
                retrieval_enabled=record.retrieval_enabled,
                directive_enabled=record.decision_directive is not None,
                differences=differences,
            )
        )
    related.sort(key=lambda item: (item.scope_relation != "same", item.conclusion_relation != "differs", not item.retrieved_in_source_run, item.memory_id))
    if any(item.scope_relation in {"same", "overlap"} and item.conclusion_relation == "differs" for item in related):
        recommendation, explanation = "revise", "本次审核结论与已有经验不同，适用范围仍可能重叠。请修订已有经验，或先用可验证条件区分两者。"
    elif any(item.scope_relation == "same" and item.conclusion_relation == "agrees" for item in related):
        recommendation, explanation = "reinforce", "相同适用范围已有一致的审核结论。优先补充原经验；有新增业务知识时，可在此确认修订。"
    elif any(item.scope_relation in {"same", "overlap", "unknown"} for item in related):
        recommendation, explanation = "inspect", "已有相关经验。可疑或转交建议不是已确认的相反事实，请结合本次业务事实确定是否需要修订。"
    elif related:
        recommendation, explanation = "distinguish", "相关经验的精确适用范围不同，可以保留分别适用的结论；请在业务描述中解释这些差异。"
    else:
        recommendation, explanation = "new", "没有发现相同或相关范围的已确认经验，可按当前业务事实审核沉淀。"
    return MemoryGovernancePreview(candidate_id=candidate.candidate_id, reviewer_verdict=reviewer_verdict, recommendation=recommendation, explanation=explanation, related_memories=related[:20], related_count=len(related))
