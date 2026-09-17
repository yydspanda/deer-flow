from datetime import timedelta

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker
from test_soc_memory_lesson_drafting import _candidate

from soc_agent.contracts import ActorContext, EntrySurface, ServiceRequestContext, SocMemoryCandidateStatus, SocMutationOperation, Verdict
from soc_agent.contracts.memory_drafts import MemoryDraftContent, MemoryDraftSaveCommand
from soc_agent.core.errors import SocServiceAuthorizationError, SocServiceConflictError
from soc_agent.core.memory_working_drafts import SocMemoryWorkingDraftService
from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables
from soc_agent.db.migration_runner import upgrade_soc_schema


def context(key="save-1", actor="reviewer"):
    return ServiceRequestContext(actor=ActorContext(actor_id=actor, roles=["soc_memory_reviewer"], surface=EntrySurface.TEST), idempotency_key=key)


def setup(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'drafts.sqlite'}")
    create_soc_tables(engine)
    repo = SqlAlchemyAlertRepository(sessionmaker(bind=engine, expire_on_commit=False))
    candidate = _candidate()
    repo.save_memory_candidate(candidate)
    service = SocMemoryWorkingDraftService(repository=repo, mutation_uow=repo)
    return repo, candidate, service


def command(view, *, expected_version=0, conclusion="待编辑"):
    return MemoryDraftSaveCommand(
        candidate_revision=view.candidate_revision,
        expected_version=expected_version,
        content=MemoryDraftContent(reviewer_verdict=Verdict.FALSE_POSITIVE, conclusion=conclusion),
    )


def test_working_draft_does_not_create_memory_or_change_candidate(tmp_path):
    repo, candidate, service = setup(tmp_path)
    before = repo.get_memory_candidate(candidate.candidate_id).model_dump_json()
    view = service.get(candidate.candidate_id, context=context())
    assert view.draft is None and view.editable
    saved = service.save(candidate.candidate_id, command(view), context=context())
    assert saved.version == 1 and saved.content.conclusion == "待编辑"
    assert saved.content.reviewer_context == ""
    assert saved.last_generation is None
    assert service.get(candidate.candidate_id, context=context()).draft == saved
    assert repo.get_memory_candidate(candidate.candidate_id).model_dump_json() == before
    assert repo.list_memory_records() == []
    assert len(repo.list_mutation_audits(operation=SocMutationOperation.MEMORY_DRAFT_SAVE)) == 1


def test_optimistic_save_and_idempotency_never_undo_later_edit(tmp_path):
    repo, candidate, service = setup(tmp_path)
    view = service.get(candidate.candidate_id, context=context())
    initial = command(view)
    first = service.save(candidate.candidate_id, initial, context=context())
    second = service.save(candidate.candidate_id, command(view, expected_version=1, conclusion="第二位审核人的补充"), context=context("save-2", "other"))
    assert second.version == 2
    assert service.save(candidate.candidate_id, initial, context=context()) == first
    assert service.get(candidate.candidate_id, context=context()).draft == second
    with pytest.raises(SocServiceConflictError):
        service.save(candidate.candidate_id, command(view, expected_version=1), context=context("save-stale"))
    with pytest.raises(SocServiceConflictError):
        service.save(candidate.candidate_id, command(view, conclusion="same key different text"), context=context())
    with pytest.raises(SocServiceConflictError):
        service.save(candidate.candidate_id, initial, context=context(actor="another"))
    assert len(repo.list_mutation_audits(operation=SocMutationOperation.MEMORY_DRAFT_SAVE)) == 2


