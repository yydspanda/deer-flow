# Trace investigation development boundary

This independently importable package contains shared contracts, an async
`TraceEngine` protocol and an authenticated HTTP shell. It does **not** include
the legacy Trace Agent, an investigation worker, an in-memory production engine,
model clients, Celery/Redis state, or a ZEUS callback sender.

Use the project environment, or install this package independently, then run:

```bash
python -m trace_agent --help
python -m trace_agent check
python -m trace_agent serve
```

`serve` requires `TRACE_AGENT_API_KEY`, binds `127.0.0.1:8300` by default, and
accepts `--host` and `--port` (or `TRACE_AGENT_PORT`). Without an engine factory,
it intentionally reports `GET /health` as `degraded/not_ready` and rejects work
with HTTP 503. `check` exits nonzero without both key and factory configuration;
with them, it reports **configured**, not worker readiness. It never imports or
executes the configured factory.

A trusted operator can set
`TRACE_AGENT_ENGINE_FACTORY=trace_agent.implementation:create_engine`. Only
`serve` imports and calls this synchronous, no-argument factory. The factory
returns a real object implementing the async methods in `TraceEngine`. No
`implementation` module or fake factory is shipped. The implementer owns
worker/resource startup, shutdown, readiness, persistent state and recovery;
Uvicorn does not automatically supervise arbitrary subprocesses created by it.
Alternatively, use `create_app(engine=..., api_key=...)` in a dedicated entry.

The five service routes are create/get/resume/cancel and health. Except for
public health, every request is authenticated with `X-API-Key` before body
validation or engine dispatch. Create uses multipart fields `file`, `task_id`
and optional `evidence_gaps`, plus `X-Request-Id`. Resume/cancel require that
operation id and `X-Expected-Trace-Revision` headers. Business responses carry
the engine's `X-Trace-Revision`; the shell never fabricates revisions or tasks.

The real engine must preserve the upstream task id and bound alert identity,
deduplicate canonical input and operations durably, read body/revision from one
snapshot, and fence old executions after cancellation or recovery. The shell
checks representations and does not claim to implement these storage semantics.
It is not mounted into Gateway by default. Tests keep lifecycle fakes exclusively
under `backend/tests/test_trace_agent_*.py`.

Implementation handoff and test commands are in
[`docs/soc-trace-agent-development.md`](../../../docs/soc-trace-agent-development.md).
The package must not import SOC or DeerFlow application modules; a real engine
is supplied by the Trace implementation owner through the protocol above.
Alert JSON with duplicate keys at any depth or non-finite values is rejected
before engine admission. Internal `TraceCreateRequest.evidence_gaps` values are
already normalized question lines, not raw multi-line form input.
