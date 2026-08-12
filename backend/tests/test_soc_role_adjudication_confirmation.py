from __future__ import annotations

import json
from pathlib import Path

import pytest

from soc_agent.contracts import (
    ActorContext,
    AnalysisRun,
    EntrySurface,
    HumanConfirmedResponseTarget,
    HumanConfirmedRole,
    RoleAdjudicationConfirmationCommand,
    ServiceRequestContext,
    SocMutationAuditRecord,
    SocMutationOperation,
)
from soc_agent.core import DeterministicAnalysisRuntime, SocReviewService
from soc_agent.core.errors import SocServiceConflictError

SAMPLES = Path(__file__).resolve().parents[1] / "samples" / "alerts"


class InMemoryRoleRepository:
    def __init__(self, run: AnalysisRun) -> None:
        self.runs = {run.run_id: run}

    def save_run(self, run: AnalysisRun) -> None:
        self.runs[run.run_id] = run.model_copy(deep=True)

    def get_run(self, run_id: str) -> AnalysisRun | None:
        run = self.runs.get(run_id)
        return run.model_copy(deep=True) if run is not None else None

    def list_runs(self, *, limit: int = 50) -> list[AnalysisRun]:
        return list(self.runs.values())[:limit]


class InMemoryMutationAuditRepository:
    def __init__(self) -> None:
        self.records: list[SocMutationAuditRecord] = []

    def append_mutation_audit(self, record: SocMutationAuditRecord) -> None:
        self.records.append(record)

    def find_mutation_audit_by_idempotency_key(
        self,
        operation: SocMutationOperation,
        idempotency_key: str,
    ) -> SocMutationAuditRecord | None:
        return next(
            (record for record in self.records if record.operation is operation and record.idempotency_key == idempotency_key),
            None,
        )

    def list_mutation_audits(self, **kwargs):  # noqa: ANN003, ANN201 - protocol test double
        return list(self.records)


def _run() -> AnalysisRun:
    payload = json.loads((SAMPLES / "malicious_ioc.json").read_text(encoding="utf-8"))
    return DeterministicAnalysisRuntime().analyze(payload)


def _context(*, key: str = "role-confirm-1") -> ServiceRequestContext:
    return ServiceRequestContext(
        request_id="REQ-ROLE-CONFIRM-1",
        idempotency_key=key,
        actor=ActorContext(
            actor_id="analyst-1",
            surface=EntrySurface.TEST,
            roles=["soc_analyst"],
        ),
    )


def _command(run_id: str, *, expected_revision: int = 0) -> RoleAdjudicationConfirmationCommand:
    return RoleAdjudicationConfirmationCommand(
        run_id=run_id,
        expected_revision=expected_revision,
        roles=[
            HumanConfirmedRole(
                role="victim",
                entity_type="ip",
                value="10.10.1.25",
                rationale="Endpoint evidence confirms this is the impacted host.",
            )
        ],
        response_targets=[
            HumanConfirmedResponseTarget(
                action_kind="isolate_host",
                target_type="ip",
                target_value="10.10.1.25",
                target_role="victim",
                rationale="Isolation should target the confirmed impacted host.",
            )
        ],
        reason="Analyst verified the endpoint role and containment target.",
    )


def test_human_role_confirmation_is_append_only_and_keeps_model_result_immutable() -> None:
    original = _run()
    assert original.analysis is not None
    model_adjudication = original.analysis.role_adjudication.model_copy(deep=True)
    repository = InMemoryRoleRepository(original)
    audits = InMemoryMutationAuditRepository()
    service = SocReviewService(
        repository=repository,
        mutation_audit_repository=audits,
    )

    record = service.confirm_role_adjudication(
        _command(original.run_id),
        context=_context(),
    )

    persisted = repository.get_run(original.run_id)
    assert persisted is not None and persisted.analysis is not None
    assert persisted.analysis.role_adjudication == model_adjudication
    assert persisted.role_adjudication_revisions == [record]
    assert record.revision == 1
    assert record.roles[0].role.value == "victim"
    assert record.response_targets[0].action_kind == "isolate_host"
    assert record.automation_allowed is False
    assert audits.records[0].operation is SocMutationOperation.REVIEW_ROLE_CONFIRM

    retried = service.confirm_role_adjudication(
        _command(original.run_id),
        context=_context(),
    )
    assert retried.revision_id == record.revision_id
    assert len(audits.records) == 1


def test_human_role_confirmation_rejects_stale_revision() -> None:
    original = _run()
    repository = InMemoryRoleRepository(original)
    service = SocReviewService(repository=repository)
    service.confirm_role_adjudication(_command(original.run_id), context=_context())

    with pytest.raises(SocServiceConflictError, match="expected revision 0, found 1"):
        service.confirm_role_adjudication(
            _command(original.run_id),
            context=_context(key="role-confirm-2"),
        )
