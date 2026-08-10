from __future__ import annotations

from pathlib import Path

import pandas as pd

from soc_agent.contracts import (
    AnalysisNodeOutput,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    TriageActivityStage,
    TriageScenarioAssessment,
    TriageScenarioOrigin,
    Verdict,
)
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.pipeline.reference_catalog import evidence_item_from_catalog
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    build_inventory,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_cross_source_runtime_review import (
    build_cross_source_runtime_review,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_corpus_inventory import (
    _row,
)

TEST_MODEL_NAME = "test-live-model"


class _FakeLiveAnalyzer:
    step_name = "analyze_llm"
    model_name = TEST_MODEL_NAME
    prompt_version = "test-live-prompt-v1"

    def analyze(self, request) -> AnalysisNodeOutput:  # noqa: ANN001
        evidence = evidence_item_from_catalog(
            request,
            description="The analyzed alert identifier.",
            preferred_paths=("alert_id",),
        )
        return AnalysisNodeOutput(
            analysis=AnalysisResult(
                verdict=Verdict.SUSPICIOUS,
                confidence=0.8,
                summary="Synthetic live analyzer result for contract testing.",
                evidence=[evidence],
                reasoning=[
                    AnalysisReasoningItem(
                        reasoning_id="R-01",
                        statement="The selected fact requires analyst review.",
                        basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                        evidence_refs=[evidence.evidence_ref],
                        confidence=0.7,
                    )
                ],
                scenario_assessments=[
                    TriageScenarioAssessment(
                        scenario_name="Synthetic contract scenario",
                        is_primary=True,
                        origin=TriageScenarioOrigin.INFERRED,
                        confidence=0.7,
                        activity_stage=TriageActivityStage.INDETERMINATE,
                        evidence_refs=[evidence.evidence_ref],
                        reasoning_refs=["R-01"],
                        rationale="Exercises the live analyzer Runtime contract.",
                    )
                ],
                evidence_gaps=["Human ground truth is not part of this unit test."],
                manual_checks=["Review the selected corpus row."],
                reason="Synthetic live analyzer contract result.",
                recommended_action="needs_human_review",
            ),
            model_name=self.model_name,
            prompt_version=self.prompt_version,
            parser_version="test-json-parser-v1",
            metadata={
                "analyzer": "json_llm",
                "repair_applied": False,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )


def _live_analysis_service() -> SocAnalysisService:
    return SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(analyzer=_FakeLiveAnalyzer())
    )


def _inventory(corpus: pd.DataFrame, *, corpus_hash: str) -> dict:
    return build_inventory(
        corpus,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256=corpus_hash,
        manifest={
            "schema_version": "soc.validation.alert_corpus_manifest.v1",
            "output": {"sha256": corpus_hash, "rows": len(corpus)},
        },
        expected_rows=len(corpus),
    )


def test_cross_source_runtime_review_selects_topic_medoids_and_known_gaps() -> None:
    corpus = pd.DataFrame(
        [
            _row(11, topic="ptp-nids", raw_logs=[{"message": "k=v"}]),
            _row(
                12,
                topic="ptp-nids",
                raw_logs=[{"message": "k=v"}, {"message": "x=y"}],
            ),
            _row(
                13,
                topic="ptp-nids",
                raw_logs=[
                    {"message": "k=v"},
                    {"message": "x=y"},
                    {"message": "z=w"},
                ],
            ),
            _row(
                21,
                topic="T_GBD_zeus_data",
                raw_logs=[{"subtype": "suspicious_email", "subject": "test"}],
            ),
            _row(
                31,
                topic="leagsoft-edr",
                raw_logs=[{"message": "event_type=process"}],
            ),
            _row(32, topic="leagsoft-edr", raw_logs=[]),
        ]
    )
    corpus_hash = "test-corpus-hash"
    inventory = _inventory(corpus, corpus_hash=corpus_hash)

    report, artifacts = build_cross_source_runtime_review(
        corpus,
        inventory,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256=corpus_hash,
        analysis_service=_live_analysis_service(),
        expected_model_name=TEST_MODEL_NAME,
        expected_source_type_by_topic={
            "T_GBD_zeus_data": "siem",
            "leagsoft-edr": "edr",
            "ptp-nids": "nids",
        },
    )

    assert report["acceptance"]["status"] == "passed"
    assert report["acceptance"]["failed_checks"] == []
    assert report["quality_findings"] == []
    assert report["acceptance"]["representative_count"] == 3
    assert report["acceptance"]["known_input_gap_count"] == 1
    assert report["coverage"]["topics"] == [
        "T_GBD_zeus_data",
        "leagsoft-edr",
        "ptp-nids",
    ]
    assert report["coverage"]["model_counts"] == {TEST_MODEL_NAME: 4}
    assert report["coverage"]["verdict_counts"] == {"suspicious": 4}
    assert report["coverage"]["token_usage"] == {
        "input_tokens": 400,
        "output_tokens": 80,
        "total_tokens": 480,
    }
    representative_ids = {
        item["topic"]: item["alert_id"]
        for item in report["samples"]
        if item["sample_kind"] == "representative"
    }
    assert representative_ids["ptp-nids"] == "12"
    assert {item["actual_source_type"] for item in report["samples"]} == {
        "edr",
        "nids",
        "siem",
    }
    gap = next(
        item for item in report["samples"] if item["sample_kind"] == "known_input_gap"
    )
    assert gap["alert_id"] == "32"
    assert gap["runtime_gap_visibility"]["bounded_evidence_present"] is False
    assert gap["runtime_gap_visibility"]["explicit_runtime_input_gap_reason"] is True
    assert gap["runtime"]["decision"]["needs_review"] is True
    assert gap["runtime"]["decision"]["automation_allowed"] is False
    assert gap["runtime"]["decision"]["evidence_state"] == "degraded"
    assert "high_value_evidence_gap" in gap["runtime"]["decision"]["review_reasons"]
    assert gap["runtime"]["analyzer"] == {
        "step_name": "analyze_llm",
        "model_name": TEST_MODEL_NAME,
        "prompt_version": "test-live-prompt-v1",
        "parser_version": "test-json-parser-v1",
        "repair_applied": False,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
        },
    }
    assert len(artifacts) == 4


