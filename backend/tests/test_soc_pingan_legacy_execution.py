from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from soc_agent.contracts import (
    AnalysisRun,
    AnalysisRunStatus,
    CallbackOutboxStatus,
    Decision,
    DecisionEvidenceState,
    ProcessingJobStatus,
    ServiceRequestContext,
    SocDecisionSnapshot,
    SocDecisionTransitionKind,
    SocDecisionTransitionRecord,
    SocOperationalDisposition,
    Verdict,
)
from soc_agent.db import SqlAlchemyProcessingJobRepository, create_soc_tables
from soc_agent.integrations.pingan.legacy_compat import (
    HttpPingAnZeusAlertCallbackPort,
    HttpPingAnZeusAlertLifecyclePort,
    PingAnAlertCallbackResponseError,
    PingAnAlertLifecycleService,
    PingAnAlertLifecycleState,
    PingAnLegacyCallbackDispatcher,
    PingAnLegacyJobWorker,
    PingAnLegacyResultMapper,
    PingAnLegacyTaskRequest,
    PingAnLegacyTaskService,
    StaticPingAnZeusAlertCallbackPort,
    StaticPingAnZeusAlertLifecyclePort,
)


def _repository() -> SqlAlchemyProcessingJobRepository:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    create_soc_tables(engine)
    return SqlAlchemyProcessingJobRepository(sessionmaker(bind=engine, expire_on_commit=False))


def _request(alert_id: str = "1965449") -> PingAnLegacyTaskRequest:
    return PingAnLegacyTaskRequest(
        app_code="zeus",
        flow_id="alert_agent",
        session_id="session-1",
        alert_id=alert_id,
        alert_data={
            "alert": {
                "id": alert_id,
                "executeType": 3,
                "profileCode": "RPAADM_002631",
                "ruleCode": "RULE-REVERSE-SHELL",
            }
        },
    )


def _run(
    *,
    alert_id: str = "1965449",
    verdict: Verdict = Verdict.SUSPICIOUS,
) -> AnalysisRun:
    return AnalysisRun(
        run_id=f"RUN-{alert_id}",
        alert_id=alert_id,
        status=AnalysisRunStatus.SUCCESS,
        model_name="deepseek-v4-flash-0731",
        prompt_version="soc-analysis-v1",
        decision=Decision(
            verdict=verdict,
            confidence=0.82,
            evidence_state=DecisionEvidenceState.SUFFICIENT,
            suggested_action="转交安全运营复核",
            needs_review=False,
            reason="当前证据支持该研判结论。",
        ),
    )


def test_lifecycle_http_port_preserves_signed_get_alert_brief_contract() -> None:
    sent: dict = {}

    def signer(*, data, app_id, app_key):
        sent["signed"] = {"data": data, "app_id": app_id, "app_key": app_key}
        return {"App-Sign": "signed"}

    def handle(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["body"] = request.read().decode()
        sent["headers"] = dict(request.headers)
        return httpx.Response(200, json={"code": 200, "data": {"status": 1}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        port = HttpPingAnZeusAlertLifecyclePort(
            base_url="https://zeus.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["zeus.example.internal"],
            signer=signer,
            client=client,
        )
        result = port.query(alert_id="1965449")

    assert result == {"code": 200, "data": {"status": 1}}
    assert sent["signed"] == {
        "data": {"alertId": 1965449},
        "app_id": "SEC-MODEL",
        "app_key": "secret",
    }
    assert sent["url"] == "https://zeus.example.internal/public/getAlertBrief"
    assert sent["body"] == json.dumps({"alertId": 1965449})
    assert sent["headers"]["app-sign"] == "signed"
    assert sent["headers"]["content-type"] == "application/json"


def test_lifecycle_service_distinguishes_pending_handled_and_unknown() -> None:
    pending = PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"A-1": {"code": 200, "data": {"status": 1}}})).check("A-1")
    handled = PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"A-2": {"code": 200, "data": {"status": 5}}})).check("A-2")
    unknown = PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"A-3": httpx.ReadTimeout("internal detail must not leak")})).check("A-3")

    assert pending.state is PingAnAlertLifecycleState.PENDING
    assert pending.provider_code == "200"
    assert pending.provider_status == "1"
    assert handled.state is PingAnAlertLifecycleState.HANDLED
    assert handled.provider_code == "200"
    assert handled.provider_status == "5"
    assert "待复核" in (handled.reason or "")
    assert unknown.state is PingAnAlertLifecycleState.UNKNOWN
    assert unknown.reason == "provider_unavailable:ReadTimeout"
    assert "internal detail" not in unknown.model_dump_json()


