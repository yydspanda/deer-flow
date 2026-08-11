# DeerFlow Backend

DeerFlow is a LangGraph-based AI super agent with sandbox execution, persistent memory, and extensible tool integration. The backend enables AI agents to execute code, browse the web, manage files, delegate tasks to subagents, and retain context across conversations - all in isolated, per-thread environments.

---

## Architecture

```
                        ┌──────────────────────────────────────┐
                        │          Nginx (Port 2026)           │
                        │      Unified reverse proxy           │
                        └───────┬──────────────────┬───────────┘
                                │
            /api/langgraph/*    │    /api/* (other)
            rewritten to /api/* │
                                ▼
               ┌────────────────────────────────────────┐
               │        Gateway API (8001)              │
               │        FastAPI REST + agent runtime    │
               │                                        │
               │ Models, MCP, Skills, Memory, Uploads,  │
               │ Artifacts, Threads, Runs, Streaming    │
               │                                        │
               │ ┌────────────────────────────────────┐ │
               │ │ Lead Agent                         │ │
               │ │ Middleware Chain, Tools, Subagents │ │
               │ └────────────────────────────────────┘ │
               └────────────────────────────────────────┘
```

**Request Routing** (via Nginx):
- `/api/langgraph/*` → Gateway LangGraph-compatible API - agent interactions, threads, streaming
- `/api/*` (other) → Gateway API - models, MCP, skills, memory, artifacts, uploads, thread-local cleanup
- `/` (non-API) → Frontend - Next.js web interface

---

## Core Components

### Lead Agent

The single LangGraph agent (`lead_agent`) is the runtime entry point, created via `make_lead_agent(config)`. It combines:

- **Dynamic model selection** with thinking and vision support
- **Middleware chain** for cross-cutting concerns (9 middlewares)
- **Tool system** with sandbox, MCP, community, and built-in tools
- **Subagent delegation** for parallel task execution
- **System prompt** with skills injection, memory context, and working directory guidance

### Middleware Chain

Middlewares execute in strict order, each handling a specific concern:

| # | Middleware | Purpose |
|---|-----------|---------|
| 1 | **ThreadDataMiddleware** | Creates per-thread isolated directories (workspace, uploads, outputs) |
| 2 | **UploadsMiddleware** | Injects newly uploaded files into conversation context |
| 3 | **SandboxMiddleware** | Acquires sandbox environment for code execution |
| 4 | **SummarizationMiddleware** | Reduces context when approaching token limits (optional) |
| 5 | **TodoListMiddleware** | Tracks multi-step tasks in plan mode (optional) |
| 6 | **TitleMiddleware** | Auto-generates conversation titles after first exchange |
| 7 | **MemoryMiddleware** | Queues conversations for async memory extraction |
| 8 | **ViewImageMiddleware** | Injects image data for vision-capable models (conditional) |
| 9 | **ClarificationMiddleware** | Intercepts clarification requests and interrupts execution (must be last) |

### Sandbox System

Per-thread isolated execution with virtual path translation:

- **Abstract interface**: `execute_command`, `read_file`, `write_file`, `list_dir`
- **Providers**: `LocalSandboxProvider` (filesystem) and `AioSandboxProvider` (Docker, in community/). Async runtime paths use async sandbox lifecycle hooks so startup, readiness polling, and release do not block the event loop. `AioSandboxProvider` validates active-cache and warm-pool containers during acquire/reuse, dropping definitively dead entries so a thread can provision a fresh sandbox after an unexpected container exit while keeping `get()` as an in-memory lookup. Backend health-check failures are treated as unknown, not dead, and a container that cannot be verified during discovery is simply not adopted (acquire falls through to create instead of failing).
- **Virtual paths**: `/mnt/user-data/{workspace,uploads,outputs}` → thread-specific physical directories
- **Skills path**: `/mnt/skills` → `deer-flow/skills/` directory
- **Skills loading**: Recursively discovers nested `SKILL.md` files under `skills/{public,custom}` and preserves nested container paths
- **SkillScan**: Native offline deterministic scanning runs before the LLM skill scanner on installs and agent-managed skill writes; `CRITICAL` findings block and warning findings become LLM context
- **File-write safety**: `str_replace` serializes read-modify-write per `(sandbox.id, path)` so isolated sandboxes keep concurrency even when virtual paths match
- **Tools**: `bash`, `ls`, `read_file`, `write_file`, `str_replace` (`write_file` overwrites by default and exposes `append` for end-of-file writes; `bash` is disabled by default when using `LocalSandboxProvider`; use `AioSandboxProvider` for isolated shell access)

