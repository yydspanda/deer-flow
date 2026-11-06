# SOC Workspace Frontend Guide

SOC pages, `src/core/soc`, and SOC components are thin clients of server-owned
contracts. Follow the repository, `frontend/AGENTS.md`, and `frontend/src/AGENTS.md`
guides. Keep business behavior in SOC modules and shared DeerFlow connections small.

Before editing, read the opening boundary rules in [workspace-guide.md](workspace-guide.md)
and the sections relevant to the task below. Their requirements remain mandatory;
the detailed guide retains the full implementation and acceptance contracts.

| Change | Required section |
| --- | --- |
| Requests, hooks, routes, review-chat binding or rendering | [API and navigation](workspace-guide.md#api-and-navigation) |
| Verdicts, traces, retries or analyst actions | [Alert results](workspace-guide.md#alert-results-and-human-intervention) |
| Candidate review, lessons, scopes or Memory activation | [Memory governance](workspace-guide.md#memory-governance) |
| Corpus workbenches, environment controls or experiments | [DEV validation](workspace-guide.md#dev-validation-surfaces) |
| Operational reporting or lifecycle views | [Operations](workspace-guide.md#operations) |
| Learning/validation batches and recovery | [Quick batch validation](workspace-guide.md#quick-batch-validation) |

Preserve server ownership of authorization, provenance, task state and persistence.
UI hints do not grant authority. Show action failures and saved-state transitions;
do not infer success from a local preview or optimistic state alone.
