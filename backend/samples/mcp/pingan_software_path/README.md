# PingAn Historical Software Path MCP

This package exposes the reviewed EDR workbook as **historical investigation
context**, not as an allowlist. The compiler preserves source lineage and keeps
path-control risk separate from prior ignored dispositions. In particular,
`D:` remains a higher-attention location even when an exact or path-family
historical match exists.

## Build the local catalog

Run from the repository root:

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_software_path_catalog.py build
```

The private source workbook remains under `validation/original_works/`. The
generated catalog and report are Git-ignored:

```text
backend/.deer-flow/pingan-context/software-path-catalog.sqlite
backend/.deer-flow/pingan-context/software-path-catalog.build-report.json
```

Inspect one exact path and optional hash:

```bash
backend/.venv/bin/python backend/scripts/soc_pingan_software_path_catalog.py query \
  'D:\\ps\\psexec.exe' \
  --md5 0123456789abcdef0123456789abcdef
```

The catalog preserves exact normalized paths and may infer a conservative family
when at least two distinct `safe_paths` rows differ in exactly one recognized
deployment segment. `other_paths` never creates a family. The old basename,
prefix, broad version wildcard, and path-segment deletion heuristics are not used.

## MCP and SOC action smoke

```bash
cd backend
export SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH="$PWD/.deer-flow/pingan-context/software-path-catalog.sqlite"
export SOC_PINGAN_SOFTWARE_PATH_MCP_PYTHON="$PWD/.venv/bin/python"
export SOC_PINGAN_SOFTWARE_PATH_MCP_SERVER="$PWD/scripts/soc_pingan_software_path_mcp_server.py"
export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$PWD/samples/mcp/pingan_software_path/extensions.local.example.json"

./.venv/bin/python -m soc_agent.cli mcp smoke \
  samples/mcp/pingan_software_path/action_adapters.json \
  --route endpoint.software_path.lookup \
  --json '{"path":"D:\\ps\\psexec.exe","context_refs":{"thread_id":"PATH-CONTEXT-SMOKE"}}' \
  --pretty
```

Every result keeps:

```json
{
  "candidate_only": true,
  "allowlist": false,
  "evidence_boundary": "investigation_only",
  "decision_impact": "none",
  "automation_eligible": false,
  "raw_rows_included": false
}
```

It may enrich Review, TUI, Web, and Lead Agent context through normal
`InvestigationEvidence` persistence. It cannot skip Runtime, mark an alert
false-positive, close ReviewQueue, authorize an action, or write memory.

This MCP boundary is intentionally different from the separately governed
PingAn fast-disposition policy. Operators may enable that default-off policy with:

```bash
export SOC_TENANT_POLICY_ENABLED=true
export SOC_TENANT_DISPOSITION_POLICY_PATH="$PWD/soc_agent/integrations/pingan/policies/tenant-disposition-v2.json"
export SOC_TENANT_POLICY_ENVIRONMENT=dev
export SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true
```

The policy provider reads canonical EDR paths directly from the completed run.
It emits `all_relevant_paths_safe` only when every relevant path is covered by an
exact `safe_paths` entry or a safe-path family. Both match types then have the
same direct `ignored` effect. Partial coverage, `other_paths`-only matches, or
hash conflicts fail closed and continue normal triage.