def test_lifecycle_service_preserves_safe_business_error_diagnostics() -> None:
    result = PingAnAlertLifecycleService(
        port=StaticPingAnZeusAlertLifecyclePort(
            {
                "A-1": {
                    "code": 40100,
                    "message": "签名验证失败-private-detail",
                }
            }
        )
    ).check("A-1")

    assert result.state is PingAnAlertLifecycleState.UNKNOWN
    assert result.provider_code == "40100"
    assert result.provider_status is None
    assert result.reason == "provider_business_error"
    assert result.response_sha256 is not None
    assert "签名验证失败" not in result.model_dump_json()


def test_lifecycle_service_preserves_legacy_status_names() -> None:
    status_names = {
        0: "已忽略",
        2: "退回中",
        3: "待确认",
        4: "处理中",
        5: "待复核",
        6: "待关闭",
        7: "子单处理中",
        8: "子单已关闭",
        9: "已关闭",
        10: "编辑",
    }

    for status_code, status_name in status_names.items():
        result = PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"A-1": {"code": 200, "data": {"status": status_code}}})).check("A-1")

        assert result.state is PingAnAlertLifecycleState.HANDLED
        assert result.provider_status == str(status_code)
        assert result.reason is not None and status_name in result.reason


def test_result_mapper_uses_effective_decision_not_only_base_model_output() -> None:
    run = _run(verdict=Verdict.TRUE_POSITIVE)
    before = SocDecisionSnapshot(
        verdict=Verdict.TRUE_POSITIVE,
        confidence=0.82,
        evidence_state=DecisionEvidenceState.SUFFICIENT,
        suggested_action="转交",
        needs_review=False,
        policy_version="base-v1",
    )
    after = before.model_copy(
        update={
            "verdict": Verdict.FALSE_POSITIVE,
            "suggested_action": "忽略",
            "policy_version": "effective-v1",
        }
    )
    transition = SocDecisionTransitionRecord(
        transition_key="a" * 64,
        run_id=run.run_id,
        alert_id=run.alert_id,
        before=before,
        after=after,
        effective_disposition=SocOperationalDisposition.IGNORED,
        transition_kind=SocDecisionTransitionKind.OVERRIDDEN,
        policy_id="effective-policy",
        policy_version="1",
        policy_hash="b" * 64,
        created_by={"actor_id": "system"},
    )

    result = PingAnLegacyResultMapper().project(
        run,
        decision_transitions=[transition],
        action_executions=[],
    )

    assert result["alert_action"] == "忽略"
    assert result["evaluation"]["gen_answer"]["evaluation_action"] == "忽略"
    assert result["model_name"] == "deepseek-v4-flash-0731"
    assert result["soc_lineage"]["base_verdict"] == "true_positive"
    assert result["soc_lineage"]["effective_verdict"] == "false_positive"
    assert result["soc_lineage"]["decision_transition_id"] == transition.transition_id


class _AnalysisService:
    def __init__(self) -> None:
        self.calls: list[tuple[dict, ServiceRequestContext]] = []

    def analyze(
        self,
        payload: dict,
        *,
        context: ServiceRequestContext,
    ) -> AnalysisRun:
        self.calls.append((payload, context))
        return _run(alert_id=str(payload["alert"]["id"]))


def test_worker_runs_precheck_runtime_projection_and_callback_outbox() -> None:
    repository = _repository()
    task_service = PingAnLegacyTaskService(repository=repository)
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    task = task_service.submit(_request(), now=now)
    analyzer = _AnalysisService()
    worker = PingAnLegacyJobWorker(
        repository=repository,
        lifecycle_service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"1965449": {"code": 200, "data": {"status": 1}}})),
        analysis_service=analyzer,
        result_mapper=PingAnLegacyResultMapper(),
        worker_id="worker-1",
        lease_seconds=300,
        now=lambda: now + timedelta(seconds=1),
    )

    result = worker.run_once()

    assert result is not None
    assert result.status is ProcessingJobStatus.COMPLETED
    assert len(analyzer.calls) == 1
    assert analyzer.calls[0][1].idempotency_key == f"processing-job:{task.id}:analysis"
    assert task_service.get_status(task.id).status == "SUCCESS"
    callback = repository.claim_next_callback(
        destination="pingan.zeus.alert_callback",
        dispatcher_id="callback-1",
        lease_seconds=30,
        now=now + timedelta(seconds=2),
    )
    assert callback is not None
    assert callback.status is CallbackOutboxStatus.SENDING
    assert callback.payload["taskId"] == task.id
    assert callback.payload["status"] == "SUCCESS"
    assert callback.payload["result"]["alert_action"] == "转交"


