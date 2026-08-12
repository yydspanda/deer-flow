"""Compare SOC memory retrieval v1/v2 over persisted real-alert requests."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from soc_agent.contracts import (
    ActorContext,
    LLMAnalysisRequest,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryRecord,
    SocMemoryTargetArtifact,
)
from soc_agent.core.service import SocMemoryService
from soc_agent.memory import (
    MEMORY_RETRIEVAL_POLICY_V1,
    InMemoryMemoryCandidateRepository,
    build_memory_retrieval_diff,
    memory_query_from_analysis_request,
)
from soc_agent.utils.hashing import stable_hash

_ANCHOR_PRIORITY = (
    "detection_key",
    "rule_code",
    "behavior_fingerprint",
    "scenario_key",
    "role_entity",
    "entity",
)


def build_report(input_dir: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for path in sorted(input_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_payload = payload.get("analysis_run")
        request_payload = (
            run_payload.get("llm_analysis_request")
            if isinstance(run_payload, dict)
            else None
        )
        if not isinstance(request_payload, dict):
            continue
        request = LLMAnalysisRequest.model_validate(request_payload)
        v2_query = memory_query_from_analysis_request(request).model_copy(
            update={"limit": 10, "max_tokens": 5000}
        )
        v1_query = memory_query_from_analysis_request(
            request,
            policy_version=MEMORY_RETRIEVAL_POLICY_V1,
        ).model_copy(update={"limit": 10, "max_tokens": 5000})
        anchor = _first_anchor(v2_query.facets)
        repository = InMemoryMemoryCandidateRepository()
        source_type = v2_query.facets.get("source_type", ["unknown"])[0]
        repository.save_memory_record(
            _record(
                request,
                suffix="BROAD",
                facets={"source_type": [source_type]},
            )
        )
        if anchor is not None:
            anchor_key, anchor_value = anchor
            repository.save_memory_record(
                _record(
                    request,
                    suffix="EXACT",
                    facets={
                        "source_type": [source_type],
                        anchor_key: [anchor_value],
                    },
                )
            )

        service = SocMemoryService(
            record_repository=repository,
            now_provider=lambda: datetime.now(UTC),
        )
        baseline = service.find_relevant_records(v1_query)
        current = service.find_relevant_records(v2_query)
        diff = build_memory_retrieval_diff(baseline, current)
        rows.append(
            {
                "source_file": path.name,
                "alert_id": request.alert_id,
                "source_type": request.source.source_type.value,
                "rule_code_present": bool(request.detection.rule_code),
                "detection_key_present": bool(request.detection.detection_key),
                "environment": request.environment,
                "scenario_keys": v2_query.facets.get("scenario_key", []),
                "behavior_fingerprint_present": bool(
                    v2_query.facets.get("behavior_fingerprint")
                ),
                "role_entities": v2_query.facets.get("role_entity", []),
                "selected_fixture_anchor": (
                    {"key": anchor[0], "value": anchor[1]}
                    if anchor is not None
                    else None
                ),
                "v1_match_ids": [item.memory_id for item in baseline.matches],
                "v2_match_ids": [item.memory_id for item in current.matches],
                "v2_skipped_missing_strong_anchor": (
                    current.skipped_missing_strong_anchor
                ),
                "retrieval_diff": diff.model_dump(
                    mode="json",
                    exclude={"schema_version"},
                ),
            }
        )

    return {
        "schema_version": "soc.memory_retrieval_v2_validation.v1",
        "evaluation_scope": "real_alert_queries_with_controlled_memory_fixtures",
        "claims": {
            "proves": [
                "real normalized requests produce replay-stable v2 facets",
                "an exact type-appropriate anchor survives retrieval",
                "a same-source-only memory is rejected by v2",
            ],
            "does_not_prove": [
                "production memory precision",
                "production memory recall",
                "quality of future human-confirmed memory records",
            ],
        },
        "summary": {
            "sample_count": len(rows),
            "rule_code_missing_count": sum(
                not row["rule_code_present"] for row in rows
            ),
            "detection_key_missing_count": sum(
                not row["detection_key_present"] for row in rows
            ),
            "behavior_fingerprint_count": sum(
                row["behavior_fingerprint_present"] for row in rows
            ),
            "scenario_facet_count": sum(bool(row["scenario_keys"]) for row in rows),
            "role_entity_facet_count": sum(bool(row["role_entities"]) for row in rows),
            "exact_fixture_retained_count": sum(
                any(memory_id.endswith("-EXACT") for memory_id in row["v2_match_ids"])
                for row in rows
            ),
            "broad_fixture_filtered_count": sum(
                not any(
                    memory_id.endswith("-BROAD") for memory_id in row["v2_match_ids"]
                )
                for row in rows
            ),
        },
        "samples": rows,
    }


def _first_anchor(
    facets: dict[str, list[str]],
) -> tuple[str, str] | None:
    for key in _ANCHOR_PRIORITY:
        values = facets.get(key, [])
        if values:
            return key, values[0]
    return None


def _record(
    request: LLMAnalysisRequest,
    *,
    suffix: str,
    facets: dict[str, list[str]],
) -> SocMemoryRecord:
    now = datetime.now(UTC)
    identity = stable_hash(
        {
            "alert_id": request.alert_id,
            "suffix": suffix,
        }
    )[:12].upper()
    return SocMemoryRecord(
        memory_id=f"MEM-{identity}-{suffix}",
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope=request.tenant_id or "global",
        tenant_id=request.tenant_id,
        source_candidate_id=f"MC-{identity}-{suffix}",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.EVAL_FIXTURE,
            source_id=f"memory-retrieval-v2:{request.alert_id}:{suffix}",
        ),
        summary=f"Controlled {suffix.lower()} retrieval fixture",
        content="This is a validation-only memory record, not production knowledge.",
        facets=facets,
        evidence_refs=[f"validation:{request.alert_id}:{suffix}"],
        validity=SocMemoryCandidateValidity(
            valid_from=now - timedelta(days=1),
            notes="Controlled retrieval validation fixture.",
        ),
        confidence=0.8,
        content_hash=stable_hash({"identity": identity, "content": suffix}),
        facets_hash=stable_hash(facets),
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=now + timedelta(days=30),
        retrieval_review_due_at=now + timedelta(days=7),
        retrieval_updated_by=ActorContext(actor_id="validation-memory-governor"),
        retrieval_updated_at=now,
        retrieval_reason="Enabled only inside the controlled validation repository.",
        created_by=ActorContext(actor_id="validation-builder"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = build_report(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
