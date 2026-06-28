# Product Manager Skills Install

## Source

`deanpeters/Product-Manager-Skills`

## Strategy

Install a focused PM toolkit globally, then use `soc-product-manager` as the SOC Agent overlay.

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

`soc-product-manager` is installed globally from `skills/soc-product-manager/` and should be used with the generic PM skills for SOC Agent work.

## Notes

Restart Codex or force reload skills if newly installed skills do not appear.
