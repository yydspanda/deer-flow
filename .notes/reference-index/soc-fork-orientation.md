# SOC Fork Orientation

## SOC Fork Overlay

This branch develops a SOC alert-triage product as an incremental DeerFlow extension.
Keep reusable upstream code vendor-neutral and place business behavior under the scoped
guides that own it:

- Backend SOC Runtime/services/contracts: `backend/soc_agent/AGENTS.md`
- Governed SOC Memory: `backend/soc_agent/memory/AGENTS.md`
- PingAn-only adapters/providers/policies: `backend/soc_agent/integrations/pingan/AGENTS.md`
- SOC Web workspace: `frontend/src/app/workspace/soc/AGENTS.md`
- Offline/local validation: `validation/AGENTS.md`

The authoritative design, execution ledger, and engineering contracts are
`.notes/ai_soc/soc-agent-solution.md`, `.notes/ai_soc/progress.md`,
`.notes/ai_soc/delivery-roadmap.md`, and
`.notes/reference-index/soc-agent-engineering-contracts.md`. Runtime owns deterministic
control flow; LLMs handle bounded uncertainty; human or governed policy owns authority.
Do not change DeerFlow core for SOC behavior unless a small generic extension point is
required and covered by upstream-facing tests.

`.notes/ai_soc/progress.md` owns one current Stage/task and at most 10 records; archive
older records monthly. Experiments use the archive's `soc-experiment` manifest. Run
`python scripts/check_soc_progress.py` after ledger/Roadmap changes; scheduled CI fails
above 10 upstream commits behind.

PingAn Apple Silicon DEV/STG uses `scripts/soc_pingan_macos_host_dev.py` with Python `3.12+`,
uv, Node `22+`, pinned pnpm, nginx `1.23+`, and approved registries. It runs local SQLite
and `LocalSandboxProvider` without Docker, exposes detected private LAN addresses by
default, and supports `--local-only`. `SOC_PINGAN_ENV=dev|stg` is changed only through
the governed switch command: DEV uses its own SQLite plus Gateway hot reload and Workbenches.
The frontend defaults to a reusable prebuilt snapshot; DEV alone can opt into
`start --frontend-mode dev`. STG uses a separate SQLite, production-optimized services, authentication, and no DEV
Workbench. That command also atomically applies the approved remote mappings
`DEV -> ZEUS PRD + Agent Platform PRD` and
`STG -> ZEUS STG + Agent Platform STG` from protected private profiles; startup
rejects a mismatch. The model target, Provider modes, and action authority remain
independent and unchanged. The reviewed legacy source does not contain the STG
`YHSYS` identity or ownership-workflow IDs, so project STG must remain fail-closed
until operators provide that complete private profile.
Trusted DEV demonstrations may explicitly add
`--demo-no-auth`; that mode maps every visitor to one synthetic administrator and must
never be used for identity/RBAC acceptance or production. Docker SOC DEV exposes the
equivalent explicit `./scripts/soc-memory-dev.sh demo-start`; ordinary `start` keeps
authentication enabled. The offline CPython bundle is only a fallback.


### SOC Agent Extension

Fork-owned SOC backend code lives under `backend/soc_agent/` and follows its nearest
`AGENTS.md`. Keep it separate from the reusable harness: entry surfaces call shared SOC
application services, vendor payload aliases stop in normalizers/adapters, and
PostgreSQL remains the production/staging SOC store. Local SQLite uses a separate SOC
database and is test/development evidence only. SOC changes require focused tests and
must preserve versioned contracts, transaction boundaries, idempotency, audit lineage,
and the separation between Runtime verdict, tenant/Memory decisions, authorization, and
external execution.
