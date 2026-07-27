"""Regenerate governed-context and authorization-shadow validation artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorContext,
    ActorType,
    AuthorizedActivityBehaviorKind,
    AuthorizedActivityBehaviorSelector,
    AuthorizedActivityPayload,
    AuthorizedActivityRecurringWindow,
    AuthorizedActivitySubjectKind,
    AuthorizedActivitySubjectSelector,
    AuthorizedActivityTargetKind,
    AuthorizedActivityTargetSelector,
    AuthorizedActivityType,
    EntrySurface,
    GovernedContextFactCreateCommand,
    GovernedContextFactQuery,
    GovernedContextFactStatus,
    GovernedContextFactTransitionCommand,
    GovernedContextSource,
    GovernedContextSourceType,
    ServiceRequestContext,
)
from soc_agent.core import (
    SocAuthorizedActivityService,
    SocGovernedContextService,
    SocNormalizationService,
)
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.pipeline.fact_reconstructor import reconstruct_facts

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_DIR = REPO_ROOT / "datas/legacy_demos"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "backend/.deer-flow/soc-runtime-validation"
TENANT = "pingan-validation"
ENVIRONMENT = "production"
LIFECYCLE_TIME = datetime(2026, 3, 1, tzinfo=UTC)


def _source_ref(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    args = parser.parse_args()
    generate(source_dir=args.source_dir, output_root=args.output_root)
    return 0


def generate(*, source_dir: Path, output_root: Path) -> None:
    lifecycle_dir = output_root / "step-11-governed-context"
    shadow_dir = output_root / "step-12-authorization-shadow"
    lifecycle_dir.mkdir(parents=True, exist_ok=True)
    shadow_dir.mkdir(parents=True, exist_ok=True)

    database_path = lifecycle_dir / "context.db"
    database_path.unlink(missing_ok=True)
    engine = create_engine(f"sqlite+pysqlite:///{database_path}")
    create_soc_tables(engine)
    repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))

    lifecycle_service = SocGovernedContextService(
        repository=repository,
        now_provider=lambda: LIFECYCLE_TIME,
    )
    proposed = lifecycle_service.propose(
        _create_command(
            payload=_hids_activity_payload(),
            source_ref="validation_truth:hids-1965448",
        ),
        context=_context("soc_analyst", actor_id="validation-author"),
    )
    active = SocGovernedContextService(
        repository=repository,
        now_provider=lambda: LIFECYCLE_TIME + timedelta(seconds=1),
    ).activate(
        GovernedContextFactTransitionCommand(
            fact_id=proposed.fact_id,
            expected_latest_version=proposed.version,
            reason="Approved for deterministic validation replay.",
        ),
        context=_context("soc_context_approver", actor_id="validation-approver"),
    )
    history = lifecycle_service.list_versions(proposed.fact_id)
    active_query = lifecycle_service.list(
        GovernedContextFactQuery(
            fact_id=proposed.fact_id,
            status=GovernedContextFactStatus.ACTIVE,
            tenant_id=TENANT,
            environment=ENVIRONMENT,
            valid_at=datetime(2026, 4, 1, tzinfo=UTC),
            latest_only=True,
        )
    )

    _write_json(lifecycle_dir / "01-proposed.json", _lifecycle_artifact("propose", proposed))
    _write_json(lifecycle_dir / "02-active.json", _lifecycle_artifact("activate", active))
    _write_json(
        lifecycle_dir / "03-history.json",
        {
            "schema_version": "soc.runtime_validation.governed_context_history.v1",
            "fact_id": proposed.fact_id,
            "versions": [item.model_dump(mode="json", exclude_none=True) for item in history],
        },
    )
    _write_json(
        lifecycle_dir / "04-active-query.json",
        {
            "schema_version": "soc.runtime_validation.governed_context_query.v1",
            "query": {
                "fact_id": proposed.fact_id,
                "status": "active",
                "tenant_id": TENANT,
                "environment": ENVIRONMENT,
                "valid_at": "2026-04-01T00:00:00Z",
            },
            "facts": [item.model_dump(mode="json", exclude_none=True) for item in active_query],
        },
    )
    _write_json(
        lifecycle_dir / "manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "track": "governed_context_lifecycle",
            "step": {"number": 11, "name": "governed_context_lifecycle"},
            "artifact_count": 4,
            "database": "context.db",
            "status": "passed" if len(history) == 2 and len(active_query) == 1 else "failed",
            "assertions": {
                "append_only_versions": len(history) == 2,
                "proposed_then_active": [item.status.value for item in reversed(history)] == ["proposed", "active"],
                "active_query_returns_latest_only": len(active_query) == 1 and active_query[0].version == 2,
            },
            "git_ignored": True,
        },
    )

    shadow_entries = [
        _run_shadow_case(
            source_dir=source_dir,
            output_dir=shadow_dir,
            name="hids-1965448",
            payload=_hids_activity_payload(),
        ),
        _run_shadow_case(
            source_dir=source_dir,
            output_dir=shadow_dir,
            name="edr-1965810",
            payload=_edr_activity_payload(),
            event_timezone="Asia/Shanghai",
        ),
    ]
    _write_json(
        shadow_dir / "manifest.json",
        {
            "schema_version": "soc.runtime_validation.manifest.v1",
            "track": "authorization_shadow",
            "step": {"number": 12, "name": "authorized_activity_shadow_match"},
            "artifact_count": len(shadow_entries),
            "status": "passed" if all(item["match_status"] == "exact" for item in shadow_entries) else "failed",
            "entries": shadow_entries,
            "boundary": _shadow_boundary(),
            "git_ignored": True,
        },
    )


def _run_shadow_case(
    *,
    source_dir: Path,
    output_dir: Path,
    name: str,
    payload: AuthorizedActivityPayload,
    event_timezone: str | None = None,
) -> dict[str, Any]:
    source_path = source_dir / f"{name}.json"
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    repository = _in_memory_repository()
    proposed, active = _activate(repository, payload, f"validation_truth:{name}")
    inspection = SocNormalizationService().inspect(raw)
    reconstruction = reconstruct_facts(inspection.alert)
    authorization = SocAuthorizedActivityService(repository=repository)
    query = authorization.build_query(
        inspection.alert,
        entities=inspection.entities,
        fact_reconstruction=reconstruction,
        tenant_id=TENANT,
        environment=ENVIRONMENT,
        event_timezone=event_timezone,
    )
    result = authorization.match(query)
    artifact_name = f"{name}.step-12.json"
    _write_json(
        output_dir / artifact_name,
        {
            "schema_version": "soc.runtime_validation.step12.v1",
            "step": {
                "number": 12,
                "name": "authorized_activity_shadow_match",
                "mode": "read_only_shadow",
            },
            "source": {
                "file": _source_ref(source_path),
                "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
            },
            "boundary": _shadow_boundary(),
            "governed_fact_versions": [
                proposed.model_dump(mode="json", exclude_none=True),
                active.model_dump(mode="json", exclude_none=True),
            ],
            "authorization_query": query.model_dump(mode="json", exclude_none=True),
            "authorization_match_result": result.model_dump(mode="json", exclude_none=True),
        },
    )
    return {
        "source": _source_ref(source_path),
        "artifact": artifact_name,
        "match_status": result.status.value,
    }


def _activate(repository, payload: AuthorizedActivityPayload, source_ref: str):
    proposed = SocGovernedContextService(
        repository=repository,
        now_provider=lambda: LIFECYCLE_TIME,
    ).propose(
        _create_command(payload=payload, source_ref=source_ref),
        context=_context("soc_analyst", actor_id="shadow-fixture-author"),
    )
    active = SocGovernedContextService(
        repository=repository,
        now_provider=lambda: LIFECYCLE_TIME + timedelta(seconds=1),
    ).activate(
        GovernedContextFactTransitionCommand(
            fact_id=proposed.fact_id,
            expected_latest_version=proposed.version,
            reason="Approved for deterministic shadow replay.",
        ),
        context=_context("soc_context_approver", actor_id="shadow-fixture-approver"),
    )
    return proposed, active


def _create_command(*, payload: AuthorizedActivityPayload, source_ref: str) -> GovernedContextFactCreateCommand:
    return GovernedContextFactCreateCommand(
        tenant_id=TENANT,
        environment=ENVIRONMENT,
        valid_from=datetime(2026, 3, 1, tzinfo=UTC),
        valid_until=datetime(2026, 6, 1, tzinfo=UTC),
        source=GovernedContextSource(
            source_type=GovernedContextSourceType.ANALYST_CONFIRMATION,
            source_ref=source_ref,
            observed_at=LIFECYCLE_TIME - timedelta(minutes=1),
        ),
        reason="Analyst-confirmed business truth used for governed validation only.",
        evidence_refs=[source_ref],
        payload=payload,
    )


def _hids_activity_payload() -> AuthorizedActivityPayload:
    return AuthorizedActivityPayload(
        activity_type=AuthorizedActivityType.AUTOMATION,
        subject_scope=[
            AuthorizedActivitySubjectSelector(
                kind=AuthorizedActivitySubjectKind.ASSET_ID,
                value="66588629935d4acc",
            )
        ],
        target_scope=[
            AuthorizedActivityTargetSelector(
                kind=AuthorizedActivityTargetKind.ASSET_ID,
                value="66588629935d4acc",
            )
        ],
        behavior_scope=[
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.SCENARIO,
                value="command_execution",
            ),
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.BEHAVIOR_SIGNATURE,
                value="java->chattr",
            ),
        ],
        recurring_windows=[
            AuthorizedActivityRecurringWindow(
                timezone="Asia/Shanghai",
                days_of_week=[0, 1, 2, 3, 4, 5, 6],
                start_time=time(23, 30),
                end_time=time(0, 30),
            )
        ],
    )


def _edr_activity_payload() -> AuthorizedActivityPayload:
    return AuthorizedActivityPayload(
        activity_type=AuthorizedActivityType.MAINTENANCE,
        subject_scope=[
            AuthorizedActivitySubjectSelector(
                kind=AuthorizedActivitySubjectKind.IP,
                value="30.162.29.85",
            )
        ],
        target_scope=[
            AuthorizedActivityTargetSelector(
                kind=AuthorizedActivityTargetKind.IP,
                value="10.43.107.39",
            )
        ],
        behavior_scope=[
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.SCENARIO,
                value="lateral_movement",
            ),
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.PROCESS,
                value="svchost.exe",
            ),
            AuthorizedActivityBehaviorSelector(
                kind=AuthorizedActivityBehaviorKind.TECHNIQUE,
                value="T1021",
            ),
        ],
    )


def _context(*roles: str, actor_id: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        actor=ActorContext(
            actor_id=actor_id,
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=list(roles),
        )
    )


def _in_memory_repository() -> SqlAlchemyAlertRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _lifecycle_artifact(action: str, fact) -> dict[str, Any]:
    return {
        "schema_version": "soc.runtime_validation.governed_context_action.v1",
        "step": {"number": 11, "name": "governed_context_lifecycle"},
        "action": action,
        "fact": fact.model_dump(mode="json", exclude_none=True),
    }


def _shadow_boundary() -> dict[str, bool]:
    return {
        "changes_detection_truth": False,
        "updates_review_queue": False,
        "closes_alert": False,
        "authorizes_response_action": False,
    }


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    raise SystemExit(main())
