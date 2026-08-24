"""Build a read-only pattern-level Memory candidate review from Runtime outputs."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.contracts import (  # noqa: E402
    ActorContext,
    ActorType,
    EntrySurface,
    MemoryPatternAggregationPolicy,
    MemoryPatternDataClass,
    MemoryPatternSourceType,
    ServiceRequestContext,
)
from soc_agent.application import build_soc_memory_profile_registry  # noqa: E402
from soc_agent.core import SocMemoryPatternService  # noqa: E402
from soc_agent.integrations.pingan.memory import PingAnSocMemoryProfile  # noqa: E402
from soc_agent.memory import (  # noqa: E402
    InMemoryMemoryPatternRepository,
    MemoryPatternIneligibleError,
)
from validation.compact_zeus.memory.seed_confirmed_memory_from_batch import (  # noqa: E402
    load_analysis_runs,
)

REPORT_SCHEMA_VERSION = "soc.validation.pattern_memory_review.v1"


def build_pattern_memory_review(
    items_dir: Path,
    *,
    policy: MemoryPatternAggregationPolicy,
    environment: str,
) -> dict[str, Any]:
    """Aggregate completed alerts without confirming or activating Memory."""

    runs = load_analysis_runs(items_dir)
    repository = InMemoryMemoryPatternRepository()
    service = SocMemoryPatternService(
        repository=repository,
        candidate_repository=repository,
        policy=policy,
        profile_registry=build_soc_memory_profile_registry(),
    )
    context = ServiceRequestContext(
        actor=ActorContext(
            actor_id="pattern-memory-review",
            actor_type=ActorType.SERVICE,
            surface=EntrySurface.TEST,
            roles=["soc_batch_runner"],
        )
    )
    items: list[dict[str, Any]] = []
    ineligible: list[dict[str, str]] = []
    for run in runs:
        try:
            result = service.observe_run(
                run,
                source_type=MemoryPatternSourceType.BATCH_ALERT,
                transport_ref=(
                    f"pattern-review:{run.alert_id}:{run.input_hash or run.run_id}"
                ),
                environment=environment,
                data_class=MemoryPatternDataClass.SIMULATION,
                context=context,
            )
        except MemoryPatternIneligibleError as exc:
            ineligible.append({"alert_id": run.alert_id, "reason": str(exc)})
            continue
        items.append(
            {
                "alert_id": run.alert_id,
                "run_id": run.run_id,
                "aggregation_key": result.observation.aggregation_key,
                "lineage_key": result.observation.lineage_key,
                "pattern": result.observation.signature.model_dump(mode="json"),
                "lesson": result.observation.lesson.model_dump(mode="json")
                if result.observation.lesson is not None
                else None,
                "support_count": result.support_count,
                "distinct_source_count": result.distinct_source_count,
                "threshold_met": result.threshold_met,
                "cohort_quality": result.cohort_quality.model_dump(mode="json"),
                "candidate_id": (
                    result.candidate.candidate_id
                    if result.candidate is not None
                    else None
                ),
                "candidate_created": result.candidate_created,
                "candidate_coverage": result.candidate_coverage,
                "note": result.note,
            }
        )

    candidates = repository.list_memory_candidates(limit=10_000)
    latest_by_aggregation: dict[str, dict[str, Any]] = {}
    for item in items:
        latest_by_aggregation[item["aggregation_key"]] = item
    withheld = [
        item
        for item in latest_by_aggregation.values()
        if item["threshold_met"] and not item["cohort_quality"]["quality_gate_passed"]
    ]
    below_threshold = [
        item for item in latest_by_aggregation.values() if not item["threshold_met"]
    ]
    equivalent_lesson_cohorts = [
        item
        for item in latest_by_aggregation.values()
        if item["candidate_coverage"] == "equivalent_lesson"
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "input_items_dir": str(items_dir),
        "policy": policy.model_dump(mode="json"),
        "data_class": MemoryPatternDataClass.SIMULATION.value,
        "boundary": {
            "candidate_status": "pending_review_only",
            "confirmed_memory_created": False,
            "retrieval_activation_performed": False,
            "runtime_decision_mutated": False,
        },
        "summary": {
            "input_alert_count": len(runs),
            "eligible_observation_count": len(items),
            "ineligible_count": len(ineligible),
            "cohort_count": len(latest_by_aggregation),
            "candidate_count": len(candidates),
            "quality_withheld_cohort_count": len(withheld),
            "below_recurrence_threshold_cohort_count": len(below_threshold),
            "equivalent_lesson_cohort_count": len(equivalent_lesson_cohorts),
            "alert_to_candidate_ratio": (
                round(len(candidates) / len(runs), 4) if runs else 0.0
            ),
        },
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "candidate_type": candidate.candidate_type.value,
                "status": candidate.status.value,
                "summary": candidate.summary,
                "content": candidate.content,
                "facets": candidate.facets,
                "evidence_refs": candidate.evidence_refs,
                "metadata": candidate.metadata,
            }
            for candidate in candidates
        ],
        "withheld_cohorts": withheld,
        "equivalent_lesson_cohorts": equivalent_lesson_cohorts,
        "below_threshold_cohorts": below_threshold,
        "ineligible": ineligible,
        "items": items,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate Runtime outputs into quality-gated pattern Memory candidates",
    )
    parser.add_argument("--input-items", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--environment", default="validation")
    parser.add_argument(
        "--window-seconds",
        type=int,
        default=PingAnSocMemoryProfile.identity.aggregation_window_seconds,
    )
    parser.add_argument("--minimum-support", type=int, default=5)
    parser.add_argument("--minimum-distinct-sources", type=int, default=5)
    parser.add_argument("--minimum-conclusive-support", type=int, default=5)
    parser.add_argument("--minimum-consistency-ratio", type=float, default=0.8)
    args = parser.parse_args()

    report = build_pattern_memory_review(
        args.input_items,
        policy=MemoryPatternAggregationPolicy(
            window_seconds=args.window_seconds,
            minimum_support=args.minimum_support,
            minimum_distinct_sources=args.minimum_distinct_sources,
            minimum_conclusive_support=args.minimum_conclusive_support,
            minimum_consistency_ratio=args.minimum_consistency_ratio,
        ),
        environment=args.environment,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
