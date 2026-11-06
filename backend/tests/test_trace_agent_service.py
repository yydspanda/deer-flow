"""The development HTTP shell admits validated work but never invents an engine."""

import json

import pytest
from fastapi.testclient import TestClient
from trace_agent.contracts import TraceCreateResponse, TraceHealth, TraceResult, TraceStatus, TraceTaskResponse
from trace_agent.engine import TraceConflict, TraceNotFound, TraceReply
from trace_agent.service import create_app

KEY = "test-only-trace-key"


class FakeEngine:
    """Test-only task store; no fake investigation implementation ships in the package."""

    def __init__(self):
        self.inputs = {}
        self.tasks = {}
        self.revisions = {}
        self.operations = {}
        self.dispatches = 0

    async def create(self, request):
        old = self.inputs.get(request.task_id)
        if old is not None and old != request:
            # Operation ids identify transport operations, not alert content.
            if (old.alert, old.evidence_gaps) != (request.alert, request.evidence_gaps):
                raise TraceConflict()
        created = old is None
        if created:
            self.inputs[request.task_id] = request
            self.tasks[request.task_id] = TraceTaskResponse(taskId=request.task_id, status="PENDING", result=TraceResult(alert_id=request.alert_id))
            self.revisions[request.task_id] = 1
            self.dispatches += 1
        return self.ack(request.task_id, created=created)

    def ack(self, task_id, *, created=False):
        task = self.tasks[task_id]
        return TraceReply(TraceCreateResponse(taskId=task_id, status=task.status), self.revisions[task_id], created=created)

    async def get(self, task_id):
        if task_id not in self.tasks:
            raise TraceNotFound()
        return TraceReply(self.tasks[task_id], self.revisions[task_id])

    async def resume(self, task_id, operation):
        await self.get(task_id)
        key = (task_id, operation.operation_id)
        if key in self.operations:
            if self.operations[key] != ("resume", operation.expected_version):
                raise TraceConflict()
            return self.ack(task_id)
        if self.revisions[task_id] != operation.expected_version or self.tasks[task_id].status != TraceStatus.FAILED:
            raise TraceConflict()
        self.operations[key] = ("resume", operation.expected_version)
        self.tasks[task_id] = self.tasks[task_id].model_copy(update={"status": TraceStatus.RETRYING})
        self.revisions[task_id] += 1
        self.dispatches += 1
        return self.ack(task_id)

    async def cancel(self, task_id, operation):
        await self.get(task_id)
        key = (task_id, operation.operation_id)
        if key in self.operations:
            if self.operations[key] != ("cancel", operation.expected_version):
                raise TraceConflict()
            return self.ack(task_id)
        if self.revisions[task_id] != operation.expected_version or self.tasks[task_id].status in {TraceStatus.SUCCESS, TraceStatus.FAILED}:
            raise TraceConflict()
        self.operations[key] = ("cancel", operation.expected_version)
        self.tasks[task_id] = self.tasks[task_id].model_copy(update={"status": TraceStatus.CANCELLED})
        self.revisions[task_id] += 1
        return self.ack(task_id)

    async def health(self):
        return TraceHealth(status="ok", worker={"status": "test_only"})


def create(client, *, key=KEY, task="task-1", alert=None, gaps="first?\n\n second?", operation="submit-1"):
    return client.post("/traces", headers={"X-API-Key": key, "X-Request-Id": operation}, data={"task_id": task, "evidence_gaps": gaps}, files={"file": ("alert.json", json.dumps(alert or {"alertId": "42"}).encode(), "application/json")})


