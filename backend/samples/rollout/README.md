# PI-05A Rollout Rehearsal

`pi05a_vendor_neutral_simulation.json` is an explicit simulation request for
the governed rollout control flow. It exercises virtual `shadow -> limited
pilot -> controlled rollout -> rollback` transitions and the ordered rollback
procedure without calling a Provider, broker, database mutation, feature-flag
service, Zeus API, or response action.

From `backend/`:

```bash
.venv/bin/python -m soc_agent.cli rollout rehearse --pretty
```

To save and replay the generated report:

```bash
.venv/bin/python -m soc_agent.cli rollout rehearse \
  --output .deer-flow/soc-internal-validation/pi-05a-simulation/rehearsal.json \
  --pretty

.venv/bin/python -m soc_agent.cli rollout rehearse \
  --baseline-json .deer-flow/soc-internal-validation/pi-05a-simulation/rehearsal.json \
  --pretty
```

A successful engineering rehearsal still emits zero real stage transitions
and external effects. Every real PI-01..05 gate remains open until fresh,
non-simulation evidence and accountable approval exist in the target
environment.