### Subagent System

Async task delegation with concurrent execution:

- **Built-in agents**: `general-purpose` (full toolset) and `bash` (command specialist, exposed only when shell access is available)
- **Concurrency**: Max 3 subagents per turn, 15-minute timeout
- **Execution**: Background thread pools with status tracking and SSE events
- **Flow**: Agent calls `task()` tool → executor runs subagent in background → polls for completion → returns result

### Memory System

LLM-powered persistent context retention across conversations:

- **Automatic extraction**: Analyzes conversations for user context, facts, and preferences
- **Scope-safe writes**: Middleware extraction stores only durable, descriptive user-level facts; global summaries also require descriptive authority, while contradiction removals and consolidated facts fail closed when scope metadata is missing or task/project-local
- **Atomic replacements**: A contradiction removal linked to a replacement runs only after the replacement survives scope/confidence gates, deduplication, and fact-limit trimming
- **Structured storage**: User context (work, personal, top-of-mind), history, and confidence-scored facts
- **Debounced updates**: Batches updates to minimize LLM calls (configurable wait time)
- **System prompt injection**: Top facts + context injected into agent prompts
- **Run-level memory identity**: `GET /api/threads/{thread_id}/runs/{run_id}/events?event_types=context:memory` returns the SHA-256 identity of the effective hidden memory block without copying memory text into the event store
- **Storage**: JSON file with mtime-based cache invalidation

### Tool Ecosystem

| Category | Tools |
|----------|-------|
| **Sandbox** | `bash`, `ls`, `read_file`, `write_file`, `str_replace` |
| **Built-in** | `present_files`, `ask_clarification`, `view_image`, `task` (subagent) |
| **Community** | Tavily (web search), Jina AI (web fetch), Crawl4AI (web fetch), Firecrawl (scraping), fastCRW (scraping), DuckDuckGo (image search) |
| **MCP** | Any Model Context Protocol server (stdio, SSE, HTTP transports) |
| **Skills** | Domain-specific workflows injected via system prompt |

### Gateway API

FastAPI application providing REST endpoints for frontend integration:

| Route | Purpose |
|-------|---------|
| `GET /api/models` | List available LLM models |
| `GET/PUT /api/mcp/config` | Manage MCP server configurations |
| `POST /api/mcp/cache/reset` | Reset cached MCP tools so they reload on next use |
| `GET/PUT /api/skills` | List and manage skills |
| `POST /api/skills/install` | Install skill from `.skill` archive |
| `GET /api/memory` | Retrieve memory data |
| `POST /api/memory/reload` | Force memory reload |
| `GET /api/memory/config` | Memory configuration |
| `GET /api/memory/status` | Combined config + data |
| `GET /api/threads/{id}/runs/{run_id}/events` | Debug/audit events for one run; filter `event_types=context:memory` for effective memory identity |
| `POST /api/threads/{id}/uploads` | Upload files (auto-converts PDF/PPT/Excel/Word to Markdown, rejects directory paths, auto-renames duplicate filenames in one request) |
| `GET /api/threads/{id}/uploads/list` | List uploaded files |
| `DELETE /api/threads/{id}` | Delete DeerFlow-managed local thread data after LangGraph thread deletion; unexpected failures are logged server-side and return a generic 500 detail |
| `GET /api/threads/{id}/artifacts/{path}` | Serve generated artifacts |