def test_source_changes_keep_old_draft_visible_but_require_explicit_rebase(tmp_path):
    repo, candidate, service = setup(tmp_path)
    view = service.get(candidate.candidate_id, context=context())
    saved = service.save(candidate.candidate_id, command(view), context=context())
    candidate.updated_at += timedelta(seconds=1)
    candidate.content += " 新来源事实。"
    repo.save_memory_candidate(candidate)
    current = service.get(candidate.candidate_id, context=context())
    assert current.stale and current.draft == saved
    with pytest.raises(SocServiceConflictError, match="来源"):
        service.save(candidate.candidate_id, command(view, expected_version=1), context=context("old-source"))
    refreshed = service.save(candidate.candidate_id, command(current, expected_version=1), context=context("new-source"))
    assert refreshed.version == 2
    assert not service.get(candidate.candidate_id, context=context()).stale


def test_reviewed_candidate_and_nonreviewer_cannot_edit_draft(tmp_path):
    repo, candidate, service = setup(tmp_path)
    view = service.get(candidate.candidate_id, context=context())
    outsider = ServiceRequestContext(actor=ActorContext(actor_id="viewer", roles=[], surface=EntrySurface.WEB))
    with pytest.raises(SocServiceAuthorizationError):
        service.get(candidate.candidate_id, context=outsider)
    with pytest.raises(SocServiceAuthorizationError):
        service.save(candidate.candidate_id, command(view), context=outsider)
    candidate.status = SocMemoryCandidateStatus.REJECTED
    repo.save_memory_candidate(candidate)
    assert not service.get(candidate.candidate_id, context=context()).editable
    with pytest.raises(SocServiceConflictError):
        service.save(candidate.candidate_id, command(view), context=context())


def test_draft_and_audit_roll_back_together(tmp_path, monkeypatch):
    repo, candidate, service = setup(tmp_path)
    view = service.get(candidate.candidate_id, context=context())

    def fail(*args, **kwargs):
        raise RuntimeError("audit write failed")

    monkeypatch.setattr(SqlAlchemyAlertRepository, "append_mutation_audit", fail)
    with pytest.raises(RuntimeError):
        service.save(candidate.candidate_id, command(view), context=context())
    assert service.get(candidate.candidate_id, context=context()).draft is None


def test_working_draft_schema_upgrades_empty_and_existing_database(tmp_path):
    url = f"sqlite+pysqlite:///{tmp_path / 'migrated.sqlite'}"
    upgrade_soc_schema(url, revision="0030_corpus_experiments")
    upgrade_soc_schema(url)
    upgrade_soc_schema(url)
    engine = create_engine(url)
    assert "soc_memory_working_drafts" in inspect(engine).get_table_names()


def test_shared_draft_http_boundary_rejects_stale_or_unauthorized_writes(tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import soc_memory as routes

    _, candidate, service = setup(tmp_path)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        role = request.headers.get("test-role", "admin")
        request.state.user = None if role == "anonymous" else SimpleNamespace(id="reviewer", system_role=role)
        request.state.auth_source = "session" if role != "anonymous" else "unknown"
        return await call_next(request)

    app.dependency_overrides[routes.get_soc_memory_working_draft_service] = lambda: service
    app.include_router(routes.router)
    with TestClient(app) as http:
        url = f"/api/soc/memory/candidates/{candidate.candidate_id}/working-draft"
        assert http.get(url, headers={"test-role": "anonymous"}).status_code == 403
        assert http.get(url, headers={"test-role": "user"}).status_code == 200
        view = http.get(url)
        assert view.status_code == 200
        body = {"expected_version": 0, "candidate_revision": view.json()["candidate_revision"], "content": {"reviewer_verdict": "true_positive", "observed_event": "运营选择的事件说明"}}
        saved = http.put(url, json=body, headers={"Idempotency-Key": "http-save"})
        assert saved.status_code == 200, saved.text
        assert saved.json()["authority"] == "draft_only"
        assert http.put(url, json=body, headers={"Idempotency-Key": "http-save"}).json() == saved.json()
        assert http.put(url, json=body, headers={"Idempotency-Key": "another-save"}).status_code == 409
        assert http.put(url, json={**body, "activate_retrieval": True}).status_code == 422
