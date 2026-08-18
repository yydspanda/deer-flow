"""Validate and render reviewer-owned business lessons for confirmed Memory."""

from __future__ import annotations

from soc_agent.contracts import (
    SocMemoryApplicabilitySpec,
    SocMemoryBusinessLesson,
    SocMemoryCandidateReviewCommand,
)


def memory_lesson_applicability_conditions(
    applicability: SocMemoryApplicabilitySpec,
) -> list[str]:
    """Render the machine-owned applicability scope for a human lesson."""

    conditions = [f"Required canonical facet {key}: {', '.join(values)}" for key, values in sorted(applicability.required_facets.items())]
    if applicability.minimum_optional_matches:
        conditions.append(f"At least {applicability.minimum_optional_matches} reviewed optional facet groups must also match.")
    return conditions


def memory_lesson_invalidation_conditions(
    model_conditions: list[str],
) -> list[str]:
    """Add deterministic invalidation floors before model-authored details."""

    baseline = [
        "任一必需 canonical facet 与当前告警不匹配时，该经验失效。",
        "当前告警出现与已审核业务结论冲突的新证据或攻击影响时，必须重新研判。",
    ]
    normalized = [" ".join(str(value).split()) for value in model_conditions if str(value).strip()]
    return list(dict.fromkeys([*baseline, *normalized]))


def promote_memory_applicability_facets(
    applicability: SocMemoryApplicabilitySpec,
    promoted_facet_keys: list[str],
) -> SocMemoryApplicabilitySpec:
    """Deterministically narrow candidate scope using reviewed optional facets."""

    promoted = list(dict.fromkeys(str(key).strip() for key in promoted_facet_keys if str(key).strip()))
    unknown = sorted(set(promoted) - set(applicability.optional_facets))
    if unknown:
        raise ValueError("promoted memory facets are not candidate optional facets: " + ", ".join(unknown))
    if not promoted:
        return applicability
    required = {
        **applicability.required_facets,
        **{key: applicability.optional_facets[key] for key in promoted},
    }
    optional = {key: values for key, values in applicability.optional_facets.items() if key not in promoted}
    if applicability.minimum_optional_matches > len(optional):
        raise ValueError("promoted memory facets leave too few optional groups for the reviewed threshold")
    context_required = list(applicability.context_only_required_facet_keys)
    if context_required or applicability.context_only_missing_facet_keys or applicability.context_only_similarity_facet_keys:
        context_required = sorted({*context_required, *promoted})
    return applicability.model_copy(
        update={
            "required_facets": required,
            "optional_facets": optional,
            "context_only_required_facet_keys": context_required,
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
        ("结论 / Conclusion", [lesson.conclusion]),
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
