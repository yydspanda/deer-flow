from __future__ import annotations

from pathlib import Path

from validation.compact_zeus.checkpoint_d.build_checkpoint_d_skill_route_coverage import (
    build_skill_route_coverage,
)
from validation.compact_zeus.checkpoint_d.test_build_checkpoint_d_bounded_analysis_input_review import (
    _corpus,
)


def test_skill_route_coverage_checks_typed_routes_and_all_profile_packages() -> None:
    report = build_skill_route_coverage(
        _corpus(),
        corpus_path=Path("corpus.pkl"),
        corpus_file_sha256="corpus-hash",
    )

    assert report["acceptance"]["status"] == "passed"
    assert report["acceptance"]["failed_checks"] == []
    assert all(report["acceptance"]["checks"].values())
    assert report["acceptance"]["processed_count"] == 1
    assert report["coverage"]["skill_selection_counts"]["soc-web-application-triage"] == 1
    assert report["profile_skill_package_inventory"]["all_projected"] is True
    assert report["findings"]["asset_only_endpoint_misroutes"] == []
    assert report["scope"]["classification"] == "offline_evaluation_not_runtime_node"
