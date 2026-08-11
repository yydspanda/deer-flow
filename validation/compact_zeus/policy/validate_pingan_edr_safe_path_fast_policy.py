#!/usr/bin/env python3
"""Run ten real PingAn EDR alerts through the safe-path policy boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = REPO_ROOT / "backend"
for import_root in (REPO_ROOT, BACKEND_ROOT):
    if str(import_root) not in sys.path:
        sys.path.insert(0, str(import_root))

from validation.compact_zeus.internal_batch.run_pingan_runtime_batch import (  # noqa: E402
    main as run_runtime_batch,
)
from validation.compact_zeus.internal_batch.run_pingan_runtime_batch import (  # noqa: E402
    prepare_batch_items,
)
from validation.compact_zeus.shared.restricted_dataframe_pickle import (  # noqa: E402
    load_dataframe_pickle,
)

from soc_agent.db import (  # noqa: E402
    SqlAlchemyAlertRepository,
    upgrade_soc_schema,
)

SCHEMA_VERSION = "soc.pingan_edr_safe_path_validation.v1"
FAST_RULE_ID = "edr-safe-software-path-fast-ignore"
PATH_SIGNAL_KEY = "endpoint.software_path.match"
FAST_SIGNAL_KEY = "endpoint.software_path.fast_disposition"
DEFAULT_SOURCE = REPO_ROOT / "validation/compact_zeus/data/corpus/full_alert_validation_corpus.pkl"
DEFAULT_CATALOG = BACKEND_ROOT / ".deer-flow/pingan-context/software-path-catalog.sqlite"
DEFAULT_POLICY = BACKEND_ROOT / "soc_agent/integrations/pingan/policies/tenant-disposition-v2.json"
DEFAULT_OUTPUT_ROOT = BACKEND_ROOT / ".deer-flow/soc-validation/edr-safe-path-ten"


@dataclass(frozen=True)
class ExpectedCase:
    alert_id: str
    expected_signal_values: tuple[str, ...]
    expected_fast_ignore: bool
    purpose: str


CASES = (
    ExpectedCase("1976406", ("exact_safe_path",), True, "exact safe path without a supplied hash"),
    ExpectedCase("1974593", ("exact_safe_path",), True, "exact path and MD5"),
    ExpectedCase("1986762", ("exact_safe_path",), True, "repeated exact-safe deployment path"),
    ExpectedCase("1976564", ("exact_safe_path",), True, "independent repeated exact-safe alert"),
    ExpectedCase(
        "1971813",
        ("other_paths_only",),
        False,
        "legacy other_paths cannot authorize ignore",
    ),
    ExpectedCase("1965810", ("hash_mismatch",), False, "exact path with a conflicting hash"),
    ExpectedCase("1984026", ("unmatched",), False, "unknown executable under a D-drive user path"),
    ExpectedCase("1968376", ("unmatched",), False, "multiple unknown endpoint paths"),
    ExpectedCase(
        "1967699",
        ("exact_safe_path", "unmatched"),
        False,
        "mixed safe and unknown paths fail closed",
    ),
    ExpectedCase(
        "1965794",
        ("exact_safe_path", "unmatched"),
        False,
        "larger mixed process tree fails closed",
    ),
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--environment", default="dev")
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    source = args.source.expanduser().resolve()
    catalog = args.catalog.expanduser().resolve()
    policy = args.policy.expanduser().resolve()
    for label, path in (("source", source), ("catalog", catalog), ("policy", policy)):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")

    output_dir = args.output_dir.expanduser().resolve() if args.output_dir is not None else DEFAULT_OUTPUT_ROOT / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    if output_dir.exists():
        parser.error(f"output directory already exists: {output_dir}")
    output_dir.mkdir(parents=True, mode=0o700)
    output_dir.chmod(0o700)

    database_path = output_dir / "soc-validation.sqlite"
    database_url = f"sqlite:///{database_path}"
    upgrade_soc_schema(database_url)
    database_path.chmod(0o600)

    environment = args.environment.strip()
    if not environment:
        parser.error("environment must not be blank")
    env = {
        "SOC_TENANT_POLICY_ENABLED": "true",
        "SOC_TENANT_DISPOSITION_POLICY_PATH": str(policy),
        "SOC_TENANT_POLICY_ENVIRONMENT": environment,
        "SOC_TENANT_POLICY_EVENT_TIMEZONE": "Asia/Shanghai",
        "SOC_TENANT_POLICY_ADVISOR_MODE": "off",
        "SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED": "true",
        "SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH": str(catalog),
        "SOC_AUTOMATION_EXECUTE_AUTHORIZED_ACTIONS": "false",
    }
    cleared = (
        "SOC_AUTOMATION_POLICY_PATH",
        "SOC_AUTOMATION_ENVIRONMENT",
        "SOC_TENANT_POLICY_SKILL_PATH",
    )
    batch_args = [
        "--source",
        str(source),
        "--output-dir",
        str(output_dir / "runtime-batch"),
        "--analyzer-mode",
        "stub",
        "--persist",
        "--database-url",
        database_url,
        "--default-tenant-id",
        "pingan",
        "--workers",
        "1",
        "--fail-fast",
    ]
    for case in CASES:
        batch_args.extend(("--alert-id", case.alert_id))

    with _scoped_environment(env, cleared=cleared):
        batch_status = run_runtime_batch(batch_args)
    if batch_status != 0:
        raise RuntimeError(f"ten-alert Runtime batch failed with status {batch_status}")

    report = _build_report(
        source=source,
        catalog=catalog,
        policy=policy,
        environment=environment,
        database_url=database_url,
        runtime_batch_dir=output_dir / "runtime-batch",
    )
    report_path = output_dir / "acceptance.json"
    _write_private_json(report_path, report)
    print(
        json.dumps(
            {
                "status": report["status"],
                "sample_count": report["summary"]["sample_count"],
                "fast_ignore_count": report["summary"]["fast_ignore_count"],
                "fail_closed_count": report["summary"]["fail_closed_count"],
                "real_path_family_match_count": report["summary"]["real_path_family_match_count"],
                "authorization_count": report["summary"]["authorization_count"],
                "execution_count": report["summary"]["execution_count"],
                "report": str(report_path),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if report["status"] == "passed" else 1


def _build_report(
    *,
    source: Path,
    catalog: Path,
    policy: Path,
    environment: str,
    database_url: str,
    runtime_batch_dir: Path,
) -> dict[str, Any]:
    frame = load_dataframe_pickle(source, required_columns={"alert_full_data"})
    items, source_errors = prepare_batch_items(
        frame,
        default_tenant_id="pingan",
        alert_ids=[case.alert_id for case in CASES],
    )
    item_by_id = {item.alert_id: item for item in items}
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
        run_by_alert_id = {run.alert_id: run for run in repository.list_runs(limit=100)}
        case_reports = [
            _evaluate_case(
                case,
                run=run_by_alert_id.get(case.alert_id),
                source_payload=item_by_id[case.alert_id].payload,
                repository=repository,
            )
            for case in CASES
        ]
    finally:
        engine.dispose()

    all_checks = [check for case in case_reports for check in case["checks"]]
    fast_ignore_count = sum(case["observed"]["fast_ignore_applied"] for case in case_reports)
    family_match_count = sum(signal["value"] == "safe_path_family" for case in case_reports for signal in case["observed"]["path_signals"])
    authorization_count = sum(case["observed"]["authorization_count"] for case in case_reports)
    execution_count = sum(case["observed"]["execution_count"] for case in case_reports)
    passed = not source_errors and len(case_reports) == 10 and all(check["passed"] for check in all_checks) and fast_ignore_count == 4 and authorization_count == 0 and execution_count == 0
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if passed else "failed",
        "scope": {
            "purpose": "Validate PingAn EDR safe-path tenant-policy integration with real alert payloads.",
            "analyzer_mode": "stub",
            "llm_quality_claim": False,
            "runtime_boundary": "production SocAnalysisService with persisted post-analysis observers",
            "source": str(source),
            "catalog": str(catalog),
            "policy": str(policy),
            "environment": environment,
            "runtime_batch_dir": str(runtime_batch_dir),
            "source_error_count": len(source_errors),
        },
        "summary": {
            "sample_count": len(case_reports),
            "passed_check_count": sum(check["passed"] for check in all_checks),
            "check_count": len(all_checks),
            "fast_ignore_count": fast_ignore_count,
            "fail_closed_count": len(case_reports) - fast_ignore_count,
            "real_path_family_match_count": family_match_count,
            "real_path_family_coverage": ("present" if family_match_count else "not_present_in_selected_real_corpus"),
            "path_family_component_coverage": "covered_by_test_soc_pingan_software_path_policy",
            "authorization_count": authorization_count,
            "execution_count": execution_count,
        },
        "cases": case_reports,
    }


def _evaluate_case(
    case: ExpectedCase,
    *,
    run: Any | None,
    source_payload: Mapping[str, Any],
    repository: SqlAlchemyAlertRepository,
) -> dict[str, Any]:
    if run is None:
        return {
            "alert_id": case.alert_id,
            "purpose": case.purpose,
            "expected": _expected_payload(case),
            "observed": _empty_observed(),
            "checks": [_check("persisted_run_exists", False, "No persisted run was found.")],
        }

    decisions = repository.list_tenant_policy_decisions(run_id=run.run_id, limit=10)
    transitions = repository.list_decision_transitions(run_id=run.run_id, limit=10)
    authorizations = repository.list_action_authorizations(run_id=run.run_id, limit=100)
    executions = repository.list_action_executions(run_id=run.run_id, limit=100)
    decision = decisions[0] if len(decisions) == 1 else None
    transition = transitions[0] if len(transitions) == 1 else None
    path_signals: list[dict[str, Any]] = []
    fast_signals: list[dict[str, Any]] = []
    if decision is not None:
        for resolution in decision.policy_signal_resolutions:
            for signal in resolution.signals:
                payload = {
                    "key": signal.signal_key,
                    "value": signal.signal_value,
                    "subject": signal.subject,
                    "evidence_paths": signal.evidence_paths,
                    "attributes": signal.attributes,
                }
                if signal.signal_key == PATH_SIGNAL_KEY:
                    path_signals.append(payload)
                elif signal.signal_key == FAST_SIGNAL_KEY:
                    fast_signals.append(payload)

    observed_values = {signal["value"] for signal in path_signals}
    base = transition.before if transition is not None else None
    effective = transition.after if transition is not None else None
    stage_names = [stage.stage.value for stage in transition.stages] if transition else []
    tenant_stage = (
        next(
            (stage for stage in transition.stages if stage.stage.value == "tenant_policy"),
            None,
        )
        if transition
        else None
    )
    fast_ignore_applied = bool(
        decision is not None
        and transition is not None
        and decision.selected_rule_id == FAST_RULE_ID
        and decision.recommended_disposition is not None
        and decision.recommended_disposition.value == "ignored"
        and transition.effective_disposition is not None
        and transition.effective_disposition.value == "ignored"
    )
    checks = [
        _check("persisted_run_exists", True, run.run_id),
        _check(
            "source_type_is_edr",
            run.llm_analysis_request is not None and run.llm_analysis_request.source.source_type.value == "edr",
        ),
        _check("raw_payload_unchanged", run.input_payload == dict(source_payload)),
        _check("one_tenant_policy_decision", len(decisions) == 1, f"count={len(decisions)}"),
        _check(
            "one_effective_transition",
            len(transitions) == 1,
            f"count={len(transitions)}",
        ),
        _check(
            "expected_path_signal_classes_present",
            set(case.expected_signal_values).issubset(observed_values),
            f"observed={sorted(observed_values)}",
        ),
        _check(
            "four_stage_lineage",
            stage_names == ["base", "memory", "tenant_policy", "effective"],
            f"stages={stage_names}",
        ),
        _check(
            "runtime_verdict_immutable",
            base is not None and effective is not None and base.verdict == effective.verdict and base.confidence == effective.confidence,
        ),
        _check(
            "no_action_authorization",
            not authorizations,
            f"count={len(authorizations)}",
        ),
        _check("no_action_execution", not executions, f"count={len(executions)}"),
    ]
    if case.expected_fast_ignore:
        checks.extend(
            (
                _check(
                    "aggregate_fast_signal_present",
                    len(fast_signals) == 1,
                    f"count={len(fast_signals)}",
                ),
                _check(
                    "fast_rule_selected",
                    decision is not None and decision.selected_rule_id == FAST_RULE_ID,
                ),
                _check("ignore_applied", fast_ignore_applied),
                _check(
                    "review_cleared",
                    base is not None and effective is not None and base.needs_review and not effective.needs_review,
                ),
                _check(
                    "tenant_stage_applied",
                    tenant_stage is not None and tenant_stage.status.value == "applied",
                ),
            )
        )
    else:
        checks.extend(
            (
                _check("aggregate_fast_signal_absent", not fast_signals),
                _check(
                    "fast_rule_not_selected",
                    decision is not None and decision.selected_rule_id != FAST_RULE_ID,
                ),
                _check("no_path_ignore_applied", not fast_ignore_applied),
                _check(
                    "review_preserved",
                    base is not None and effective is not None and base.needs_review == effective.needs_review,
                ),
            )
        )

    return {
        "alert_id": case.alert_id,
        "purpose": case.purpose,
        "expected": _expected_payload(case),
        "observed": {
            "run_id": run.run_id,
            "runtime_status": run.status.value,
            "source_system": run.llm_analysis_request.source.source_system if run.llm_analysis_request else None,
            "base_verdict": base.verdict.value if base else None,
            "base_needs_review": base.needs_review if base else None,
            "tenant_policy_status": decision.evaluation_status.value if decision else None,
            "selected_rule_id": decision.selected_rule_id if decision else None,
            "path_signals": path_signals,
            "fast_signals": fast_signals,
            "effective_verdict": effective.verdict.value if effective else None,
            "effective_needs_review": effective.needs_review if effective else None,
            "effective_disposition": transition.effective_disposition.value if transition and transition.effective_disposition else None,
            "fast_ignore_applied": fast_ignore_applied,
            "authorization_count": len(authorizations),
            "execution_count": len(executions),
        },
        "checks": checks,
    }


def _expected_payload(case: ExpectedCase) -> dict[str, Any]:
    return {
        "path_signal_values": list(case.expected_signal_values),
        "fast_ignore": case.expected_fast_ignore,
    }


def _empty_observed() -> dict[str, Any]:
    return {
        "run_id": None,
        "path_signals": [],
        "fast_signals": [],
        "fast_ignore_applied": False,
        "authorization_count": 0,
        "execution_count": 0,
    }


def _check(name: str, passed: bool, detail: str | None = None) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "detail": detail}


@contextmanager
def _scoped_environment(
    updates: Mapping[str, str],
    *,
    cleared: Sequence[str] = (),
) -> Iterator[None]:
    names = set(updates).union(cleared)
    previous = {name: os.environ.get(name) for name in names}
    try:
        for name in cleared:
            os.environ.pop(name, None)
        os.environ.update(updates)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.chmod(0o600)


if __name__ == "__main__":
    raise SystemExit(main())
