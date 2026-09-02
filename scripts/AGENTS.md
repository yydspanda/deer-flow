## Service Startup Contracts

The root `PORT` value configures Docker's published nginx ingress only; local
orchestration pins Next.js to `3000`. Runtime commands launch from the already
synchronized environment with `uv run --no-sync`. Production Compose probes
Gateway `/health`, and `deploy.sh` waits for all services before reporting
success; failures print Compose status and recent Gateway logs.

PingAn internal Apple Silicon DEV/STG uses
`scripts/soc_pingan_macos_host_dev.py` as a tenant-specific wrapper around the
same host launcher. `install` may use only the configured PingAn PyPI/NPM
registries; `start` must retain `--skip-install`, disable Next.js telemetry, and
use `LocalSandboxProvider`. `SOC_PINGAN_ENV` is the single host Runtime selector:
`dev` enables the Memory/Corpus workbenches and uses `soc_agent_dev.db`; `stg`
disables those DEV surfaces, rejects `--demo-no-auth`, and uses
`soc_agent_stg.db`. DEV launches DeerFlow with `--dev`; STG launches it with
`--prod` so HMR is not part of the staging process. Memory, tenant-policy, and
automation scopes must be derived from that selector rather than configured
independently. Reviewed tenant policy
remains enabled and real external action execution remains disabled in both
profiles. DEV must validate the canonical Memory corpus plus the merged
corpus/index/payload store before starting; STG must not depend on those
demonstration artifacts. The wrapper must not add Docker as a prerequisite,
hard-code a developer home path, or create a second Gateway/frontend/nginx implementation.
Before starting any sidecar, `start` must resolve the absolute local SOC SQLite URL,
run the SOC migration once, and pass that URL to the API/worker with their own
auto-migration disabled. `status` reports the persisted `soc_alembic_version` without
creating a missing database. A pristine SQLite initialization may clean its own partial
artifacts and retry one transient `disk I/O error`; an existing database is never deleted
or retried destructively. Generated Runbooks therefore must not require operators to
source local profiles and repeat migration manually.
After external corpus staging, `check` must also validate all runtime files,
mode-`0600` local profiles, the project model-gateway references, isolated SQLite
configuration, and the non-root nginx config. It must reject retired LiteLLM model
references before dependency installation or migration.

Host Runtime scope does not imply a ZEUS target. The initial private profile keeps
the Runtime, SQLite, Memory, automation, and Workbench in `dev`, but pins the shared
asset/TI/security-tag/lifecycle/callback ZEUS target to reviewed PRD configuration.
The legacy model-profile preparer statically writes that PRD target and explicit
confirmation into the mode-`0600` ignored env. Transfer validation must reject any
other ZEUS target; lifecycle and callback still start in `fake` mode. The governed
runtime switch may change only `SOC_PINGAN_ENV` between `dev` and `stg`; it must
preserve ZEUS/model credentials, Provider modes, and action authority byte-for-byte.

That wrapper also owns three sidecars through
`scripts/soc_pingan_host_sidecars.py`: the loopback project model gateway on `4001`, the
legacy ZEUS compatibility API on `8090`, and one durable compatibility worker. Internal
DEV may bind authenticated `8090` to the trusted LAN to preserve the old upstream entry;
`--local-only` must force it back to loopback, and `4001` must never leave loopback. Sidecar PID/state
files are marker-checked before signaling, logs remain under the ignored Host DEV state
directory, and `stop` includes disabled services so stale processes cannot survive a
configuration change. Keep the model gateway single-process because its admission
semaphore is process-local. The old `sec_know_model`, LiteLLM, Celery, and Redis startup
scripts are reference material, not Host DEV dependencies.

