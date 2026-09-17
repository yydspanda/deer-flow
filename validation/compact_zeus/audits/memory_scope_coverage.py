"""Read saved DEV runs without changing operational Memory or invoking a model."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from soc_agent.contracts import AnalysisRun, SocMemoryCandidateType  # noqa: E402
from soc_agent.integrations.pingan.memory.profile import (  # noqa: E402
    PingAnSocMemoryProfile,
)
from soc_agent.memory.retrieval import memory_query_from_analysis_request  # noqa: E402
from soc_agent.memory.scoring import evaluate_memory_scope  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = PingAnSocMemoryProfile(semantic_features=True)
    queries, cases = {}, []
    with sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        for alert_id in ("2457581", "2457177", "2480991", "2488405"):
            rows = connection.execute(
                "SELECT run_payload FROM soc_analysis_runs WHERE alert_id=? AND status='success' ORDER BY created_at DESC",
                (alert_id,),
            ).fetchall()
            for (payload,) in rows:
                run = AnalysisRun.model_validate_json(payload)
                if (
                    run.llm_analysis_request
                    and run.normalization_assistance
                    and run.normalization_assistance.mode == "apply"
                ):
                    break
            else:
                cases.append({"alert_id": alert_id, "status": "no_saved_apply_run"})
                continue
            query = memory_query_from_analysis_request(
                run.llm_analysis_request, profile=profile
            )
            queries[alert_id] = query
            old = PingAnSocMemoryProfile(
                semantic_features=True, stable_semantics=False
            ).project_query_facets(run.llm_analysis_request)
            cases.append(
                {
                    "alert_id": alert_id,
                    "run_id": run.run_id,
                    "saved_request_sha256": hashlib.sha256(
                        run.llm_analysis_request.model_dump_json().encode()
                    ).hexdigest(),
                    "old_core": old.get("behavior_component_core", []),
                    "new_core": query.facets.get("behavior_component_core", []),
                    "projection_gaps": query.projection_gaps,
                }
            )
    reference = queries.get("2457581")
    if reference:
        spec = profile.build_applicability(
            consensus_facets=reference.facets, strong_anchor_facets=reference.facets
        )
        for case in cases:
            if case["alert_id"] in queries:
                report = evaluate_memory_scope(
                    spec,
                    SocMemoryCandidateType.DETECTION_LESSON,
                    queries[case["alert_id"]],
                    {},
                )
                case["simulated_scope_from_2457581"] = report.model_dump(mode="json")
    report = {
        "schema_version": "soc.memory_scope_coverage_audit.v1",
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "read_only_saved_canonical_reprojection",
        "real_llm_calls": 0,
        "operational_writes": 0,
        "synthetic_scope": True,
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "upstream": subprocess.check_output(
            ["git", "rev-parse", "upstream/main"], cwd=ROOT, text=True
        ).strip(),
        "hardware": platform.platform(),
        "command": sys.argv,
        "cases": cases,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    args.output.chmod(0o600)
    print(
        json.dumps(
            {
                "report": str(args.output),
                "cases": [
                    {
                        "alert_id": c["alert_id"],
                        "status": c.get("simulated_scope_from_2457581", {}).get(
                            "status", c.get("status")
                        ),
                        "uncovered": c.get("simulated_scope_from_2457581", {}).get(
                            "uncovered_behavior_components", []
                        ),
                    }
                    for c in cases
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