### IM Channels

The IM bridge supports Feishu, Slack, and Telegram. Slack and Telegram still use the final `runs.wait()` response path, while Feishu now streams through `runs.stream(["messages-tuple", "values"])`, serializes rapid same-thread turns inside the channel manager, and updates a single in-thread card per source message in place.

For Feishu card updates, DeerFlow stores the running card's `message_id` per inbound message and patches that same card until the run finishes, preserving the existing `OK` / `DONE` reaction flow. When a follow-up arrives inside an existing Feishu topic while another turn is still running, the later message now waits on the mapped DeerFlow `thread_id`, receives a queued/running card on that exact source message, and keeps a compact source-message blockquote in subsequent patches so rapid consecutive questions remain distinguishable.

---

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- API keys for your chosen LLM provider

### Installation

```bash
cd deer-flow

# Copy configuration files
cp config.example.yaml config.yaml

# Install backend dependencies
cd backend
make install
```

### Experimental SOC Agent CLI

This branch includes an incremental SOC Agent extension under `backend/soc_agent/`.
It reuses DeerFlow's model, skill, MCP, custom-agent, and TUI infrastructure while
keeping SOC contracts, services, persistence, and adapters outside the harness core.

Run a deterministic sample analysis:

```bash
cd backend
python -m soc_agent.cli analyze samples/alerts/approved_scanner.json --pretty
```

When installed as a backend console script, the equivalent entry point is:

```bash
soc analyze samples/alerts/approved_scanner.json --pretty
```

The runtime keeps deterministic control flow and uses a deterministic analyzer by
default; an explicitly selected DeerFlow model can run the bounded LLM node. SOC
business persistence supports PostgreSQL. With local `database.backend: sqlite`, SOC
commands and Gateway routes automatically use the separate
`{database.sqlite_dir}/soc_agent_dev.db`; `--database-url` and `SOC_DATABASE_URL`
remain explicit overrides.
Kafka ingestion, ReviewQueue Web/TUI, governed context, shadow disposition proposals,
and append-only evaluation outcomes are available; production response side effects
remain disabled without a reviewed Automation Policy or human grant plus a real adapter.

Tenant-specific operational handling is a separate default-off post-Runtime layer. It records
`Base -> Memory -> Tenant Policy -> Effective` without rewriting the immutable Runtime decision.
Enable deterministic tenant rules explicitly; the optional policy Skill runs only after a
deterministic no-match:

```bash
export SOC_TENANT_POLICY_ENABLED=true
export SOC_TENANT_DISPOSITION_POLICY_PATH=/reviewed/policy.json
export SOC_TENANT_POLICY_ENVIRONMENT=dev

# Optional bounded LLM policy advisor
export SOC_TENANT_POLICY_ADVISOR_MODE=llm
export SOC_TENANT_POLICY_SKILL_PATH=/reviewed/policy-skill/SKILL.md
export SOC_TENANT_POLICY_MODEL=deepseek-v4-flash
```

Tenant policy may change effective review/disposition only when its reviewed mode is `enforced`.
It never authorizes blocking, isolation, suppression, or another external action; that requires an
independent `SOC_AUTOMATION_POLICY_PATH`. For PingAn, the integration-owned policy treats HTTP
`200` as request success only and does not derive a disposition from it. A deterministic ignore rule
uses only canonical HTTP `100..599` statuses and applies when every observed HTTP transaction is
non-`200`; exact forced-transfer rules take priority. Provider success/compromise labels prevent this
ignore rule from matching but do not deterministically escalate; response effects, request-body
semantics and attack-attempt cases remain bounded Policy-Skill/Runtime reasoning.

PingAn EDR also has a separate, high-throughput software-path policy. It is disabled by default and
requires both the tenant-policy layer and a compiled local catalog:

```bash
export SOC_PINGAN_SOFTWARE_PATH_FAST_POLICY_ENABLED=true
export SOC_PINGAN_SOFTWARE_PATH_CATALOG_PATH=/approved/software-path-catalog.sqlite
```

