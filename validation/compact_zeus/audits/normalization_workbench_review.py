"""Run selected DEV alerts, then inspect frozen semantic proposals without extra LLM calls."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sqlite3
import sys
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from itertools import combinations
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))

from soc_agent.contracts import (  # noqa: E402
    AlertInput,
    NormalizationAssistRequest,
    NormalizationAssistResult,
    SensitiveEvidenceMode,
)
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile  # noqa: E402
from soc_agent.llm.normalization import apply_normalization_changes  # noqa: E402
from soc_agent.normalizers.semantic_observations import observation_projection_present  # noqa: E402
from soc_agent.pipeline.analysis_context import (  # noqa: E402
    build_llm_analysis_request,
    project_analysis_context,
)
from soc_agent.pipeline.extractor import extract_entities  # noqa: E402
from soc_agent.pipeline.fact_reconstructor import reconstruct_facts  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402
from validation.compact_zeus.audits.normalization_assistance_trial import (  # noqa: E402
    git_value,
    write_json,
)

IDS = ["2448412", "2448932", "2448580", "2464210", "2455416"]


def artifacts(bundle):
    return {a["artifact_id"]: a["payload"] for a in bundle["artifacts"]}


def consumer_projection(alert, *, semantic_features):
    request = build_llm_analysis_request(
        alert,
        extract_entities(alert),
        reconstruct_facts(alert),
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )
    request.environment = "dev-corpus-eval"
    profile = PingAnSocMemoryProfile(semantic_features=semantic_features)
    facets = profile.project_query_facets(request)
    spec = profile.build_applicability(
        consensus_facets=facets, strong_anchor_facets=facets
    )
    return {
        "facets": facets,
        "applicability": spec,
        "request": request,
        "profile": asdict(profile.identity),
    }


def inspect_frozen_review(bundle, directory, *, persisted_run):
    stored = artifacts(bundle)
    if persisted_run["run_id"] != bundle["run_id"]:
        raise ValueError("persisted run/audit identity mismatch")
    alert = AlertInput.model_validate(persisted_run["normalized_alert"])
    # The Web DTO intentionally omits nulls, including in raw dictionaries; hash
    # validation must use the full frozen persistence payload, not that projection.
    request = NormalizationAssistRequest.model_validate(
        persisted_run["normalization_assist_request"]
    )
    report = NormalizationAssistResult.model_validate(
        persisted_run["normalization_assistance"]
    )
    if report.mode != "shadow" or report.request_hash != stable_hash(
        request.model_dump(mode="json")
    ):
        raise ValueError("expected a frozen shadow review with matching request hash")
    # This copy is an offline consumer experiment, never persisted as an operational Run.
    applied = report.model_copy(deep=True)
    applied.mode = "apply"
    after = apply_normalization_changes(alert, request, applied)
    variants = {
        "adapter_v5": consumer_projection(alert, semantic_features=False),
        "adapter_v6": consumer_projection(alert, semantic_features=True),
        "supplemented_v6": consumer_projection(after, semantic_features=True),
    }
    write_json(directory / "offline-canonical-after.json", after)
    write_json(directory / "offline-consumer-projections.json", variants)
    before_facets = variants["adapter_v5"]["facets"]
    after_facets = variants["supplemented_v6"]["facets"]
    base_v6 = variants["adapter_v6"]["facets"]
    projected = project_analysis_context(variants["supplemented_v6"]["request"])

    def components(f):
        return f.get("behavior_component_core", [])

    row = {
        "alert_id": bundle["alert_id"],
        "run_id": bundle["run_id"],
        "model": report.model_name,
        "prompt_version": report.prompt_version,
        "config_hash": request.configuration_hash,
        "data_hash": bundle["input_hash"],
        "review_status": report.status,
        "changes": len(report.changes) + len(report.observation_changes),
        "issues": report.issues,
        "review_metadata": report.metadata,
        "raw_preserved": alert.raw == after.raw,
        "offline_merge_applied": report.status not in {"failed", "skipped"},
        "model_input_present": sum(
            observation_projection_present(projected, c)
            for c in report.observation_changes
        ),
        "before_fingerprint": before_facets.get("behavior_fingerprint", []),
        "after_fingerprint": after_facets.get("behavior_fingerprint", []),
        "before_strength": before_facets.get("behavior_strength", []),
        "after_strength": after_facets.get("behavior_strength", []),
        "adapter_v6_fingerprint": base_v6.get("behavior_fingerprint", []),
        "adapter_v6_strength": base_v6.get("behavior_strength", []),
        "before_components": components(before_facets),
        "after_components": components(after_facets),
        "added_by_model": sorted(
            set(components(after_facets)) - set(components(base_v6))
        ),
        "removed_by_model": sorted(
            set(components(base_v6)) - set(components(after_facets))
        ),
        "decision": {
            "base_decision": stored["decision-lineage"].get("base_decision"),
            "transitions": [
                {
                    k: transition.get(k)
                    for k in ("before", "after", "effective_disposition")
                }
                for transition in stored["decision-lineage"].get(
                    "decision_transitions", []
                )
            ],
        },
        "quality": stored["output-validation"].get("analysis_output_quality"),
        "run_identity": stored["run-manifest"]["run_identity"],
        "extra_model_calls_for_consumer_comparison": 0,
        "operational_groups_changed_by_comparison": False,
    }
    write_json(directory / "consumer-summary.json", row)
    return row


def load_persisted_run(database, run_id):
    with sqlite3.connect(
        database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        row = connection.execute(
            "SELECT run_payload FROM soc_analysis_runs WHERE run_id=?", (run_id,)
        ).fetchone()
    if row is None:
        raise ValueError(f"run {run_id} not found in the selected read-only database")
    return json.loads(row[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:2026")
    parser.add_argument("--alert-ids", nargs="+", default=IDS)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collection-id", default="verified")
    parser.add_argument(
        "--database-file",
        type=Path,
        default=ROOT
        / "backend/.deer-flow/soc-validation/memory-dev-web/soc-memory-dev.sqlite",
    )
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument(
        "--collect-only",
        action="store_true",
        help="Read already submitted runs without resubmitting",
    )
    args = parser.parse_args()
    if not args.collection_id.isidentifier():
        parser.error("--collection-id must be a simple identifier")
    if not args.confirm_live or urlparse(args.base_url).hostname not in {
        "localhost",
        "127.0.0.1",
    }:
        parser.error("explicit --confirm-live and a loopback DEV endpoint are required")
    if not 1 <= len(args.alert_ids) <= 10 or len(set(args.alert_ids)) != len(
        args.alert_ids
    ):
        parser.error("select 1..10 distinct alert IDs")
    if not args.collect_only:
        args.output.mkdir(parents=True, mode=0o700, exist_ok=False)
    prefix = "/api/soc/dev/corpus-workbench"
    started = time.monotonic()
    with httpx.Client(base_url=args.base_url, timeout=60, trust_env=False) as client:

        def get(path):
            response = client.get(prefix + path)
            response.raise_for_status()
            return response.json()

        state = get("?limit=1&unprocessed_only=false")
        if state["safety"].get("normalization_review_mode") != "shadow":
            raise ValueError(
                "this validation expects the existing shadow DEV configuration"
            )
        if not args.collect_only:
            write_json(args.output / "workbench-before.json", state)
        for alert_id in args.alert_ids:
            case_dir = args.output / alert_id
            if args.collect_only:
                if not (case_dir / "accepted.json").exists():
                    raise ValueError(f"{alert_id} has no prior submission to collect")
                continue
            case_dir.mkdir(mode=0o700)
            response = client.get(prefix + f"/alerts/{alert_id}/audit")
            if response.status_code == 200:
                write_json(case_dir / "before-audit.json", response.json())
            elif response.status_code != 404:
                response.raise_for_status()
        pending = [] if args.collect_only else list(args.alert_ids)
        active = (
            {alert_id: time.monotonic() for alert_id in args.alert_ids}
            if args.collect_only
            else {}
        )
        rows = []
        while pending or active:
            if time.monotonic() - started > 1800:
                raise TimeoutError(
                    "validation deadline exceeded; inspect existing jobs, do not resubmit"
                )
            activity = get("/activity")
            for _ in range(min(activity["available_slots"], len(pending))):
                alert_id = pending.pop(0)
                response = client.post(
                    prefix + f"/alerts/{alert_id}/process",
                    headers={"Idempotency-Key": "semantic-review-" + uuid.uuid4().hex},
                )
                response.raise_for_status()
                write_json(args.output / alert_id / "accepted.json", response.json())
                active[alert_id] = time.monotonic()
                print(f"START {alert_id}", flush=True)
            for alert_id in list(active):
                execution = get(f"/alerts/{alert_id}/execution")
                status = execution["status"]
                still_active = any(
                    item["alert_id"] == alert_id
                    for item in get("/activity")["executions"]
                )
                prior_path = args.output / alert_id / "before-audit.json"
                prior_run_id = (
                    json.loads(prior_path.read_text(encoding="utf-8")).get("run_id")
                    if prior_path.exists()
                    else None
                )
                fresh_run = (
                    execution.get("run_id") and execution["run_id"] != prior_run_id
                )
                print(
                    f"{alert_id}: {status} / {execution.get('current_phase')} / {execution.get('elapsed_ms')} ms",
                    flush=True,
                )
                if (
                    still_active
                    or not fresh_run
                    or status in {"running", "not_started"}
                ):
                    if time.monotonic() - active[alert_id] > 900:
                        raise TimeoutError(
                            f"{alert_id} is still active; inspect the DEV execution without resubmitting"
                        )
                    continue
                bundle = get(f"/alerts/{alert_id}/audit")
                directory = args.output / alert_id / args.collection_id
                directory.mkdir(mode=0o700, exist_ok=False)
                write_json(directory / "after-audit.json", bundle)
                try:
                    persisted_run = load_persisted_run(
                        args.database_file, bundle["run_id"]
                    )
                    write_json(directory / "persisted-run.json", persisted_run)
                    row = inspect_frozen_review(
                        bundle, directory, persisted_run=persisted_run
                    )
                except (ValueError, KeyError, TypeError) as exc:
                    row = {
                        "alert_id": alert_id,
                        "run_id": execution.get("run_id"),
                        "inspection_error": str(exc),
                    }
                rows.append(row)
                del active[alert_id]
                print(
                    f"DONE {alert_id}: review={row.get('review_status')}, changes={row.get('changes')}, fingerprint={bool(row.get('after_fingerprint'))}",
                    flush=True,
                )
            if pending or active:
                time.sleep(10)
    pairs = [
        {
            "ids": [a["alert_id"], b["alert_id"]],
            "same_before": bool(a.get("before_fingerprint"))
            and a.get("before_fingerprint") == b.get("before_fingerprint"),
            "same_after": bool(a.get("after_fingerprint"))
            and a.get("after_fingerprint") == b.get("after_fingerprint"),
        }
        for a, b in combinations(rows, 2)
    ]
    summary = {
        "task_id": "PI-03E",
        "created_at": datetime.now(UTC).isoformat(),
        "code_commit": git_value("rev-parse", "HEAD"),
        "upstream_commit": git_value("rev-parse", "upstream/main"),
        "hardware": f"{platform.platform()} / Python {platform.python_version()} / CPUs {os.cpu_count()}",
        "command": sys.argv,
        "duration_ms": round((time.monotonic() - started) * 1000),
        "scope": "Real DEV shadow runs plus offline application of the same frozen proposals; not real apply-mode verdict quality or group migration",
        "rows": rows,
        "pairs": pairs,
    }
    write_json(
        args.output
        / (
            f"{args.collection_id}-summary.json"
            if args.collect_only
            else "summary.json"
        ),
        summary,
    )
    print(
        json.dumps(
            {
                "saved": str(args.output),
                "cases": len(rows),
                "duration_ms": summary["duration_ms"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
