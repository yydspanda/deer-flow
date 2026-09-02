# PingAn Offline Release Contract

Use this reference after selecting a release mode. It defines stable release
invariants; current task state remains in the authoritative `.notes/ai_soc`
documents.

## Environment Boundary

- Target: Apple Silicon macOS, no Docker requirement, no public network.
- Checkout: `$HOME/deer-flow`; resolve `$HOME` at runtime and never embed one
  developer's username.
- Runtime: `scripts/soc_pingan_macos_host_dev.py`, Python `3.12+`, project uv
  environment, pinned pnpm, and non-root nginx.
- Persistence: checkout-owned, isolated SOC SQLite files for DEV and STG. Preserve
  `backend/.deer-flow/data` together with SQLite WAL/SHM/journal sidecars; switching
  the deployment profile selects `soc_agent_dev.db` or `soc_agent_stg.db` without
  copying records between them.
- Private configuration: `.env.soc-dev.local`, `config.pingan-dev.local`, RSA
  material, and PingAn profiles use the separate private-overlay boundary.
- Governed PingAn mapping: project DEV activates the stored ZEUS PRD profile;
  project STG activates the stored ZEUS STG profile. Model/Agent Platform targets,
  Provider execution modes, and action authority remain independently configured.
- Large corpus PKLs and Workbench payload SQLite remain separately staged from
  approved internal storage and are never source-update payloads.

## Mode Decision

| Condition | Required mode |
|---|---|
| First install or target has no verifiable Git base | Full handoff |
| Target commit differs from declared base or has tracked drift | Full handoff or explicit recovery; no incremental apply |
| Exact base is an ancestor of target and only tracked code changed | Incremental Git bundle |
| Only ignored private endpoint/credential/profile data changed | Private-overlay refresh |
| Source and private profile both changed | Incremental/full source artifact plus a separate private overlay |
| Database/corpus/runtime data changed through normal operation | Preserve in place; never package as source |

The currently generated source archive excludes `.git/` and selected tracked
reference/data trees. Its recorded commit is release provenance, not an incremental
apply capability. Treat every checkout installed from that archive as
`git_baseline_available=false`; do not run `git init`, synthesize a branch, or apply a
Git bundle over it. A future incremental bootstrap must be designed and accepted as a
separate change.

Dependency or SOC migration changes do not automatically require a full source
archive, but the release manifest must require the corresponding install or
`soc db upgrade` step. An updater that cannot perform those steps safely must
fall back to full handoff.

## Incremental Manifest

Every bundle release must have a JSON manifest next to it. At minimum record:

```json
{
  "schema_version": "soc.pingan_incremental_release.v1",
  "release_kind": "git_bundle",
  "branch": "yyds-dev",
  "base_commit": "<40-hex prerequisite>",
  "target_commit": "<40-hex target>",
  "bundle": {
    "filename": "<name>.bundle",
    "sha256": "<64-hex>",
    "size_bytes": 0
  },
  "changes": {
    "commit_count": 0,
    "added": [],
    "modified": [],
    "deleted": [],
    "renamed": []
  },
  "requirements": {
    "dependency_install": false,
    "soc_database_upgrade": false,
    "private_overlay_refresh": false,
    "service_restart": true
  },
  "source_worktree_dirty": false,
  "final_handoff_eligible": true
}
```

Do not include commit messages containing sensitive business data in the public
manifest. A bounded subject list may be shown interactively but is not required
in the artifact.

## Change Classification

Set `dependency_install=true` when the range changes dependency declarations or
locks, including backend `pyproject.toml`/`uv.lock` or frontend
`package.json`/`pnpm-lock.yaml`.

Set `soc_database_upgrade=true` when the range changes
`backend/soc_agent/db/migrations/` or migration ownership code. The apply path
must use the absolute checkout-resolved SOC database URL and verify
`soc_alembic_version` afterward.

Set `private_overlay_refresh=true` when tracked profile preparers, private-overlay
schema/validation, internal model gateway profile shape, or required private
inventory changes. This flag means a separately approved overlay may be needed;
the Git bundle must still contain no secret values.

Treat installer, Host DEV lifecycle, persistence allowlist, model gateway, legacy
ZEUS compatibility, and transfer-builder changes as high-attention changes in the
operator summary.

## Internal Apply Gates

Before changing the target:

1. Verify artifact SHA-256 and `git bundle verify`.
2. Verify target is a Git checkout and `HEAD == base_commit`.
3. Reject tracked modifications, an unresolved operation, or a non-fast-forward
   target. Ignored Runtime/private files are expected and must not be erased.
4. Verify the bundle advertises exactly `target_commit` for the expected branch.
5. Preserve the existing request/validation evidence and the runtime-state
   allowlist owned by `INSTALL-PINGAN-MAC.sh`.

Apply in this order:

```text
preflight
  -> fetch bundle into a temporary ref
  -> prove fast-forward target
  -> stop Host DEV and release 3000/8001/2026/4001/8090
  -> create rollback identity
  -> advance tracked source
  -> dependency install when declared
  -> SOC migration when declared
  -> start Host DEV
  -> health, sidecar, and release-specific smoke
  -> retain report and remove rollback only after acceptance
```

If any post-update gate fails, stop the new services and restore the exact base
tracked tree without deleting ignored state. Report the failed gate; do not retry
external model/ZEUS operations unless their idempotency/recovery contract explicitly
allows it.

## Full Handoff Gates

Use `scripts/build_pingan_internal_transfer.py` and its generated installer and
Runbook. Final handoff requires a clean committed worktree, complete required
source inventory, valid source/private manifests, safe archive members, exact
SHA-256, and `final_handoff_eligible=true`.

The full installer remains the recovery path when the incremental prerequisite
cannot be proven. It must preserve declared runtime data and never replace or
recreate an existing SOC database during routine redeploy.
