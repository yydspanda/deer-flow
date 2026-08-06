# PI-03C Skill Improvement Simulation

`pi03c_simulation_feedback.json` contains explicit simulation fixtures for the
feedback-derived Skill candidate workflow. The source IDs resemble repeated
analyst corrections, but they are not real analyst labels and cannot support a
quality claim, Skill edit, package activation, memory write, or Runtime change.

Run the isolated SQLite workflow from `backend/`:

```bash
SOC_DATABASE_URL=sqlite:///.deer-flow/pi03c-simulation.db \
  .venv/bin/python -m soc_agent.cli skill-improvement ingest \
  samples/eval/skill_improvement/pi03c_simulation_feedback.json \
  --threshold 3 --init-db --pretty
```

Then use `soc skill-improvement list|get|review|replay` against the same
database. Real correction and external-disposition adapters must emit the same
typed command from server-owned classification before they can enter this lane.
