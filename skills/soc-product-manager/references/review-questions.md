# SOC Product Review Questions

Use these questions when the user proposes a vague feature or asks “should we build this?”

Ask no more than 3 questions at a time.

## First Questions

1. Who is the user for this feature: analyst, shift lead, admin, manager, or developer?
2. What decision or action becomes faster, safer, or more consistent?
3. What bad outcome are we trying to avoid: missed attack, wasted analyst time, knowledge pollution, or poor auditability?

## Scope Questions

- Is this needed for Phase 1 CLI, Phase 4 daemon, or Phase 5 Web UI/Knowledge RAG?
- Can it work without a custom Web UI?
- Can it work without autonomous action?
- Does it need historical data, or can it start with one alert?

## Safety Questions

- What is the cost if the agent is wrong?
- Does a human review the decision before it affects future alerts?
- What evidence must be stored for audit?
- How do we undo the feature’s effect?

## Measurement Questions

- What metric proves it helped?
- How many alerts/users must use it before we trust it?
- What threshold turns it from “interesting” to “ship it”?

## Cutline Questions

- What is the smallest version that creates value?
- What can be faked manually for the first version?
- What should explicitly not be built until after real analyst feedback?
