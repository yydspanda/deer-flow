"""Build the local SOC Alpha acceptance evidence package.

The core journey intentionally enters through the public CLI and registered
Gateway handlers/service dependencies. It does not replace focused transport or
integration suites; the root orchestration script adds Kafka and frontend
evidence before this module seals one report.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = BACKEND_ROOT.parent
DEFAULT_OUTPUT_DIR = BACKEND_ROOT / ".deer-flow" / "soc-alpha-acceptance"
sys.path.insert(0, str(BACKEND_ROOT))

from app.gateway.routers import soc_external_dispositions, soc_review  # noqa: E402
from app.gateway.routers.soc_transport import SOC_API_VERSION  # noqa: E402
from soc_agent.cli import main as soc_main  # noqa: E402
from soc_agent.contracts import SocExternalDispositionIngressCommand  # noqa: E402
from soc_agent.db import SqlAlchemyAlertRepository, to_sync_database_url  # noqa: E402

REPORT_SCHEMA_VERSION = "soc.alpha_acceptance_report.v1"
CORE_SCHEMA_VERSION = "soc.alpha_core_acceptance.v1"
FRONTEND_STATUS_SCHEMA_VERSION = "soc.alpha_frontend_status.v1"
KAFKA_STATUS_SCHEMA_VERSION = "soc.alpha_kafka_status.v1"
EXPECTED_SAMPLE_IDS = {
    "pingan-apt-action-evidence",
    "pingan-edr-action-evidence",
    "pingan-hids-action-evidence",
}


class AcceptanceFailure(RuntimeError):
    """Raised when one acceptance assertion cannot be proven."""


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    output_dir = Path(args.output_dir).resolve()

    if args.command == "prepare":
        _prepare_output_dir(output_dir)
        return 0
    if args.command == "core":
        output_dir.mkdir(parents=True, exist_ok=True)
        database_url = args.database_url or _default_database_url(output_dir / "soc_alpha_core.db")
        try:
            result = run_core_acceptance(output_dir=output_dir, database_url=database_url)
        except Exception as exc:  # noqa: BLE001 - acceptance boundary must preserve a failure artifact
            result = {
                "schema_version": CORE_SCHEMA_VERSION,
                "status": "failed",
                "generated_at": _utc_now(),
                "failure_reasons": [f"{type(exc).__name__}: {exc}"],
            }
            _write_json(output_dir / "core" / "core-result.json", result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["status"] == "passed" else 1
    if args.command == "finalize":
        report = finalize_acceptance_report(output_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["status"] == "passed" else 1
    raise AssertionError(f"unhandled command: {args.command}")


def run_core_acceptance(*, output_dir: Path, database_url: str) -> dict[str, Any]:
    """Run APT/EDR/HIDS through CLI, SQL, Gateway feedback, audit, and replay."""

    core_dir = output_dir / "core"
    core_dir.mkdir(parents=True, exist_ok=True)

    demo = _invoke_soc_json(
        [
            "demo",
            "run",
            "all",
            "--database-url",
            database_url,
            "--init-db",
            "--pretty",
        ]
    )
    _write_json(core_dir / "cli-demo.json", demo)

    sample_ids = {str(item.get("sample_id")) for item in demo.get("results", [])}
    run_ids = [str(value) for value in demo.get("run_ids", [])]
    queue_ids = [str(value) for value in demo.get("queue_ids", [])]
    _require(demo.get("failed_count") == 0, "CLI demo reported a failed sample")
    _require(sample_ids == EXPECTED_SAMPLE_IDS, f"unexpected sample coverage: {sorted(sample_ids)}")
    _require(len(run_ids) == 3 and len(queue_ids) == 3, "expected three runs and three review items")

    cli_runs: list[dict[str, Any]] = []
    cli_contexts: list[dict[str, Any]] = []
    replay_runs: list[dict[str, Any]] = []
    for run_id, queue_id in zip(run_ids, queue_ids, strict=True):
        run = _invoke_soc_json(["show", run_id, "--database-url", database_url, "--pretty"])
        context = _invoke_soc_json(["review", "context", queue_id, "--database-url", database_url, "--pretty"])
        replay = _invoke_soc_json(["replay", run_id, "--database-url", database_url, "--analyzer-mode", "stub", "--pretty"])
        _require(context["queue_item"]["run_id"] == run_id, f"queue {queue_id} points to another run")
        _require(replay.get("replay_of_run_id") == run_id, f"replay lineage missing for {run_id}")
        _require(replay.get("run_id") != run_id, f"replay reused original run id {run_id}")
        cli_runs.append(run)
        cli_contexts.append(context)
        replay_runs.append(replay)

    _write_json(core_dir / "cli-runs.json", cli_runs)
    _write_json(core_dir / "cli-review-contexts.json", cli_contexts)
    _write_json(core_dir / "cli-replays.json", replay_runs)

    api_journey = _run_gateway_journey(
        database_url=database_url,
        run_id=run_ids[0],
        alert_id=str(demo["results"][0]["alert_id"]),
        queue_id=queue_ids[0],
    )
    _write_json(core_dir / "gateway-feedback-journey.json", api_journey)

    repository = _repository(database_url)
    corrected_run = repository.get_run(run_ids[0])
    decision_audits = repository.list_audit_records(run_ids[0])
    mutation_audits = repository.list_mutation_audits(run_id=run_ids[0], limit=100)
    review_item = repository.get_review_item(queue_ids[0])
    _require(corrected_run is not None and corrected_run.decision is not None, "feedback target run was not corrected")
    _require(corrected_run.decision.verdict.value == "false_positive", "feedback correction verdict was not persisted")
    _require(review_item is not None and review_item.status.value == "closed", "feedback did not close the review item")
    audit_actions = {record.action.value for record in decision_audits}
    _require({"analysis", "correction", "external_disposition"} <= audit_actions, f"missing decision audits: {audit_actions}")
    _require(len(mutation_audits) >= 2, "expected durable mutation audits for external feedback")

    persistence_evidence = {
        "schema_version": "soc.alpha_persistence_evidence.v1",
        "target_run_id": run_ids[0],
        "target_queue_id": queue_ids[0],
        "persisted_verdict": corrected_run.decision.verdict.value,
        "persisted_queue_status": review_item.status.value,
        "decision_audit_actions": sorted(audit_actions),
        "decision_audits": [item.model_dump(mode="json", exclude_none=True) for item in decision_audits],
        "mutation_audits": [item.model_dump(mode="json", exclude_none=True) for item in mutation_audits],
    }
    _write_json(core_dir / "persistence-audit.json", persistence_evidence)

    result = {
        "schema_version": CORE_SCHEMA_VERSION,
        "stage_task_id": "BG-P1-05",
        "status": "passed",
        "generated_at": _utc_now(),
        "database": {
            "backend": _database_backend(database_url),
            "locator": _database_locator(database_url),
            "production_equivalence_claimed": False,
        },
        "coverage": {
            "sample_ids": sorted(sample_ids),
            "source_types": sorted({str(item.get("source_type")) for item in demo["results"]}),
            "run_ids": run_ids,
            "queue_ids": queue_ids,
            "replay_run_ids": [str(item["run_id"]) for item in replay_runs],
        },
        "checks": {
            "cli_all_samples_passed": True,
            "review_context_loaded_for_each_sample": True,
            "gateway_review_api_loaded": api_journey["review_context_status"] == 200,
            "external_feedback_applied": api_journey["first_apply"]["correction_applied"],
            "external_feedback_exact_retry_idempotent": api_journey["duplicate_apply"]["idempotent"],
            "external_feedback_changed_retry_rejected": api_journey["changed_retry_status"] == 409,
            "review_state_persisted": review_item.status.value == "closed",
            "decision_and_mutation_audit_persisted": bool(decision_audits and mutation_audits),
            "replay_lineage_verified": all(replay.get("replay_of_run_id") == original for replay, original in zip(replay_runs, run_ids, strict=True)),
        },
        "mock_and_data_boundaries": _core_boundaries(),
        "failure_semantics": [
            {
                "case": "changed external feedback retry",
                "expected": "HTTP 409 conflict; no second logical mutation",
                "observed_status": api_journey["changed_retry_status"],
            },
            {
                "case": "analysis replay",
                "expected": "new run id with replay_of_run_id pointing to the immutable source run",
                "observed": True,
            },
        ],
        "evidence_files": [
            "core/cli-demo.json",
            "core/cli-runs.json",
            "core/cli-review-contexts.json",
            "core/cli-replays.json",
            "core/gateway-feedback-journey.json",
            "core/persistence-audit.json",
        ],
        "failure_reasons": [],
    }
    _write_json(core_dir / "core-result.json", result)
    return result


def _run_gateway_journey(
    *,
    database_url: str,
    run_id: str,
    alert_id: str,
    queue_id: str,
) -> dict[str, Any]:
    app_state = SimpleNamespace(
        soc_alert_repository=_repository(database_url),
        soc_external_disposition_mapping_config={
            "status_mappings": [
                {
                    "external_system": "alpha-fixture-itsm",
                    "external_status": "false-positive-closed",
                    "canonical_status": "closed_false_positive",
                    "trust_level": "high",
                    "apply_to_review": True,
                    "notes": "BG-P1-05 local acceptance fixture",
                }
            ]
        },
    )
    request = SimpleNamespace(
        headers={
            "X-Request-Id": "req-alpha-feedback-001",
            "X-Trace-Id": "trace-alpha-feedback-001",
            "x-soc-surface": "api",
        },
        state=SimpleNamespace(
            user=SimpleNamespace(id="alpha-external-adapter", system_role="admin"),
            auth_source="external_adapter",
        ),
        app=SimpleNamespace(state=app_state),
    )
    review_service = soc_review.get_soc_review_service(request)
    external_service = soc_external_dispositions.get_soc_external_disposition_service(request)

    event = {
        "event": {
            "external_system": "alpha-fixture-itsm",
            "external_case_id": "ALPHA-CASE-001",
            "source_event_id": "ALPHA-EVENT-001",
            "source_version": "1",
            "soc_alert_id": alert_id,
            "soc_run_id": run_id,
            "soc_queue_id": queue_id,
            "external_status": "false-positive-closed",
            "external_reason": "Alpha fixture analyst confirmed an authorized test.",
            "external_tags": ["alpha_fixture", "authorized_test"],
            "operator": {"actor_id": "alpha-reviewer"},
            "updated_at": _utc_now(),
            "raw_payload_hash": hashlib.sha256(b"alpha-feedback-fixture-v1").hexdigest(),
            "metadata": {"fixture": True, "stage_task_id": "BG-P1-05"},
        }
    }
    command = SocExternalDispositionIngressCommand.model_validate(event)
    queue_response = soc_review.list_review_items(
        review_service,
        status=None,
        limit=20,
    )
    initial_context = soc_review.get_review_context(queue_id, review_service)
    first = soc_external_dispositions.apply_external_disposition(
        command,
        request,
        external_service,
    )
    duplicate = soc_external_dispositions.apply_external_disposition(
        command,
        request,
        external_service,
    )
    changed = json.loads(json.dumps(event))
    changed["event"]["external_reason"] = "Changed retry must conflict."
    changed_retry_status = 200
    changed_retry_problem: dict[str, Any] = {}
    try:
        soc_external_dispositions.apply_external_disposition(
            SocExternalDispositionIngressCommand.model_validate(changed),
            request,
            external_service,
        )
    except HTTPException as exc:
        changed_retry_status = exc.status_code
        changed_retry_problem = {"status": exc.status_code, "detail": str(exc.detail)}
    updated_context = soc_review.get_review_context(queue_id, review_service)

    first_payload = first.model_dump(mode="json", exclude_none=True)
    duplicate_payload = duplicate.model_dump(mode="json", exclude_none=True)
    updated_payload = updated_context.model_dump(mode="json", exclude_none=True)
    _require(any(item.queue_id == queue_id for item in queue_response.items), "target queue missing from API")
    _require(initial_context.queue_item.queue_id == queue_id, "review context handler returned another queue")
    _require(first.correction_applied is True, "trusted feedback did not apply correction")
    _require(duplicate.idempotent is True, "exact feedback retry was not idempotent")
    _require(changed_retry_status == 409, f"changed feedback retry returned {changed_retry_status}")
    _require(updated_context.queue_item.status.value == "closed", "updated API context did not show closed queue")
    _require(updated_context.external_dispositions, "updated API context omitted external disposition")
    _require(updated_context.memory_candidates, "updated API context omitted feedback memory candidate")

    route_paths = {route.path for route in [*soc_review.router.routes, *soc_external_dispositions.router.routes]}
    _require("/api/soc/review/items/{queue_id}/context" in route_paths, "review context route is not registered")
    _require("/api/soc/external-dispositions" in route_paths, "external feedback route is not registered")

    return {
        "schema_version": "soc.alpha_gateway_journey.v1",
        "execution_mode": "registered_gateway_handlers",
        "transport_contract_evidence": "backend/tests/test_soc_api_transport.py",
        "queue_list_status": 200,
        "review_context_status": 200,
        "api_version": SOC_API_VERSION,
        "request_id": request.state.soc_request_id,
        "trace_id": request.state.soc_trace_id,
        "registered_routes": sorted(route_paths),
        "initial_queue_status": initial_context.queue_item.status.value,
        "first_apply": first_payload,
        "duplicate_apply": duplicate_payload,
        "changed_retry_status": changed_retry_status,
        "changed_retry_problem": changed_retry_problem,
        "updated_queue_status": updated_payload["queue_item"]["status"],
        "external_disposition_count": len(updated_payload["external_dispositions"]),
        "memory_candidate_count": len(updated_payload["memory_candidates"]),
    }


def finalize_acceptance_report(output_dir: Path) -> dict[str, Any]:
    """Merge core, Kafka, and frontend evidence into one release-level report."""

    core = _read_json_or_failure(
        output_dir / "core" / "core-result.json",
        schema_version=CORE_SCHEMA_VERSION,
        reason="core result artifact is missing or invalid",
    )
    kafka_status = _read_json_or_failure(
        output_dir / "kafka" / "status.json",
        schema_version=KAFKA_STATUS_SCHEMA_VERSION,
        reason="Kafka status artifact is missing",
    )
    frontend_status = _read_json_or_failure(
        output_dir / "frontend" / "status.json",
        schema_version=FRONTEND_STATUS_SCHEMA_VERSION,
        reason="frontend status artifact is missing",
    )
    kafka_results = (
        [
            _read_json_or_failure(
                path,
                schema_version="soc.kafka_smoke_result.v1",
                reason=f"Kafka result artifact is invalid: {path.name}",
            )
            for path in sorted((output_dir / "kafka").glob("*.json"))
            if path.name != "status.json"
        ]
        if (output_dir / "kafka").exists()
        else []
    )

    component_status = {
        "core": core.get("status") == "passed",
        "kafka": kafka_status.get("status") == "passed",
        "frontend": frontend_status.get("status") == "passed",
    }
    kafka_alert_ids = sorted(str(result.get("alert_id")) for result in kafka_results if result.get("schema_version") == "soc.kafka_smoke_result.v1")
    expected_kafka_alert_ids = ["1965810", "2026494", "HIDS-2026-0001"]
    kafka_coverage_ok = kafka_alert_ids == expected_kafka_alert_ids
    component_status["kafka"] = component_status["kafka"] and kafka_coverage_ok

    failure_reasons: list[str] = []
    if not component_status["core"]:
        failure_reasons.append("core CLI/DB/API/feedback/audit/replay acceptance failed")
    if not component_status["kafka"]:
        failure_reasons.append("local Kafka APT/EDR/HIDS processed/commit/DLQ acceptance failed")
    if not component_status["frontend"]:
        failure_reasons.append("focused SOC frontend unit/browser regression failed")

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "acceptance_version": "alpha-v1",
        "stage_task_id": "BG-P1-05",
        "generated_at": _utc_now(),
        "status": "passed" if all(component_status.values()) else "failed",
        "component_status": component_status,
        "coverage": {
            "core_sample_ids": core.get("coverage", {}).get("sample_ids", []),
            "core_source_types": core.get("coverage", {}).get("source_types", []),
            "kafka_alert_ids": kafka_alert_ids,
            "surfaces": ["CLI", "Kafka daemon", "SQL", "Gateway API", "Review Web", "feedback", "audit", "replay"],
        },
        "core": core,
        "kafka": {
            "status": kafka_status,
            "results": kafka_results,
            "expected_alert_ids": expected_kafka_alert_ids,
            "coverage_verified": kafka_coverage_ok,
        },
        "frontend": frontend_status,
        "mock_and_data_boundaries": [
            *_core_boundaries(),
            {
                "capability": "Kafka broker",
                "mode": "local_real_broker",
                "claim": "Real Kafka protocol/offset/DLQ smoke against an ephemeral local Redpanda container; no production ACL, TLS, capacity, or recovery claim.",
            },
            {
                "capability": "Review Web",
                "mode": "mocked_transport_browser_regression",
                "claim": "Real rendered React workflow and request contract with deterministic network fixtures; backend business correctness is proven separately by the real Gateway/SQL journey.",
            },
        ],
        "known_failure_semantics": [
            "Changed retries for the same external source event return conflict rather than applying a second mutation.",
            "Kafka invalid JSON is dead-lettered and committed; successfully processed offsets become idle on the next poll.",
            "Replay creates a new run linked to the immutable source run.",
            "Any missing component artifact or non-zero frontend/Kafka command marks the aggregate report failed.",
            "Mock providers and shadow-only proposals never establish production facts or enable response automation.",
        ],
        "commands": {
            "all": "./scripts/soc-alpha-acceptance.sh all",
            "core": "./scripts/soc-alpha-acceptance.sh core",
            "kafka": "./scripts/soc-alpha-acceptance.sh kafka",
            "frontend": "./scripts/soc-alpha-acceptance.sh frontend",
            "finalize": "./scripts/soc-alpha-acceptance.sh finalize",
        },
        "artifact_manifest": _artifact_manifest(output_dir),
        "failure_reasons": failure_reasons,
    }
    _write_json(output_dir / "alpha-acceptance-report.json", report)
    return report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate SOC Alpha acceptance evidence")
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR), help="Gitignored acceptance output directory")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("prepare", help="Clear the known local acceptance output tree")
    core = subparsers.add_parser("core", help="Run CLI/DB/API/feedback/audit/replay acceptance")
    core.add_argument("--database-url", help="SOC database URL; defaults to isolated local SQLite")
    subparsers.add_parser("finalize", help="Merge core, Kafka, and frontend artifacts")
    return parser


def _prepare_output_dir(output_dir: Path) -> None:
    if "soc-alpha" not in output_dir.name.lower():
        raise AcceptanceFailure("refusing to clear an acceptance output directory whose basename does not contain 'soc-alpha'")
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)


def _invoke_soc_json(args: list[str]) -> dict[str, Any]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = soc_main(args)
    if code != 0:
        raise AcceptanceFailure(f"SOC CLI failed ({code}): {' '.join(args)}; {stderr.getvalue().strip()}")
    try:
        payload = json.loads(stdout.getvalue())
    except json.JSONDecodeError as exc:
        raise AcceptanceFailure(f"SOC CLI emitted invalid JSON for {' '.join(args)}") from exc
    if not isinstance(payload, dict):
        raise AcceptanceFailure(f"SOC CLI did not emit an object for {' '.join(args)}")
    return payload


def _repository(database_url: str) -> SqlAlchemyAlertRepository:
    engine = create_engine(to_sync_database_url(database_url), pool_pre_ping=True)
    return SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _core_boundaries() -> list[dict[str, str]]:
    return [
        {
            "capability": "alert inputs",
            "mode": "fixture",
            "claim": "Committed, sanitized PingAn APT/EDR/HIDS fixtures exercise real normalizer/runtime contracts.",
        },
        {
            "capability": "analyzer",
            "mode": "deterministic_stub",
            "claim": "Repeatable acceptance baseline; no live-model quality or provider availability claim.",
        },
        {
            "capability": "read-only investigation providers",
            "mode": "mock",
            "claim": "Real action/evidence boundary with mock CMDB/EDR/TI/tag facts; PA-12 remains data-gated.",
        },
        {
            "capability": "external disposition source",
            "mode": "fixture_over_real_gateway_service",
            "claim": "Real authenticated canonical ingress, SQL mutation, audit, and idempotency; no real Zeus/ITSM feed or credential claim.",
        },
        {
            "capability": "core database",
            "mode": "local_sqlite",
            "claim": "Real local transaction/repository path; production PostgreSQL capacity and recovery remain Stage 4 evidence.",
        },
        {
            "capability": "high-risk response",
            "mode": "disabled",
            "claim": "Approval/preflight contracts exist, but no production side effect is executed.",
        },
    ]


def _artifact_manifest(output_dir: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "alpha-acceptance-report.json" or path.suffix == ".db":
            continue
        content = path.read_bytes()
        artifacts.append(
            {
                "path": path.relative_to(output_dir).as_posix(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "size_bytes": len(content),
            }
        )
    return artifacts


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AcceptanceFailure(f"acceptance artifact must be an object: {path}")
    return payload


def _read_json_or_failure(path: Path, *, schema_version: str, reason: str) -> dict[str, Any]:
    if path.exists():
        try:
            return _read_json(path)
        except (AcceptanceFailure, OSError, json.JSONDecodeError) as exc:
            return {
                "schema_version": schema_version,
                "status": "failed",
                "failure_reasons": [f"{reason} ({type(exc).__name__})"],
            }
    return {
        "schema_version": schema_version,
        "status": "failed",
        "failure_reasons": [reason],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _default_database_url(path: Path) -> str:
    return f"sqlite+pysqlite:///{path.resolve()}"


def _database_backend(database_url: str) -> str:
    return database_url.split(":", 1)[0].split("+", 1)[0]


def _database_locator(database_url: str) -> str:
    if _database_backend(database_url) == "sqlite":
        return database_url.split(":///", 1)[-1]
    return "configured-external-database"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceFailure(message)


if __name__ == "__main__":
    raise SystemExit(main())
