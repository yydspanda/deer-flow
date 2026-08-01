from __future__ import annotations

from pathlib import Path

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_bounded_analysis_input_review import (
    SensitiveEvidenceMode,
    build_bounded_analysis_input_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_entity_extraction_review import (
    build_entity_extraction_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_fact_reconstruction_review import (
    build_fact_reconstruction_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_normalization_review import (
    build_normalization_review,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_skill_context_review import (
    build_skill_context_review,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_bounded_analysis_input_review import (
    _corpus,
)


def test_skill_context_review_projects_real_skill_packages_without_running_llm() -> None:
    corpus = _corpus()
    d1_review = build_normalization_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
    )
    d2_review = build_entity_extraction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
    )
    d3_review = build_fact_reconstruction_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
    )
    d4_review = build_bounded_analysis_input_review(
        corpus,
        alert_id=1,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
        normalization_review=d1_review,
        entity_review=d2_review,
        fact_review=d3_review,
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
    )

    review = build_skill_context_review(d4_review, alert_id=1)

    assert review["acceptance"]["status"] == "passed"
    assert review["acceptance"]["failed_checks"] == []
    assert all(review["acceptance"]["checks"].values())
    selected = {item["skill_name"]: item for item in review["skill_context"]["selected_skills"]}
    assert "soc-alert-triage" in selected
    assert "soc-network-apt-triage" in selected
    assert "soc-web-application-triage" in selected
    assert all(item["guidance_source"] == "references/runtime-guidance.md" and item["estimated_token_count"] <= item["token_budget"] and len(item["package_hash"]) == 64 and len(item["guidance_hash"]) == 64 for item in selected.values())
    assert review["llm_analysis_request"]["skill_context"] == review["skill_context"]
    assert review["scope"]["not_performed"] == [
        "prompt_rendering",
        "analyzer_or_llm",
        "evidence_grounding",
        "decision_policy",
        "persistence",
    ]
