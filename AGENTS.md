# AGENTS.md

This file provides guidance to AI coding agents (Claude Code, Codex, and others) when working with code in this repository. It is the source of truth; the sibling `CLAUDE.md` imports it via `@AGENTS.md`.

It is the **monorepo orientation layer**: it maps the whole repo and points to the
module guides that own the depth. For anything inside a module, read that module's
guide rather than expecting full detail here:

- **[backend/AGENTS.md](backend/AGENTS.md)** — backend depth: harness/app split, agent and
  middleware chain, sandbox, MCP, skills, memory, IM channels, persistence/migrations,
  config system, test layout.
- **[frontend/AGENTS.md](frontend/AGENTS.md)** — frontend depth: Next.js App Router layout,
  thread/streaming data flow, code style, commands.

## What is DeerFlow

DeerFlow is a LangGraph-based AI super-agent system with a full-stack architecture. The
backend runs a "super agent" with sandboxed execution, persistent memory, subagent
delegation, and extensible tools (built-in, MCP, community), all per-thread isolated. The
frontend is a Next.js chat UI. External IM platforms (Feishu, Slack, Telegram, Discord,
DingTalk) bridge into the same agent through the Gateway.

## Service Topology

A single `make dev` / Docker stack runs four cooperating services:

| Service | Port | Role |
| --- | --- | --- |
| **Nginx** | `2026` | Unified reverse-proxy entry point — open this in the browser |
| **Gateway API** | `8001` | FastAPI REST API + embedded LangGraph-compatible agent runtime |
| **Frontend** | `3000` | Next.js web interface |
| **Provisioner** | `8002` | Optional — only when sandbox is configured for provisioner/K8s mode |

Nginx is the single public entry: it serves the frontend and proxies `/api/langgraph/*`
to the Gateway's LangGraph runtime, rewriting it to Gateway's native `/api/*` routes; all
other `/api/*` go straight to the Gateway REST routers. See
[backend/AGENTS.md](backend/AGENTS.md) for the runtime and router detail.

## Repository Map

```text
deer-flow/
├── Makefile                        # Root orchestration: drives the full stack
├── config.example.yaml             # Template -> copy to config.yaml
├── extensions_config.example.json  # Template -> copy to extensions_config.json
├── backend/                        # Python backend — see backend/AGENTS.md
├── frontend/                       # Next.js frontend — see frontend/AGENTS.md
├── docker/                         # docker-compose files, nginx config, provisioner
├── skills/                         # Agent skills
├── contracts/                      # Cross-component JSON contracts
├── scripts/                        # Root orchestration scripts
├── tests/                          # Root-level tests
└── docs/                           # Cross-cutting docs, plans, and design notes
```

Runtime config lives at the **repo root**: copy `config.example.yaml` to `config.yaml`
and `extensions_config.example.json` to `extensions_config.json`. Both real files are
gitignored and may be edited at runtime via the Gateway API. Config schema and resolution
order are documented in [backend/AGENTS.md](backend/AGENTS.md).

## Commands: Root vs. Module

Root `make` targets drive the whole stack:

```bash
make setup
make doctor
make config
make check
make install
make dev
make start
make stop
make up / down
make docker-start / docker-stop / docker-logs
```

Per-module commands drive a single module:

```bash
cd backend && make dev
cd backend && make test
cd backend && make lint
cd backend && make format

cd frontend && pnpm dev
cd frontend && pnpm check
cd frontend && pnpm test
```

Rule of thumb: **root `make` = the full application**; **`backend/Makefile` and
`frontend` (`pnpm`) = per-module work.**

## Where to Go Next

- Backend work → **[backend/AGENTS.md](backend/AGENTS.md)**
- Frontend work → **[frontend/AGENTS.md](frontend/AGENTS.md)**
- Setup & install → **[Install.md](Install.md)**, **[CONTRIBUTING.md](CONTRIBUTING.md)**
- Project overview & usage → **[README.md](README.md)** and translations
- Security policy → **[SECURITY.md](SECURITY.md)**
- Changes → **[CHANGELOG.md](CHANGELOG.md)**

## Cross-Cutting Conventions

These apply repo-wide; module guides own the module-specific detail.

- **Documentation update policy** — keep docs in sync with code: update `README.md` for
  user-facing changes and the relevant `AGENTS.md` for development/architecture changes in
  the same change set.
- **Test-driven development** — features and bug fixes ship with tests. Backend tests live
  in `backend/tests/`; frontend tests live in `frontend/tests/`.
- **Format before pushing** — run `make format` for backend changes and `pnpm check` for
  frontend changes. Backend CI enforces `ruff format --check`.

## SOC Agent Branch Context

This branch is used to build a SOC alert triage agent on top of DeerFlow + LangGraph.
The current authoritative plan is
[.notes/ai_soc/soc-agent-solution.md](.notes/ai_soc/soc-agent-solution.md).