def test_unavailable_default_never_starts_worker_or_persists(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with TestClient(create_app(api_key=KEY)) as client:
        assert client.get("/health").json()["status"] == "degraded"
        assert create(client).status_code == 503
        assert client.get("/traces/task-1", headers={"X-API-Key": KEY}).status_code == 503
    assert list(tmp_path.iterdir()) == []


def test_unconfigured_authentication_cannot_report_ready_even_with_an_injected_engine():
    with TestClient(create_app(engine=FakeEngine())) as client:
        assert client.get("/health").json()["status"] == "degraded"
        assert create(client).status_code == 503


def test_authentication_precedes_validation_and_every_repeated_request():
    engine = FakeEngine()
    with TestClient(create_app(engine=engine, api_key=KEY)) as client:
        assert create(client, key="wrong-key").status_code == 401
        assert engine.dispatches == 0
        response = create(client)
        assert response.status_code == 202
        assert response.json() == {"taskId": "task-1", "status": "PENDING"}
        assert response.headers["X-Trace-Revision"] == "1"
        assert create(client, key="wrong-key").status_code == 401
        assert engine.dispatches == 1
        assert client.get("/traces/task-1").status_code == 401
        assert client.get("/openapi.json").status_code == 401
        malformed = client.post("/traces", content=b"invalid multipart body", headers={"X-API-Key": "wrong-key", "Content-Type": "multipart/form-data; boundary=broken"})
        assert malformed.status_code == 401


def test_create_reuses_current_status_and_rejects_changed_input():
    engine = FakeEngine()
    with TestClient(create_app(engine=engine, api_key=KEY)) as client:
        assert create(client).status_code == 202
        assert create(client, operation="submit-2").status_code == 200
        assert create(client, alert={"alertId": "43"}).status_code == 409
        assert engine.dispatches == 1
        engine.tasks["task-1"] = TraceTaskResponse(taskId="task-1", status="SUCCESS", result=TraceResult(alert_id="42", report="# report", verdict="待核实", verdict_reason="还缺授权证据。", riskLevel="", completeness=1, confidence=0.5))
        engine.revisions["task-1"] = 2
        assert create(client).json()["status"] == "SUCCESS"
        response = client.get("/traces/task-1", headers={"X-API-Key": KEY})
        assert response.headers["X-Trace-Revision"] == "2"
        assert response.json()["result"]["alert_id"] == "42"


def test_non_success_get_preserves_nulls_and_identity():
    with TestClient(create_app(engine=FakeEngine(), api_key=KEY)) as client:
        create(client)
        response = client.get("/traces/task-1", headers={"X-API-Key": KEY})
        assert response.json()["result"] == dict(alert_id="42", report=None, verdict=None, verdict_reason=None, riskLevel=None, completeness=None, confidence=None)
        assert client.get("/traces/unknown", headers={"X-API-Key": KEY}).status_code == 404


def test_resume_operation_deduplicates_across_later_failure_and_cancel_closes_result():
    engine = FakeEngine()
    with TestClient(create_app(engine=engine, api_key=KEY)) as client:
        create(client)
        engine.tasks["task-1"] = engine.tasks["task-1"].model_copy(update={"status": TraceStatus.FAILED})
        engine.revisions["task-1"] = 2
        headers = {"X-API-Key": KEY, "X-Request-Id": "resume-1", "X-Expected-Trace-Revision": "2"}
        assert client.post("/traces/task-1/resume", headers=headers).headers["X-Trace-Revision"] == "3"
        engine.tasks["task-1"] = engine.tasks["task-1"].model_copy(update={"status": TraceStatus.FAILED})
        engine.revisions["task-1"] = 4
        assert client.post("/traces/task-1/resume", headers=headers).json()["status"] == "FAILED"
        assert engine.dispatches == 2
        headers["X-Request-Id"] = "resume-2"
        assert client.post("/traces/task-1/resume", headers=headers).status_code == 409
        headers["X-Expected-Trace-Revision"] = "4"
        assert client.post("/traces/task-1/resume", headers=headers).status_code == 202
        headers.update({"X-Request-Id": "cancel-1", "X-Expected-Trace-Revision": "5"})
        assert client.post("/traces/task-1/cancel", headers=headers).json()["status"] == "CANCELLED"
        assert client.post("/traces/task-1/cancel", headers=headers).headers["X-Trace-Revision"] == "6"


@pytest.mark.parametrize("alert", [{"foo": "bar"}, {"alertId": "42", "alert": {"alertId": "43"}}])
def test_invalid_alert_identity_never_reaches_engine(alert):
    engine = FakeEngine()
    with TestClient(create_app(engine=engine, api_key=KEY)) as client:
        assert create(client, alert=alert).status_code == 422
    assert engine.dispatches == 0


@pytest.mark.parametrize(
    "raw",
    [
        b'{"alertId":"first","alertId":"second"}',
        b'{"alert":{"alertId":"first","alertId":"second"}}',
        b'{"alertId":"42","detail":{"value":1,"value":2}}',
        b'{"alertId":"42","value":NaN}',
        b'{"alertId":"42","value":Infinity}',
        b'{"alertId":"42","value":-Infinity}',
    ],
)
def test_ambiguous_or_nonfinite_alert_json_never_reaches_engine(raw):
    engine = FakeEngine()
    with TestClient(create_app(engine=engine, api_key=KEY)) as client:
        response = client.post(
            "/traces",
            headers={"X-API-Key": KEY, "X-Request-Id": "submit-1"},
            data={"task_id": "task-1"},
            files={"file": ("alert.json", raw, "application/json")},
        )
        assert response.status_code == 422
    assert engine.dispatches == 0


def test_engine_exception_does_not_echo_credentials_or_payload():
    class Broken(FakeEngine):
        async def get(self, task_id):
            raise RuntimeError(f"private data with {KEY}")

    with TestClient(create_app(engine=Broken(), api_key=KEY)) as client:
        response = client.get("/traces/task-1", headers={"X-API-Key": KEY})
        assert response.status_code == 502
        assert KEY not in response.text


def test_untrusted_engine_snapshot_is_revalidated_and_keeps_requested_task_identity():
    class Invalid(FakeEngine):
        async def get(self, task_id):
            body = TraceTaskResponse.model_construct(taskId="other-task", status="SUCCESS", result=TraceResult(alert_id="42"))
            return TraceReply(body, 1)

    with TestClient(create_app(engine=Invalid(), api_key=KEY)) as client:
        assert client.get("/traces/task-1", headers={"X-API-Key": KEY}).status_code == 502
