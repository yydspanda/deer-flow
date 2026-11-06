# Upstream v2.2.0 integration and fork boundaries

## Scope

The integration branch `integration/upstream-v2.2.0-20261105` merges stable tag
`v2.2.0` (`58fc5bd5b8e6ea46538e9481ba06ca2a46e14e2a`) into fork commit
`1b870282792c93a664567eb77c84b3d771ab3f1a`. The original `yyds-dev` checkout remains
separate during validation. This is a code integration, not an internal deployment.
The legacy `sec_know_model` delivery and its 120-second polling update are separate.

The fetched `upstream/main` is already on 2.3.0 development. The chosen stable tag
has release-only commits and is not an ancestor of that main snapshot. Integrating
this tag does not claim to catch up with main or close real PingAn integration gates.
Keep the existing upstream-drift threshold; reassess main separately.

## Original conflicts

| Area | Paths |
| --- | --- |
| Agent assembly and execution | `backend/packages/harness/deerflow/agents/lead_agent/agent.py`; `agents/middlewares/configured_extensions.py`; `client.py`; `tools/builtins/task_tool.py` (last three under the same `deerflow/` directory) |
| Gateway | `backend/app/gateway/routers/threads.py`; `backend/app/gateway/services.py` |
| Frontend | `frontend/.gitignore`; `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`; `frontend/src/components/workspace/workspace-nav-chat-list.tsx`; `frontend/src/core/threads/types.ts` |
| Startup | `scripts/serve.sh` |
| Tests | `backend/tests/test_agent_guidance_check.py`; `test_client.py`; `test_compose_default_bind_host.py`; `test_gateway_services.py`; `test_subagent_prompt_security.py`; `test_threads_router.py` (all under `backend/tests/`) |
| Guidance and lock | `AGENTS.md`; `scripts/AGENTS.md`; `backend/uv.lock` |

These are 20 conflicted files, not 20 business features. Automatically merged files
also require compatibility checks against the new dependency versions.

## Keep business code separate

- Agent-chat review actions and queue-bound navigation live in
  `frontend/src/components/workspace/soc/soc-agent-review-actions.tsx` and
  `frontend/src/core/soc/agent-review.ts`. The shared page keeps only the necessary
  data and render connections. The SOC review transport retains `assistant_id=lead_agent`
  with `agent_name=soc-triage` in context; ordinary agents use upstream routing.
- The SOC navigation item lives in `soc-navigation-item.tsx`. The shared navigation
  list imports and renders it.
- Shared thread types use the upstream extension-compatible shape. No SOC-specific
  fields need to remain in `frontend/src/core/threads/types.ts`.
- Existing SOC backend services remain in `backend/soc_agent/`; Gateway adapters
  remain in SOC-named modules. Do not inline their business rules into generic routes.
- Detailed fork operating instructions live in scoped SOC reference documents.
  `scripts/AGENTS.md` can remain identical to upstream, with the mandatory SOC
  reference linked from the root guide. Guidance size gates remain enforced.

## Small framework differences still required

| Hook | Why retained | Contract to preserve |
| --- | --- | --- |
| Named-agent middleware and skills | Installed SOC profiles depend on mandatory guards and a bounded skill set | Agent-specific configuration participates in graph caching; missing/broken profiles fail instead of silently removing guards |
| Embedded runtime context | Internal SOC analysis attaches trusted extension state | Deep-copy namespaced keys; reject identity, authorization and upstream server-owned keys |
| Subagent output policy | SOC specialists must not emit disallowed result forms | Configured rejection keeps usage/error metadata and does not emit rejected result text |
| Gateway admission and message provenance | SOC binding is built server-side and cannot be supplied by clients | Preserve upstream reserved metadata and strip untrusted SOC provenance after upstream message normalization |
| Local frontend entry and resolved environment | PingAn host startup owns its prebuilt snapshot and private environment | `--frontend-entry` and `--skip-env`; normal env loading still uses upstream's safe parser |

These are reviewed source differences with tests, not monkey patches. A patch file
or automatic text replacement still has to be reconciled when upstream changes the
same contract, and can conceal incompatibilities if applied without review.
Upstream extension APIs should be the first choice for new behavior. Moving existing
admission/provenance guards into plugins requires equivalent trusted hooks first;
removing them just to obtain a clean diff would change behavior.

## Verification

Validation uses an isolated worktree and environments synchronized to committed
lockfiles. No model/provider requests, production database migrations, service
starts, or internal release package updates are part of this merge.
Completed checks use the v2.2.0 lockfiles (groups can overlap; do not add their counts):

| Check | Result |
| --- | --- |
| Harness/SDK/agent delegation, skills, authorization and knowledge contracts | 665 passed, 29 skipped |
| Gateway/SOC admission and state, host bootstrap and migrations | 605 passed, 5 skipped; the skips require a PostgreSQL test URI |
| Gateway/Agent blocking-I/O | 16 passed |
| Local startup | 67 passed |
| PingAn Mac host launcher | 66 passed |
| Safe dotenv, caller-resolved environment and guidance gates | 23 passed |
| Frontend SOC, threads, agents, extensions and SDK transport | 651 passed |
| Frontend static validation and production build | ESLint, TypeScript and Next.js build passed; no service started |
| Backend style and release identity | Ruff check/format, lockfile check and version-source check passed |

The expanded SOC regression selected 3,524 tests in four groups: 3,475 passed,
43 skipped and six failed on the first complete isolated run. The failures were
tracked to old test inputs/assertions or a stale sample-config version:

- Corpus restart tests assumed `off` was still the default. Explicit `off`/`apply`
  inputs now exercise real setting changes, LAN authority denial and reviewed-Memory
  requirements; validation restart, learning restart and default-option modules
  passed together (47 tests).
- The PingAn example still declared config version 38. The real migrations were
  verified to preserve this profile's settings, then its version was advanced to 63;
  all 12 tests in the sample-validation module passed.
- Governance-schema expectations were updated to migration 0033's current-only
  unique indexes, including their columns and predicates.
- Stub application composition now passes the typed `SocAnalyzerMode.STUB` value;
  the tests explicitly reject any attempt to load private operator configuration.

The final two modules and their related pattern-revision tests passed together
(64 tests). All six failed cases are covered by these passing focused runs.
This records the broad run and focused corrections separately; it does not claim
a second full-suite run.
Earlier fixture corrections explicitly select `off` for stub analysis and for a
genuinely changed corpus configuration. Production behavior and authority checks
are not weakened to accommodate tests. Runs clear inherited SOC/config-path variables.
The expanded run uses real temporary SQLite databases on tmpfs because this host's
disk fsync stalls made the disk-backed run impractical; transaction, locking and sync
code is unchanged. These are functional tests, not production disk-performance evidence.

## Future sync procedure

1. Measure main and stable-release changes; record the selected upstream commit.
2. Merge in an isolated worktree; inspect both conflicts and automatically merged
   integration points.
3. Prefer upstream implementations plus calls into separately owned SOC modules.
   Avoid placing business implementation inside shared pages or framework functions.
4. Run focused contract tests plus the affected SOC/Gateway/frontend/startup suites
   against the new lockfiles. Only then promote the integration branch.
5. When upstream supplies an equivalent hook, remove the local framework difference
   while retaining its behavior tests. Do not maintain a second source tree or apply
   unchecked patches at service startup.
