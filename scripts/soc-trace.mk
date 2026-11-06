# Fork-owned optional Trace development commands. No ordinary startup enables it.
.PHONY: soc-trace-check soc-trace-serve soc-trace-test

soc-trace-check:
	@cd backend && uv run --no-sync python scripts/soc_trace_agent.py check

soc-trace-serve:
	@cd backend && uv run --no-sync python scripts/soc_trace_agent.py serve

soc-trace-test:
	@cd backend && uv run --no-sync python -m pytest tests/test_trace_agent_*.py tests/test_soc_trace_*.py tests/test_trace_scaffold_*.py -q
