import { describe, expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryDecisionCapability } from "@/components/workspace/soc/soc-memory-decision-capability";
import type { SocMemoryRecord } from "@/core/soc";

function record(
  decisionDirective: SocMemoryRecord["decision_directive"],
): SocMemoryRecord {
  return {
    decision_directive: decisionDirective,
  } as SocMemoryRecord;
}

describe("SocMemoryDecisionCapability", () => {
  test("makes reference-only use the primary visible state", () => {
    const html = renderToStaticMarkup(
      <SocMemoryDecisionCapability record={record(null)} />,
    );

    expect(html).toContain('data-memory-use-mode="reference_only"');
    expect(html).toContain('data-memory-use-primary="true"');
    expect(html).toContain("仅供研判参考");
    expect(html).toContain("不会直接改变最终结论");
  });

  test("keeps exact conclusion reuse visually distinct", () => {
    const html = renderToStaticMarkup(
      <SocMemoryDecisionCapability
        record={record({
          effect: "override",
          target_verdict: "false_positive",
          minimum_match_score: 5,
        } as SocMemoryRecord["decision_directive"])}
      />,
    );

    expect(html).toContain('data-memory-use-mode="exact_match_decision"');
    expect(html).toContain("精确匹配可复用结论");
  });
});