def test_worker_skips_model_when_zeus_already_handled() -> None:
    repository = _repository()
    task_service = PingAnLegacyTaskService(repository=repository)
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    task = task_service.submit(_request(), now=now)
    analyzer = _AnalysisService()
    worker = PingAnLegacyJobWorker(
        repository=repository,
        lifecycle_service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"1965449": {"code": 200, "data": {"status": 9}}})),
        analysis_service=analyzer,
        result_mapper=PingAnLegacyResultMapper(),
        worker_id="worker-1",
        lease_seconds=300,
        now=lambda: now + timedelta(seconds=1),
    )

    result = worker.run_once()

    assert result is not None
    assert result.status is ProcessingJobStatus.SKIPPED_EXTERNAL_HANDLED
    assert analyzer.calls == []
    response = task_service.get_status(task.id)
    assert response.status == "SUCCESS"
    assert response.result["alert_action"] == "已介入"


def test_worker_projects_expired_alert_and_enqueues_success_callback() -> None:
    repository = _repository()
    task_service = PingAnLegacyTaskService(
        repository=repository,
        queue_ttl_seconds=5,
    )
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    task = task_service.submit(_request(), now=now)
    analyzer = _AnalysisService()
    worker = PingAnLegacyJobWorker(
        repository=repository,
        lifecycle_service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({})),
        analysis_service=analyzer,
        result_mapper=PingAnLegacyResultMapper(),
        worker_id="worker-1",
        lease_seconds=300,
        now=lambda: now + timedelta(seconds=6),
    )

    result = worker.run_once()

    assert result is not None
    assert result.status is ProcessingJobStatus.EXPIRED_BEFORE_ANALYSIS
    assert analyzer.calls == []
    response = task_service.get_status(task.id)
    assert response.status == "SUCCESS"
    assert response.result["alert_action"] == "过期"
    callback = repository.claim_next_callback(
        destination="pingan.zeus.alert_callback",
        dispatcher_id="callback-1",
        lease_seconds=30,
        now=now + timedelta(seconds=7),
    )
    assert callback is not None
    assert callback.payload["status"] == "SUCCESS"
    assert callback.payload["result"]["alert_action"] == "过期"
    assert [event.event_type for event in repository.list_events(task.id)] == [
        "submitted",
        "claimed",
        "queue_deadline_expired",
    ]


def test_worker_uses_read_only_analysis_when_external_lifecycle_is_unknown() -> None:
    repository = _repository()
    task_service = PingAnLegacyTaskService(repository=repository)
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    task_service.submit(_request(), now=now)
    action_capable = _AnalysisService()
    read_only = _AnalysisService()
    worker = PingAnLegacyJobWorker(
        repository=repository,
        lifecycle_service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"1965449": httpx.ReadTimeout("unavailable")})),
        analysis_service=action_capable,
        unknown_lifecycle_analysis_service=read_only,
        result_mapper=PingAnLegacyResultMapper(),
        worker_id="worker-1",
        lease_seconds=300,
        now=lambda: now + timedelta(seconds=1),
    )

    result = worker.run_once()

    assert result is not None
    assert result.status is ProcessingJobStatus.COMPLETED
    assert action_capable.calls == []
    assert len(read_only.calls) == 1
    assert result.result_payload["soc_lineage"]["external_lifecycle_state"] == "unknown"


def test_callback_http_port_preserves_signed_alert_model_callback_contract() -> None:
    sent: dict = {}
    payload = {
        "taskId": "JOB-1",
        "status": "SUCCESS",
        "result": {"alert_action": "转交"},
    }

    def signer(*, data, app_id, app_key):
        sent["signed"] = {"data": data, "app_id": app_id, "app_key": app_key}
        return {"App-Sign": "signed"}

    def handle(request: httpx.Request) -> httpx.Response:
        sent["url"] = str(request.url)
        sent["headers"] = dict(request.headers)
        sent["body"] = request.read().decode()
        return httpx.Response(200, json={"code": 200, "message": "ok"})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        port = HttpPingAnZeusAlertCallbackPort(
            base_url="https://zeus.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["zeus.example.internal"],
            signer=signer,
            client=client,
        )
        metadata = port.send(payload)

    assert sent["signed"] == {
        "data": payload,
        "app_id": "SEC-MODEL",
        "app_key": "secret",
    }
    assert sent["url"] == "https://zeus.example.internal/public/alertModelCallback"
    assert sent["body"] == json.dumps(payload)
    assert sent["headers"]["app-sign"] == "signed"
    assert sent["headers"]["content-type"] == "application/json"
    assert metadata["http_status"] == 200
    assert metadata["provider_code"] == "200"
    assert metadata["mocked"] is False
    assert "message" not in metadata


