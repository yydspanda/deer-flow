from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from soc_agent.contracts import (
    ActorContext,
    AlertClassification,
    AlertSourceRef,
    AlertSourceType,
    AnalysisContextReferenceKind,
    DetectionRuleRef,
    LLMAnalysisRequest,
    SocMemoryCandidateSource,
    SocMemoryCandidateSourceType,
    SocMemoryCandidateType,
    SocMemoryCandidateValidity,
    SocMemoryMatch,
    SocMemoryRecord,
    SocMemoryRetrievalResult,
    SocMemoryTargetArtifact,
)
from soc_agent.core.runtime import analyze_alert
from soc_agent.memory import (
    ConfirmedMemoryAnalysisRequestEnricher,
    memory_query_from_analysis_request,
)
from soc_agent.pipeline.reference_catalog import finalize_analysis_reference_catalogs

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class StaticMemoryRetriever:
    def __init__(self, record: SocMemoryRecord) -> None:
        self.record = record
        self.queries = []

    def find_relevant_records(self, query):  # noqa: ANN001, ANN201 - protocol test double
        self.queries.append(query)
        match = SocMemoryMatch(
            memory_id=self.record.memory_id,
            version=self.record.version,
            record=self.record,
            score=8.5,
            match_reasons=["facet:detection_key=sample:rule"],
            matched_facets={"detection_key": ["sample:rule"]},
            token_estimate=40,
            content_hash=self.record.content_hash,
            facets_hash=self.record.facets_hash,
        )
        return SocMemoryRetrievalResult(
            query=query,
            matches=[match],
            total_candidate_count=1,
            returned_count=1,
            total_token_estimate=40,
            max_tokens=query.max_tokens,
        )


class FailingMemoryRetriever:
    def find_relevant_records(self, query):  # noqa: ANN001, ANN201 - protocol test double
        raise RuntimeError("database details must not enter model-visible warnings")


def _request() -> LLMAnalysisRequest:
    return LLMAnalysisRequest(
        alert_id="ALT-MEMORY-RUNTIME-1",
        tenant_id="pingan",
        source=AlertSourceRef(
            source_type=AlertSourceType.SIEM,
            source_system="sample-siem",
            product="sample-product",
        ),
        detection=DetectionRuleRef(
            rule_code="RULE-001",
            rule_name="Suspicious email",
            rule_category="phishing",
            detection_key="sample:rule",
        ),
        classification=AlertClassification(
            severity="high",
            category="suspicious_email",
        ),
    )


def _record() -> SocMemoryRecord:
    now = datetime.now(UTC)
    return SocMemoryRecord(
        memory_id="MEM-RUNTIME-001",
        version=2,
        memory_type=SocMemoryCandidateType.DETECTION_LESSON,
        target_artifact=SocMemoryTargetArtifact.TENANT_MEMORY,
        tenant_scope="pingan",
        tenant_id="pingan",
        source_candidate_id="MC-RUNTIME-001",
        source=SocMemoryCandidateSource(
            source_type=SocMemoryCandidateSourceType.CORRECTION,
            source_id="COR-RUNTIME-001",
            run_id="RUN-RUNTIME-001",
            alert_id="ALT-SOURCE-001",
        ),
        summary="Confirmed phishing review boundary",
        content="The pattern supports a phishing attempt, but does not prove a click or endpoint impact.",
        facets={"detection_key": ["sample:rule"], "category": ["suspicious_email"]},
        evidence_refs=["correction:COR-RUNTIME-001"],
        validity=SocMemoryCandidateValidity(
            valid_from=now - timedelta(days=1),
            notes="Reviewed tenant lesson.",
        ),
        confidence=0.9,
        content_hash="sha256:" + "a" * 64,
        facets_hash="sha256:" + "b" * 64,
        retrieval_enabled=True,
        retrieval_policy_version="soc.memory_retrieval_activation_policy.v1",
        retrieval_valid_until=now + timedelta(days=30),
        retrieval_review_due_at=now + timedelta(days=7),
        retrieval_updated_by=ActorContext(actor_id="memory-governor"),
        retrieval_updated_at=now,
        retrieval_reason="Approved for bounded Runtime retrieval.",
        created_by=ActorContext(actor_id="memory-reviewer"),
    )


def test_memory_query_uses_only_generic_canonical_dimensions() -> None:
    query = memory_query_from_analysis_request(_request())

    assert query.tenant_scope == "pingan"
    assert query.tenant_id == "pingan"
    assert query.facets["source_type"] == ["siem"]
    assert query.facets["source_system"] == ["sample-siem"]
    assert query.facets["detection_key"] == ["sample:rule"]
    assert query.facets["rule_code"] == ["RULE-001"]
    assert query.facets["category"] == ["suspicious_email"]
    assert "alert_id" not in query.facets
    assert "run_id" not in query.facets
    assert query.policy_version == "soc.memory_retrieval_policy.v2"
    assert query.metadata == {
        "source": "fixed_runtime_pre_llm",
        "alert_id": "ALT-MEMORY-RUNTIME-1",
        "strong_anchor_keys_present": [
            "detection_key",
            "rule_code",
            "source_system",
        ],
    }


def test_confirmed_memory_is_projected_as_stable_m_reference() -> None:
    retriever = StaticMemoryRetriever(_record())
    enriched = ConfirmedMemoryAnalysisRequestEnricher(retriever)(_request())
    finalized = finalize_analysis_reference_catalogs(enriched)

    memory_items = [item for item in finalized.context_catalog if item.kind is AnalysisContextReferenceKind.CONFIRMED_MEMORY]
    assert len(memory_items) == 1
    item = memory_items[0]
    assert item.context_ref.startswith("M-")
    assert item.source_id == "MEM-RUNTIME-001@v2"
    assert item.label == "Confirmed phishing review boundary"
    assert "does not prove a click" in item.summary
    assert retriever.queries[0].require_retrieval_enabled is True


def test_memory_retrieval_failure_is_sanitized_and_non_blocking() -> None:
    enriched = ConfirmedMemoryAnalysisRequestEnricher(FailingMemoryRetriever())(_request())

    assert enriched.context_catalog == []
    assert enriched.warnings == ["confirmed memory retrieval unavailable (RuntimeError)"]
    assert "database details" not in enriched.warnings[0]


def test_runtime_enriches_request_before_analyzer_and_journal_boundary() -> None:
    payload = json.loads((SAMPLES / "malicious_ioc.json").read_text(encoding="utf-8"))
    retriever = StaticMemoryRetriever(_record())

    run = analyze_alert(
        payload,
        analysis_request_enricher=ConfirmedMemoryAnalysisRequestEnricher(retriever),
    )

    assert run.llm_analysis_request is not None
    memory_items = [item for item in run.llm_analysis_request.context_catalog if item.kind is AnalysisContextReferenceKind.CONFIRMED_MEMORY]
    assert len(memory_items) == 1
    reference_step = next(step for step in run.steps if step.step_name == "reference_catalog")
    assert reference_step.metadata["context_kind_counts"]["confirmed_memory"] == 1
