from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from validation.compact_zeus.memory.seed_confirmed_memory_from_batch import (
    _harden_sqlite_database_files,
    build_candidate_command,
    seed_confirmed_memory,
)

from soc_agent.contracts import AnalysisRun, MemoryAdmissionStatus
from soc_agent.core import SocAnalysisService, SocMemoryService
from soc_agent.memory import (
    InMemoryMemoryCandidateRepository,
    MemoryAdmissionService,
    memory_query_from_analysis_request,
)

_SAMPLE = (
    Path(__file__).resolve().parents[3]
    / "backend"
    / "samples"
    / "alerts"
    / "pingan_legacy_apt.json"
)


def _run() -> AnalysisRun:
    payload = json.loads(_SAMPLE.read_text(encoding="utf-8"))
    return SocAnalysisService().analyze(payload)


def test_build_candidate_uses_shared_reusable_facets_and_explicit_simulation() -> None:
    command = build_candidate_command(
        _run(),
        source_batch_sha256="a" * 64,
        now=datetime(2026, 8, 15, tzinfo=UTC),
    )

    admission = MemoryAdmissionService().evaluate(command)

    assert admission.status is MemoryAdmissionStatus.ADMITTED
    assert command.source.metadata["promote_to_memory"] is True
    assert command.metadata["simulation"] is True
    assert command.facets["detection_key"]
    assert command.facets["rule_code"]
    assert command.facets["scenario_key"]


def test_seed_confirms_activates_and_retrieves_matching_record() -> None:
    run = _run()
    repository = InMemoryMemoryCandidateRepository()
    report = seed_confirmed_memory(
        [run],
        repository=repository,
        source_batch_sha256="b" * 64,
        now=datetime.now(UTC),
    )

    assert report["candidate_count"] == 1
    assert report["confirmed_record_count"] == 1
    assert report["retrieval_enabled_count"] == 1
    assert report["decision_directive_count"] == 0
    assert report["items"][0]["memory_type"] == "detection_lesson"

    assert run.llm_analysis_request is not None
    result = SocMemoryService(record_repository=repository).find_relevant_records(
        memory_query_from_analysis_request(run.llm_analysis_request)
    )
    assert result.returned_count == 1
    assert result.matches[0].memory_id == report["items"][0]["memory_id"]
    assert result.matches[0].matched_anchor_facets


def test_harden_sqlite_database_files_marks_database_and_sidecars_private(
    tmp_path: Path,
) -> None:
    database = tmp_path / "memory.sqlite"
    sidecar = tmp_path / "memory.sqlite-wal"
    database.write_text("database", encoding="utf-8")
    sidecar.write_text("wal", encoding="utf-8")
    database.chmod(0o644)
    sidecar.chmod(0o644)

    _harden_sqlite_database_files(f"sqlite:///{database}")

    assert database.stat().st_mode & 0o777 == 0o600
    assert sidecar.stat().st_mode & 0o777 == 0o600