`backend/scripts/soc_pingan_legacy_fake_acceptance.py` is the no-network
composition check and must always report `simulated=true`. It constructs its own
value-free protocol fixture and must not depend on `datas/legacy_demos`, PKL files, or
private alert input. The separate
`soc_pingan_prepare_legacy_live_request.py` accepts one approved pending alert ID,
reads the complete payload from the hash-bound Workbench payload store, creates a
fresh session, and atomically writes the private request at mode `0600`; operators
must not copy/edit the tracked protocol example. `soc_pingan_set_legacy_provider_mode.py`
must update lifecycle and callback together without printing the private env. The separate
`soc_pingan_legacy_live_acceptance.py` accepts only a private mode-`0600`
`*.local.json`, loopback compatibility API, explicit `--confirm-live`, and both
provider modes set to `internal`. Its pass gate requires a fresh durable job,
idempotent replay, real pending-alert lifecycle evidence, a Runtime run/model,
and a delivered real callback attempt. Reports must contain hashes and state
only, never request/result bodies or credentials.
Run `soc_pingan_zeus_lifecycle_smoke.py` first against the same prepared request.
It is read-only and must prove `provider_code=200`, `provider_status=1`, and
`mocked=false` before a model-backed task is submitted. All ISEC providers must sign
the exact serialized bytes later passed to HTTPX with `content=`; `json=` must not
reserialize a signed body.
For an unknown lifecycle business code, the separate
`soc_pingan_zeus_lifecycle_response_probe.py` may print and save the complete response
only after explicit live confirmation. It must reuse the Worker Provider, require the
private prepared request, store only under an ignored mode-`0600` `*.local.json`, and
must not weaken the bounded lifecycle smoke or become a normal telemetry path.
The live command must receive the checkout-resolved absolute SOC database URL and
must verify the SOC migration/evidence tables before its first network request. A
failure after durable submission is recovered only with the same private request and
explicit `--resume-existing`; do not prepare a new session or rerun completed Runtime
and callback work. A resumed pass replaces the fresh-submission gate only, while all
idempotency, lifecycle, Runtime, and real-callback gates remain mandatory.
Do not resume a Job whose persisted provider/worker result itself failed, or after
changing signing/provider/worker code. Keep that Job as audit evidence and create a fresh
session only after the read-only lifecycle smoke passes. Local self-submission proves the
component path; definitive compatibility still requires ZEUS-originated submission and
old-page readback.

Final PingAn transfer packaging first runs the model-gateway and workflow
legacy-profile preparers in the external preparation checkout. Both are static AST
readers: they must not import or execute legacy packages. The generated
`.env.soc-dev.local` and `.secrets/eagw-private-key.der` are mode `0600`, belong only
in the private overlay, and must never enter the source archive or report. Build only
from a clean commit and inspect both archives before replacing the previous
`READY-TO-TRANSFER` contents.

Generated transfer Runbook command blocks must be independently copyable. Any
block that sources `.env.soc-dev.local` must first resolve the current checkout
with `backend/scripts/soc_pingan_local_paths.py --shell` in that same block; do
not rely on `TARGET_REPO`, `SOC_REPO_ROOT`, or shell exports left by an earlier
Runbook step.
Interactive `read` commands inside a `bash <<'MARKER'` block must read from
`/dev/tty`; otherwise they consume the next line of the heredoc as user input
and corrupt the remaining shell program.
Generated Runbooks must present the normal first-run sequence as the primary
path. Put status probes that exist only for interrupted-run recovery after that
sequence, rather than implying a service can already be running before its first
start step.
Clean installation must be delegated to the generated standalone
`INSTALL-PINGAN-MAC.sh` and invoked with `bash`, never sourced. The installer
resolves the transfer directory from its own `BASH_SOURCE`, verifies the exact
archive hashes and staged checkout before stopping the old checkout, and keeps
all `exit` behavior inside its child shell so a failed install cannot close the
operator's interactive terminal. Do not put a bare `exit` in a Runbook command
block.
Do not instruct operators to replace JSON placeholders or edit generated private
requests/config by hand when a bounded repository command can derive and validate them.
Clean redeploy instructions must extract into staging first, invoke the old checkout's
Host DEV `stop` command before deleting that checkout, and fail closed while any of
`3000/8001/2026/4001/8090` is still listening. Never remove or move a running checkout:
daemon processes can retain a deleted cwd and evade the replacement checkout's PID state.
An existing checkout is also the owner of persistent Host DEV state. The generated installer
must copy the explicit allowlist (`backend/.deer-flow/data`, JWT/user/agent/thread Memory and
workspace state, managed integrations/subagents, retrieval state, and internal validation
evidence) into the staged release before deleting its rollback checkout. It must preserve the
whole SQLite data directory so each database travels with its `-wal`/`-shm`/`-journal` sidecars,
while new private-overlay config and `pingan-context` remain release-owned. PID files, logs,
generated skill views, caches, old transfer artifacts, and other ignored output must not leak
across releases. Any restore failure rolls back the old checkout; routine redeploy must never
delete or recreate an existing database.

Large PingAn corpus artifacts are transferred separately, not embedded in either
archive. The private overlay carries the matching corpus manifest/index, and
`scripts/soc_pingan_stage_internal_corpus.py` must validate every source file under
`$HOME/Downloads/source` or `$HOME/Downloads/corpus` before atomically placing it in
the checkout at mode `0600`. Resolve the home directory at runtime; never hard-code a
developer's `/Users/...` path. Missing or mismatched files fail closed before Host DEV
check/install.

