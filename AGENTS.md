# AGENTS.md — DeerFlow 2.0

Full-stack super agent harness. Backend: Python 3.12 + LangGraph + FastAPI. Frontend: Next.js 16 + React 19 + TypeScript + pnpm 10.

## Commands

### Bootstrap (from repo root)

```
make check              # Verify Node 22+, pnpm, uv, nginx installed
make install            # Backend (uv sync) + Frontend (pnpm install)
make setup              # Interactive config wizard → config.yaml + .env
make doctor             # Validate config and system requirements
```

### Run (from repo root)

```
make dev                # Gateway(8001) + Frontend(3000) + nginx(2026)
make dev-daemon         # Same as dev but in background
make stop               # Stop all local services
```

Docker:

```
make docker-init        # Pull sandbox image (once)
make docker-start       # Docker dev (localhost:2026)
make docker-stop
make up / make down     # Docker production
```

### Backend (run from `backend/`)

```
make lint               # ruff check + ruff format --check
make format             # ruff check --fix && ruff format
make test               # PYTHONPATH=. uv run pytest tests/ -v
```

Single test: `cd backend && PYTHONPATH=. uv run pytest tests/test_foo.py -v`

### Frontend (run from `frontend/`)

```
pnpm lint               # ESLint
pnpm typecheck          # tsc --noEmit
pnpm test               # vitest run (tests/unit/**/*.test.ts)
pnpm test:e2e           # Playwright E2E tests (tests/e2e/**/*.spec.ts)
pnpm format:write       # Prettier
```

Build requires `BETTER_AUTH_SECRET=local-dev-secret pnpm build` (fails without it).
**Do not use `pnpm check`** — it is broken. Use `pnpm lint && pnpm typecheck` instead.

### Validation order before PR

1. `cd backend && make lint && make test`
2. `cd frontend && pnpm lint && pnpm typecheck`
3. Frontend build if env/auth/routing changed: `BETTER_AUTH_SECRET=local-dev-secret pnpm build`

## Architecture

```
Browser → nginx(:2026)
  ├── /api/langgraph/*  → Gateway (:8001) embedded agent runtime
  ├── /api/*            → Gateway API (:8001)
  └── /*                → Frontend (:3000)
```

`make dev` runs the agent runtime inside Gateway — no separate LangGraph server process. Gateway embeds the runtime via `RunManager` + `run_agent()` + `StreamBridge`.

### Key directories

| Path | Role |
|------|------|
| `config.yaml` | Main app config (gitignored, copy from `config.example.yaml`). Override path via `DEER_FLOW_CONFIG_PATH`. |
| `extensions_config.json` | MCP servers + skills config (gitignored) |
| `backend/packages/harness/deerflow/` | Publishable harness package (`deerflow-harness`, import as `deerflow.*`) |
| `backend/app/` | Unpublished application layer — Gateway API, IM channels (import as `app.*`) |
| `backend/langgraph.json` | LangGraph Studio entrypoint: `deerflow.agents:make_lead_agent` (only used by LangGraph Studio, not by `make dev`) |
| `backend/tests/` | Backend tests (pytest) |
| `frontend/src/core/` | Frontend business logic (threads, api, artifacts, i18n) |
| `frontend/src/components/ui/` | Shadcn-generated (do not manually edit) |
| `frontend/tests/unit/` | Unit tests (Vitest, mirrors `src/` layout) |
| `frontend/tests/e2e/` | E2E tests (Playwright, Chromium, mocked backend) |
| `skills/public/` | Built-in skills |
| `skills/custom/` | User skills (gitignored) |

### Harness/App boundary (enforced in CI)

- `app.*` may import `deerflow.*` — allowed.
- `deerflow.*` must **never** import `app.*` — enforced by `tests/test_harness_boundary.py`.

## Config

- `config.yaml` values with `$VAR` are resolved from env vars.
- `config.yaml` is auto-reloaded on mtime change (no manual restart needed for model metadata updates).
- `make config` aborts if config already exists. Use `make config-upgrade` to merge new fields from `config.example.yaml`.
- `config_version` in `config.example.yaml` tracks schema version; startup warns if user config is outdated.

## Toolchain

- Python 3.12+, `uv` package manager
- Node.js 22+, pnpm 10.26.2 (pinned via `packageManager` in `frontend/package.json`)
- nginx (required for `make dev`)
- ruff for backend lint/format (line-length 240, double quotes, spaces)

## CI (all PRs)

- **Lint check**: backend `make lint`, frontend `pnpm format && pnpm lint && pnpm typecheck && pnpm build`
- **Backend tests**: `cd backend && uv sync --group dev && make test`
- **Frontend tests**: `cd frontend && pnpm install --frozen-lockfile && pnpm test`
- **E2E tests**: triggered only on `frontend/**` changes
- Draft PRs skip backend/frontend/E2E test jobs

## Gotchas

- `BETTER_AUTH_SECRET` is required for frontend production build.
- `make config` is not idempotent — it refuses to overwrite existing config.
- Proxy env vars can silently break `pnpm install`; unset them if install fails.
- `make dev` stops existing services first; interrupt noise in logs is expected.
- Windows: use Git Bash only; `cmd.exe`/PowerShell are not supported.
- Frontend `@/*` path alias maps to `src/*`.
- Backend `PYTHONPATH=.` is required for `pytest` (tests import from repo root).
- `packages/harness` is a uv workspace member; its `pyproject.toml` holds the real dependency list.
- `langgraph.json` is only used by LangGraph Studio. `make dev` runs the agent in Gateway directly via `make_lead_agent`.
