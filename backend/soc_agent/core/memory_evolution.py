"""Operational Memory use, feedback, health and revision workflow."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from soc_agent.contracts import (
    SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
    ActorContext,
    ActorType,
    AnalysisContextCatalogItem,
    AnalysisContextReferenceKind,
    AnalysisRun,
    CorrectionRecord,
    DecisionConfidenceSource,
    EntrySurface,
    ServiceRequestContext,
    SocAutomationContributorKind,
    SocAutomationContributorRef,
    SocAutomationContributorRole,
    SocDecisionTransitionKind,
    SocMemoryApplicabilityReport,
    SocMemoryApplicabilityStatus,
    SocMemoryFeedbackAlignment,
    SocMemoryFeedbackEvent,
    SocMemoryFeedbackResult,
    SocMemoryFeedbackSource,
    SocMemoryFeedbackTrust,
    SocMemoryHealthRecord,
    SocMemoryHealthStatus,
    SocMemoryLineageReport,
    SocMemoryRecord,
    SocMemoryRetrievalActivationAction,
    SocMemoryRetrievalActivationCommand,
    SocMemoryRevisionProposal,
    SocMemoryRevisionProposalStatus,
    SocMemoryRevisionReviewCommand,
    SocMemoryRevisionReviewDecision,
    SocMemoryRevisionReviewResult,
    SocMemoryUseEffect,
    SocMemoryUseRecord,
    SocMutationOperation,
    Verdict,
)
from soc_agent.protocols import (
    MemoryEvolutionRepository,
    MemoryRecordRepository,
    SocAutomationRepository,
    SocMutationAuditRepository,
    SocMutationUnitOfWork,
)

from .access_control import require_actor_roles
from .errors import (
    SocServiceConflictError,
    SocServiceNotFoundError,
    SocServiceNotImplementedError,
)
from .mutation_audit import (
    build_mutation_audit,
    mutation_audit_repository_from,
    mutation_idempotency_key,
    mutation_uow_from,
    validate_mutation_retry,
)


class SocMemoryEvolutionError(ValueError):
    """Memory feedback could not be persisted coherently."""


class SocMemoryEvolutionService:
    """Observe Memory use and evolve it only through append-only feedback."""

    REVISION_REVIEWER_ROLES = frozenset({"soc_memory_reviewer", "soc_admin"})

    def __init__(
        self,
        *,
        repository: MemoryEvolutionRepository,
        memory_record_repository: MemoryRecordRepository,
        automation_repository: SocAutomationRepository | None = None,
        mutation_audit_repository: SocMutationAuditRepository | None = None,
        mutation_uow: SocMutationUnitOfWork | None = None,
        now_provider: Callable[[], datetime] | None = None,
        transaction_active: bool = False,
    ) -> None:
        self._repository = repository
        self._memory_record_repository = memory_record_repository
        self._automation_repository = automation_repository
        self._mutation_audit_repository = mutation_audit_repository or mutation_audit_repository_from(repository)
        self._mutation_uow = mutation_uow or mutation_uow_from(repository)
        self._now = now_provider or (lambda: datetime.now(UTC))
        self._transaction_active = transaction_active

    def observe(
        self,
        run: AnalysisRun,
        *,
        context: ServiceRequestContext,
    ) -> None:
        self.capture_run_usage(run)

    def capture_run_usage(self, run: AnalysisRun) -> list[SocMemoryUseRecord]:
        """Persist one final use effect per Run and immutable Memory version."""

        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return SocMemoryEvolutionService(
                    repository=repository,
                    memory_record_repository=repository,
                    automation_repository=(
                        repository
                        if callable(
                            getattr(
                                repository,
                                "list_decision_transitions",
                                None,
                            )
                        )
                        else None
                    ),
                    mutation_audit_repository=repository,
                    mutation_uow=self._mutation_uow,
                    now_provider=self._now,
                    transaction_active=True,
                ).capture_run_usage(run)
        if run.llm_analysis_request is None or run.decision is None:
            return []
        transition = self._latest_transition(run.run_id)
        effective_verdict = transition.after.verdict if transition is not None else run.decision.verdict
        contributor_by_ref = {item.ref_id: item for item in (transition.contributors if transition is not None else []) if item.kind is SocAutomationContributorKind.CONFIRMED_MEMORY}
        projections_by_memory: dict[
            tuple[str, int],
            list[tuple[AnalysisContextCatalogItem, float, str, str]],
        ] = {}
        for item in run.llm_analysis_request.context_catalog:
            if item.kind is not AnalysisContextReferenceKind.CONFIRMED_MEMORY:
                continue
            memory_id = item.metadata.get("memory_id")
            memory_version = item.metadata.get("memory_version")
            score = item.metadata.get("retrieval_score")
            content_hash = item.metadata.get("record_content_hash")
            facets_hash = item.metadata.get("record_facets_hash")
            if not isinstance(memory_id, str) or not isinstance(memory_version, int) or not isinstance(score, (int, float)) or not isinstance(content_hash, str) or not isinstance(facets_hash, str):
                continue
            projections_by_memory.setdefault(
                (memory_id, memory_version),
                [],
            ).append((item, float(score), content_hash, facets_hash))

        existing_by_memory: dict[tuple[str, int], SocMemoryUseRecord] = {}
        for existing in self._repository.list_memory_uses(
            run_id=run.run_id,
            limit=10_000,
        ):
            existing_by_memory.setdefault(
                (existing.memory_id, existing.memory_version),
                existing,
            )

        uses: list[SocMemoryUseRecord] = []
        for (memory_id, memory_version), projections in projections_by_memory.items():
            existing = existing_by_memory.get((memory_id, memory_version))
            if existing is not None:
                uses.append(existing)
                continue
            item, score, content_hash, facets_hash = _select_memory_projection(
                projections,
                contributor_by_ref,
            )
            idempotency_key = f"memory-use:{run.run_id}:{memory_id}:v{memory_version}"
            existing = self._repository.find_memory_use_by_idempotency_key(idempotency_key)
            if existing is not None:
                uses.append(existing)
                existing_by_memory[(memory_id, memory_version)] = existing
                continue
            contributor = contributor_by_ref.get(item.context_ref)
            effect = _use_effect(transition, contributor)
            applicability = _applicability_report(item.metadata)
            record = SocMemoryUseRecord(
                idempotency_key=idempotency_key,
                memory_id=memory_id,
                memory_version=memory_version,
                memory_content_hash=content_hash,
                memory_facets_hash=facets_hash,
                run_id=run.run_id,
                alert_id=run.alert_id,
                tenant_id=run.llm_analysis_request.tenant_id,
                context_ref=item.context_ref,
                retrieval_policy_version=str(item.metadata.get("retrieval_policy_version") or "soc.memory_retrieval_policy.v2"),
                retrieval_score=score,
                matched_facets=_matched_facets(item.metadata),
                applicability_report=applicability,
                base_verdict=run.decision.verdict,
                effective_verdict=effective_verdict,
                effect=effect,
                directive_applied=contributor is not None,
                decision_transition_id=(transition.transition_id if transition is not None else None),
                created_at=self._now(),
            )
            self._repository.save_memory_use(record)
            self._increment_use_health(record)
            uses.append(record)
            existing_by_memory[(memory_id, memory_version)] = record
        return uses

    def get_lineage(self, memory_id: str) -> SocMemoryLineageReport:
        record = self._memory_record_repository.get_memory_record(memory_id)
        if record is None:
            raise SocMemoryEvolutionError(f"memory record {memory_id} not found")
        uses = self._repository.list_memory_uses(memory_id=memory_id, limit=10_000)
        feedback = self._repository.list_memory_feedback(
            memory_id=memory_id,
            limit=10_000,
        )
        proposals = self._repository.list_memory_revision_proposals(
            memory_id=memory_id,
            limit=10_000,
        )
        versions = {
            record.version,
            *(item.memory_version for item in uses),
            *(item.memory_version for item in feedback),
            *(item.memory_version for item in proposals),
        }
        health = [
            item
            for version in sorted(versions)
            if (
                item := self._repository.get_memory_health(
                    memory_id,
                    version,
                )
            )
            is not None
        ]
        return SocMemoryLineageReport(
            record=record,
            uses=uses,
            feedback=feedback,
            health=health,
            revision_proposals=proposals,
        )

    def list_revision_proposals(
        self,
        *,
        memory_id: str | None = None,
        status: SocMemoryRevisionProposalStatus | None = None,
        limit: int = 100,
    ) -> list[SocMemoryRevisionProposal]:
        """List review work without exposing repository-specific queries."""

        return self._repository.list_memory_revision_proposals(
            memory_id=memory_id,
            status=status,
            limit=limit,
        )

    def get_revision_proposal(
        self,
        proposal_id: str,
    ) -> SocMemoryRevisionProposal:
        proposal = self._repository.get_memory_revision_proposal(proposal_id)
        if proposal is None:
            raise SocServiceNotFoundError(f"memory revision proposal {proposal_id} not found")
        return proposal

    def review_revision_proposal(
        self,
        command: SocMemoryRevisionReviewCommand,
        *,
        context: ServiceRequestContext,
    ) -> SocMemoryRevisionReviewResult:
        """Resolve a contradiction proposal without mutating its Memory record."""

        require_actor_roles(
            context,
            self.REVISION_REVIEWER_ROLES,
            operation="reviewing a SOC memory revision proposal",
        )
        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return SocMemoryEvolutionService(
                    repository=repository,
                    memory_record_repository=repository,
                    automation_repository=(
                        repository
                        if callable(
                            getattr(
                                repository,
                                "list_decision_transitions",
                                None,
                            )
                        )
                        else None
                    ),
                    mutation_audit_repository=repository,
                    mutation_uow=self._mutation_uow,
                    now_provider=self._now,
                    transaction_active=True,
                ).review_revision_proposal(command, context=context)
        if self._mutation_audit_repository is None:
            raise SocServiceNotImplementedError("memory revision review requires a SocMutationAuditRepository")

        command_payload = command.model_dump(mode="json")
        idempotency_key = mutation_idempotency_key(context)
        existing_audit = self._mutation_audit_repository.find_mutation_audit_by_idempotency_key(
            SocMutationOperation.MEMORY_REVISION_REVIEW,
            idempotency_key,
        )
        if existing_audit is not None:
            validate_mutation_retry(
                existing_audit,
                command=command_payload,
                target_type="memory_revision_proposal",
                target_id=command.proposal_id,
            )
            proposal = self.get_revision_proposal(command.proposal_id)
            expected_status = _revision_status_for_decision(command.decision)
            if proposal.status is not expected_status:
                raise SocServiceConflictError("memory revision review retry no longer references the persisted proposal state")
            return SocMemoryRevisionReviewResult(
                proposal=proposal,
                previous_status=SocMemoryRevisionProposalStatus.PENDING_REVIEW,
                decision=command.decision,
                audit_id=existing_audit.audit_id,
                reviewed_at=proposal.reviewed_at or existing_audit.occurred_at,
            )

        proposal = self.get_revision_proposal(command.proposal_id)
        if proposal.status is not SocMemoryRevisionProposalStatus.PENDING_REVIEW:
            raise SocServiceConflictError(f"memory revision proposal {proposal.proposal_id} is already {proposal.status.value}")
        reviewed_at = self._now()
        if reviewed_at.utcoffset() is None:
            raise SocMemoryEvolutionError("memory evolution clock must be timezone-aware")
        updated = proposal.model_copy(
            update={
                "status": _revision_status_for_decision(command.decision),
                "reviewed_at": reviewed_at,
                "reviewed_by": context.actor.actor_id,
                "review_reason": command.reason,
            }
        )
        if not self._repository.compare_and_set_memory_revision_proposal(
            updated,
            expected_status=SocMemoryRevisionProposalStatus.PENDING_REVIEW,
        ):
            raise SocServiceConflictError(f"memory revision proposal {proposal.proposal_id} changed during review")
        audit = build_mutation_audit(
            operation=SocMutationOperation.MEMORY_REVISION_REVIEW,
            target_type="memory_revision_proposal",
            target_id=proposal.proposal_id,
            context=context,
            reason=command.reason,
            command=command_payload,
            result_ref=f"{proposal.proposal_id}:{updated.status.value}",
            payload={
                "memory_id": proposal.memory_id,
                "memory_version": proposal.memory_version,
                "source_feedback_id": proposal.source_feedback_id,
                "previous_status": proposal.status.value,
                "status": updated.status.value,
                "memory_record_changed": False,
                "retrieval_reenabled": False,
            },
        )
        self._mutation_audit_repository.append_mutation_audit(audit)
        return SocMemoryRevisionReviewResult(
            proposal=updated,
            previous_status=proposal.status,
            decision=command.decision,
            audit_id=audit.audit_id,
            reviewed_at=reviewed_at,
        )

    def record_correction_feedback(
        self,
        run: AnalysisRun,
        correction: CorrectionRecord,
        *,
        context: ServiceRequestContext,
    ) -> SocMemoryFeedbackResult:
        """Apply one explicit final outcome to every Memory used by the run."""

        if self._mutation_uow is not None and not self._transaction_active:
            with self._mutation_uow.mutation_transaction() as repository:
                return SocMemoryEvolutionService(
                    repository=repository,
                    memory_record_repository=repository,
                    automation_repository=(
                        repository
                        if callable(
                            getattr(
                                repository,
                                "list_decision_transitions",
                                None,
                            )
                        )
                        else None
                    ),
                    mutation_audit_repository=repository,
                    mutation_uow=self._mutation_uow,
                    now_provider=self._now,
                    transaction_active=True,
                ).record_correction_feedback(
                    run,
                    correction,
                    context=context,
                )
        uses = self._repository.list_memory_uses(run_id=run.run_id, limit=100)
        feedback_events: list[SocMemoryFeedbackEvent] = []
        health_records: list[SocMemoryHealthRecord] = []
        proposals: list[SocMemoryRevisionProposal] = []
        suspended: list[str] = []
        source, trust = _feedback_source_and_trust(correction)
        for use in uses:
            memory = self._memory_record_repository.get_memory_record(use.memory_id)
            if memory is None or memory.version < use.memory_version:
                continue
            target = memory.decision_directive.target_verdict if memory.decision_directive is not None else None
            alignment = _feedback_alignment(
                target,
                correction.corrected_verdict,
            )
            idempotency_key = f"memory-feedback:{correction.correction_id}:{use.use_id}"
            event = self._repository.find_memory_feedback_by_idempotency_key(idempotency_key)
            if event is None:
                event = SocMemoryFeedbackEvent(
                    idempotency_key=idempotency_key,
                    use_id=use.use_id,
                    memory_id=use.memory_id,
                    memory_version=use.memory_version,
                    run_id=run.run_id,
                    alert_id=run.alert_id,
                    tenant_id=use.tenant_id,
                    source=source,
                    trust=trust,
                    final_verdict=correction.corrected_verdict,
                    memory_target_verdict=target,
                    alignment=alignment,
                    reason=correction.reason,
                    source_ref=correction.correction_id,
                    actor_id=context.actor.actor_id,
                    created_at=self._now(),
                )
                self._repository.save_memory_feedback(event)
                health = self._apply_feedback_health(use, event)
            else:
                health = self._repository.get_memory_health(
                    use.memory_id,
                    use.memory_version,
                )
            feedback_events.append(event)
            if health is not None:
                health_records.append(health)

            if alignment is SocMemoryFeedbackAlignment.CONTRADICTS and trust is SocMemoryFeedbackTrust.HIGH:
                proposal = self._revision_proposal(memory, use, event)
                proposals.append(proposal)
                if _dangerous_false_negative(target, correction.corrected_verdict):
                    if self._suspend_retrieval(memory, event):
                        suspended.append(memory.memory_id)

        return SocMemoryFeedbackResult(
            feedback_events=feedback_events,
            health_records=health_records,
            revision_proposals=proposals,
            suspended_memory_ids=sorted(set(suspended)),
        )

    def _latest_transition(self, run_id: str):
        if self._automation_repository is None:
            return None
        transitions = self._automation_repository.list_decision_transitions(
            run_id=run_id,
            limit=20,
        )
        return max(
            transitions,
            key=lambda item: (item.created_at, item.transition_id),
            default=None,
        )

    def _increment_use_health(self, use: SocMemoryUseRecord) -> None:
        for _ in range(3):
            current = self._repository.get_memory_health(
                use.memory_id,
                use.memory_version,
            )
            if current is None:
                updated = SocMemoryHealthRecord(
                    memory_id=use.memory_id,
                    memory_version=use.memory_version,
                    use_count=1,
                    last_use_at=use.created_at,
                    updated_at=use.created_at,
                )
                expected = None
            else:
                updated = current.model_copy(
                    update={
                        "version": current.version + 1,
                        "use_count": current.use_count + 1,
                        "last_use_at": use.created_at,
                        "updated_at": use.created_at,
                    }
                )
                expected = current.version
            if self._repository.compare_and_set_memory_health(
                updated,
                expected_version=expected,
            ):
                return
        raise SocMemoryEvolutionError("memory use health update conflicted repeatedly")

    def _apply_feedback_health(
        self,
        use: SocMemoryUseRecord,
        event: SocMemoryFeedbackEvent,
    ) -> SocMemoryHealthRecord:
        for _ in range(3):
            current = self._repository.get_memory_health(
                use.memory_id,
                use.memory_version,
            ) or SocMemoryHealthRecord(
                memory_id=use.memory_id,
                memory_version=use.memory_version,
            )
            increments = {
                SocMemoryFeedbackAlignment.SUPPORTS: "support_count",
                SocMemoryFeedbackAlignment.CONTRADICTS: "contradiction_count",
                SocMemoryFeedbackAlignment.NOT_APPLICABLE: "not_applicable_count",
                SocMemoryFeedbackAlignment.UNKNOWN: "unknown_count",
            }
            counter = increments[event.alignment]
            values = {
                counter: getattr(current, counter) + 1,
                "last_feedback_at": event.created_at,
                "last_feedback_id": event.feedback_id,
                "updated_at": event.created_at,
            }
            status = current.status
            if event.alignment is SocMemoryFeedbackAlignment.CONTRADICTS and current.status is not SocMemoryHealthStatus.SUSPENDED:
                status = SocMemoryHealthStatus.WATCH
            values["status"] = status
            if (
                self._repository.get_memory_health(
                    use.memory_id,
                    use.memory_version,
                )
                is None
            ):
                updated = current.model_copy(update=values)
                expected = None
            else:
                updated = current.model_copy(update={**values, "version": current.version + 1})
                expected = current.version
            if self._repository.compare_and_set_memory_health(
                updated,
                expected_version=expected,
            ):
                return updated
        raise SocMemoryEvolutionError("memory feedback health update conflicted repeatedly")

    def _revision_proposal(
        self,
        memory: SocMemoryRecord,
        use: SocMemoryUseRecord,
        event: SocMemoryFeedbackEvent,
    ) -> SocMemoryRevisionProposal:
        idempotency_key = f"memory-revision:{event.feedback_id}"
        existing = self._repository.find_memory_revision_proposal_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing
        exclusions = {key: values for key, values in use.matched_facets.items() if key in {"environment", "entity", "role_entity", "source_system"} and values}
        proposal = SocMemoryRevisionProposal(
            idempotency_key=idempotency_key,
            memory_id=memory.memory_id,
            memory_version=use.memory_version,
            source_feedback_id=event.feedback_id,
            reason=(f"High-trust final outcome contradicts the reviewed Memory directive; review applicability exclusions, conclusion, or deprecation. Feedback: {event.reason}"),
            proposed_excluded_facets=exclusions,
            proposed_target_verdict=event.final_verdict,
            created_at=event.created_at,
        )
        self._repository.save_memory_revision_proposal(proposal)
        return proposal

    def _suspend_retrieval(
        self,
        memory: SocMemoryRecord,
        event: SocMemoryFeedbackEvent,
    ) -> bool:
        if not memory.retrieval_enabled or memory.version != event.memory_version:
            return False
        from soc_agent.core.service import SocMemoryService

        service = SocMemoryService(
            record_repository=self._memory_record_repository,
            mutation_audit_repository=self._mutation_audit_repository,
            mutation_uow=self._mutation_uow,
            now_provider=self._now,
            _transaction_active=self._transaction_active,
        )
        service.set_retrieval_activation(
            SocMemoryRetrievalActivationCommand(
                memory_id=memory.memory_id,
                action=SocMemoryRetrievalActivationAction.DISABLE,
                expected_record_version=memory.version,
                reason=(f"Safety suspension after high-trust risk feedback contradicted a benign Memory directive ({event.feedback_id})."),
                policy_version=SOC_MEMORY_RETRIEVAL_ACTIVATION_POLICY_VERSION,
                metadata={
                    "source": "memory_feedback_safety_monitor",
                    "feedback_id": event.feedback_id,
                },
            ),
            context=ServiceRequestContext(
                actor=ActorContext(
                    actor_id="soc-memory-safety-monitor",
                    actor_type=ActorType.SERVICE,
                    surface=EntrySurface.DAEMON,
                    roles=["soc_memory_safety_monitor"],
                ),
                trace_id=f"memory-feedback:{event.feedback_id}",
                idempotency_key=f"memory-safety:{event.feedback_id}",
            ),
        )
        health = self._repository.get_memory_health(
            event.memory_id,
            event.memory_version,
        )
        if health is not None:
            suspended = health.model_copy(
                update={
                    "version": health.version + 1,
                    "status": SocMemoryHealthStatus.SUSPENDED,
                    "suspension_reason": ("High-trust final risk verdict contradicted an active benign directive."),
                    "updated_at": event.created_at,
                }
            )
            if not self._repository.compare_and_set_memory_health(
                suspended,
                expected_version=health.version,
            ):
                raise SocMemoryEvolutionError("memory health changed during safety suspension")
        return True


def _use_effect(transition, contributor) -> SocMemoryUseEffect:
    if transition is None or contributor is None:
        return SocMemoryUseEffect.CONTEXT_ONLY
    if transition.transition_kind is SocDecisionTransitionKind.CONFLICTED:
        return SocMemoryUseEffect.CONFLICTED
    if contributor.role is SocAutomationContributorRole.OVERRIDES:
        return SocMemoryUseEffect.OVERRIDDEN
    return SocMemoryUseEffect.REINFORCED


def _select_memory_projection(
    projections: list[tuple[AnalysisContextCatalogItem, float, str, str]],
    contributor_by_ref: dict[str, SocAutomationContributorRef],
) -> tuple[AnalysisContextCatalogItem, float, str, str]:
    """Prefer the projection that actually contributed to the final decision."""

    for projection in projections:
        contributor = contributor_by_ref.get(projection[0].context_ref)
        if contributor is not None and contributor.role is SocAutomationContributorRole.OVERRIDES:
            return projection
    for projection in projections:
        if projection[0].context_ref in contributor_by_ref:
            return projection
    return projections[0]


def _applicability_report(metadata: dict) -> SocMemoryApplicabilityReport:
    payload = metadata.get("applicability_report")
    if isinstance(payload, dict):
        return SocMemoryApplicabilityReport.model_validate(payload)
    return SocMemoryApplicabilityReport(
        status=SocMemoryApplicabilityStatus.LEGACY_ANCHOR_ONLY,
        policy_version="soc.memory_applicability_policy.legacy",
        reason_codes=["historical_context_without_applicability_report"],
    )


def _matched_facets(metadata: dict) -> dict[str, list[str]]:
    payload = metadata.get("matched_facets")
    if not isinstance(payload, dict):
        return {}
    return {str(key): [str(value) for value in values if str(value)] for key, values in payload.items() if isinstance(values, list)}


def _feedback_source_and_trust(
    correction: CorrectionRecord,
) -> tuple[SocMemoryFeedbackSource, SocMemoryFeedbackTrust]:
    if correction.confidence_source is DecisionConfidenceSource.EXTERNAL_DISPOSITION:
        return (
            SocMemoryFeedbackSource.EXTERNAL_DISPOSITION,
            SocMemoryFeedbackTrust.HIGH,
        )
    return (
        SocMemoryFeedbackSource.ANALYST_CORRECTION,
        SocMemoryFeedbackTrust.HIGH,
    )


def _feedback_alignment(
    target: Verdict | None,
    final: Verdict,
) -> SocMemoryFeedbackAlignment:
    if target is None:
        return SocMemoryFeedbackAlignment.UNKNOWN
    if target is final or _verdict_class(target) == _verdict_class(final):
        return SocMemoryFeedbackAlignment.SUPPORTS
    return SocMemoryFeedbackAlignment.CONTRADICTS


def _dangerous_false_negative(
    target: Verdict | None,
    final: Verdict,
) -> bool:
    return target is Verdict.FALSE_POSITIVE and _verdict_class(final) == "risk"


def _revision_status_for_decision(
    decision: SocMemoryRevisionReviewDecision,
) -> SocMemoryRevisionProposalStatus:
    if decision is SocMemoryRevisionReviewDecision.ACCEPT:
        return SocMemoryRevisionProposalStatus.ACCEPTED
    return SocMemoryRevisionProposalStatus.REJECTED


def _verdict_class(verdict: Verdict) -> str:
    if verdict in {Verdict.TRUE_POSITIVE, Verdict.SUSPICIOUS}:
        return "risk"
    if verdict is Verdict.FALSE_POSITIVE:
        return "benign"
    return "unresolved"


__all__ = ["SocMemoryEvolutionError", "SocMemoryEvolutionService"]