## Shell Script Invocation Contract

Root Makefile recipes must invoke repository `.sh` files through
`RUN_SHELL_SCRIPT`. On POSIX this expands to `$(BASH)`; on Windows it uses the
Git Bash wrapper. Shell scripts that invoke sibling repository scripts must
likewise prefix the target with `bash`. This keeps documented `make` commands
working when a source archive, `core.fileMode=false`, or a non-POSIX filesystem
does not preserve executable bits.

## Public Skill Review Waivers

`review_changed_public_skills.py` keeps the analyzer strict and applies narrow
CI-only exceptions from `.github/skill-review-waivers.v1.json`. The manifest is
versioned by `contracts/skill_review/waiver_manifest.v1.schema.json`; each entry
must identify one current error by package, source, rule, path, line, and
evidence, and pin the complete source file with SHA-256 plus an expiry date.
Blockers are never waivable, and waived errors are still printed with their
original severity and justification.

For pull requests, only the base revision's manifest is effective. The head
manifest is parsed and checked against the current analyzer output, but cannot
self-authorize a finding in the same pull request. Push comparisons use the
same before/after trust boundary. A waiver-only change can therefore land
without weakening its own check, then become effective for later changes after
it is part of the trusted base.

## Backend Static Analysis Commands

The root `detect-thread-boundaries` target statically inventories execution
boundaries under `backend/app/` and `backend/packages/harness/deerflow/`. It
prints a concise count by execution domain and writes the complete, versioned
JSON payload to `.deer-flow/thread-boundary-inventory.json`. Every finding has
a stable `boundary_kind`: `asyncio_default_executor`, `dedicated_executor`,
`anyio_worker_thread`, `direct_event_loop_blocking`, `separate_event_loop`, or
`unresolved_dynamic_boundary`.

The AST inventory covers `asyncio.to_thread`, default and explicit
`run_in_executor` submissions, imported aliases, simple same-module helper
wrappers (after pre-registering dedicated executor targets), `set_default_executor`,
`ThreadPoolExecutor` construction/submission,
additional event loops, synchronous LangChain tools, and direct
`BaseChatModel` fallback inheritance. It remains read-only and does not alter
executor routing or sizing.

To supplement the static scan with configured runtime types, run:

```bash
python scripts/detect_thread_boundaries.py \
  --runtime-config config.yaml \
  --json-output .deer-flow/thread-boundary-inventory.json
```

Runtime inspection imports configured tool objects and model classes so it can
record concrete tool names/types/modules, sync functions, async coroutines,
and `_agenerate`/`_astream` ownership. It does not invoke tools, instantiate
models, or call external services; import failures remain in the JSON as
`unresolved_dynamic_boundary` records. The detector implementation and focused
coverage live in `tests/support/detectors/thread_boundaries.py` and
`tests/test_detect_thread_boundaries.py`.

The `detect-blocking-io` target parses `app/`, `packages/harness/deerflow/`,
and `scripts/` with AST. By default it reports only blocking IO candidates that
are inside async code, reachable from async code in the same file, or reachable
from sync-only `AgentMiddleware` before/after hooks that LangGraph can execute
on the async graph path. It prints a concise summary and writes complete JSON
findings to `.deer-flow/blocking-io-findings.json` at the repository root
(both `make detect-blocking-io` from the repo root and `cd backend && make
detect-blocking-io` resolve to the same repo-root path). JSON findings include
`priority`, `location`, `blocking_call`, `event_loop_exposure`, `reason`, and
`code` for model-assisted or manual review. `priority` is a deterministic
review ordering from operation type, not proof of a bug. Bare-name same-file
calls are resolved by function name, so duplicate helper names in one file can
conservatively over-report async reachability. The call graph also resolves
multi-hop `self.`/`cls.` attribute chains (`self.store.flush()`) and local
variables or parameters traced back — within the same function only — to a
`self.`/`cls.` attribute (`store = self.store; store.flush()`); both fall back
to the same bare-method-name resolution as an unresolvable receiver, so they
share its over-report risk rather than adding a new kind. Deeper cross-function
or cross-module aliasing is out of scope and stays an unreported false
negative.

