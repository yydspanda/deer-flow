"""Seed governed in-sample memory records from reviewed Runtime batch outputs."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "backend"
for import_root in (ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from soc_agent.contracts import (  # noqa: E402
    ActorContext,
    ActorType,
    AnalysisRun,
    EntrySurface,
    MemoryAdmissionStatus,
    ServiceRequestContext,
    SocMemoryCandidateCreateCommand,
    SocMemoryCandidateReviewCommand,
    SocMemoryCandidateReviewDecision,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryDecisionImpact,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryTargetArtifact,
)
from soc_agent.core import SocMemoryService  # noqa: E402
from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    to_sync_database_url,
    upgrade_soc_schema,
)
from soc_agent.memory import (  # noqa: E402
    MemoryAdmissionService,
    memory_facets_from_analysis_run,
)

REPORT_SCHEMA_VERSION = "soc.validation.in_sample_memory_seed.v1"
_LABELS = ["simulation", "in-sample", "user-approved", "memory-eval"]


def _enum_value(value: Any) -> str:
    """Return a stable text value for enum-backed and legacy string snapshots."""

    return str(getattr(value, "value", value))


def load_analysis_runs(items_dir: Path) -> list[AnalysisRun]:
    """Load complete AnalysisRun snapshots without reading raw source payload files."""

    runs: list[AnalysisRun] = []
    for path in sorted(items_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_payload = payload.get("analysis_run")
        if not isinstance(run_payload, dict):
            raise ValueError(f"{path} does not contain analysis_run")
        run = AnalysisRun.model_validate(run_payload)
        if run.analysis is None or run.llm_analysis_request is None:
            raise ValueError(f"{path} does not contain a completed analysis result")
        runs.append(run)
    if not runs:
        raise ValueError(f"no Runtime batch items found in {items_dir}")
    return runs


def build_candidate_command(
    run: AnalysisRun,
    *,
    source_batch_sha256: str,
    now: datetime,
) -> SocMemoryCandidateCreateCommand:
    """Build one explicitly simulated candidate through the shared facet contract."""

    analysis = run.analysis
    request = run.llm_analysis_request
    if analysis is None or request is None:
        raise ValueError(f"analysis run {run.run_id} is incomplete")

    facets = memory_facets_from_analysis_run(run)
    scenarios = [
        item.scenario_key or item.scenario_name
        for item in analysis.scenario_assessments
    ]
    direction = analysis.network_direction
    roles = [
        f"{_enum_value(item.role)}={item.value} ({_enum_value(item.status)})"
        for item in analysis.role_adjudication.roles
        if item.value is not None
    ]
    rule_label = (
        request.detection.rule_code
        or request.detection.detection_key
        or request.detection.rule_name
        or "unkeyed-detection"
    )
    evidence_refs = list(
        dict.fromkeys(
            [
                *analysis.decision_evidence_refs,
                *analysis.decision_reasoning_refs,
            ]
        )
    )
    if not evidence_refs:
        evidence_refs = [f"run:{run.run_id}"]

    content_lines = [
        "该记录来自用户明确批准的同批回放实验，不代表独立生产真值。",
        f"历史结论：{_enum_value(analysis.verdict)}，置信度 {analysis.confidence:.2f}。",
        f"历史理由：{analysis.reason}",
    ]
    if scenarios:
        content_lines.append(f"历史场景：{', '.join(scenarios)}。")
    if direction is not None:
        content_lines.append(
            "历史方向："
            f"{_enum_value(direction.boundary_direction)} / "
            f"{_enum_value(direction.observed_flow)}。"
        )
    if roles:
        content_lines.append(f"历史角色：{'; '.join(roles)}。")
    content_lines.append(
        "仅在当前告警命中受治理的强锚点时作为 M-* 历史经验；当前告警证据仍由 Runtime 独立分析。"
    )

    return SocMemoryCandidateCreateCommand(
        candidate_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        summary=(
            f"{_enum_value(request.source.source_type)} / {rule_label} "
            f"历史研判：{_enum_value(analysis.verdict)}"
        ),
        content="\n".join(content_lines),
        tenant_scope=request.tenant_id or "global",
        tenant_id=request.tenant_id,
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.EVAL_FIXTURE,
            source_surface=EntrySurface.CLI,
            source_id=f"in-sample-memory-eval:{source_batch_sha256}:{run.alert_id}",
            run_id=run.run_id,
            alert_id=run.alert_id,
            eval_sample_id=run.alert_id,
            metadata={
                "promote_to_memory": True,
                "simulation": True,
                "in_sample": True,
                "source_batch_sha256": source_batch_sha256,
            },
        ),
        evidence_refs=evidence_refs,
        validity=SocMemoryCandidateValidity(
            valid_from=now,
            valid_until=now + timedelta(days=30),
            review_after_days=7,
            notes=(
                "User-approved in-sample Runtime replay fixture; never treat as "
                "independent analyst truth or production knowledge."
            ),
        ),
        idempotency_key=(
            "memory-eval:in-sample:"
            f"{source_batch_sha256}:{run.alert_id}:{run.input_hash}"
        ),
        confidence=0.8,
        facets=facets,
        decision_impact=SocMemoryDecisionImpact.DETECTION_DECISION,
        review_owner="soc-memory-eval",
        labels=list(_LABELS),
        metadata={
            "promote_to_memory": True,
            "simulation": True,
            "in_sample": True,
            "source_batch_sha256": source_batch_sha256,
            "source_model_name": run.model_name,
            "source_prompt_version": run.prompt_version,
            "source_verdict": _enum_value(analysis.verdict),
        },
    )


def seed_confirmed_memory(
    runs: Sequence[AnalysisRun],
    *,
    repository: Any,
    source_batch_sha256: str,
    now: datetime,
) -> dict[str, Any]:
    """Admit, confirm, and retrieval-enable one simulation record per run."""

    admission_service = MemoryAdmissionService()
    memory_service = SocMemoryService(
        candidate_repository=repository,
        record_repository=repository,
        mutation_audit_repository=repository,
    )
    items: list[dict[str, Any]] = []
    for run in runs:
        command = build_candidate_command(
            run,
            source_batch_sha256=source_batch_sha256,
            now=now,
        )
        admission = admission_service.evaluate(command)
        if admission.status is not MemoryAdmissionStatus.ADMITTED:
            raise ValueError(
                f"memory candidate for alert {run.alert_id} was not admitted: "
                f"{[item.value for item in admission.reason_codes]}"
            )

        candidate = memory_service.propose_candidate(
            command,
            context=_context(run.alert_id, "propose", roles=["analyst"]),
        )
        record = repository.get_memory_record_by_candidate_id(candidate.candidate_id)
        if record is None:
            reviewed = memory_service.review_candidate(
                SocMemoryCandidateReviewCommand(
                    candidate_id=candidate.candidate_id,
                    decision=SocMemoryCandidateReviewDecision.CONFIRM,
                    reason=(
                        "用户明确批准这条历史结论进入同批 Memory 召回实验；"
                        "仅用于验证受治理链路，不代表生产真值。"
                    ),
                    metadata={"simulation": True, "in_sample": True},
                ),
                context=_context(
                    run.alert_id,
                    "confirm",
                    roles=["soc_memory_reviewer"],
                ),
            )
            candidate = reviewed.candidate
            record = reviewed.memory_record
        else:
            stored_candidate = repository.get_memory_candidate(candidate.candidate_id)
            if stored_candidate is not None:
                candidate = stored_candidate
        if record is None:
            raise ValueError(f"memory record was not created for alert {run.alert_id}")

        if not record.retrieval_enabled:
            record = memory_service.set_retrieval_activation(
                SocMemoryRetrievalActivationCommand(
                    memory_id=record.memory_id,
                    action=SocMemoryRetrievalActivationAction.ENABLE,
                    expected_record_version=record.version,
                    reason=(
                        "Enable bounded retrieval for the explicit in-sample Memory "
                        "selection experiment."
                    ),
                    activation_valid_until=now + timedelta(days=30),
                    review_after_days=7,
                    metadata={"simulation": True, "in_sample": True},
                ),
                context=_context(
                    run.alert_id,
                    "activate",
                    roles=["soc_memory_reviewer"],
                ),
            ).record

        items.append(
            {
                "alert_id": run.alert_id,
                "source_run_id": run.run_id,
                "candidate_id": candidate.candidate_id,
                "candidate_status": _enum_value(candidate.status),
                "memory_id": record.memory_id,
                "memory_type": _enum_value(record.memory_type),
                "memory_version": record.version,
                "retrieval_enabled": record.retrieval_enabled,
                "admission": admission.model_dump(mode="json"),
                "summary": record.summary,
                "facets": record.facets,
                "content_hash": record.content_hash,
                "facets_hash": record.facets_hash,
            }
        )

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        "data_class": "simulation",
        "evaluation_design": "same-alert in-sample retrieval wiring check",
        "source_batch_sha256": source_batch_sha256,
        "candidate_count": len(items),
        "confirmed_record_count": sum(
            item["candidate_status"] == "confirmed" for item in items
        ),
        "retrieval_enabled_count": sum(item["retrieval_enabled"] for item in items),
        "decision_directive_count": 0,
        "limitations": [
            "The records are derived from the same alerts that will be replayed.",
            "They validate admission, confirmation, activation, retrieval and M-* projection only.",
            "They do not provide independent accuracy or generalization evidence.",
            "No typed decision directive or action authority is attached.",
        ],
        "items": items,
    }


def _context(
    alert_id: str, operation: str, *, roles: list[str]
) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id="soc-memory-eval-reviewer",
            actor_type=ActorType.USER,
            surface=EntrySurface.CLI,
            roles=roles,
        ),
        trace_id=f"memory-eval:{alert_id}:{operation}",
        idempotency_key=f"memory-eval:{alert_id}:{operation}:v1",
    )


def _sha256_directory(items_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(items_dir.glob("*.json")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _harden_sqlite_database_files(database_url: str) -> None:
    """Keep local experiment databases private like the adjacent artifacts."""

    url = make_url(to_sync_database_url(database_url))
    if url.get_backend_name() != "sqlite" or not url.database:
        return
    database = Path(url.database).expanduser().resolve()
    for path in (
        database,
        database.with_name(f"{database.name}-wal"),
        database.with_name(f"{database.name}-shm"),
    ):
        if path.exists():
            path.chmod(0o600)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-items", type=Path, required=True)
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--init-db", action="store_true")
    parser.add_argument(
        "--confirm-in-sample",
        action="store_true",
        help="Required acknowledgement that these records are same-cohort simulation fixtures.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.confirm_in_sample:
        raise ValueError("seeding same-alert Memory requires --confirm-in-sample")
    items_dir = args.input_items.expanduser().resolve()
    if not items_dir.is_dir():
        raise ValueError(f"input items directory does not exist: {items_dir}")
    if args.init_db:
        upgrade_soc_schema(args.database_url)

    engine = create_engine(
        to_sync_database_url(args.database_url),
        pool_pre_ping=True,
    )
    try:
        repository = SqlAlchemyAlertRepository(
            sessionmaker(bind=engine, expire_on_commit=False)
        )
        now = datetime.now(UTC)
        report = seed_confirmed_memory(
            load_analysis_runs(items_dir),
            repository=repository,
            source_batch_sha256=_sha256_directory(items_dir),
            now=now,
        )
    finally:
        engine.dispose()
        _harden_sqlite_database_files(args.database_url)

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    output.chmod(0o600)
    print(
        json.dumps(
            {
                "candidate_count": report["candidate_count"],
                "confirmed_record_count": report["confirmed_record_count"],
                "retrieval_enabled_count": report["retrieval_enabled_count"],
                "output": str(output),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