When every canonical process/executable path in the alert matches either an exact legacy
`safe_paths` entry or a conservatively inferred safe-path family, the reviewed PingAn policy records
`ignored` and clears per-alert review. Exact and family matches have the same disposition authority.
Unknown paths, `other_paths`-only entries, hash conflicts, invalid paths, or an over-budget path set
emit no aggregate signal and fall back to normal triage. The immutable Runtime verdict remains visible.

Run the fixed ten-alert real-EDR integration acceptance from the repository root:

```bash
backend/.venv/bin/python \
  validation/compact_zeus/policy/validate_pingan_edr_safe_path_fast_policy.py
```

It uses the deterministic analyzer to test normalization, catalog matching, tenant-policy persistence,
and effective-decision lineage without making an LLM quality claim or authorizing an external action.

Automatic read-only investigation is default-off and runs after an existing persisted
`AnalysisRun`, never inside the fixed Runtime. Kafka daemon commands may opt in with one
explicit enrichment composition and repeatable `--enrichment-action-config` allowlists.
Execution, attempts, result mode, retries, evidence, and linked replay are durable. Inspect
them with `soc investigation get`, generate the recomputable D4 report/addendum bundle with
`soc investigation report`, or create a linked replay with `soc investigation replay`.
The report reads persisted execution state and validated evidence without calling a Provider
or changing the base run. It measures action-attempt latency and evidence coverage; Provider
network latency and cost remain explicit `not_measured` gaps until those sources exist. Replay
requires a new idempotency key, reason, explicit configs, and `--confirm-investigation`.

Internal DEV validation uses that isolated local SOC SQLite database. Endpoint process trees,
commands, login context, and host events are consumed from bounded alert-native evidence;
the Runtime does not advertise synthetic process-tree or host-context lookup actions.

For PingAn internal DEV, start from `samples/pingan_dev/`. The tracked profile routes
`deepseek-v4-flash` through the local OpenAI-compatible model gateway and configures the
real `asset.locate` boundary plus the local historical software-path MCP through
environment variables. Put runnable credentials in
Git-ignored `config.pingan-dev.local` / `.env.soc-dev.local`, then run the no-network
`scripts/soc_pingan_dev_preflight.py` before `scripts/soc_pingan_asset_direct_smoke.py`
or MCP smoke. The self-contained ZEUS signer does not import the legacy application;
the internal Agent Platform `run_workflow` package must still be available on the DEV host.

Compile the private historical EDR workbook once, then query it through the generic
`endpoint.software_path.lookup` action:

```bash
python scripts/soc_pingan_software_path_catalog.py build
python scripts/soc_pingan_software_path_catalog.py query 'D:\\ps\\psexec.exe'
```

The generated SQLite catalog is Git-ignored and mode `0600`. It preserves every exact path and also
infers conservative one-variable-segment families from `safe_paths` only; it does not restore the old
basename, prefix, version-wildcard, or deleted-segment fuzzy matching. The read-only MCP result remains
investigation-only candidate context and cannot alter Runtime verdicts, close reviews, authorize
actions, or write confirmed memory. Only the separately enabled PingAn tenant-policy signal provider
can turn complete exact/family coverage into an auditable `ignored` disposition. See
`samples/mcp/pingan_software_path/README.md` for MCP smoke commands.

For approved internal PKL validation, install the optional workbook/batch dependencies and use the
resumable `5 -> 50 -> all` runner from the repository root:

```bash
uv sync --locked --extra pingan-dev
cd ..
backend/.venv/bin/python \
  validation/compact_zeus/internal_batch/run_pingan_runtime_batch.py \
  --source /approved/path/alerts.pkl --analyzer-mode llm \
  --model-name deepseek-v4-flash --limit 5 --plan-only
```

