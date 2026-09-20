"""Existing exact conditions survive review, persistence and future retrieval."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from test_soc_pingan_memory_profile import _observe, _reviewed_business_lesson, _run, _service

from soc_agent.application.memory import build_soc_memory_profile_registry
from soc_agent.contracts import (
    ActorContext,
    ActorType,
    Decision,
    EntityKind,
    EntityMention,
    EntrySurface,
    ServiceRequestContext,
    SocMemoryCandidateReviewCommand,
    SocMemoryRunPromotionCommand,
    Verdict,
)
from soc_agent.core import SocMemoryService, SocReviewService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.models import SocMemoryRecordFacetRow
from soc_agent.memory import ConfirmedMemoryAnalysisRequestEnricher, memory_query_from_analysis_request
from soc_agent.memory.lessons import promote_memory_applicability_facets


@pytest.fixture
def repository(tmp_path, monkeypatch):
    monkeypatch.setenv("SOC_NORMALIZATION_ASSIST_MODE", "off")
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'exact-entity.sqlite'}")
    create_soc_tables(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield SqlAlchemyAlertRepository(factory), factory
    engine.dispose()


def _actor(role: str, key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        idempotency_key=key,
        actor=ActorContext(
            actor_id="exact-entity-reviewer",
            actor_type=ActorType.USER,
            surface=EntrySurface.TEST,
            roles=[role],
        ),
    )


@pytest.mark.parametrize(
    ("promotion", "value"),
    [
        ("automatic", "https://example.test/" + "a" * (512 - len("url:https://example.test/"))),
        ("manual", "https://example.test/" + "a" * (512 - len("url:https://example.test/"))),
        ("automatic", "sha256:" + "a" * 64),
        ("manual", "sha256:" + "a" * 64),
    ],
    ids=["automatic-512", "manual-512", "automatic-literal", "manual-literal"],
)
def test_reviewed_exact_entity_stays_scoped_across_sqlite_retrieval(repository, promotion, value):
    store, factory = repository
    registry = build_soc_memory_profile_registry()
    run = _run(1, service_url=value)
    run.decision = Decision(
        verdict=run.analysis.verdict,
        confidence=run.analysis.confidence,
        suggested_action=run.analysis.recommended_action,
        needs_review=True,
        reason=run.analysis.reason,
    )
    store.save_run(run)
    frozen_run = store.get_run(run.run_id).model_dump(mode="json")
    pattern_service = _service(store)
    first = _observe(pattern_service, run, "long-exact:1")
    frozen_observation = first.observation.model_dump(mode="json")
    if promotion == "automatic":
        candidate = _observe(pattern_service, _run(2, service_url=value), "long-exact:2").candidate
    else:
        command = SocMemoryRunPromotionCommand(run_id=run.run_id)
        context = _actor("soc_analyst", "long-exact:promote")
        review_service = SocReviewService(
            repository=store,
            memory_candidate_repository=store,
            memory_pattern_observation_repository=store,
            memory_profile_registry=registry,
        )
        candidate = review_service.promote_run_to_memory(command, context=context).memory_candidate
        assert review_service.promote_run_to_memory(command, context=context).memory_candidate.candidate_id == candidate.candidate_id

    assert candidate is not None
    exact_request = _run(3, service_url=value).llm_analysis_request
    changed_request = _run(4, service_url=value + "changed").llm_analysis_request
    # New validation inputs may contain unrelated oversized evidence. It must
    # remain in the query and cannot alter an existing reviewed exact limit.
    long_evidence = "https://evidence.example/query?sql=" + "z" * 600
    for request in (exact_request, changed_request):
        request.extracted_entities.mentions.append(EntityMention(kind=EntityKind.URL, key="url:" + long_evidence, value=long_evidence))
    exact_query = memory_query_from_analysis_request(exact_request, profile=registry.resolve_request(exact_request))
    assert "url:" + long_evidence in exact_query.facets["entity"]
    changed_query = memory_query_from_analysis_request(changed_request, profile=registry.resolve_request(changed_request))
    entity = next(item for item in candidate.facets["entity"] if item.startswith("url:"))
    assert entity == f"url:{value}"
    assert len(entity) <= 512
    assert entity in exact_query.facets["entity"]
    assert entity not in changed_query.facets["entity"]
    assert exact_query.facets["behavior_fingerprint"] == changed_query.facets["behavior_fingerprint"]
    narrowed = promote_memory_applicability_facets(candidate.applicability, [], {"entity": [entity]})
    now = datetime.now(UTC)
    memory_service = SocMemoryService(
        candidate_repository=store,
        record_repository=store,
        mutation_audit_repository=store,
        profile_registry=registry,
        now_provider=lambda: now,
    )
    record = memory_service.review_candidate(
        SocMemoryCandidateReviewCommand(
            candidate_id=candidate.candidate_id,
            decision="confirm",
            reason="模拟人工核对：仅允许审核过的完整 URL 复用结论。",
            record_lesson=_reviewed_business_lesson("模拟审核：完整 URL 符合已核实的内部业务访问。"),
            record_applicability=narrowed,
            confirmed_verdict=Verdict.FALSE_POSITIVE,
            apply_to_future_matches=True,
            activate_retrieval=True,
            activation_valid_until=now + timedelta(days=30),
            activation_review_after_days=7,
        ),
        context=_actor("soc_memory_reviewer", "long-exact:confirm"),
    ).memory_record
    assert record is not None
    assert record.retrieval_enabled
    assert record.applicability.reuse_conditions[0].values == [entity]

    # Read through a new repository instance and the SQL facet index, not a
    # process-local candidate cache. Original alert evidence stays unchanged.
    reloaded_store = SqlAlchemyAlertRepository(factory)
    with factory() as session:
        indexed_entities = session.scalars(
            select(SocMemoryRecordFacetRow.facet_value).where(
                SocMemoryRecordFacetRow.memory_id == record.memory_id,
                SocMemoryRecordFacetRow.facet_key == "entity",
            )
        ).all()
    assert entity in indexed_entities
    assert all(len(item) <= 512 for item in indexed_entities)
    assert record.memory_id in {item.memory_id for item in reloaded_store.find_memory_candidate_records(exact_query)}
    reloaded_service = SocMemoryService(record_repository=reloaded_store, profile_registry=registry, now_provider=lambda: now)
    exact = reloaded_service.find_directive_records(exact_query)
    changed = reloaded_service.find_directive_records(changed_query)
    assert [match.memory_id for match in exact.matches] == [record.memory_id]
    assert exact.matches[0].applicability_report.status.value == "applicable"
    assert [match.memory_id for match in changed.matches] == [record.memory_id]
    assert changed.matches[0].applicability_report.status.value == "partial"
    assert changed.matches[0].applicability_report.context_only_allowed
    assert changed.matches[0].applicability_report.missing_reuse_conditions[0].values == [entity]
    enricher = ConfirmedMemoryAnalysisRequestEnricher(reloaded_service, profile_registry=registry, environment="prd")
    for request, directive_applicable in ((exact_request, True), (changed_request, False)):
        contexts = [item for item in enricher(request).context_catalog if item.kind.value == "confirmed_memory"]
        assert len(contexts) == 1
        assert contexts[0].metadata["decision_directive_applicable"] is directive_applicable

    assert reloaded_store.get_run(run.run_id).model_dump(mode="json") == frozen_run
    assert _observe(pattern_service, run, "long-exact:1").observation.model_dump(mode="json") == frozen_observation


@pytest.mark.parametrize("promotion", ["automatic", "manual"])
def test_oversized_entity_is_filtered_while_remaining_scope_and_query_are_preserved(repository, promotion):
    store, _ = repository
    value = "https://example.test/query?sql=" + "a" * 1500
    run = _run(1, service_url=value)
    run.decision = Decision(
        verdict=run.analysis.verdict,
        confidence=run.analysis.confidence,
        suggested_action=run.analysis.recommended_action,
        needs_review=True,
        reason=run.analysis.reason,
    )
    store.save_run(run)
    frozen = store.get_run(run.run_id).model_dump(mode="json")
    registry = build_soc_memory_profile_registry()
    query_before = memory_query_from_analysis_request(run.llm_analysis_request, profile=registry.resolve_request(run.llm_analysis_request))
    assert "url:" + value in query_before.facets["entity"]
    if promotion == "automatic":
        service = _service(store)
        first = _observe(service, run, "oversized:observe")
        assert first.observation is not None
        assert first.candidate is None
        candidate = _observe(service, _run(2, service_url=value), "oversized:observe-2").candidate
        assert len(store.list_memory_pattern_observations()) == 2
    else:
        review = SocReviewService(
            repository=store,
            memory_candidate_repository=store,
            memory_pattern_observation_repository=store,
            memory_profile_registry=registry,
        )
        command = SocMemoryRunPromotionCommand(run_id=run.run_id)
        context = _actor("soc_analyst", "oversized:promote")
        candidate = review.promote_run_to_memory(command, context=context).memory_candidate
        assert review.promote_run_to_memory(command, context=context).memory_candidate.candidate_id == candidate.candidate_id
    assert candidate is not None
    assert candidate.status.value == "pending_review"
    assert candidate.applicability is not None
    assert candidate.facets["behavior_fingerprint"] == query_before.facets["behavior_fingerprint"]
    assert all(len(v) <= 512 for key in ("entity", "role_entity") for v in candidate.facets.get(key, []))
    assert "url:" + value not in candidate.facets.get("entity", [])
    assert "sha256:" not in " ".join(candidate.facets.get("entity", []))
    assert len(store.list_memory_candidates()) == 1
    assert store.get_run(run.run_id).model_dump(mode="json") == frozen
    query_after = memory_query_from_analysis_request(run.llm_analysis_request, profile=registry.resolve_request(run.llm_analysis_request))
    assert query_after.model_dump(mode="json") == query_before.model_dump(mode="json")
