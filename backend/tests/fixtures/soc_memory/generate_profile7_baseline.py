"""Explicit, one-time synthetic fixture generation under the shipped old SOC code.

Normal tests never import or execute this generator. No model or business DB is used.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import NAMESPACE_URL, uuid5

SOURCE_COMMIT = "2b4d3e43109acb75eb88ed9eb69353760d8facb1"
FIXTURE_ID = "pingan-profile7-v5-release-20260918"
CLOCK = datetime(2026, 8, 15, 2, tzinfo=UTC)
ENVIRONMENT = "dev-corpus-eval"
APPROVED_URL = "https://updates.example.test/approved"


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _generate(old_root: Path, destination: Path):
    sys.path[:0] = [str(old_root / "backend"), str(old_root / "backend/tests")]
    os.environ["SOC_NORMALIZATION_ASSIST_MODE"] = "off"

    from test_soc_pingan_memory_profile import _windows_update_entities, _windows_update_run

    from soc_agent.application.memory import build_soc_memory_profile_registry
    from soc_agent.contracts import (
        ActorContext,
        ActorType,
        AlertInput,
        EntrySurface,
        HttpEntityRef,
        MemoryPatternDataClass,
        MemoryPatternSourceType,
        NormalizationAssistResult,
        ServiceRequestContext,
        SocMemoryBusinessLesson,
        SocMemoryCandidateReviewCommand,
        SocMemoryCandidateReviewDecision,
        Verdict,
    )
    from soc_agent.core import SocMemoryPatternService, SocMemoryService
    from soc_agent.memory import InMemoryMemoryPatternRepository, memory_query_from_analysis_request
    from soc_agent.memory.lessons import promote_memory_applicability_facets
    from soc_agent.pipeline.extractor import extract_entities

    # A stale editable installation must never supply the generator's SOC code.
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if name.startswith("soc_agent") and filename:
            assert Path(filename).resolve().is_relative_to(old_root), (name, filename)

    identity = {"profile_id": "pingan.soc", "profile_version": "7", "feature_schema_version": "pingan.soc.memory_features.v5"}
    registry = build_soc_memory_profile_registry()
    repository = InMemoryMemoryPatternRepository()
    service = SocMemoryPatternService(repository=repository, candidate_repository=repository, profile_registry=registry, now_provider=lambda: CLOCK)
    context = ServiceRequestContext(
        request_id="synthetic-legacy-accumulation",
        actor=ActorContext(actor_id="synthetic-observer", actor_type=ActorType.SERVICE, surface=EntrySurface.TEST, roles=["soc_batch_runner"]),
    )

    def make_run(index, *, host="SYNTHETIC-ENDPOINT", url=APPROVED_URL, parent="wuauserv"):
        run = _windows_update_run(index, entities=_windows_update_entities(class_id=f"{index:08d}-1111-1111-1111-111111111111", host_name=host, parent_service=parent))
        request = run.llm_analysis_request
        request.environment = ENVIRONMENT
        request.memory_profile = dict(identity)
        request.canonical_entities.http = HttpEntityRef(host="updates.example.test", path="/approved", url=url)
        request.extracted_entities = extract_entities(AlertInput(alert_id=run.alert_id, source=request.source, detection=request.detection, classification=request.classification, entities=request.canonical_entities))
        run.normalization_assistance = NormalizationAssistResult(mode="apply", status="unchanged", request_hash="synthetic-review", model_name="synthetic-no-model", prompt_version="synthetic-v1")
        run.ended_at = run.started_at + timedelta(seconds=1)
        return run

    sequence = iter(range(1, 10_000))

    def fixture_uuid():
        return uuid5(NAMESPACE_URL, f"{FIXTURE_ID}:{next(sequence)}")

    with (
        patch("soc_agent.contracts.schemas.uuid4", fixture_uuid),
        patch("soc_agent.contracts.memory_patterns.uuid4", fixture_uuid),
        patch("soc_agent.contracts.common.uuid4", lambda: uuid5(NAMESPACE_URL, FIXTURE_ID + ":request")),
    ):
        runs = [make_run(i, host=f"SYNTHETIC-ENDPOINT-{i}") for i in range(1, 6)]
        for run in runs:
            result = service.observe_run(run, source_type=MemoryPatternSourceType.BATCH_ALERT, transport_ref=f"synthetic:{run.run_id}", environment=ENVIRONMENT, data_class=MemoryPatternDataClass.OPERATIONAL, context=context)
        candidate = result.candidate
        assert candidate is not None and result.support_count == 5
        selected = promote_memory_applicability_facets(candidate.applicability, [], {"entity": ["url:" + APPROVED_URL]})
        memory = SocMemoryService(candidate_repository=repository, record_repository=repository, mutation_audit_repository=repository, profile_registry=registry, now_provider=lambda: CLOCK)
        reviewed = memory.review_candidate(
            SocMemoryCandidateReviewCommand(
                candidate_id=candidate.candidate_id,
                decision=SocMemoryCandidateReviewDecision.CONFIRM,
                reason="合成兼容性样本审核，不代表真实业务授权。",
                record_lesson=SocMemoryBusinessLesson(
                    conclusion="模拟审核：规定更新行为属于误报。",
                    business_rationale=["全部记录为旧版本代码生成的合成测试数据。"],
                    applicability_conditions=["检测器与核心行为一致，且 URL 等于已选值。"],
                    generalization_boundaries=["主机名和 ClassId 可变化，已选 URL 不可变化。"],
                    invalidation_conditions=["检测、父服务、已选 URL 或租户环境变化时不可直接复用。"],
                    handling_guidance=["仅审核范围内复用，其余继续正常分析。"],
                ),
                record_applicability=selected,
                confirmed_verdict=Verdict.FALSE_POSITIVE,
                apply_to_future_matches=True,
                clear_review_on_match=True,
                activate_retrieval=True,
                activation_valid_until=CLOCK + timedelta(days=60),
                activation_review_after_days=30,
            ),
            context=ServiceRequestContext(
                request_id="synthetic-legacy-review",
                idempotency_key="synthetic-legacy-review",
                actor=ActorContext(actor_id="synthetic-reviewer", actor_type=ActorType.USER, surface=EntrySurface.TEST, roles=["soc_memory_reviewer"]),
            ),
        )
    record = reviewed.memory_record
    assert record is not None and record.retrieval_enabled
    exact = make_run(6, host="SYNTHETIC-ENDPOINT-1")
    generalized = make_run(7, host="SYNTHETIC-OTHER-ENDPOINT")
    behavior_changed = make_run(8, parent="RemoteRegistry")
    entity_changed = make_run(9, url=APPROVED_URL + "-different")
    queries = {"exact": exact.llm_analysis_request, "allowed_host_change": generalized.llm_analysis_request, "changed_behavior": behavior_changed.llm_analysis_request, "changed_entity": entity_changed.llm_analysis_request}
    for name, request in queries.items():
        query = memory_query_from_analysis_request(request, profile=registry.resolve_request(request))
        found = memory.find_directive_records(query)
        applicable = [m for m in found.matches if m.applicability_report and m.applicability_report.status.value == "applicable"]
        assert bool(applicable) == (name in {"exact", "allowed_host_change"}), name
    payload = {
        "runs": [r.model_dump(mode="json") for r in runs],
        "observations": [o.model_dump(mode="json") for o in repository.list_memory_pattern_observations(limit=100)],
        "candidates": [reviewed.candidate.model_dump(mode="json")],
        "records": [record.model_dump(mode="json")],
        "queries": {key: value.model_dump(mode="json") for key, value in queries.items()},
    }
    source_files = {str(p.relative_to(old_root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((old_root / "backend/soc_agent").rglob("*.py"))}
    document = {
        "fixture_id": FIXTURE_ID,
        "source_commit": SOURCE_COMMIT,
        "source_tree_sha256": hashlib.sha256(_canonical(source_files)).hexdigest(),
        "clock": CLOCK.isoformat(),
        "synthetic": True,
        "real_model_calls": 0,
        "payload_sha256": hashlib.sha256(_canonical(payload)).hexdigest(),
        "payload": payload,
    }
    destination.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in document.items() if key != "payload"}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--isolated-baseline", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.isolated_baseline:
        _generate(args.isolated_baseline.resolve(), args.output.resolve())
        return
    repo = Path(__file__).resolve().parents[4]
    archive = subprocess.check_output(["git", "archive", SOURCE_COMMIT, "backend/soc_agent", "backend/tests/test_soc_pingan_memory_profile.py"], cwd=repo)
    with tempfile.TemporaryDirectory(prefix="soc-memory-legacy-") as folder:
        old_root = Path(folder)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(old_root, filter="data")
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--isolated-baseline", str(old_root), "--output", str(args.output.resolve())], cwd=old_root, check=True)


if __name__ == "__main__":
    main()
