"""Authenticated DEV controls share durable jobs; requests never call the model."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_soc_corpus_experiment_repository import repository
from test_soc_corpus_experiments import prepare, service

from app.gateway.routers import soc_corpus_experiments as routes
from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.demo.corpus_experiment_dispatcher import CorpusExperimentDispatcher


def client(tmp_path):
    repo = repository(tmp_path)
    prepare(repo)
    calls = []
    svc = service(repo, calls)
    dispatcher = CorpusExperimentDispatcher(svc, interval_seconds=0.01)
    application = SimpleNamespace(service=svc, dispatcher=dispatcher, defaults=SocAnalysisExecutionOptions(), full_flow_defaults=SocAnalysisExecutionOptions(), max_concurrency=3, workbench=None)
    app = FastAPI()

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.user = SimpleNamespace(id="test-operator", system_role=request.headers.get("test-role", "admin"))
        request.state.auth_source = "session"
        return await call_next(request)

    app.dependency_overrides[routes.get_corpus_experiment_application] = lambda: application
    app.include_router(routes.router, prefix="/api/soc/dev/corpus-workbench")
    return TestClient(app), application, calls


def test_missing_schema_explains_upgrade_without_hiding_configuration(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from soc_agent.db import SqlAlchemyAlertRepository, create_soc_tables

    http, application, calls = client(tmp_path)
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'old.db'}")
    application.service.store = SqlAlchemyAlertRepository(sessionmaker(bind=engine)).corpus_experiments()
    root = "/api/soc/dev/corpus-workbench"
    assert http.get(root + "/experiments/configuration").status_code == 200
    for path in ("/experiments", "/experiments/EXP-test/rounds"):
        response = http.get(root + path)
        assert response.status_code == 503
        assert response.headers["X-SOC-Error-Code"] == "corpus_schema_upgrade_required"
        assert "升级数据库" in response.json()["detail"]
        assert "删除" in response.json()["detail"]
    assert http.post(root + "/experiments", json={"name": "test"}).status_code == 503
    assert http.get(root + "/experiments", headers={"test-role": "user"}).status_code == 403
    assert not calls and not application.dispatcher.is_running
    create_soc_tables(engine)
    assert http.get(root + "/experiments").json() == []


def test_only_learning_candidates_can_be_drafted_through_background_api(tmp_path):
    import time

    from test_soc_memory_draft_jobs import generated
    from test_soc_memory_lesson_drafting import _candidate
    from test_soc_memory_working_drafts import command, context

    from soc_agent.core.memory_draft_jobs import SocMemoryDraftJobService
    from soc_agent.core.memory_working_drafts import SocMemoryWorkingDraftService

    http, application, calls = client(tmp_path)
    repo = application.service.repository
    candidate = _candidate()
    candidate.metadata["experiment_id"] = "EXP-test"
    repo.save_memory_candidate(candidate)
    draft_service = SocMemoryWorkingDraftService(repository=repo, mutation_uow=repo)
    view = draft_service.get(candidate.candidate_id, context=context())
    draft_service.save(candidate.candidate_id, command(view), context=context())
    application.draft_jobs = SocMemoryDraftJobService(repository=repo, drafter_factory=lambda: SimpleNamespace(draft_business_lesson=lambda candidate_id, **kwargs: generated(candidate_id)))
    application.dispatcher = CorpusExperimentDispatcher(application.service, draft_jobs=application.draft_jobs, interval_seconds=0.01)
    root = "/api/soc/dev/corpus-workbench/experiments/EXP-test"
    input_page = http.get(root + "/draft-inputs").json()
    assert input_page["total"] == 1
    assert input_page["items"][0]["draft"]["content"]["reviewer_verdict"] == "false_positive"
    body = {"commands": [{"candidate_id": candidate.candidate_id, "candidate_revision": view.candidate_revision, "expected_version": 1, "regenerate": True}]}
    try:
        assert http.post(root + "/draft-jobs", json=body, headers={"test-role": "user"}).status_code == 403
        submitted = http.post(root + "/draft-jobs", json=body, headers={"Idempotency-Key": "generate"})
        assert submitted.status_code == 202, submitted.text
        job_id = submitted.json()["items"][0]["job_id"]
        assert http.post(root + "/draft-jobs", json=body, headers={"Idempotency-Key": "generate"}).json()["items"][0]["job_id"] == job_id
        deadline = time.monotonic() + 10
        result = {}
        while time.monotonic() < deadline:
            result = http.get(root + f"/draft-jobs/{job_id}").json()
            if result.get("status") in {"completed", "failed"}:
                break
            time.sleep(0.02)
        assert result["status"] == "completed", result
        assert result["generated_draft"]["lesson"]["conclusion"]
        assert result["provider_call_count"] == 1 and result["usage"]["input_tokens"] == 100
        assert repo.list_memory_records() == [] and calls == []
        outside = _candidate()
        outside.idempotency_key = "memory:test:outside-experiment"
        repo.save_memory_candidate(outside)
        body["commands"][0]["candidate_id"] = outside.candidate_id
        assert http.post(root + "/draft-jobs", json=body, headers={"Idempotency-Key": "outside"}).status_code == 409
    finally:
        application.dispatcher.stop()


def test_round_controls_are_admin_only_and_submission_is_idempotent(tmp_path):
    http, application, calls = client(tmp_path)
    root = "/api/soc/dev/corpus-workbench"
    command = {"experiment_id": "EXP-test", "selection": {"batch": "learning"}, "execution_limit": 2}
    assert http.post(root + "/rounds", json=command, headers={"test-role": "user"}).status_code == 403
    assert http.post(root + "/rounds", json=command).status_code == 400
    first = http.post(root + "/rounds", json=command, headers={"Idempotency-Key": "request-1"})
    assert first.status_code == 201, first.text
    replay = http.post(root + "/rounds", json=command, headers={"Idempotency-Key": "request-1"})
    assert replay.json()["round_id"] == first.json()["round_id"]
    assert not calls
    round_id = first.json()["round_id"]
    page = http.get(root + f"/rounds/{round_id}/items?limit=1&offset=1")
    assert page.json()["total"] == 10
    assert page.json()["items"][0]["alert_id"] == "1"
    assert http.get(root + f"/rounds/{round_id}").json()["counts"] == {"queued": 2}
    assert http.get(root + "/rounds/absent").status_code == 404
    assert http.get(root + "/experiments", headers={"test-role": "user"}).status_code == 403
    assert http.get(root + "/rounds?experiment_id=EXP-test").json()[0]["round_id"] == round_id
    brief = http.get(root + "/experiments/EXP-test/rounds?batch=learning").json()
    assert brief[0]["round_id"] == round_id
    assert set(brief[0]) == {"round_id", "batch", "state", "created_at"}
    assert http.get(root + "/experiments/EXP-test/rounds?batch=validation").json() == []
    assert not application.dispatcher.is_running
    assert http.get(root + "/experiments/EXP-test/candidates").json() == {"items": [], "total": 0}
    assert http.get(root + "/experiments/EXP-test/candidates", headers={"test-role": "user"}).status_code == 403
    assert http.get(root + "/experiments/absent/candidates").status_code == 404
    assert http.get(root + "/experiments/EXP-test/candidates?review_stage=invalid").status_code == 422


def test_start_returns_without_waiting_for_batch_and_pause_preserves_results(tmp_path):
    import time

    http, application, calls = client(tmp_path)
    root = "/api/soc/dev/corpus-workbench"
    created = http.post(root + "/rounds", json={"experiment_id": "EXP-test", "selection": {"batch": "learning"}, "execution_limit": 1}, headers={"Idempotency-Key": "run-one"}).json()
    round_id = created["round_id"]
    try:
        response = http.post(root + f"/rounds/{round_id}/start", json={})
        assert response.status_code == 202, response.text
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = http.get(root + f"/rounds/{round_id}").json()
            if result["completed_count"] == 1:
                break
            time.sleep(0.02)
        assert result["completed_count"] == 1
        assert len(calls) == 1
    finally:
        application.dispatcher.stop()


def test_round_export_reads_its_saved_result_and_rejects_other_corpus(tmp_path):
    http, application, calls = client(tmp_path)
    root = "/api/soc/dev/corpus-workbench"
    application.workbench = SimpleNamespace(batch_plan_id="a" * 64, experiment_label=lambda member, decision_available: {"available": True, "scorable": decision_available})
    created = http.post(root + "/rounds", json={"experiment_id": "EXP-test", "selection": {"batch": "learning"}, "execution_limit": 1}, headers={"Idempotency-Key": "export-one"}).json()
    from test_soc_corpus_experiments import context

    application.service.start(created["round_id"], context=context())
    application.service.execute_one(created["round_id"])
    response = http.get(root + f"/rounds/{created['round_id']}/results?limit=1")
    assert response.status_code == 200, response.text
    row = response.json()["items"][0]
    assert row["run_id"] == f"RUN-{created['round_id']}-0"
    assert row["status"] == "completed"
    assert response.json()["source_identity"] == {"sha256": "b" * 64}
    application.workbench.batch_plan_id = "c" * 64
    assert http.get(root + f"/rounds/{created['round_id']}/results").status_code == 409
    assert len(calls) == 1
