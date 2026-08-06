# PI-05 Rollout Simulation

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

## PI-05B Simulation Completion Gate

`pi05b_local_simulation.json` points to the Git-ignored reports produced by
PI-01E, PI-03B/C, PI-04 and PI-05A. Relative paths are resolved from this
request file, so the command works independently of the caller's current
directory. Missing, malformed or overclaiming artifacts fail the completion
gate; they are never replaced by narrative assertions.

First save the PI-03C replay and PI-04 local snapshot, then aggregate all
components from `backend/`:

```bash
.venv/bin/python -m soc_agent.cli skill-improvement replay CANDIDATE_ID \
  --database-url sqlite+pysqlite:////absolute/path/to/pi03c-simulation.db \
  --output .deer-flow/soc-internal-validation/pi-03c-simulation/replay-report.json

SOC_KAFKA_ENABLED=false .venv/bin/python -m soc_agent.cli ops snapshot \
  --database-url sqlite+pysqlite:////absolute/path/to/pi03c-simulation.db \
  --output .deer-flow/soc-internal-validation/pi-04b-simulation/operations-snapshot.json

.venv/bin/python -m soc_agent.cli rollout completion \
  --output .deer-flow/soc-internal-validation/pi-05b-simulation/completion-report.json \
  --pretty

.venv/bin/python -m soc_agent.cli rollout completion \
  --baseline-json .deer-flow/soc-internal-validation/pi-05b-simulation/completion-report.json \
  --output .deer-flow/soc-internal-validation/pi-05b-simulation/completion-report-replay.json
```

A PI-05B pass means only that the frozen local simulation product track is
complete and replayable. The report always keeps all seven real integration
gates open, the real stage at `not_started`, and `pilot_ready=false` plus
`production_ready=false`.
