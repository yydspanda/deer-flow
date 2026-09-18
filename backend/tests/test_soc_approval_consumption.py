from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    ActorAuthSource,
    ActorContext,
    EntrySurface,
    ServiceRequestContext,
    SocAgentApprovalRequest,
    SocAgentApprovedActionCommand,
    SocAgentRiskLevel,
    SocMutationOperation,
)
from soc_agent.core import SocAgentApprovalService, SocServiceError
from soc_agent.db import SocBase, SqlAlchemyAlertRepository


def _context(key: str) -> ServiceRequestContext:
    return ServiceRequestContext(
        request_id=f"REQ-{key}",
        idempotency_key=key,
        actor=ActorContext(
            actor_id="approval-operator",
            surface=EntrySurface.CLI,
            roles=["soc_admin"],
            auth_source=ActorAuthSource.LOCAL_CLI,
        ),
    )


def _seed_approval(path: Path):
    engine = create_engine(f"sqlite:///{path}")
    SocBase.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    repository = SqlAlchemyAlertRepository(session_factory)
    service = SocAgentApprovalService(grant_repository=repository, request_repository=repository)
    request = SocAgentApprovalRequest(
        approval_request_id="APR-CONSUME-RACE",
        permission_decision_id="PERM-CONSUME-RACE",
        route="mcp",
        action="block_ip",
        risk_level=SocAgentRiskLevel.HIGH_RISK,
        requested_by=_context("submit").actor,
        reason="Approved boundary concurrency fixture",
    )
    service.submit_request(request, context=_context("submit"))
    grant = service.approve(request.approval_request_id, context=_context("approve"), reason="Review complete")
    command = SocAgentApprovedActionCommand(
        route=grant.route,
        action=grant.action,
        execution_token_id=grant.execution_token_id,
        dry_run=False,
        payload={"target": "192.0.2.1"},
    )
    return repository, service, command, session_factory


@pytest.mark.parametrize("same_key,changed_payload", [(False, False), (True, False), (True, True)])
def test_concurrent_consumption_preserves_one_result_and_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, same_key: bool, changed_payload: bool) -> None:
    repository, service, command, session_factory = _seed_approval(tmp_path / "concurrent.db")
    peer_repository = SqlAlchemyAlertRepository(session_factory)
    peer_service = SocAgentApprovalService(grant_repository=peer_repository, request_repository=peer_repository)
    barrier = Barrier(2)
    original_get = SqlAlchemyAlertRepository.get_approval_grant_by_token

    def read_before_either_write(self, token):
        grant = original_get(self, token)
        if grant is not None and grant.status == "approved":
            barrier.wait(timeout=10)
        return grant

    monkeypatch.setattr(SqlAlchemyAlertRepository, "get_approval_grant_by_token", read_before_either_write)
    contexts = [_context("execute-1"), _context("execute-1" if same_key else "execute-2")]
    commands = [command, command.model_copy(update={"payload": {"target": "192.0.2.2"}}) if changed_payload else command]

    def execute(index):
        try:
            return (service, peer_service)[index].execute_approved_action(commands[index], context=contexts[index])
        except SocServiceError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(execute, range(2)))

    successes = [result for result in results if not isinstance(result, SocServiceError)]
    failures = [result for result in results if isinstance(result, SocServiceError)]
    expected_successes = 2 if same_key and not changed_payload else 1
    assert len(successes) == expected_successes
    assert len(failures) == 2 - expected_successes
    assert len({result.payload["execution_result_id"] for result in successes}) == 1
    persisted = repository.get_approval_grant_by_token(command.execution_token_id)
    assert persisted.status == "consumed"
    assert persisted.execution_result_payload == successes[0].model_dump(mode="json")
    audits = repository.list_mutation_audits(operation=SocMutationOperation.APPROVAL_ACTION_EXECUTE)
    assert len(audits) == 1
    assert audits[0].result_ref == persisted.execution_result_id

    winner = next(index for index, result in enumerate(results) if not isinstance(result, SocServiceError))
    assert service.execute_approved_action(commands[winner], context=contexts[winner]) == successes[0]
    assert repository.get_approval_grant_by_token(command.execution_token_id) == persisted
    assert repository.list_mutation_audits(operation=SocMutationOperation.APPROVAL_ACTION_EXECUTE) == audits


@pytest.mark.parametrize("fail_after_write", [1, 2])
def test_consumption_and_audit_roll_back_together(tmp_path: Path, fail_after_write: int) -> None:
    repository, _, command, session_factory = _seed_approval(tmp_path / "rollback.db")
    initial = repository.get_approval_grant_by_token(command.execution_token_id)

    def fail(write_count):
        if write_count == fail_after_write:
            raise RuntimeError("injected consumption failure")

    failing_repository = SqlAlchemyAlertRepository(session_factory, mutation_write_hook=fail)
    service = SocAgentApprovalService(grant_repository=failing_repository, request_repository=failing_repository)
    with pytest.raises(RuntimeError, match="injected consumption failure"):
        service.execute_approved_action(command, context=_context("execute"))
    assert repository.get_approval_grant_by_token(command.execution_token_id) == initial
    assert repository.list_mutation_audits(operation=SocMutationOperation.APPROVAL_ACTION_EXECUTE) == []


def test_retry_checks_audit_committed_between_initial_audit_and_grant_reads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repository, service, command, session_factory = _seed_approval(tmp_path / "audit-read-race.db")
    peer_repository = SqlAlchemyAlertRepository(session_factory)
    peer_service = SocAgentApprovalService(grant_repository=peer_repository, request_repository=peer_repository)
    original_find = SqlAlchemyAlertRepository.find_mutation_audit_by_idempotency_key
    context = _context("execute")
    winning_results = []
    peer_started = False

    def read_before_peer_commits(self, operation, key):
        nonlocal peer_started
        audit = original_find(self, operation, key)
        if operation == SocMutationOperation.APPROVAL_ACTION_EXECUTE and not peer_started:
            assert audit is None
            peer_started = True
            winning_results.append(peer_service.execute_approved_action(command, context=context))
        return audit

    monkeypatch.setattr(SqlAlchemyAlertRepository, "find_mutation_audit_by_idempotency_key", read_before_peer_commits)
    changed = command.model_copy(update={"payload": {"target": "192.0.2.2"}})
    with pytest.raises(SocServiceError, match="different content"):
        service.execute_approved_action(changed, context=context)
    stored = repository.get_approval_grant_by_token(command.execution_token_id)
    assert stored.execution_result_payload == winning_results[0].model_dump(mode="json")
    assert len(repository.list_mutation_audits(operation=SocMutationOperation.APPROVAL_ACTION_EXECUTE)) == 1