The runner reuses the production `SocAnalysisService` and invokes no MCP tool by default. Its
optional PI-01D3/D4 bridge requires `--persist`, explicit composition/action configs, and
`--confirm-investigation`; successful base analysis remains in the item artifact even when
investigation fails. Enabled items include the durable workflow plus a secret-free shadow report
and deterministic investigation addendum, while the manifest aggregates route/result/retry,
evidence-coverage and action-attempt latency telemetry. See
`validation/compact_zeus/internal_batch/README.md` and the internal continuation handoff before
running a live or persistent batch.

Initialize or upgrade the local SOC schema from `backend/`; no database URL is needed when
`config.yaml` uses `database.backend: sqlite`:

```bash
soc db upgrade
```

Inspect exact persisted backlog counts and passive Kafka readiness without exposing
connection details:

```bash
soc ops snapshot --pretty
# Add --check-broker only for an explicit connectivity probe.
```

The same passive snapshot is available from `GET /api/soc/operations/snapshot`.
Consumer lag, model compute, and production SLO status remain explicitly
`not_measured` until those telemetry sources are connected.

Run the release-level local Alpha gate from the repository root:

```bash
./scripts/soc-alpha-acceptance.sh all
```

The command generates
`backend/.deer-flow/soc-alpha-acceptance/alpha-acceptance-report.json` and component evidence for
APT/EDR/HIDS across CLI, local SQL, a real local Kafka-compatible broker, registered Gateway
handlers/services, Review Web browser regression, feedback, audit, and replay. It deliberately uses
a deterministic analyzer, local SQLite, mock investigation providers, and mocked browser transport;
a passing report proves the documented local/test Alpha boundary, not production readiness. See
`.notes/ai_soc/alpha-acceptance-runbook.md` for prerequisites and failure semantics.

Package that acceptance report with the full SOC backend and architecture/migration
release gates:

```bash
./scripts/soc-alpha-readiness.sh all
```

The resulting
`backend/.deer-flow/soc-alpha-readiness/alpha-readiness-report.json` records the source commit,
artifact hashes, parsed test counts, completeness-matrix state and Stage 4 handoff references. A
technical pass remains `pending_owner_review` and `production_ready=false`; see
`.notes/ai_soc/alpha-readiness-package.md` for deployment, rollback and sign-off requirements.

Open the SOC review workbench:

```bash
soc review tui --actor-id analyst-1
```

After a ReviewQueue item is closed, disposition labels remain a separate explicit
action. In the TUI use `/outcome DPROP-... disposition idempotency-key reason` for a
primary label or `/sample-outcome DSAMPLE-... DPROP-... disposition idempotency-key reason`
for an independent sampled review. The Web workbench exposes the same structured
fields at `/workspace/soc/review`; neither surface infers labels from close reasons.
Its `抽样复核` view lists persisted hash-ranked campaigns and opens only manifest-selected
work in the same capture form. Use a distinct `--actor-id` for the independent quality reviewer.

Run the bounded main-orchestrator evaluation with a seeded historical copy of each
APT/EDR/HIDS alert:

```bash
soc eval pingan-main --pretty
```

The resulting `UnifiedInvestigationReport` includes structured correlation matches,
explainable match reasons, and evidence loaded from the matched historical run. This
evaluation uses local/mock action providers and does not enable automatic suppression or
response.

Measure the deterministic correlation scorer separately from duplicate-incident identity:

```bash
soc eval correlation --pretty
soc eval correlation --baseline-json prior-correlation-report.json --pretty
```

The versioned corpus labels `same_incident`, `related_distinct`, and `unrelated` pairs. The
report includes retrieval and offline dedup precision/recall, matched-reason distribution,
candidate fan-out, and evidence-lineage checks. It is read-only and always reports
`shadow_dedup_allowed=false`; a score or threshold does not suppress alerts.

### Configuration

Edit `config.yaml` in the project root:

```yaml
models:
  - name: gpt-4o
    display_name: GPT-4o
    use: langchain_openai:ChatOpenAI
    model: gpt-4o
    api_key: $OPENAI_API_KEY
    supports_thinking: false
    supports_vision: true

  - name: gpt-5-responses
    display_name: GPT-5 (Responses API)
    use: langchain_openai:ChatOpenAI
    model: gpt-5
    api_key: $OPENAI_API_KEY
    use_responses_api: true
    output_version: responses/v1
    supports_vision: true
```

