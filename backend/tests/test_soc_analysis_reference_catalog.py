from __future__ import annotations

import json
from pathlib import Path

from soc_agent.contracts import (
    AlertEntitySet,
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    BoundedAnalysisEvidence,
    HostEntityRef,
    LLMAnalysisRequest,
)
from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.pipeline.analysis_context import project_analysis_context
from soc_agent.pipeline.reference_catalog import (
    evidence_ref_for,
    finalize_analysis_reference_catalogs,
)

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


def _request(sample: str):
    payload = json.loads((SAMPLES / sample).read_text(encoding="utf-8"))
    return build_analysis_request_for_payload(payload)


def test_reference_catalog_is_stable_atomic_and_excludes_runtime_metadata() -> None:
    first = _request("malicious_ioc.json")
    second = _request("malicious_ioc.json")

    assert first.evidence_catalog == second.evidence_catalog
    assert len({item.evidence_ref for item in first.evidence_catalog}) == len(first.evidence_catalog)
    rule = next(item for item in first.evidence_catalog if item.source_path == "detection.rule_code")
    assert rule.value == "EDR-IOC-001"
    assert rule.evidence_ref == evidence_ref_for("detection.rule_code", "EDR-IOC-001")
    assert not any(
        "field_trusts" in item.source_path or "source_field_semantics" in item.source_path or item.source_path.endswith(".confidence") or item.source_path.startswith("fact_reconstruction.warnings") for item in first.evidence_catalog
    )


def test_reference_catalog_separates_skill_and_adapter_context_from_event_facts() -> None:
    request = _request("pingan_legacy_hids.json")

    assert any(item.context_ref.startswith("S-") for item in request.context_catalog)
    assert any(item.context_ref.startswith("A-") for item in request.context_catalog)
    assert not any(item.source_path.startswith("skill_context") or item.source_path.startswith("evidence.source_field_semantics") for item in request.evidence_catalog)
    assert any("#parsed" in item.source_path for item in request.evidence_catalog)


def test_reference_catalog_caps_context_without_dropping_skills() -> None:
    request = _request("pingan_legacy_hids.json")
    governed = [
        AnalysisContextCatalogItem(
            context_ref=f"C-{index:012X}",
            kind=AnalysisContextReferenceKind.GOVERNED_CONTEXT,
            label=f"context-{index}",
            source_id=f"context-{index}",
            summary="reviewed governed context",
        )
        for index in range(120)
    ]

    finalized = finalize_analysis_reference_catalogs(request.model_copy(update={"context_catalog": governed}))

    assert len(finalized.context_catalog) == 100
    assert len(finalized.evidence_catalog) <= 150
    assert any(item.kind is AnalysisContextReferenceKind.SKILL for item in finalized.context_catalog)
    assert any(item.kind is AnalysisContextReferenceKind.ADAPTER_CONTRACT for item in finalized.context_catalog)


def test_reference_catalog_exposes_only_runtime_typed_role_entities() -> None:
    request = LLMAnalysisRequest(
        alert_id="1965891",
        canonical_entities=AlertEntitySet(
            host=HostEntityRef(host_name="PBNJ-D0174"),
        ),
        primary_evidence=BoundedAnalysisEvidence(
            source_path="alert.hitLog[0].zeusRawLogs[0]",
            layer="raw_structured",
            trust_level="high",
            content=json.dumps(
                {
                    "computername": "PBNJ-D0174",
                    "src_port": 30000,
                }
            ),
        ),
    )

    finalized = finalize_analysis_reference_catalogs(request)
    canonical_host = next(item for item in finalized.evidence_catalog if item.source_path == "canonical_entities.host.host_name")
    raw_host = next(item for item in finalized.evidence_catalog if item.source_path.endswith("#parsed.computername"))
    raw_port = next(item for item in finalized.evidence_catalog if item.source_path.endswith("#parsed.src_port"))
    projected = project_analysis_context(finalized)
    role_entities = projected["reference_catalogs"]["role_entities"]

    assert canonical_host.entity_type == "host"
    assert raw_host.entity_type is None
    assert raw_port.entity_type is None
    assert [item["evidence_ref"] for item in role_entities] == [canonical_host.evidence_ref]
