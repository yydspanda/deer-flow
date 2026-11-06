"""Synthetic, in-process SOC -> Trace wire acceptance; no deployed fake engine."""

from __future__ import annotations

import httpx
import pytest
from trace_agent.contracts import TraceCreateResponse, TraceHealth, TraceResult, TraceTaskResponse
from trace_agent.engine import TraceReply
from trace_agent.service import create_app

from soc_agent.integrations.trace_agent import TraceClient, TraceClientError, TraceClientSettings

TASK = "synthetic-task-1"
ALERT = "synthetic-alert-1"
KEY = "offline-test-key"


class RecordingEngine:
    """Return explicit test snapshots; does not emulate persistence or investigation."""

    def __init__(self):
        self.calls = []
        self.status = "PENDING"
        self.revision = 1

    async def create(self, request):
        self.calls.append(request)
        return TraceReply(TraceCreateResponse(taskId=request.task_id, status=self.status), self.revision, created=True)

    async def get(self, task_id):
        result = TraceResult(alert_id=ALERT)
        if self.status == "SUCCESS":
            result = TraceResult(alert_id=ALERT, report="# 合成测试报告", verdict="待核实", verdict_reason="测试没有提供实际日志。", riskLevel="", completeness=0.5, confidence=0.5)
        return TraceReply(TraceTaskResponse(taskId=task_id, status=self.status, result=result), self.revision)

    async def resume(self, task_id, operation):
        self.calls.append(operation)
        self.status = "RETRYING"
        self.revision += 1
        return TraceReply(TraceCreateResponse(taskId=task_id, status=self.status), self.revision)

    async def cancel(self, task_id, operation):
        self.calls.append(operation)
        self.status = "CANCELLED"
        self.revision += 1
        return TraceReply(TraceCreateResponse(taskId=task_id, status=self.status), self.revision)

    async def health(self):
        return TraceHealth(status="ok")


@pytest.mark.asyncio
async def test_soc_client_and_trace_shell_share_the_complete_wire_contract():
    engine = RecordingEngine()
    transport = httpx.ASGITransport(app=create_app(engine=engine, api_key=KEY))
    settings = TraceClientSettings(enabled=True, base_url="http://trace.test", api_key=KEY)
    async with TraceClient(settings, transport=transport) as client:
        health = await client.health()
        assert health.status == "ok"
        ack = await client.create(task_id=TASK, expected_alert_id=ALERT, alert={"alertId": ALERT}, evidence_gaps="核查已有的登录记录", request_id="create-1")
        assert ack.revision == 1
        pending = await client.get(TASK, expected_alert_id=ALERT)
        assert pending.data.result.report is None
        assert pending.alert_identity_verified
        engine.status = "SUCCESS"
        engine.revision = 2
        success = await client.get(TASK, expected_alert_id=ALERT)
        assert success.data.result.verdict == "待核实"
        assert success.revision == 2
        engine.status = "FAILED"
        engine.revision = 3
        retry = await client.resume(TASK, expected_alert_id=ALERT, request_id="resume-1", expected_revision=3)
        assert retry.data.status == "RETRYING"
        cancelled = await client.cancel(TASK, expected_alert_id=ALERT, request_id="cancel-1", expected_revision=4)
        assert cancelled.data.status == "CANCELLED"
        assert (await client.get(TASK, expected_alert_id=ALERT)).data.result.report is None
    assert engine.calls[0].evidence_gaps == ["核查已有的登录记录"]
    assert engine.calls[1].expected_version == 3
    assert engine.calls[2].operation_id == "cancel-1"


@pytest.mark.asyncio
async def test_auth_failure_never_reaches_engine():
    engine = RecordingEngine()
    settings = TraceClientSettings(enabled=True, base_url="http://trace.test", api_key="wrong-key")
    async with TraceClient(settings, transport=httpx.ASGITransport(app=create_app(engine=engine, api_key=KEY))) as client:
        with pytest.raises(TraceClientError):
            await client.create(task_id=TASK, expected_alert_id=ALERT, alert={"alertId": ALERT}, request_id="create-1")
    assert engine.calls == []


@pytest.mark.asyncio
async def test_unconnected_default_shell_is_degraded_and_cannot_accept_work():
    settings = TraceClientSettings(enabled=True, base_url="http://trace.test", api_key=KEY)
    async with TraceClient(settings, transport=httpx.ASGITransport(app=create_app(api_key=KEY))) as client:
        assert (await client.health()).status == "degraded"
        with pytest.raises(TraceClientError):
            await client.create(task_id=TASK, expected_alert_id=ALERT, alert={"alertId": ALERT}, request_id="create-1")
