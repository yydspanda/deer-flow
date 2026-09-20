"""On-demand, source-backed reuse limits, separate from cohort consensus."""

from collections import Counter, defaultdict

from soc_agent.contracts import SocMemoryCandidateType, SocMemoryQuery
from soc_agent.memory.facets import filter_learning_entity_facets
from soc_agent.memory.scoring import evaluate_memory_scope
from soc_agent.utils.model_json import model_json

ENTITY_PREFIXES = {"ip", "host", "user", "account", "asset", "domain", "source", "destination", "attacker", "victim"}


def scope_samples(candidate, repository):
    metadata = {**candidate.source.metadata, **candidate.metadata}
    ids = set(metadata.get("observation_ids", []))
    getter = getattr(repository, "get_memory_pattern_observation", None)
    samples = []
    if getter:
        for identifier in sorted(ids):
            item = getter(identifier)
            if item is None or item.tenant_id != candidate.tenant_id:
                continue
            spec = candidate.applicability
            if spec and (item.profile_id, item.profile_version, item.feature_schema_version) != (spec.profile_id, spec.profile_version, spec.feature_schema_version):
                continue
            samples.append(
                {
                    "observation_id": identifier,
                    "source_id": item.source.source_id,
                    "alert_id": item.source.alert_id,
                    "run_id": item.source.run_id,
                    "facets": {**item.signature.facets, "environment": [item.environment]},
                    "scope_bindings": getattr(item.signature, "scope_bindings", []),
                    "projection_gaps": getattr(item.signature, "projection_gaps", []),
                }
            )
    if not ids:
        samples.append({"source_id": candidate.source.source_id or candidate.candidate_id, "alert_id": candidate.source.alert_id, "run_id": candidate.source.run_id, "facets": candidate.facets})
    run_getter = getattr(repository, "get_run", None)
    for sample in samples:
        if not sample.get("scope_bindings") and run_getter and sample.get("run_id"):
            run = run_getter(sample["run_id"])
            if run and run.llm_analysis_request and run.llm_analysis_request.tenant_id == candidate.tenant_id:
                from soc_agent.memory.scope_bindings import scope_bindings

                sample["scope_bindings"] = scope_bindings(run.llm_analysis_request)
        sample["facets"] = {key: list(values) for key, values in sample["facets"].items()}
        for binding in sample.get("scope_bindings", []):
            for key, values in binding.facets.items():
                sample["facets"][key] = sorted(set(sample["facets"].get(key, [])) | set(values))
    return samples


def option_page(candidate, repository, *, facet_key=None, prefix=None, search="", offset=0, limit=10):
    samples = scope_samples(candidate, repository)
    sources = defaultdict(dict)
    for sample in samples:
        # Source facts and object coverage remain complete; only new choices
        # obey the applicability condition's existing strip-based limit.
        selectable = filter_learning_entity_facets(sample["facets"])
        for key in ("entity", "role_entity"):
            for value in selectable.get(key, []):
                kind, separator, _ = value.partition(":")
                if separator and kind in ENTITY_PREFIXES:
                    sources[(key, kind, value)][sample["source_id"]] = sample
    groups = sorted({(key, kind) for key, kind, _ in sources})
    entries = []
    for (key, kind, value), occurrences in sources.items():
        if facet_key != key or prefix != kind or search.casefold() not in value.casefold():
            continue
        current = any(s["alert_id"] == candidate.source.alert_id for s in occurrences.values())
        entries.append(
            {"facet_key": key, "value_prefix": kind, "value": value, "sample_count": len(occurrences), "from_current_alert": current, "source_alert_ids": sorted({s["alert_id"] for s in occurrences.values() if s["alert_id"]})[:5]}
        )
    entries.sort(key=lambda item: (not item["from_current_alert"], -item["sample_count"], item["value"]))
    return {"groups": [{"facet_key": key, "value_prefix": kind} for key, kind in groups], "items": entries[offset : offset + limit], "total": len(entries), "offset": offset, "limit": limit, "source_sample_count": len(samples)}


