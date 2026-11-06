"""Offline wire and trust-boundary tests for the opt-in Trace client."""

from __future__ import annotations

import json
from email.parser import BytesParser
from email.policy import default

import httpx
import pytest

from soc_agent.integrations.trace_agent import TraceClient, TraceClientError, TraceClientSettings

TASK = "11111111-1111-4111-8111-111111111111"
ALERT = "synthetic-alert-7"
SECRET = "synthetic-private-trace-key"


def settings(**changes):
    return TraceClientSettings(enabled=True, base_url="https://trace.example.internal/v1", api_key=SECRET, **changes)


def snapshot(status="SUCCESS", **changes):
    result = dict(alert_id=ALERT, report=None, verdict=None, verdict_reason=None, riskLevel=None, completeness=None, confidence=None)
    if status == "SUCCESS":
        result.update(report="# 合成调查报告", verdict="待核实", verdict_reason="合成环境未提供授权证明。", riskLevel="", completeness=0.5, confidence=0.8)
    body = {"taskId": TASK, "status": status, "result": result}
    body.update(changes)
    return body


@pytest.mark.asyncio
async def test_disabled_client_makes_no_requests_for_all_five_operations():
    requests = []
    async with TraceClient(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        calls = (
            lambda: client.create(task_id=TASK, expected_alert_id=ALERT, alert={"alertId": ALERT}, request_id="create-1"),
            lambda: client.get(TASK, expected_alert_id=ALERT),
            lambda: client.resume(TASK, expected_alert_id=ALERT, request_id="resume-1", expected_revision=1),
            lambda: client.cancel(TASK, expected_alert_id=ALERT, request_id="cancel-1", expected_revision=1),
            lambda: client.health(),
        )
        for call in calls:
            with pytest.raises(TraceClientError, match="disabled"):
                await call()
    assert requests == []


@pytest.mark.asyncio
async def test_create_preserves_original_alert_and_explicit_question_order():
    requests = []
    original = {"alert": {"alertId": ALERT, "event_content": "原文  中的空格不能丢"}, "metadata": {"synthetic": True}}
    original_json = json.dumps(original, ensure_ascii=False)

    def handle(request):
        requests.append(request)
        return httpx.Response(202, json={"taskId": TASK, "status": "PENDING"}, headers={"X-Trace-Revision": "1"})

    async with TraceClient(settings(), transport=httpx.MockTransport(handle)) as client:
        reply = await client.create(task_id=TASK, expected_alert_id=ALERT, alert=original, evidence_gaps="  核查授权记录  \n\n核查前后登录\n", request_id="create-stable")
    assert reply.revision == 1 and reply.data.status.value == "PENDING"
    assert reply.alert_identity_verified is False  # An ACK does not contain alert identity.
    assert json.dumps(original, ensure_ascii=False) == original_json
    (request,) = requests
    assert str(request.url) == "https://trace.example.internal/v1/traces"
    assert request.headers["X-API-Key"] == SECRET
    assert request.headers["X-Request-Id"] == "create-stable"
    mime = BytesParser(policy=default).parsebytes(b"Content-Type: " + request.headers["Content-Type"].encode() + b"\r\n\r\n" + request.content)
    parts = {part.get_param("name", header="content-disposition"): part.get_payload(decode=True) for part in mime.iter_parts()}
    assert json.loads(parts["file"]) == original
    assert parts["task_id"].decode() == TASK
    assert parts["evidence_gaps"].decode() == "核查授权记录\n核查前后登录"


@pytest.mark.asyncio
async def test_empty_questions_are_allowed_for_an_explicit_sample_request():
    def handle(request):
        assert b'name="evidence_gaps"' not in request.content
        return httpx.Response(200, json={"taskId": TASK, "status": "SUCCESS"}, headers={"X-Trace-Revision": "3"})

    async with TraceClient(settings(), transport=httpx.MockTransport(handle)) as client:
        reply = await client.create(task_id=TASK, expected_alert_id=ALERT, alert={"alertId": ALERT}, request_id="sample-1")
    assert reply.data.status.value == "SUCCESS"  # Existing task reuse is not a new dispatch.


@pytest.mark.asyncio
async def test_get_checks_both_identities_and_accepts_valid_failed_snapshots():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=snapshot("FAILED", errorCode="TRACE_EXECUTION_FAILED"), headers={"X-Trace-Revision": "9"})

    async with TraceClient(settings(), transport=httpx.MockTransport(handle)) as client:
        reply = await client.get(TASK, expected_alert_id=ALERT)
    assert reply.revision == 9 and reply.alert_identity_verified is True
    assert reply.data.status.value == "FAILED" and reply.data.result.report is None
    assert requests[0].url.path.endswith("/traces/" + TASK)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["wrong_task", "wrong_alert", "bad_state", "missing_reason", "bad_score", "null_result", "missing_revision", "bad_revision", "invalid_json"])
async def test_invalid_snapshot_never_becomes_a_usable_response(kind):
    body = snapshot()
    headers = {"X-Trace-Revision": "2"}
    if kind == "wrong_task":
        body["taskId"] = "different-task"
    elif kind == "wrong_alert":
        body["result"]["alert_id"] = "different-alert"
    elif kind == "bad_state":
        body["status"] = "resumable"
    elif kind == "missing_reason":
        body["result"]["verdict_reason"] = ""
    elif kind == "bad_score":
        body["result"]["confidence"] = 1.01
    elif kind == "null_result":
        body["result"] = None
    elif kind == "missing_revision":
        headers.clear()
    elif kind == "bad_revision":
        headers["X-Trace-Revision"] = "0"
    response = httpx.Response(200, content=b"{not-json" if kind == "invalid_json" else json.dumps(body).encode(), headers=headers)
    calls = []

    def handle(request):
        calls.append(request)
        return response

    async with TraceClient(settings(), transport=httpx.MockTransport(handle)) as client:
        with pytest.raises(TraceClientError):
            await client.get(TASK, expected_alert_id=ALERT)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("http_status", [200, 202])
