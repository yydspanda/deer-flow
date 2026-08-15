# soc-product-manager skill

## Location

`.agents/skills/soc-product-manager/`

## Purpose

Project-specific product-management skill for the SOC Agent. Use it before turning feature ideas into implementation tasks.

## Use Cases

| Use case | Output |
|---|---|
| Discuss whether to build a feature | PM verdict: build / defer / spike / reject |
| Prepare implementation | Mini PRD with scope, non-goals, metrics, risks |
| Start coding soon | User stories, acceptance criteria, test cases |
| Debate roadmap phase | Phase cut and evidence required to move earlier |

## Notes

The skill is stored in the repository and auto-discovered by Codex as a repository-scoped
workflow. It should not be copied into a user-global skills directory because its source
documents and decisions are specific to this SOC Agent project. Invoke it explicitly with
`$soc-product-manager`, or let Codex select it from its description.
