"""Read-only saved-input regression of semantic identities and fact snapshot reuse."""

from __future__ import annotations

import argparse
import json
import platform
import sqlite3
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from soc_agent.contracts import AnalysisRun, SocMemoryCandidateType, SocMemoryRecord  # noqa: E402
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions  # noqa: E402
from soc_agent.core.service import DeterministicAnalysisRuntime, SocAnalysisService  # noqa: E402
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables  # noqa: E402
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile  # noqa: E402
from soc_agent.integrations.pingan.memory.semantic_features import _subject  # noqa: E402
from soc_agent.llm.analyzer import LLMChatResponse  # noqa: E402
from soc_agent.llm.normalization import JsonLLMNormalizationReviewer  # noqa: E402
from soc_agent.memory.retrieval import memory_query_from_analysis_request  # noqa: E402
from soc_agent.memory.scoring import evaluate_memory_scope  # noqa: E402
from soc_agent.normalizers.semantic_observations import OBJECT_FIELDS  # noqa: E402
from soc_agent.prompts.normalization import NORMALIZATION_PROMPT_VERSION, SYSTEM_PROMPT  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402


def recorded_proposals(run: AnalysisRun) -> dict:
    """Recreate the saved source-bound event/object choices, not a new model result."""
    result = {"objects": [], "events": []}
    for change in run.normalization_assistance.observation_changes:
        if not change.target.startswith("entities.detections["):
            continue
        event = {
            k: v
            for k, v in change.after.items()
            if k
            in {"kind", "name", "category", "detector_id", "reported_result", "action"}
        }
        event.update(
            source_id=change.source_id,
            source_quote=change.source_quote,
            subject_refs=[],
        )
        for ref in change.after["subject_refs"]:
            kind = ref.split(".")[1]
            attributes = {
                k: v
                for k, v in _subject(run.normalized_alert.entities, ref).items()
                if k in OBJECT_FIELDS[kind] and v is not None
            }
            object_id = f"n{len(result['objects'])}"
            result["objects"].append(
                {
                    "id": object_id,
                    "kind": kind,
                    "attributes": attributes,
                    "source_id": change.source_id,
                    "source_quote": change.source_quote,
                }
            )
            event["subject_refs"].append(object_id)
        result["events"].append(event)
    return result