Set your API keys:

```bash
export OPENAI_API_KEY="your-api-key-here"
```

### Running

**Full Application** (from project root):

```bash
make dev  # Starts Gateway + Frontend + Nginx
```

Access at: http://localhost:2026

**Backend Only** (from backend directory):

```bash
# Gateway API + embedded agent runtime
make dev
```

Direct access: Gateway at http://localhost:8001

**Terminal Workbench (TUI)** — a terminal-native UI over the embedded harness,
no services required:

```bash
uv pip install 'deerflow-harness[tui]'   # optional 'textual' dependency
deerflow                                 # launch the TUI
deerflow --print "summarize this repo"   # headless one-shot
deerflow --recursion-limit 250 --print "run a longer task"
```

Sessions opened in the TUI appear in the Web UI sidebar (it writes the shared
`threads_meta` store under the local default user). See [docs/TUI.md](docs/TUI.md).

---

## Project Structure

```
backend/
├── packages/harness/           # deerflow-harness package (import: deerflow.*)
│   └── deerflow/
│       ├── agents/             # Agent system
│       │   ├── lead_agent/     # Main agent (factory, prompts)
│       │   ├── middlewares/    # Middleware components
│       │   ├── memory/         # Memory extraction & storage
│       │   └── thread_state.py # ThreadState schema
│       ├── sandbox/            # Sandbox execution
│       │   ├── local/          # Local filesystem provider
│       │   ├── sandbox.py      # Abstract interface
│       │   ├── tools.py        # bash, ls, read/write/str_replace
│       │   └── middleware.py   # Sandbox lifecycle
│       ├── subagents/          # Subagent delegation
│       │   ├── builtins/       # general-purpose, bash agents
│       │   ├── executor.py     # Background execution engine
│       │   └── registry.py     # Agent registry
│       ├── tools/builtins/     # Built-in tools
│       ├── mcp/                # MCP protocol integration
│       ├── models/             # Model factory
│       ├── skills/             # Skill discovery & loading
│       ├── config/             # Configuration system
│       ├── runtime/            # Embedded run execution (RunManager, StreamBridge)
│       ├── persistence/        # Checkpointer/store engines & schema migrations
│       ├── guardrails/         # Pre-tool-call authorization providers
│       ├── tracing/            # Tracer factory & trace metadata
│       ├── uploads/            # Uploads manager
│       ├── tui/                # Terminal UI (`deerflow` console script)
│       ├── community/          # Community tools & providers
│       ├── reflection/         # Dynamic module loading
│       └── utils/              # Utilities
├── app/                        # FastAPI Gateway + IM channels (import: app.*)
│   ├── gateway/                # Gateway API
│   │   ├── app.py              # Application setup
│   │   └── routers/            # Route modules
│   └── channels/               # IM channel integrations
├── docs/                       # Documentation
├── tests/                      # Test suite
├── langgraph.json              # LangGraph graph registry for tooling/Studio compatibility
├── pyproject.toml              # Python dependencies
├── Makefile                    # Development commands
└── Dockerfile                  # Container build
```

`langgraph.json` is not the default service entrypoint.  The scripts and Docker
deployments run the Gateway embedded runtime; the file is kept for LangGraph
tooling, Studio, or direct LangGraph Server compatibility.

---

## Configuration

### Main Configuration (`config.yaml`)

Place in project root. Config values starting with `$` resolve as environment variables.

Key sections:
- `models` - LLM configurations with class paths, API keys, thinking/vision flags
- `tools` - Tool definitions with module paths and groups
- `tool_groups` - Logical tool groupings
- `sandbox` - Execution environment provider
- `skills` - Skills directory paths
- `title` - Auto-title generation settings
- `summarization` - Context summarization settings
- `subagents` - Subagent system (enabled/disabled)
- `memory` - Memory system settings (enabled, storage, debounce, facts limits)