def test_callback_http_port_exposes_safe_business_error_metadata() -> None:
    payload = {
        "taskId": "JOB-1",
        "status": "SUCCESS",
        "result": {"alert_action": "转交"},
    }

    def handle(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 40100, "message": "签名验证失败-private-detail"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        port = HttpPingAnZeusAlertCallbackPort(
            base_url="https://zeus.example.internal",
            app_id="SEC-MODEL",
            app_key="secret",
            allowed_hosts=["zeus.example.internal"],
            signer=lambda **_kwargs: {"App-Sign": "signed"},
            client=client,
        )
        with pytest.raises(PingAnAlertCallbackResponseError) as error:
            port.send(payload)

    assert error.value.response_metadata["http_status"] == 200
    assert error.value.response_metadata["provider_code"] == "40100"
    assert error.value.response_metadata["mocked"] is False
    assert len(error.value.response_metadata["response_sha256"]) == 64
    assert "签名验证失败" not in str(error.value)


def test_callback_failure_retries_only_outbox_and_never_repeats_analysis() -> None:
    repository = _repository()
    task_service = PingAnLegacyTaskService(repository=repository)
    now = datetime(2026, 9, 1, 3, 0, tzinfo=UTC)
    task = task_service.submit(_request(), now=now)
    analyzer = _AnalysisService()
    worker = PingAnLegacyJobWorker(
        repository=repository,
        lifecycle_service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"1965449": {"code": 200, "data": {"status": 1}}})),
        analysis_service=analyzer,
        result_mapper=PingAnLegacyResultMapper(),
        worker_id="worker-1",
        lease_seconds=300,
        now=lambda: now + timedelta(seconds=1),
    )
    completed = worker.run_once()
    assert completed is not None
    transport = StaticPingAnZeusAlertCallbackPort([httpx.ReadTimeout("private detail"), {"code": 200}])
    clock_values = iter(
        [
            now + timedelta(seconds=2),
            now + timedelta(seconds=3),
            now + timedelta(seconds=40),
            now + timedelta(seconds=41),
        ]
    )
    dispatcher = PingAnLegacyCallbackDispatcher(
        repository=repository,
        port=transport,
        dispatcher_id="callback-1",
        lease_seconds=30,
        retry_backoff_seconds=30,
        now=lambda: next(clock_values),
    )

    retrying = dispatcher.run_once()
    delivered = dispatcher.run_once()

    assert retrying is not None
    assert retrying.status is CallbackOutboxStatus.RETRY_WAIT
    assert retrying.last_error_code == "ReadTimeout"
    assert "private detail" not in (retrying.last_error_message or "")
    assert delivered is not None
    assert delivered.status is CallbackOutboxStatus.DELIVERED
    assert len(transport.calls) == 2
    assert len(analyzer.calls) == 1
    assert repository.get(task.id).status is ProcessingJobStatus.COMPLETED


def test_callback_failure_persists_safe_provider_metadata() -> None:
    repository = _repository()
    task_service = PingAnLegacyTaskService(repository=repository)
    now = datetime(2026, 9, 2, 3, 0, tzinfo=UTC)
    task = task_service.submit(_request(), now=now)
    worker = PingAnLegacyJobWorker(
        repository=repository,
        lifecycle_service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"1965449": {"code": 200, "data": {"status": 1}}})),
        analysis_service=_AnalysisService(),
        result_mapper=PingAnLegacyResultMapper(),
        worker_id="worker-1",
        lease_seconds=300,
        now=lambda: now + timedelta(seconds=1),
    )
    completed = worker.run_once()
    assert completed is not None
    metadata = {
        "http_status": 200,
        "provider_code": "40100",
        "response_sha256": "a" * 64,
        "mocked": False,
    }
    unsafe_metadata = {**metadata, "private_message": "do-not-persist"}
    dispatcher = PingAnLegacyCallbackDispatcher(
        repository=repository,
        port=StaticPingAnZeusAlertCallbackPort(
            [
                PingAnAlertCallbackResponseError(
                    "ZEUS callback returned a non-success business code",
                    response_metadata=unsafe_metadata,
                )
            ]
        ),
        dispatcher_id="callback-1",
        lease_seconds=30,
        max_attempts=1,
        retry_backoff_seconds=0,
        now=iter(
            [
                now + timedelta(seconds=2),
                now + timedelta(seconds=3),
            ]
        ).__next__,
    )

    failed = dispatcher.run_once()

    assert failed is not None
    assert failed.status is CallbackOutboxStatus.DEAD_LETTER
    assert failed.response_metadata == metadata
    assert "private_message" not in failed.response_metadata
    attempts = repository.list_callback_attempts(failed.outbox_id)
    assert len(attempts) == 1
    assert attempts[0].response_metadata == metadata
    assert repository.get(task.id).status is ProcessingJobStatus.COMPLETED