async def test_repeated_operations_keep_request_id_and_expected_revision_without_automatic_get(http_status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(http_status, json={"taskId": TASK, "status": "RETRYING"}, headers={"X-Trace-Revision": "8"})

    async with TraceClient(settings(), transport=httpx.MockTransport(handle)) as client:
        for _ in range(2):
            reply = await client.resume(TASK, expected_alert_id=ALERT, request_id="resume-stable", expected_revision=7)
            assert not reply.alert_identity_verified
        await client.cancel(TASK, expected_alert_id=ALERT, request_id="cancel-stable", expected_revision=8)
    assert [r.method for r in requests] == ["POST", "POST", "POST"]
    assert [r.url.path for r in requests] == [f"/v1/traces/{TASK}/resume", f"/v1/traces/{TASK}/resume", f"/v1/traces/{TASK}/cancel"]
    assert [r.headers["X-Request-Id"] for r in requests] == ["resume-stable", "resume-stable", "cancel-stable"]
    assert [r.headers["X-Expected-Trace-Revision"] for r in requests] == ["7", "7", "8"]


@pytest.mark.asyncio
async def test_full_operation_response_also_checks_alert_identity():
    async with TraceClient(settings(), transport=httpx.MockTransport(lambda request: httpx.Response(200, json=snapshot(), headers={"X-Trace-Revision": "10"}))) as client:
        reply = await client.cancel(TASK, expected_alert_id=ALERT, request_id="cancel-done", expected_revision=9)
    assert reply.alert_identity_verified is True
    assert reply.data.result.report


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["redirect", "http", "timeout", "transport", "oversize"])
async def test_failures_are_bounded_never_retry_and_do_not_expose_response_or_credentials(failure):
    calls = []

    def handle(request):
        calls.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(SECRET, request=request)
        if failure == "transport":
            raise httpx.ConnectError(SECRET, request=request)
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "https://other.example.invalid/"}, text=SECRET)
        if failure == "oversize":
            return httpx.Response(200, content=(SECRET * 100).encode())
        return httpx.Response(409, text=SECRET)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as injected:
        async with TraceClient(settings(max_response_bytes=128), client=injected) as client:
            with pytest.raises(TraceClientError) as error:
                await client.get(TASK, expected_alert_id=ALERT)
            assert SECRET not in str(error.value)
            assert SECRET not in repr(error.value)
        assert not injected.is_closed
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_health_uses_business_status_and_full_flag_without_claiming_ready():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "degraded", "database": "down", "broker": "down", "worker": "down"})

    async with TraceClient(settings(), transport=httpx.MockTransport(handle)) as client:
        health = await client.health(full=True)
    assert health.status == "degraded"
    assert requests[0].url.params["full"] == "true"
    assert requests[0].url.path == "/v1/health"


@pytest.mark.asyncio
async def test_invalid_create_identity_or_operation_headers_do_not_send():
    calls = []
    async with TraceClient(settings(), transport=httpx.MockTransport(lambda request: calls.append(request))) as client:
        for arguments in (
            {"task_id": "../escape", "expected_alert_id": ALERT, "alert": {"alertId": ALERT}, "request_id": "op"},
            {"task_id": TASK, "expected_alert_id": ALERT, "alert": {"alertId": "other"}, "request_id": "op"},
            {"task_id": TASK, "expected_alert_id": ALERT, "alert": {"alertId": ALERT}, "request_id": "bad\r\nheader"},
            {"task_id": TASK, "expected_alert_id": ALERT, "alert": {"alertId": ALERT}, "request_id": None},
        ):
            with pytest.raises(TraceClientError):
                await client.create(**arguments)
        for operation in (client.resume, client.cancel):
            with pytest.raises(TraceClientError, match="invalid_request_id"):
                await operation(TASK, expected_alert_id=ALERT, request_id=None, expected_revision=1)
    assert calls == []


def test_configuration_is_explicit_opt_in_and_hides_key_in_representation(monkeypatch):
    monkeypatch.setenv("SOC_TRACE_AGENT_ENABLED", "true")
    assert TraceClientSettings().enabled is False
    configured = TraceClientSettings.from_env({"SOC_TRACE_AGENT_ENABLED": "true", "SOC_TRACE_AGENT_BASE_URL": "http://127.0.0.1:8010", "SOC_TRACE_AGENT_API_KEY": SECRET})
    assert configured.enabled is True and SECRET not in repr(configured)
    assert TraceClientSettings.from_env({}).enabled is False
    with pytest.raises(ValueError):
        TraceClientSettings.from_env({"SOC_TRACE_AGENT_ENABLED": "maybe"})


@pytest.mark.parametrize("base_url", [" https://trace.example.internal", "https://trace.example.internal/\n", "https://trace.example.internal/\tpath"])
def test_base_url_rejects_whitespace_or_control_characters_without_normalizing_them(base_url):
    with pytest.raises(ValueError):
        TraceClientSettings(enabled=True, base_url=base_url, api_key=SECRET)