Provider note:
- `models[*].use` references provider classes by module path (for example `langchain_openai:ChatOpenAI`).
- If a provider module is missing, DeerFlow now returns an actionable error with install guidance (for example `uv add langchain-google-genai`).

### Extensions Configuration (`extensions_config.json`)

MCP servers and skill states in a single file:

```json
{
  "mcpServers": {
    "github": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": {"GITHUB_TOKEN": "$GITHUB_TOKEN"}
    },
    "secure-http": {
      "enabled": true,
      "type": "http",
      "url": "https://api.example.com/mcp",
      "oauth": {
        "enabled": true,
        "token_url": "https://auth.example.com/oauth/token",
        "grant_type": "client_credentials",
        "client_id": "$MCP_OAUTH_CLIENT_ID",
        "client_secret": "$MCP_OAUTH_CLIENT_SECRET"
      }
    },
    "postgres": {
      "enabled": false,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-postgres", "postgresql://localhost/mydb"],
      "description": "PostgreSQL database access",
      "routing": {
        "mode": "prefer",
        "priority": 50,
        "keywords": ["orders", "users", "SQL", "database", "table"]
      },
      "tools": {
        "query": {
          "routing": {
            "priority": 100,
            "keywords": ["query database", "orders table", "metrics"]
          }
        }
      }
    }
  },
  "skills": {
    "pdf-processing": {"enabled": true}
  }
}
```

`routing` adds soft MCP preference hints to the agent prompt. It helps the
model prefer a configured MCP tool for matching requests without forbidding
other tools. When `tool_search.enabled=true` defers MCP schemas, matching
routing metadata can auto-promote up to `tool_search.auto_promote_top_k`
deferred schemas before the model call.

### Environment Variables

- `DEER_FLOW_CONFIG_PATH` - Override config.yaml location
- `DEER_FLOW_EXTENSIONS_CONFIG_PATH` - Override extensions_config.json location
- Model API keys: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, etc.
- Tool API keys: `TAVILY_API_KEY`, `GITHUB_TOKEN`, etc.

### LangSmith Tracing

