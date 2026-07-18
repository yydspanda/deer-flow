"""Governed schema-baseline and normalization-maintenance workflow."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from soc_agent.contracts import (
    ActorContext,
    AnalysisRun,
    EvidenceFieldImportance,
    MessageSchemaObservation,
    MessageSchemaStatus,
    NormalizationBaselineAcceptCommand,
    NormalizationBaselineStatus,
    NormalizationMaintenanceIssue,
    NormalizationMaintenanceIssueStatus,
    NormalizationMaintenanceIssueType,
    NormalizationMaintenanceIssueUpdateCommand,
    NormalizationMaintenanceSeverity,
    NormalizationMonitoringResult,
    NormalizationSchemaBaseline,
    ServiceRequestContext,
    SocEvent,
    SocEventType,
)
from soc_agent.protocols import (
    NormalizationMaintenanceIssueRepository,
    NormalizationSchemaBaselineRepository,
    SocEventSink,
)

from .access_control import require_actor_roles
from .service import NoopEventSink, SocServiceNotFoundError, SocServiceNotImplementedError

_BASELINE_ROLES = frozenset({"soc_admin", "soc_engineer"})


class SocNormalizationMaintenanceService:
    """Create governed baselines and deduplicated parser/mapping issues."""

    def __init__(
        self,
        *,
        baseline_repository: NormalizationSchemaBaselineRepository | None = None,
        issue_repository: NormalizationMaintenanceIssueRepository | None = None,
        event_sink: SocEventSink | None = None,
    ) -> None:
        self._baseline_repository = baseline_repository
        self._issue_repository = issue_repository
        self._event_sink = event_sink or NoopEventSink()

    def accept_baseline(
        self,
        command: NormalizationBaselineAcceptCommand,
        *,
        context: ServiceRequestContext,
    ) -> NormalizationSchemaBaseline:
        repository = self._require_baseline_repository()
        require_actor_roles(
            context,
            _BASELINE_ROLES,
            operation="accepting a normalization baseline",
        )
        now = datetime.now(UTC)
        active = self._active_baselines(
            tenant_id=command.tenant_id,
            source_system=command.source_system,
            adapter=command.adapter,
            parser_name=command.parser_name,
            parser_version=command.parser_version,
        )
        next_version = max((item.version for item in active), default=0) + 1
        for previous in active:
            previous.status = NormalizationBaselineStatus.SUPERSEDED
            previous.superseded_at = now
            previous.updated_at = now
            repository.save_normalization_baseline(previous)

        baseline = NormalizationSchemaBaseline(
            version=next_version,
            tenant_id=command.tenant_id,
            source_system=command.source_system,
            adapter=command.adapter,
            parser_name=command.parser_name,
            parser_version=command.parser_version,
            accepted_fingerprints=sorted(set(command.accepted_fingerprints)),
            reason=command.reason,
            approved_by=context.actor,
            created_at=now,
            updated_at=now,
        )
        repository.save_normalization_baseline(baseline)
        resolved_issue_ids = self._resolve_issues_covered_by_baseline(
            baseline,
            actor=context.actor,
            resolved_at=now,
        )
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.NORMALIZATION_BASELINE_ACCEPTED,
                request_id=context.request_id,
                actor=context.actor,
                payload={
                    "baseline_id": baseline.baseline_id,
                    "version": baseline.version,
                    "adapter": baseline.adapter,
                    "parser_name": baseline.parser_name,
                    "parser_version": baseline.parser_version,
                    "fingerprint_count": len(baseline.accepted_fingerprints),
                    "resolved_issue_ids": resolved_issue_ids,
                },
            )
        )
        return baseline

    def list_baselines(
        self,
        *,
        status: NormalizationBaselineStatus | None = None,
        tenant_id: str | None = None,
        source_system: str | None = None,
        limit: int = 50,
    ) -> list[NormalizationSchemaBaseline]:
        return self._require_baseline_repository().list_normalization_baselines(
            status=status,
            tenant_id=tenant_id,
            source_system=source_system,
            limit=limit,
        )

    def list_issues(
        self,
        *,
        status: NormalizationMaintenanceIssueStatus | None = None,
        tenant_id: str | None = None,
        source_system: str | None = None,
        limit: int = 50,
    ) -> list[NormalizationMaintenanceIssue]:
        return self._require_issue_repository().list_normalization_issues(
            status=status,
            tenant_id=tenant_id,
            source_system=source_system,
            limit=limit,
        )

    def update_issue(
        self,
        command: NormalizationMaintenanceIssueUpdateCommand,
        *,
        context: ServiceRequestContext,
    ) -> NormalizationMaintenanceIssue:
        require_actor_roles(
            context,
            _BASELINE_ROLES,
            operation="updating a normalization maintenance issue",
        )
        repository = self._require_issue_repository()
        issue = repository.get_normalization_issue(command.issue_id)
        if issue is None:
            raise SocServiceNotFoundError(f"normalization issue {command.issue_id} not found")
        now = datetime.now(UTC)
        issue.status = NormalizationMaintenanceIssueStatus(command.status)
        issue.resolution_reason = command.reason
        if issue.status is NormalizationMaintenanceIssueStatus.ACKNOWLEDGED:
            issue.acknowledged_by = context.actor
            issue.acknowledged_at = now
        else:
            issue.resolved_by = context.actor
            issue.resolved_at = now
        repository.save_normalization_issue(issue)
        self._event_sink.emit(
            SocEvent(
                event_type=SocEventType.NORMALIZATION_ISSUE_UPDATED,
                request_id=context.request_id,
                run_id=issue.run_id,
                alert_id=issue.alert_id,
                actor=context.actor,
                payload={
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type.value,
                    "status": issue.status.value,
                },
            )
        )
        return issue

    def monitor_run(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> NormalizationMonitoringResult:
        if run.normalization_report is None:
            return NormalizationMonitoringResult(run_id=run.run_id, alert_id=run.alert_id)
        repository = self._require_issue_repository()
        report = run.normalization_report
        tenant_id = run.llm_analysis_request.tenant_id if run.llm_analysis_request is not None else None
        source_system = report.source_system
        candidates: list[NormalizationMaintenanceIssue] = []
        baseline_ids: set[str] = set()

        for observation in report.message_schemas:
            baseline = self._matching_baseline(
                tenant_id=tenant_id,
                source_system=source_system,
                adapter=report.adapter,
                parser_name=observation.parser_name,
                parser_version=observation.parser_version,
            )
            if baseline is not None:
                baseline_ids.add(baseline.baseline_id)
            elif observation.parser_name and observation.parser_version:
                candidates.append(
                    _issue_for_observation(
                        run,
                        adapter=report.adapter,
                        tenant_id=tenant_id,
                        source_system=source_system,
                        observation=observation,
                        issue_type=NormalizationMaintenanceIssueType.BASELINE_MISSING,
                    )
                )
            if baseline is not None and observation.schema_fingerprint and observation.schema_fingerprint not in baseline.accepted_fingerprints:
                candidates.append(
                    _issue_for_observation(
                        run,
                        adapter=report.adapter,
                        tenant_id=tenant_id,
                        source_system=source_system,
                        observation=observation,
                        issue_type=NormalizationMaintenanceIssueType.NOVEL_SCHEMA,
                    )
                )
            if observation.status is MessageSchemaStatus.DEGRADED:
                candidates.append(
                    _issue_for_observation(
                        run,
                        adapter=report.adapter,
                        tenant_id=tenant_id,
                        source_system=source_system,
                        observation=observation,
                        issue_type=NormalizationMaintenanceIssueType.DEGRADED_SCHEMA,
                    )
                )
            elif observation.status is MessageSchemaStatus.UNSUPPORTED:
                candidates.append(
                    _issue_for_observation(
                        run,
                        adapter=report.adapter,
                        tenant_id=tenant_id,
                        source_system=source_system,
                        observation=observation,
                        issue_type=NormalizationMaintenanceIssueType.UNSUPPORTED_SCHEMA,
                    )
                )

        coverage = run.llm_analysis_request.evidence_coverage if run.llm_analysis_request is not None else None
        if coverage is not None:
            for gap in coverage.high_value_gaps:
                candidates.append(
                    _issue_for_gap(
                        run,
                        adapter=report.adapter,
                        tenant_id=tenant_id,
                        source_system=source_system,
                        field_path=gap.field_path,
                        expected_target=gap.expected_target,
                        details={"reason": gap.reason, "rule_id": gap.rule_id, "importance": gap.importance},
                        importance=gap.importance,
                    )
                )
            for source_path in coverage.llm_truncated_evidence_paths:
                candidates.append(
                    _issue_for_truncation(
                        run,
                        adapter=report.adapter,
                        tenant_id=tenant_id,
                        source_system=source_system,
                        source_path=source_path,
                    )
                )

        created: list[str] = []
        updated: list[str] = []
        issues: list[NormalizationMaintenanceIssue] = []
        for candidate in candidates:
            existing = repository.find_normalization_issue_by_dedupe_key(candidate.dedupe_key)
            if existing is None:
                repository.save_normalization_issue(candidate)
                issue = candidate
                created.append(issue.issue_id)
                should_emit = True
            else:
                issue = _record_recurrence(existing, run=run, details=candidate.details)
                repository.save_normalization_issue(issue)
                updated.append(issue.issue_id)
                should_emit = issue.status is NormalizationMaintenanceIssueStatus.OPEN
            issues.append(issue)
            if should_emit:
                self._event_sink.emit(_drift_event(issue, context=context))

        return NormalizationMonitoringResult(
            run_id=run.run_id,
            alert_id=run.alert_id,
            baseline_ids=sorted(baseline_ids),
            created_issue_ids=created,
            updated_issue_ids=updated,
            issues=issues,
        )

    def _matching_baseline(
        self,
        *,
        tenant_id: str | None,
        source_system: str | None,
        adapter: str,
        parser_name: str | None,
        parser_version: str | None,
    ) -> NormalizationSchemaBaseline | None:
        if self._baseline_repository is None or parser_name is None or parser_version is None:
            return None
        candidates = self._baseline_repository.list_normalization_baselines(
            status=NormalizationBaselineStatus.ACTIVE,
            adapter=adapter,
            parser_name=parser_name,
            parser_version=parser_version,
            limit=100,
        )
        exact = [item for item in candidates if item.tenant_id == tenant_id and item.source_system == source_system]
        return max(exact, key=lambda item: item.version) if exact else None

    def _active_baselines(
        self,
        *,
        tenant_id: str | None,
        source_system: str | None,
        adapter: str,
        parser_name: str,
        parser_version: str,
    ) -> list[NormalizationSchemaBaseline]:
        repository = self._require_baseline_repository()
        return [
            item
            for item in repository.list_normalization_baselines(
                status=NormalizationBaselineStatus.ACTIVE,
                adapter=adapter,
                parser_name=parser_name,
                parser_version=parser_version,
                limit=100,
            )
            if item.tenant_id == tenant_id and item.source_system == source_system
        ]

    def _resolve_issues_covered_by_baseline(
        self,
        baseline: NormalizationSchemaBaseline,
        *,
        actor: ActorContext,
        resolved_at: datetime,
    ) -> list[str]:
        if self._issue_repository is None:
            return []
        candidates = self._issue_repository.list_normalization_issues(
            status=NormalizationMaintenanceIssueStatus.OPEN,
            tenant_id=baseline.tenant_id,
            source_system=baseline.source_system,
            limit=500,
        )
        resolved: list[str] = []
        for issue in candidates:
            if issue.tenant_id != baseline.tenant_id or issue.source_system != baseline.source_system:
                continue
            if issue.adapter != baseline.adapter:
                continue
            if issue.parser_name != baseline.parser_name or issue.parser_version != baseline.parser_version:
                continue
            covered = issue.issue_type is NormalizationMaintenanceIssueType.BASELINE_MISSING or (issue.issue_type is NormalizationMaintenanceIssueType.NOVEL_SCHEMA and issue.schema_fingerprint in baseline.accepted_fingerprints)
            if not covered:
                continue
            issue.status = NormalizationMaintenanceIssueStatus.RESOLVED
            issue.resolved_by = actor
            issue.resolved_at = resolved_at
            issue.resolution_reason = f"covered by accepted normalization baseline {baseline.baseline_id}"
            self._issue_repository.save_normalization_issue(issue)
            resolved.append(issue.issue_id)
        return resolved

    def _require_baseline_repository(self) -> NormalizationSchemaBaselineRepository:
        if self._baseline_repository is None:
            raise SocServiceNotImplementedError("normalization baseline operation requires a repository")
        return self._baseline_repository

    def _require_issue_repository(self) -> NormalizationMaintenanceIssueRepository:
        if self._issue_repository is None:
            raise SocServiceNotImplementedError("normalization issue operation requires a repository")
        return self._issue_repository


def _issue_for_observation(
    run: AnalysisRun,
    *,
    adapter: str,
    tenant_id: str | None,
    source_system: str | None,
    observation: MessageSchemaObservation,
    issue_type: NormalizationMaintenanceIssueType,
) -> NormalizationMaintenanceIssue:
    severity = {
        NormalizationMaintenanceIssueType.BASELINE_MISSING: NormalizationMaintenanceSeverity.INFO,
        NormalizationMaintenanceIssueType.NOVEL_SCHEMA: NormalizationMaintenanceSeverity.WARNING,
        NormalizationMaintenanceIssueType.DEGRADED_SCHEMA: NormalizationMaintenanceSeverity.WARNING,
        NormalizationMaintenanceIssueType.UNSUPPORTED_SCHEMA: NormalizationMaintenanceSeverity.CRITICAL,
    }[issue_type]
    identity = observation.schema_fingerprint or observation.source_path
    return NormalizationMaintenanceIssue(
        dedupe_key=_dedupe_key(tenant_id, source_system, adapter, issue_type.value, identity),
        issue_type=issue_type,
        severity=severity,
        tenant_id=tenant_id,
        source_system=source_system,
        adapter=adapter,
        parser_name=observation.parser_name,
        parser_version=observation.parser_version,
        schema_fingerprint=observation.schema_fingerprint,
        source_path=observation.source_path,
        run_id=run.run_id,
        alert_id=run.alert_id,
        details={"warnings": observation.warnings, "field_count": observation.field_count},
    )


def _issue_for_gap(
    run: AnalysisRun,
    *,
    adapter: str,
    tenant_id: str | None,
    source_system: str | None,
    field_path: str,
    expected_target: str,
    details: dict[str, object],
    importance: str = EvidenceFieldImportance.HIGH.value,
) -> NormalizationMaintenanceIssue:
    severity = NormalizationMaintenanceSeverity.CRITICAL if importance == EvidenceFieldImportance.CRITICAL.value else NormalizationMaintenanceSeverity.WARNING
    return NormalizationMaintenanceIssue(
        dedupe_key=_dedupe_key(
            tenant_id,
            source_system,
            adapter,
            NormalizationMaintenanceIssueType.HIGH_VALUE_GAP.value,
            expected_target,
            field_path,
        ),
        issue_type=NormalizationMaintenanceIssueType.HIGH_VALUE_GAP,
        severity=severity,
        tenant_id=tenant_id,
        source_system=source_system,
        adapter=adapter,
        source_path=field_path,
        expected_target=expected_target,
        run_id=run.run_id,
        alert_id=run.alert_id,
        details=details,
    )


def _issue_for_truncation(
    run: AnalysisRun,
    *,
    adapter: str,
    tenant_id: str | None,
    source_system: str | None,
    source_path: str,
) -> NormalizationMaintenanceIssue:
    return NormalizationMaintenanceIssue(
        dedupe_key=_dedupe_key(
            tenant_id,
            source_system,
            adapter,
            NormalizationMaintenanceIssueType.EVIDENCE_TRUNCATED.value,
            source_path,
        ),
        issue_type=NormalizationMaintenanceIssueType.EVIDENCE_TRUNCATED,
        severity=NormalizationMaintenanceSeverity.WARNING,
        tenant_id=tenant_id,
        source_system=source_system,
        adapter=adapter,
        source_path=source_path,
        run_id=run.run_id,
        alert_id=run.alert_id,
        details={"reason": "bounded analysis evidence was truncated"},
    )


def _record_recurrence(
    issue: NormalizationMaintenanceIssue,
    *,
    run: AnalysisRun,
    details: dict[str, object],
) -> NormalizationMaintenanceIssue:
    issue.occurrence_count += 1
    issue.last_seen_at = datetime.now(UTC)
    issue.run_id = run.run_id
    issue.alert_id = run.alert_id
    issue.details = details
    if issue.status in {
        NormalizationMaintenanceIssueStatus.RESOLVED,
        NormalizationMaintenanceIssueStatus.IGNORED,
    }:
        issue.status = NormalizationMaintenanceIssueStatus.OPEN
        issue.resolved_by = None
        issue.resolved_at = None
        issue.resolution_reason = "reopened after recurrence"
    return issue


def _drift_event(issue: NormalizationMaintenanceIssue, *, context: ServiceRequestContext) -> SocEvent:
    return SocEvent(
        event_type=SocEventType.NORMALIZATION_DRIFT_DETECTED,
        request_id=context.request_id,
        run_id=issue.run_id,
        alert_id=issue.alert_id,
        actor=context.actor,
        payload={
            "issue_id": issue.issue_id,
            "issue_type": issue.issue_type.value,
            "severity": issue.severity.value,
            "status": issue.status.value,
            "occurrence_count": issue.occurrence_count,
            "source_system": issue.source_system,
            "parser_name": issue.parser_name,
            "parser_version": issue.parser_version,
            "schema_fingerprint": issue.schema_fingerprint,
            "expected_target": issue.expected_target,
        },
    )


def _dedupe_key(*parts: str | None) -> str:
    normalized = "|".join(part or "<none>" for part in parts)
    return f"normalization:{hashlib.sha256(normalized.encode()).hexdigest()}"


__all__ = ["SocNormalizationMaintenanceService"]
