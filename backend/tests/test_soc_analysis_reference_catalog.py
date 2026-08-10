from __future__ import annotations

import json
from pathlib import Path

from soc_agent.core.runtime import build_analysis_request_for_payload
from soc_agent.pipeline.reference_catalog import evidence_ref_for

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