def candidate_with_scope_selections(candidate, repository, selections):
    """Validate only chosen values; never ship a complete entity union to the UI."""
    if selections and any(len(value.strip()) > 512 for key in ("entity", "role_entity") for value in selections.get(key, [])):
        raise ValueError("附加实体条件不能超过 512 字符，请选择其他适用条件")
    if not selections or candidate.applicability is None:
        return candidate
    optional = {key: list(values) for key, values in candidate.applicability.optional_facets.items()}
    missing = {key: set(values) - set(optional.get(key, [])) for key, values in selections.items()}
    if not any(missing.values()):
        return candidate
    samples = scope_samples(candidate, repository)
    for key, values in missing.items():
        if not values:
            continue
        if key not in {"entity", "role_entity"} or any(value.partition(":")[0] not in ENTITY_PREFIXES for value in values):
            raise ValueError("附加条件只能来自候选来源样本中的真实实体")
        allowed = {value for sample in samples for value in sample["facets"].get(key, [])}
        if not values <= allowed:
            raise ValueError("所选实体不在候选来源样本中，请刷新后重新选择")
        optional[key] = sorted(set(optional.get(key, [])) | values)
        if len(optional[key]) > 100:
            raise ValueError("一次审核最多保留 100 个同维度实体值，请按业务范围细分")
    spec = candidate.applicability.model_copy(update={"optional_facets": optional})
    return candidate.model_copy(update={"applicability": spec})


def preview_sample_coverage(candidate, repository, spec):
    samples = scope_samples(candidate, repository)
    counts = {"total": len(samples), "applicable": 0, "partial": 0, "not_applicable": 0}
    for sample in samples:
        report = sample_scope_report(sample, spec)
        counts[report.status.value if report.status.value in counts else "not_applicable"] += 1
    return counts


def sample_scope_report(sample, spec):
    query = SocMemoryQuery(
        facets=sample["facets"],
        scope_bindings=sample.get("scope_bindings", []),
        projection_gaps=sample.get("projection_gaps", []),
        metadata={"memory_profile_id": spec.profile_id, "memory_profile_version": spec.profile_version, "memory_feature_schema_version": spec.feature_schema_version},
    )
    return evaluate_memory_scope(spec, SocMemoryCandidateType.DETECTION_LESSON, query, {})


def sample_matches(sample, spec):
    return sample_scope_report(sample, spec).status.value == "applicable"


def scope_lesson_sources(candidate, repository, spec):
    """Build an ephemeral drafting snapshot only when limits exclude sources."""
    samples = scope_samples(candidate, repository)
    covered = [sample for sample in samples if sample_matches(sample, spec)]
    if len(covered) == len(samples):
        return candidate
    if not covered:
        raise ValueError("所选条件没有共同覆盖来源样本，不能生成该范围的经验")
    getter = getattr(repository, "get_memory_pattern_observation", None)
    lessons = []
    facets = {}
    for sample in covered:
        for key, values in sample["facets"].items():
            facets.setdefault(key, set()).update(values)
        item = getter(sample["observation_id"]) if getter and sample.get("observation_id") else None
        lesson = getattr(item, "lesson", None)
        lessons.append({"alert_id": sample["alert_id"], "observed_conclusion": lesson.model_dump(mode="json") if lesson else None})
    counts = Counter(item["observed_conclusion"]["verdict"] for item in lessons if item["observed_conclusion"])
    metadata = {key: value for key, value in candidate.metadata.items() if key not in {"cohort_quality", "representative_run_ids", "representative_alert_ids"}}
    metadata.update(
        {
            "observation_ids": [s["observation_id"] for s in covered if s.get("observation_id")],
            "support_count": len(covered),
            "distinct_source_count": len(covered),
            "cohort_quality": {"support_count": len(covered), "distinct_source_count": len(covered), "verdict_counts": dict(counts)},
        }
    )
    # No unselected source narrative is carried into this narrower draft.
    return candidate.model_copy(
        update={
            "summary": f"所选适用范围内的 {len(covered)} 条告警",
            "content": model_json(lessons),
            "facets": filter_learning_entity_facets({k: sorted(v) for k, v in facets.items()}),
            "metadata": metadata,
            "evidence_refs": [],
            "source": candidate.source.model_copy(update={"alert_id": covered[0]["alert_id"], "run_id": covered[0]["run_id"], "metadata": {"observation_ids": metadata["observation_ids"]}}),
        }
    )
