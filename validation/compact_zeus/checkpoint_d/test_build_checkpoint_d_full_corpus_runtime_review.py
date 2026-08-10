from __future__ import annotations

from pathlib import Path

import pandas as pd

from soc_agent.contracts import (
    AnalysisNodeOutput,
    AnalysisReasoningBasis,
    AnalysisReasoningItem,
    AnalysisResult,
    SensitiveEvidenceMode,
    Verdict,
)
from soc_agent.core import DeterministicAnalysisRuntime, SocAnalysisService
from soc_agent.pipeline.reference_catalog import evidence_item_from_catalog
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_corpus_inventory import (
    build_inventory,
)
from validation.compact_zeus.checkpoint_d.build_checkpoint_d_full_corpus_runtime_review import (
    _write_diagnostics,
    build_full_corpus_runtime_review,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_corpus_inventory import (
    _row,
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


def _service(*, analyzer=None) -> SocAnalysisService:  # noqa: ANN001
    return SocAnalysisService(
        runtime=DeterministicAnalysisRuntime(
            analyzer=analyzer,
            sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        )
    )


def test_full_corpus_runtime_review_reexecutes_every_d0_row_stably() -> None:
    corpus = pd.DataFrame(
        [
            _row(11, topic="ptp-nids", raw_logs=[{"message": "k=secret-value"}]),
            _row(
                21,
                topic="T_GBD_zeus_data",
                raw_logs=[{"subtype": "suspicious_email", "subject": "test"}],
            ),
            _row(31, topic="leagsoft-edr", raw_logs=[]),
        ]
    )
    corpus_hash = "test-corpus-hash"
    inventory = _inventory(corpus, corpus_hash=corpus_hash)

    report, diagnostics = build_full_corpus_runtime_review(
        corpus,
        inventory,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256=corpus_hash,
        analysis_service=_service(),
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        expected_rows=3,
        expected_source_type_by_topic={
            "T_GBD_zeus_data": "siem",
            "leagsoft-edr": "edr",
            "ptp-nids": "nids",
        },
    )

    assert report["acceptance"]["status"] == "passed"
    assert report["acceptance"]["processed_row_count"] == 3
    assert report["acceptance"]["stable_row_count"] == 3
    assert report["acceptance"]["known_input_gap_count"] == 1
    assert report["acceptance"]["failed_checks"] == []
    assert report["coverage"]["analyzer_step_counts"] == {"analyze_stub": 3}
    assert report["coverage"]["source_type_counts"] == {
        "edr": 1,
        "nids": 1,
        "siem": 1,
    }
    assert report["coverage"]["review_reason_counts"]["stub_analyzer"] == 3
    assert "evidence_quality_row_counts" in report["coverage"]
    assert "message_schema_status_counts" in report["coverage"]
    assert "omission_reason_totals" in report["coverage"]
    assert diagnostics == {}
    assert "secret-value" not in str(report)
    assert all(row["reexecution"]["stable"] for row in report["rows"])
    gap = next(row for row in report["rows"] if row["input"]["known_input_gap"])
    assert gap["checks"]["first_run"]["known_gap_has_no_bounded_evidence"]
    assert gap["checks"]["first_run"]["known_gap_is_explicit"]


def test_full_corpus_runtime_review_fails_broken_d0_lineage() -> None:
    corpus = pd.DataFrame([_row(1, topic="ptp-nids", raw_logs=[{"message": "k=v"}])])
    inventory = _inventory(corpus, corpus_hash="original-hash")

    report, _ = build_full_corpus_runtime_review(
        corpus,
        inventory,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="different-hash",
        analysis_service=_service(),
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        expected_rows=1,
        expected_source_type_by_topic={"ptp-nids": "nids"},
    )

    assert report["acceptance"]["status"] == "failed"
    assert "d0_links_exact_corpus_file" in report["acceptance"]["failed_checks"]


class _ChangingStubAnalyzer:
    step_name = "analyze_stub"
    model_name = "stub"
    prompt_version = "stub"

    def __init__(self) -> None:
        self._call_count = 0

    def analyze(self, request) -> AnalysisNodeOutput:  # noqa: ANN001
        self._call_count += 1
        evidence = evidence_item_from_catalog(
            request,
            description="Analyzed alert identifier.",
            preferred_paths=("alert_id",),
        )
        return AnalysisNodeOutput(
            analysis=AnalysisResult(
                verdict=Verdict.UNKNOWN,
                confidence=0.4 + (self._call_count * 0.01),
                summary="Synthetic changing output.",
                evidence=[evidence],
                reasoning=[
                    AnalysisReasoningItem(
                        reasoning_id="R-01",
                        statement="The selected fact requires analyst review.",
                        basis=[AnalysisReasoningBasis.CURRENT_EVIDENCE],
                        evidence_refs=[evidence.evidence_ref],
                        confidence=0.4 + (self._call_count * 0.01),
                    )
                ],
                evidence_gaps=["Synthetic stability test gap."],
                manual_checks=["Review the deterministic replay hashes."],
                reason="Synthetic stability test.",
                recommended_action="needs_human_review",
            ),
            model_name=self.model_name,
            prompt_version=self.prompt_version,
            metadata={"analyzer": "stub"},
        )


def test_full_corpus_runtime_review_detects_semantic_instability() -> None:
    corpus = pd.DataFrame([_row(1, topic="ptp-nids", raw_logs=[{"message": "k=v"}])])
    corpus_hash = "test-corpus-hash"
    inventory = _inventory(corpus, corpus_hash=corpus_hash)

    report, diagnostics = build_full_corpus_runtime_review(
        corpus,
        inventory,
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256=corpus_hash,
        analysis_service=_service(analyzer=_ChangingStubAnalyzer()),
        sensitive_evidence_mode=SensitiveEvidenceMode.FULL,
        expected_rows=1,
        expected_source_type_by_topic={"ptp-nids": "nids"},
    )

    assert report["acceptance"]["status"] == "failed"
    assert "all_rows_are_semantically_stable" in report["acceptance"]["failed_checks"]
    row = report["rows"][0]
    assert row["reexecution"]["stable"] is False
    assert "semantic_reexecution_stable" in row["failed_checks"]
    assert row["reexecution"]["differing_semantic_step_outputs"]
    assert len(diagnostics) == 1


def test_write_diagnostics_prunes_owned_stale_files(tmp_path: Path) -> None:
    diagnostics_dir = tmp_path / "diagnostics"
    diagnostics_dir.mkdir()
    stale = diagnostics_dir / "old.runtime-diagnostic.json"
    stale.write_text("{}\n", encoding="utf-8")

    _write_diagnostics(
        {"diagnostics/current.runtime-diagnostic.json": {"schema_version": "test.v1"}},
        tmp_path,
    )

    assert stale.exists() is False
    assert (diagnostics_dir / "current.runtime-diagnostic.json").exists()
