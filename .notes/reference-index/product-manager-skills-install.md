# Product Manager Skills Install

## Source

`deanpeters/Product-Manager-Skills`

## Strategy

Install a focused, reusable PM toolkit globally, then use the repository-scoped
`soc-product-manager` as the SOC Agent overlay.

## Implicit / Frequent Skills

These are installed for normal implicit use:

- `problem-framing-canvas`
- `prd-development`
- `user-story`
- `prioritization-advisor`
- `opportunity-solution-tree`
- `roadmap-planning`
- `jobs-to-be-done`
- `discovery-interview-prep`
- `user-story-mapping`
- `user-story-splitting`
- `derisk-measurement-advisor`
- `recommendation-canvas`
- `context-engineering-advisor`
- `feature-investment-advisor`

## Explicit / Low-Frequency Skills

These are installed but configured with:

```yaml
policy:
  allow_implicit_invocation: false
```

Use them by explicit `$skill-name` invocation when needed:

- `company-intel`
- `company-research`
- `customer-journey-map`
- `customer-journey-mapping-workshop`
- `discovery-process`
- `epic-breakdown-advisor`
- `epic-hypothesis`
- `finance-based-pricing-advisor`
- `finance-metrics-quickref`
- `lean-ux-canvas`
- `pestel-analysis`
- `pol-probe`
- `pol-probe-advisor`
- `positioning-statement`
- `positioning-workshop`
- `press-release`
- `proto-persona`
- `stakeholder-identification`
- `stakeholder-mapping`
- `stakeholder-engagement-advisor`
- `tam-sam-som-calculator`
- `business-health-diagnostic`
- `saas-revenue-growth-metrics`
- `saas-economics-efficiency-metrics`
- `workshop-facilitation`

## SOC Overlay

`soc-product-manager` lives at `.agents/skills/soc-product-manager/`. Codex discovers it only
while working in this repository, and it should be used with the generic global PM skills for
SOC Agent work. Do not install a second user-global copy: the overlay depends on this project's
`.notes/ai_soc/` sources and is not a cross-project workflow.

## Notes

Codex normally detects skill changes automatically. Restart Codex if a newly installed or moved
skill does not appear.