class SavedResponseClient:
    def __init__(self, output):
        self.output, self.calls = output, 0

    def complete(self, messages, *, model_name):
        self.calls += 1
        return LLMChatResponse(content=json.dumps(self.output), model_name=model_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--before-run", required=True)
    parser.add_argument("--after-run", required=True)
    parser.add_argument("--memory-id")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use the configured provider for semantic review only, in an isolated test store",
    )
    args = parser.parse_args()
    with sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        runs = [
            AnalysisRun.model_validate_json(
                connection.execute(
                    "SELECT run_payload FROM soc_analysis_runs WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            for run_id in (args.before_run, args.after_run)
        ]
        memory = (
            SocMemoryRecord.model_validate_json(
                connection.execute(
                    "SELECT record_payload FROM soc_memory_records WHERE memory_id=?",
                    (args.memory_id,),
                ).fetchone()[0]
            )
            if args.memory_id
            else None
        )
    assert runs[0].input_hash == runs[1].input_hash
    output = recorded_proposals(runs[0])
    client = SavedResponseClient(output)
    model = "saved-response-simulation"
    if args.live:
        from soc_agent.llm.settings import SocLLMSettings, build_configured_chat_client

        settings = SocLLMSettings.from_env()
        client, model = build_configured_chat_client(
            settings=settings, run_name="soc_normalization_stability_acceptance"
        )
    reviewer = JsonLLMNormalizationReviewer(client=client, model_name=model)
    profile = PingAnSocMemoryProfile(semantic_features=True)

    class SavedScope:
        def __call__(self, request):
            return request.model_copy(
                update={"environment": runs[0].llm_analysis_request.environment}
            )

    cases = []
    with tempfile.TemporaryDirectory(prefix="soc-semantic-stability-") as directory:
        engine = create_engine(f"sqlite:///{Path(directory) / 'isolated.sqlite'}")
        create_soc_tables(engine)
        repository = SqlAlchemyAlertRepository(sessionmaker(bind=engine))

        def analyze(refresh=False):
            service = SocAnalysisService(
                repository=repository,
                runtime=DeterministicAnalysisRuntime(
                    normalization_reviewer=reviewer,
                    analysis_request_enricher=SavedScope(),
                    execution_options=SocAnalysisExecutionOptions(
                        normalization_review_mode="apply", refresh_normalization=refresh
                    ),
                ),
            )
            result = service.analyze(runs[0].input_payload)
            assert result.normalization_assistance.status not in {
                "failed",
                "skipped",
            }, result.normalization_assistance.issues
            assert result.analysis is not None, (
                result.failure
            )  # A new analysis, not a cached decision.
            return result

        first = analyze()
        second = analyze()
        assert (
            second.normalization_assistance.metadata["reused_from_run_id"]
            == first.run_id
        )
        assert second.normalization_assistance.metadata["provider_call_count"] == 0
        assert profile.project_run_facets(first) == profile.project_run_facets(second)
        samples = [("initial", first), ("same_input_rerun", second)]
        if not args.live:
            client.output = recorded_proposals(runs[1])
            refreshed = analyze(refresh=True)
            assert client.calls == 2
            initial = first.normalized_alert.entities.detections[0]
            current = refreshed.normalized_alert.entities.detections[0]
            assert initial.detector_id == current.detector_id == "0x5dc9"
            assert initial.identifiers == current.identifiers
            assert len(refreshed.normalized_alert.entities.detections) == 2
            assert refreshed.normalization_assistance.metadata[
                "fact_snapshot_comparison"
            ]["changed"]
            samples.append(("explicit_recheck_with_extra_detection", refreshed))
        for label, result in samples:
            query = memory_query_from_analysis_request(
                result.llm_analysis_request, profile=profile
            )
            spec = profile.build_applicability(
                consensus_facets=profile.project_run_facets(first),
                strong_anchor_facets=profile.project_run_facets(first),
            )
            cases.append(
                {
                    "case": label,
                    "normalization": result.normalization_assistance.model_dump(
                        mode="json"
                    ),
                    "facts": [
                        d.model_dump(mode="json")
                        for d in result.normalized_alert.entities.detections
                    ],
                    "core": query.facets.get("behavior_component_core", []),
                    "simulated_initial_scope": evaluate_memory_scope(
                        spec, SocMemoryCandidateType.DETECTION_LESSON, query, {}
                    ).model_dump(mode="json"),
                    "existing_memory_scope": evaluate_memory_scope(
                        memory.applicability, memory.memory_type, query, {}
                    ).model_dump(mode="json")
                    if memory
                    else None,
                }
            )
        engine.dispose()
    report = {
        "schema_version": "soc.normalization_identity_stability.v1",
        "passed": True,
        "created_at": datetime.now(UTC).isoformat(),
        "mode": "live_semantic_only" if args.live else "saved_choices_simulation",
        "real_model_calls": 1 if args.live else 0,
        "operational_writes": 0,
        "primary_analyzer": "stub",
        "model": model,
        "prompt_version": NORMALIZATION_PROMPT_VERSION,
        "prompt_hash": stable_hash(SYSTEM_PROMPT),
        "data_hash": runs[0].input_hash,
        "before_run": args.before_run,
        "after_run": args.after_run,
        "head": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "upstream_commit": subprocess.check_output(
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
                "passed": True,
                "report": str(args.output),
                "real_model_calls": report["real_model_calls"],
                "operational_writes": 0,
                "cases": [
                    {
                        "case": c["case"],
                        "core": c["core"],
                        "existing_memory_scope": c["existing_memory_scope"],
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