def test_cross_source_runtime_review_fails_broken_d0_corpus_lineage() -> None:
    corpus = pd.DataFrame([_row(1, topic="ptp-nids", raw_logs=[{"message": "k=v"}])])
    inventory = _inventory(corpus, corpus_hash="original-hash")

    report, _ = build_cross_source_runtime_review(
        corpus,
        inventory,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="different-hash",
        analysis_service=_live_analysis_service(),
        expected_model_name=TEST_MODEL_NAME,
        expected_source_type_by_topic={"ptp-nids": "nids"},
    )

    assert report["acceptance"]["status"] == "failed"
    assert "d0_links_exact_corpus_file" in report["acceptance"]["failed_checks"]


def test_cross_source_runtime_review_rejects_silent_stub_fallback() -> None:
    corpus = pd.DataFrame([_row(1, topic="ptp-nids", raw_logs=[{"message": "k=v"}])])
    corpus_hash = "test-corpus-hash"
    inventory = _inventory(corpus, corpus_hash=corpus_hash)

    report, _ = build_cross_source_runtime_review(
        corpus,
        inventory,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256=corpus_hash,
        analysis_service=SocAnalysisService(),
        expected_model_name=TEST_MODEL_NAME,
        expected_source_type_by_topic={"ptp-nids": "nids"},
    )

    assert report["acceptance"]["status"] == "failed"
    assert (
        "every_sample_used_requested_live_model"
        in report["acceptance"]["failed_checks"]
    )
    sample = report["samples"][0]
    assert sample["checks"]["requested_live_model_is_explicit"] is False