DeerFlow has built-in [LangSmith](https://smith.langchain.com) integration for observability. When enabled, all LLM calls, agent runs, tool executions, and middleware processing are traced and visible in the LangSmith dashboard.

**Setup:**

1. Sign up at [smith.langchain.com](https://smith.langchain.com) and create a project.
2. Add the following to your `.env` file in the project root:

```bash
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=lsv2_pt_xxxxxxxxxxxxxxxx
LANGSMITH_PROJECT=xxx
```

**Legacy variables:** The `LANGCHAIN_TRACING_V2`, `LANGCHAIN_API_KEY`, `LANGCHAIN_PROJECT`, and `LANGCHAIN_ENDPOINT` variables are also supported for backward compatibility. `LANGSMITH_*` variables take precedence when both are set.

### Langfuse Tracing

DeerFlow also supports [Langfuse](https://langfuse.com) observability for LangChain-compatible runs.

Add the following to your `.env` file:

```bash
LANGFUSE_TRACING=true
LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
LANGFUSE_BASE_URL=https://cloud.langfuse.com
```

If you are using a self-hosted Langfuse deployment, set `LANGFUSE_BASE_URL` to your Langfuse host.

### Dual Provider Behavior

If both LangSmith and Langfuse are enabled, DeerFlow initializes and attaches both callbacks so the same run data is reported to both systems.

If a provider is explicitly enabled but required credentials are missing, or the provider callback cannot be initialized, DeerFlow raises an error when tracing is initialized during model creation instead of silently disabling tracing.

**Docker:** In `docker-compose.yaml`, tracing is disabled by default (`LANGSMITH_TRACING=false`). Set `LANGSMITH_TRACING=true` and/or `LANGFUSE_TRACING=true` in your `.env`, together with the required credentials, to enable tracing in containerized deployments.

---

## Development

### Commands

```bash
make install    # Install dependencies
make dev        # Run Gateway API + embedded agent runtime (port 8001)
make gateway    # Run Gateway API without reload (port 8001)
make lint       # Run linter (ruff)
make format     # Format code (ruff)
make detect-blocking-io  # Inventory blocking IO that may block the backend event loop
make migrate-rev MSG="..."  # Autogenerate a new alembic revision against the live ORM models
```

### Schema Migrations

DeerFlow's application tables (`runs`, `threads_meta`, `feedback`, `users`,
`run_events`, and the `channel_*` tables) are owned by alembic. The Gateway
runs `alembic upgrade head` automatically on startup via
`bootstrap_schema(engine, backend=...)`, so operators do not run `alembic`
manually in production. Bootstrap is concurrency-safe (Postgres advisory lock
across processes; per-engine `asyncio.Lock` inside one SQLite process) and
idempotent against pre-existing schemas (empty / legacy / versioned).

When you add or change an ORM model, ship the change as a new revision under
`packages/harness/deerflow/persistence/migrations/versions/`:

```bash
make migrate-rev MSG="add foo column to runs"
```

The target invokes `scripts/_autogen_revision.py`, which builds a fresh temp
SQLite at `head` and diffs the live models against it — so a clean checkout
does not need a pre-existing `./data/deerflow.db`. Review the generated file
and switch raw `op.add_column` / `op.drop_column` calls to the idempotent
helpers in `migrations/_helpers.py` before committing. There is no
`make migrate` / `make migrate-stamp` target on purpose — Gateway startup is
the only execution path, which keeps operational mistakes off the table. See
`backend/CLAUDE.md` (Schema Migrations) for the full design.

### Code Style

- **Linter/Formatter**: `ruff`
- **Line length**: 240 characters
- **Python**: 3.12+ with type hints
- **Quotes**: Double quotes
- **Indentation**: 4 spaces

### Testing

```bash
# Offline backend suite (live external-API tests are excluded)
make test

# Explicit real-API DeerFlowClient integration suite
make test-live
```

The live suite requires a valid root `config.yaml` and API credentials. It may
incur API costs or create local sandboxes, artifacts, and files, so it is not
part of default test runs or CI. Direct pytest invocation of
`tests/test_client_live.py` also requires
`DEER_FLOW_RUN_LIVE_TESTS=1`.

`make detect-blocking-io` statically scans backend business code for blocking
IO that may run on the backend event loop and is not test-coverage-bound. It
prints a concise summary for human review and writes complete JSON findings to
`.deer-flow/blocking-io-findings.json` at the repository root (regardless of
whether the target is invoked from the repo root or from `backend/`). JSON
findings include both broad IO category and review-oriented fields such as
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code`. `priority` is a deterministic review ordering from the operation type,
not proof of a bug. Bare-name same-file calls are resolved by function name,
so duplicate helper names in one file can conservatively over-report async
reachability.

---

## Technology Stack

- **LangGraph** (1.0.6+) - Agent framework and multi-agent orchestration
- **LangChain** (1.2.3+) - LLM abstractions and tool system
- **FastAPI** (0.115.0+) - Gateway REST API
- **langchain-mcp-adapters** - Model Context Protocol support
- **agent-sandbox** - Sandboxed code execution
- **markitdown** - Multi-format document conversion
- **tavily-python** / **firecrawl-py** - Web search and scraping

---

## Documentation

- [Configuration Guide](docs/CONFIGURATION.md)
- [Architecture Details](docs/ARCHITECTURE.md)
- [API Reference](docs/API.md)
- [File Upload](docs/FILE_UPLOAD.md)
- [Path Examples](docs/PATH_EXAMPLES.md)
- [Context Summarization](docs/summarization.md)
- [Plan Mode](docs/plan_mode_usage.md)
- [Setup Guide](docs/SETUP.md)

---

## License

See the [LICENSE](../LICENSE) file in the project root.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution guidelines.
