# SOC to Trace boundary

This module is opt-in client and copy-only handoff code. Importing it must not read
environment/configuration, start processes, connect to Trace, request a model,
enqueue investigation, or send callbacks. Pass explicit settings or an environment
mapping; `SOC_TRACE_AGENT_ENABLED` defaults off and is not an automatic-dispatch flag.

Use `trace_agent.contracts` from the shared backend uv workspace. HTTP calls make
one attempt, never follow redirects, bound response bytes and validate response
identity/version. Errors expose only operation/code/status, never provider bodies,
credentials or raw headers. An operation ACK cannot verify alert identity; callers
must obtain a validated full GET snapshot before adopting a report. No poller,
durable task coordinator or ZEUS outbox belongs in this scaffold.

Handoff inputs come from authenticated shared services: explicit business task,
tenant/environment/Run binding, saved original input and current effective outcome.
Never infer public task identity from run_id or alert_id. Preserve old Base answers
for audit; do not revive superseded gaps or treat all manual_checks as Trace work.
Questions must be explicitly selected and retain current-advice/operator provenance;
an empty list supports explicit sampling. Do not add keyword routing or model calls.

Tests under `backend/tests/test_soc_trace_*.py` use only synthetic data and
httpx MockTransport or the contract-only ASGI service. Run those tests after edits;
live investigation and ZEUS acceptance are separate, explicitly authorized work.