Current SOC direction:

- This repo is a fork of upstream DeerFlow. Keep SOC Agent work as incremental business
  extension whenever possible: add SOC-specific modules, adapters, schemas, routes, and
  docs instead of modifying upstream core code. Only touch existing DeerFlow core files
  for small, generic extension points or framework fixes that are clearly useful beyond
  SOC, and keep those changes easy to explain for future upstream sync.
- PostgreSQL is the SOC business store; do not use SQLite/`alerts.db` for SOC runtime data.
- SOC persistence code lives under `backend/soc_agent/db/` and implements repository
  protocols from `backend/soc_agent/protocols.py`; keep it separate from DeerFlow harness
  persistence unless a generic upstream extension point is genuinely needed.
- SOC schema migrations live under `backend/soc_agent/db/migrations/` and are applied with
  `soc db upgrade`; the migration version table is `soc_alembic_version`.
- Kafka/Redpanda is planned for Phase 4 daemon ingestion; local broker default is `localhost:9092`.
- Phase 1 target is CLI + Runtime reliability: fixed pipeline, schema/domain validation,
  step trace, audit logging, basic rate limiting, and `analyze` / `correct` / `replay`.
- LLMs do not own the main control flow. Runtime owns the deterministic pipeline; LLM nodes
  handle bounded reasoning and can only suggest soft routes from a whitelist.
- LLM-discovered knowledge is candidate knowledge only. It must be confirmed by a human before
  it can affect future decisions.

SOC phase plan:

| Phase | Goal |
| --- | --- |
| Phase 1 | CLI + Runtime reliability loop |
| Phase 2 | Correlation + Main Agent dedup |
| Phase 3 | Learning + classifier pre-judge + limited LLM Advisory Router |
| Phase 4 | Daemon mode + Sub Agent parallelism + replay/router evaluation |
| Phase 5 | Knowledge RAG, threat intel, review UI, MITRE ATT&CK, stale-knowledge handling |

### SOC Agent Development Workflow

SOC Agent work is a long-running product and engineering effort. Do not start from code
search alone. Use this order:

1. Read the current plan in `.notes/ai_soc/` and the relevant engineering/tooling
   contracts in `.notes/reference-index/`.
2. Derive the smallest Phase-aligned implementation slice from those docs.
3. Use CodeGraph only after the slice is clear, to verify DeerFlow code locations,
   reusable APIs, and low-intrusion integration points.
4. Implement SOC-specific behavior as incremental modules/adapters first; avoid changing
   upstream DeerFlow core unless a small generic extension point is required.
5. If the slice changes product direction, runtime pipeline, contract semantics, phase
   scope, or next-step sequencing, update `.notes/ai_soc/soc-agent-solution.md` in the
   same change set; keep `.notes/reference-index/soc-agent-engineering-contracts.md`
   aligned for engineering rules.
6. After code changes, run `codegraph sync .` from the repo root so the local
   CodeGraph index includes newly added or edited SOC symbols before the next slice.
7. Update `.notes/ai_soc/progress.md` after each completed slice with status, changed
   files, verification, and next step.

Progress is not tracked in chat history. The durable task ledger is
`.notes/ai_soc/progress.md`; keep it current whenever SOC Agent work advances.

## Notes Entry Points

[.notes/README.md](.notes/README.md) is the notes index. Main active docs:

| Path | Purpose |
| --- | --- |
| `.notes/ai_soc/soc-agent-solution.md` | Current SOC Agent design |
| `.notes/ai_soc/progress.md` | Durable SOC Agent progress ledger |
| `.notes/reference-index/soc-agent-engineering-contracts.md` | Engineering contracts: style, API, events, Kafka, permissions, tests |
| `.notes/reference/cross-project-workflow.md` | Cross-project reference workflow |
| `.notes/research/hermes-vs-deerflow-agent-patterns.md` | Referenced Claude Code/Hermes design-pattern research |
| `.notes/archive/` | Historical or low-frequency notes; not development source of truth |

## Reference Projects

Reference projects are read-only. Use Understand Anything and CodeGraph when consulting them;
do not directly modify files in those projects.

| Project | Path | Use |
| --- | --- | --- |
| claude-code-sourcemap | `/home/yydspei/projects/claude-code-sourcemap` | Claude Code source and agent architecture patterns |
| claude-mem | `/home/yydspei/projects/claude-mem` | Memory-system implementation reference |
| hermes-agent | `/home/yydspei/projects/hermes-agent` | Hermes Agent framework and interaction patterns |
| openclaw | `/home/yydspei/projects/openclaw` | Personal AI assistant and multi-platform agent reference |

Cross-project rules:

- Prefer Understand Anything for broad architecture exploration.
- Prefer CodeGraph for exact symbol/function/class lookup.
- Record reusable findings in `.notes/reference-index/`.
- Re-implement design ideas in this repo; do not copy reference-project code directly.