That same-function alias tracing is deliberately narrower than the symbolic
names `dotted_name()` builds for blocking-call pattern matching elsewhere in
this module: receiver/alias extraction uses a restricted extractor that only
recognizes `Name`/`Attribute` chains, so a `Call` or `Subscript` result (e.g.
`factory().flush()`, or `client = factory(); client.flush()` /
`client = clients[0]; client.flush()`) is never treated as inheriting its
base's alias-worthiness — including when the unsupported node is buried
deeper in the chain (`factory().client.flush()`, `clients[0].client.flush()`):
an unrecognized shape anywhere in the chain makes the whole receiver
unresolved, it never falls back to just the chain's trailing attribute name,
or that name alone could still collide with an unrelated traced parameter or
local alias. Reassigning a traced name to a non-traceable value (anything
other than a `self.`/`cls.` attribute or an already-traced name) kills its
alias instead of leaving it traceable, so a stale alias from an earlier
assignment cannot keep exposing an unrelated same-named method after the
variable is reassigned to something else; the assignment's right-hand side is
always analyzed against the alias state as it stood *before* this kill-or-add
update, matching Python's own evaluate-then-bind order, so
`client = client.flush()` still resolves that call against `client`'s prior
(pre-reassignment) alias instead of the state after it's gone. `if`/`else`
branches get isolated alias state — an alias added in one branch cannot leak
into the other — and the state after the whole `if` is the union of what each
branch produced (a conservative may-alias join), so the result no longer
depends on which branch is textually `body` vs. `orelse`. This branch
isolation is deliberately scoped to `ast.If` only; `ast.Try`/`ast.Match` have
different, more complex control-flow semantics and keep the older unisolated
traversal. Finally, a function's decorators and parameter defaults are
analyzed in the *enclosing* scope rather than the new function's own, and
parameter/return annotations get the same enclosing-scope treatment unless
the module postpones annotation evaluation (`from __future__ import
annotations`), in which case they are skipped entirely, in either scope —
those expressions run at definition time, before the function has ever been
called (or, when postponed, never run at all), so a call there is never
attributed to the function being defined (it moves to whatever scope actually
contains the `def`, e.g. the enclosing function, or disappears if that scope
is module/class level and therefore never async-reachable). PEP 695
type-parameter bounds are not visited in either scope: CPython evaluates each
one lazily, in its own hidden function, only if something like `T.__bound__`
is actually accessed, never as part of running the `def` statement itself.
A `lambda`'s body and a bare generator expression's element/filters/later
`for` clauses are excluded from traversal ONLY while walking another
function's own definition-time expressions (decorators, parameter defaults/
annotations, return annotation): there, we know structurally that the
enclosing `def` statement is executing right now, and neither a lambda body
nor a generator's element runs just because the lambda/generator object is
created — only a lambda's own parameter defaults and a generator's
outermost iterable are genuinely eager at that moment. This exclusion is
absolute and has no exceptions: even a lambda that is immediately invoked at
its own definition site (`(lambda: ...)()`), or a generator passed directly
to an eager-consuming builtin, is still excluded when it appears inside
another function's decorator/default/annotation — a narrow, intentional
limitation given how rarely a definition-time expression contains an
executed call at all, preferred over special-casing specific shapes there.

Everywhere else — module level, class bodies, and ordinary function-body
statements — a lambda body or generator expression's element is scanned
unconditionally, the same conservative, over-report-rather-than-infer stance
this file already takes for reachability elsewhere (the `ast.If` may-alias
union, the bare-name call-graph resolution). This file does not attempt to
distinguish a lambda that is invoked immediately, invoked later through a
stored variable, passed as a callback, or never called at all, nor a
generator that is consumed by an eager builtin (`list`, `sum`, `any`, etc.),
wrapped in another lazy iterator (`map`, `filter`), or never consumed —
telling these apart in the general case would mean inferring evaluation
order and consumption across arbitrary code rather than reading a fixed,
structural fact, so none of them are special-cased; all are scanned the
same way. This is intentionally informational and is not run from CI in
this round.

For a diff-scoped view of the same findings, `scripts/scan_changed_blocking_io.py`
(repo root) reports findings on the added lines of `git diff <base>...HEAD`
plus findings new versus the merge base (so a new async caller exposing an
untouched sync helper in the same file is still reported) — used by the
`blocking-io-guard` skill (`.agent/skills/blocking-io-guard/`) as the
deterministic scope step before routing each candidate to a fix and/or a
`tests/blocking_io/` runtime anchor.

Regression tests related to Docker/provisioner behavior:
- `tests/test_docker_sandbox_mode_detection.py` (mode detection from `config.yaml`)
- `tests/test_provisioner_kubeconfig.py` (kubeconfig file/directory handling)
- `tests/test_provisioner_request_threading.py` (keeps provisioner sandbox CRUD
  endpoints as sync FastAPI handlers so synchronous K8s client calls run in the
  Starlette worker pool instead of on the ASGI event loop)

