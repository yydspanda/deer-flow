#!/usr/bin/env python3
"""Replay a saved DEV audit with an explicitly simulated opposite Memory."""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from soc_agent.application.memory import build_soc_memory_profile_registry  # noqa: E402
from soc_agent.contracts import (  # noqa: E402
    LLMAnalysisRequest,
    SocMemoryDecisionDirective,
    SocMemoryDecisionImpact,
    SocMemoryRecord,
    Verdict,
)
from soc_agent.core.service import SocMemoryService  # noqa: E402
from soc_agent.memory import (  # noqa: E402
    ConfirmedMemoryAnalysisRequestEnricher,
    InMemoryMemoryCandidateRepository,
    memory_query_from_analysis_request,
)
from soc_agent.prompts import ANALYSIS_PROMPT_VERSION, build_analysis_prompt  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402


def replay(audit: dict, memory: SocMemoryRecord) -> dict:
    artifact = next(
        item
        for item in audit["artifacts"]
        if "runtime_request_audit" in item["payload"]
    )
    request = LLMAnalysisRequest.model_validate(
        artifact["payload"]["runtime_request_audit"]
    )
    request.context_catalog = [
        item
        for item in request.context_catalog
        if item.kind.value != "confirmed_memory"
    ]
    registry = build_soc_memory_profile_registry()
    profile = registry.resolve_request(request)
    query = memory_query_from_analysis_request(request, profile=profile)
    applicability = profile.build_applicability(
        consensus_facets=query.facets, strong_anchor_facets=query.facets
    )
    if applicability is None or memory.reviewed_verdict not in {
        Verdict.FALSE_POSITIVE,
        Verdict.TRUE_POSITIVE,
    }:
        raise ValueError(
            "Replay requires typed behavior and a definite reviewed source lesson"
        )
    opposite = (
        Verdict.TRUE_POSITIVE
        if memory.reviewed_verdict is Verdict.FALSE_POSITIVE
        else Verdict.FALSE_POSITIVE
    )
    simulated = memory.model_copy(
        deep=True,
        update={
            "memory_id": "MEM-SIMULATED-EXACT",
            "version": 1,
            "source_candidate_id": "MC-SIMULATED-EXACT",
            "summary": f"[SIMULATION] Exact current behavior reviewed as {opposite.value}",
            "content": "Synthetic opposite review for selection-boundary testing; not analyst truth.",
            "content_hash": stable_hash(
                {"simulation": True, "verdict": opposite.value}
            ),
            "facets": query.facets,
            "facets_hash": stable_hash(query.facets),
            "applicability": applicability,
            "reviewed_verdict": opposite,
            "business_lesson": None,
            "decision_impact": SocMemoryDecisionImpact.DETECTION_DECISION,
            "decision_directive": None,
            "metadata": {"simulation": True},
        },
    )
    simulated = SocMemoryRecord.model_validate(simulated.model_dump(mode="json"))
    directive = simulated.model_copy(
        update={
            "decision_directive": SocMemoryDecisionDirective(
                effect="override",
                target_verdict=opposite,
                required_facet_keys=sorted(applicability.required_facets),
                rationale="Simulated reviewer-enabled exact reuse; never published.",
            )
        }
    )
    cases = []
    now = datetime.now(UTC)
    for label, records in (
        ("reference_only_baseline", [memory]),
        ("opposite_exact_context", [memory, simulated]),
        ("opposite_exact_directive", [memory, directive]),
    ):
        repository = InMemoryMemoryCandidateRepository()
        for record in records:
            repository.save_memory_record(record)
        service = SocMemoryService(
            record_repository=repository,
            profile_registry=registry,
            now_provider=lambda: now,
        )
        retrieved = service.find_relevant_records(query)
        enriched = ConfirmedMemoryAnalysisRequestEnricher(
            service, profile_registry=registry
        )(request)
        prompt = build_analysis_prompt(enriched)
        selected = [
            item
            for item in enriched.context_catalog
            if item.kind.value == "confirmed_memory"
        ]
        cases.append(
            {
                "case": label,
                "retrieved": [
                    {
                        "memory_id": item.memory_id,
                        "status": item.applicability_report.status.value,
                    }
                    for item in retrieved.matches
                ],
                "model_memory": [
                    {
                        "source_id": item.source_id,
                        "comparison": item.memory_comparison.model_dump(mode="json"),
                    }
                    for item in selected
                ],
                "audit_only": [
                    item.model_dump(mode="json")
                    for item in enriched.memory_context_exclusions
                ],
                "prompt_example": prompt.example_id,
                "prompt_sha256": stable_hash(prompt.messages()),
                "prompt_chars": len(prompt.system) + len(prompt.user),
                "excluded_record_visible_in_prompt": any(
                    item.source_id in prompt.user
                    for item in enriched.memory_context_exclusions
                ),
            }
        )
    passed = [item["source_id"] for item in cases[0]["model_memory"]] == [
        f"{memory.memory_id}@v{memory.version}"
    ] and all(
        [item["source_id"] for item in case["model_memory"]]
        == ["MEM-SIMULATED-EXACT@v1"]
        and len(case["audit_only"]) == 1
        and not case["excluded_record_visible_in_prompt"]
        for case in cases[1:]
    )
    return {
        "schema_version": "soc.validation.memory_context_precedence.v1",
        "simulated_opposite_review": True,
        "source_alert_id": request.alert_id,
        "source_run_id": audit["run_id"],
        "source_memory_id": memory.memory_id,
        "passed": passed,
        "real_model_calls": 0,
        "production_database_writes": 0,
        "new_alert_verdict": None,
        "evaluated_at": now.isoformat(),
        "prompt_version": ANALYSIS_PROMPT_VERSION,
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-file", type=Path, required=True)
    parser.add_argument("--memory-records", type=Path, required=True)
    parser.add_argument("--memory-id", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        parser.error("output directory must be empty; preserve previous evidence")
    audit = json.loads(args.audit_file.read_text(encoding="utf-8"))
    inventory = json.loads(args.memory_records.read_text(encoding="utf-8"))
    memory = SocMemoryRecord.model_validate(
        next(item for item in inventory["items"] if item["memory_id"] == args.memory_id)
    )
    report = replay(audit, memory)
    report.update(
        input_hashes={
            str(path): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (args.audit_file, args.memory_records)
        },
        code_hash=hashlib.sha256(
            (ROOT / "backend/soc_agent/memory/retrieval.py").read_bytes()
        ).hexdigest(),
        upstream_commit=subprocess.check_output(
            ["git", "rev-parse", "upstream/main"], cwd=ROOT, text=True
        ).strip(),
        hardware=f"{platform.system()} {platform.machine()} Python {platform.python_version()}; CPU only",
        command=sys.argv,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for source, name in (
        (args.audit_file, "source-audit.json"),
        (args.memory_records, "source-records.json"),
    ):
        snapshot = args.output_dir / name
        snapshot.write_bytes(source.read_bytes())
        snapshot.chmod(0o600)
    path = args.output_dir / "comparison.json"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    path.chmod(0o600)
    print(
        json.dumps(
            {"passed": report["passed"], "report": str(path), "real_model_calls": 0},
            ensure_ascii=False,
        )
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
