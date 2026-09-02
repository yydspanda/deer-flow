---
name: pingan-internal-release
description: Prepare, inspect, or apply DeerFlow SOC releases for the offline PingAn Apple Silicon DEV/STG environment. Use when packaging code for internal transfer, choosing between a full handoff and an incremental update, rebuilding the private overlay, switching the governed Runtime profile, or recovering an internal deployment. Do not use for ordinary Git pushes or public deployments.
---

# PingAn Internal Release

Prepare an auditable offline release for this repository. Do not adapt a generic
"copy files from the last N commits" recipe: Git history, private configuration,
runtime data, migrations, service lifecycle, and rollback are part of the release.

## Read First

Read only the sections needed for the requested operation:

1. `AGENTS.md`, `scripts/AGENTS.md`, and
   `backend/soc_agent/integrations/pingan/AGENTS.md`.
2. `.notes/ai_soc/integrations/pingan-internal-continuation-handoff.md` for the
   current internal gate and handoff sequence.
3. `.notes/ai_soc/progress.md` for the current execution pointer.
4. [references/release-contract.md](references/release-contract.md) for mode
   selection, bundle metadata, application gates, and rollback.

Treat generated Runbooks and manifests as evidence for one release, not as the
source of current project status.

## Select One Release Mode

Choose the smallest mode that remains recoverable:

- **Full handoff:** initial install, target without a verified Git baseline,
  recovery from unknown drift, incompatible base, or an explicitly requested
  complete rebuild. Use the repository's existing full transfer builder.
- **Incremental Git bundle:** routine tracked-code update when the target reports
  an exact base commit and that base is an ancestor of the intended target.
  Transfer Git objects, not a ZIP of copied files.
- **Private-overlay refresh:** credentials or internal endpoint/profile data
  changed while tracked code did not. Keep it separate from the source update and
  use only approved private transport.

If code and private configuration both changed, produce two independently hashed
artifacts. If the target cannot prove its base commit, stop and use full handoff.

The current maintained full source archive deliberately excludes `.git/`. A Mac
installed from that archive is therefore **not** eligible for Git-bundle updates,
even when its Runbook records a source commit. Keep using full handoff for that
checkout. Do not initialize Git in place: the curated source archive omits tracked
reference/data trees and is not a complete Git worktree. Incremental mode becomes
available only after a separately reviewed deployment path creates a complete,
verifiable Git checkout.

## Required Workflow

1. Inspect `git status`, active branch, target commit, upstream relation, and the
   exact base commit reported by the internal target. Do not infer the base solely
   from "last N commits".
2. Show the commit range and `git diff --name-status -M <base> <target>` before
   building. Call out deletions, renames, lockfiles, migrations, deployment code,
   and private-profile generator changes.
3. Require a clean committed target for a final artifact. Never package local
   secrets, ignored Runtime state, PKL/XLSX/SQLite data, `.venv`, `node_modules`,
   logs, or Git credentials.
4. Build and inspect the selected artifact. Record full commit IDs, SHA-256,
   branch/ref, changed paths, release requirements, and whether the artifact is a
   final or development-only handoff.
5. Keep generated artifacts under the Git-ignored
   `backend/.deer-flow/internal-transfer/` tree. Do not delete an older verified
   package until its replacement passes inspection and the user authorizes the
   deletion.
6. Before an internal mutation, require explicit user authorization. Verify the
   target baseline, stop the old Host DEV checkout, preserve declared runtime
   state, apply only a fast-forward update, run conditional dependency/migration
   steps, restart, and verify health. On failure, restore the exact prior commit
   and preserved state.
7. Report what was proven locally and what still requires the internal Mac. Mock,
   archive inspection, or external tests do not close a real PingAn gate.

## Project-Specific Commands

For full handoff, use the maintained builder rather than recreating packaging
logic:

```bash
backend/.venv/bin/python scripts/build_pingan_internal_transfer.py \
  --include-private-overlay
```

Inspect both resulting archives with the same builder's `--inspect` mode. The
generated `INSTALL-PINGAN-MAC.sh` and Runbook own the data-preserving redeploy.

For an incremental release, use `git bundle` with an exact prerequisite:

```bash
git merge-base --is-ancestor <base-commit> <target-ref>
git diff --name-status -M <base-commit> <target-ref>
git bundle create <output.bundle> <target-ref> ^<base-commit>
git bundle verify <output.bundle>
git bundle list-heads <output.bundle>
```

The target ref must resolve to the recorded target commit. A successful local
`git bundle verify` proves bundle structure only; the internal target must also
verify that it owns the prerequisite base before applying it.

## Prohibited Shortcuts

- Do not generate `import.sh`/`import.bat` that blindly copies files into an active
  checkout or executes an unverified deletion list.
- Do not hard-code `/Users/<person>`; resolve the target as `$HOME/deer-flow` and
  confirm it before mutation.
- Do not use `git diff-tree HEAD~N..HEAD` as the release identity. Commit counts are
  a convenience for display, not a compatibility contract.
- Do not apply a bundle with merge, rebase, force, or a dirty tracked worktree.
  The update must be exact-base and fast-forward only.
- Do not combine source, private overlay, corpus, SQLite, or generated validation
  results into one archive.
- Do not claim rollback, migration, model, ZEUS callback, or browser acceptance
  unless the corresponding command actually ran and its evidence was retained.
