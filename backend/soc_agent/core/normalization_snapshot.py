"""Reuse facts from an identical, versioned review, never a prior decision."""

from collections.abc import Callable

from soc_agent.contracts import AnalysisRun, NormalizationAssistRequest
from soc_agent.utils.hashing import stable_hash

NormalizationHistory = Callable[[str], list[AnalysisRun]]


def find_snapshot(run: AnalysisRun, request: NormalizationAssistRequest, previous: AnalysisRun | None, history: NormalizationHistory | None) -> AnalysisRun | None:
    candidates = [previous] if previous is not None else history(run.alert_id) if history is not None else []
    request_hash = stable_hash(request.model_dump(mode="json"))
    # The repository's latest view may include a recovery-updated old run.
    for candidate in sorted(candidates, key=lambda item: item.started_at, reverse=True):
        report = candidate.normalization_assistance
        if (
            candidate.run_id != run.run_id
            and candidate.input_hash == run.input_hash
            and candidate.alert_id == run.alert_id
            and candidate.normalized_alert is not None
            and candidate.normalized_alert.tenant_id == run.normalized_alert.tenant_id
            and report is not None
            and report.status not in {"failed", "skipped"}
            and report.request_hash == request_hash
        ):
            return candidate
    return None


def reused_report(previous: AnalysisRun):
    report = previous.normalization_assistance.model_copy(deep=True)
    metadata = report.metadata
    # Keep identity/reference provenance, but do not bill historical calls or
    # provider latency as work performed in this run.
    for key in ("usage", "usage_measurement", "provider_duration_ms", "provider_admission_wait_ms", "admission_wait_duration_ms", "client_total_duration_ms", "duration_ms", "fact_snapshot_comparison"):
        metadata.pop(key, None)
    metadata.update({"reused_from_run_id": previous.run_id, "snapshot_origin_run_id": metadata.get("snapshot_origin_run_id", previous.run_id), "usage": {}, "provider_call_count": 0})
    return report


def compare_snapshots(previous: AnalysisRun, alert) -> dict:
    before = stable_hash(previous.normalized_alert.entities.model_dump(mode="json"))
    after = stable_hash(alert.entities.model_dump(mode="json"))
    return {"previous_run_id": previous.run_id, "same_input": True, "changed": before != after, "before_entities_hash": before, "after_entities_hash": after}