Blocking-IO runtime gate (`tests/blocking_io/`):
- Wraps every item under `tests/blocking_io/` with a strict Blockbuster
  context scoped to `app.*` and `deerflow.*` (see
  `tests/support/detectors/blocking_io_runtime.py`). Any sync blocking IO
  call whose stack passes through DeerFlow business code while running on
  the asyncio event loop raises `BlockingError` and fails the test.
- Regression anchors live there: `test_skills_load.py` (locks the
  `asyncio.to_thread` offload around `LocalSkillStorage.load_skills`, fix
  for #1917); `test_sqlite_lifespan.py` (locks the offload around
  SQLite path resolution plus `ensure_sqlite_parent_dir`, fix for #1912);
  `test_jsonl_run_event_store.py` (locks `JsonlRunEventStore`'s async
  API — including idempotent singleton-event writes — offloading its file IO
  via `asyncio.to_thread`); `test_run_journal_callbacks.py` (locks
  `RunJournal.run_inline` tool callbacks to in-memory/event-loop-safe work);
  `test_integrations_router.py` (locks Lark integration install and auth
  completion route handlers offloading archive filesystem work and `lark-cli`
  subprocesses);
  `test_uploads_middleware.py` (locks `UploadsMiddleware.abefore_agent`
  offloading the uploads-directory scan off the event loop);
  `test_uploads_router.py` (locks Gateway upload/list/delete endpoints
  offloading upload directory creation, staged writes, chmod/cleanup,
  directory scans/deletes, and remote sandbox sync off the event loop);
  `test_feishu_receive_file.py` (locks Feishu attachment path preparation and
  persistence plus remote sandbox acquisition/sync off the event loop, and
  skips redundant sandbox sync when thread data is already mounted);
  `test_channel_outbound_files.py` (locks Feishu, Telegram, and WeCom outbound
  attachment open/read/hash work off the event loop);
  `test_openviking_memory_backend.py` (locks the official OpenViking backend's
  async add/context/search entrypoints offloading synchronous SDK and cursor
  filesystem IO); and
  `test_workspace_changes_recorder.py` (locks the offload around the snapshot
  text cache lifecycle — roots resolution, `mkdtemp`, and the `shutil.rmtree`
  on both the capture-failure branch and `record_workspace_changes`' `finally`).
- `test_gate_smoke.py` is a meta-test asserting the gate actually catches
  unoffloaded blocking IO and that the `@pytest.mark.allow_blocking_io`
  opt-out works.
- Coverage boundary: the gate only sees code that test execution actually
  touches. Static AST coverage is a separate concern (out of scope for
  this PR).
- CI: runs on every PR via `.github/workflows/backend-blocking-io-tests.yml`,
  hard-fail.

Boundary check (harness → app import firewall):
- `tests/test_harness_boundary.py` — ensures `packages/harness/deerflow/` never imports from `app.*`

Memory backend async boundary:
- `MemoryMiddleware.aafter_agent` calls `MemoryManager.aadd`; network-backed
  managers must override their `a*` methods to offload or use native async I/O.
- The mem0 backend requires an HTTPS `base_url` by default because requests
  carry an API token. Plain HTTP requires the explicit
  `backend_config.allow_insecure_http: true` local-development opt-in.
- Gateway memory routes offload the synchronous management contract with
  `asyncio.to_thread`, so backend file or HTTP I/O does not run on the ASGI
  event loop. Gateway startup and shutdown also resolve the manager off-loop,
  because a backend's `from_config` may perform a fail-fast connectivity check.
- A backend may set `requires_passive_writes_in_tool_mode = True` when tool-mode
  search is supported but durable writes still depend on conversation-level
  extraction. Such backends receive memory tools and retain `MemoryMiddleware`.
- Prompt recall rethrows `MemoryManagerError` only when backend config declares
  `failure_policy.read: fail_closed`; other recall errors preserve the existing
  log-and-empty-context behavior.

CI runs these regression tests for every pull request via [.github/workflows/backend-unit-tests.yml](../.github/workflows/backend-unit-tests.yml).

Agentic browser sessions are process-local. The Gateway startup safety gate rejects
`GATEWAY_WORKERS > 1` when `browser_navigate` is configured, because ordinary
uvicorn worker dispatch does not provide thread affinity for browser tools, REST
navigation, and the Live WebSocket.

Browser Live screenshots remain JPEG bytes inside the harness and the Gateway's
bounded, drop-oldest frame queue. WebSocket clients that request
`frame_format=binary` receive binary messages; control metadata remains JSON.
The legacy no-parameter protocol still base64-encodes frames into JSON at the
Gateway boundary for backward compatibility. Unknown `frame_format` values
receive a JSON error and close code 1008.
