import { describe, expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocDecisionLineageTable } from "@/components/workspace/soc/soc-decision-lineage-table";

describe("SocDecisionLineageTable", () => {
  test("shows changed handling and review even when verdict and confidence are unchanged", () => {
    const html = renderToStaticMarkup(
      <SocDecisionLineageTable
        stages={[
          {
            stage: "base",
            status: "observed",
            verdict: "suspicious",
            confidence: 0.68,
            needs_review: false,
            suggested_action: "核实活动用途。",
            summary: "初判。",
          },
          {
            stage: "tenant_policy",
            status: "applied",
            verdict: "suspicious",
            confidence: 0.68,
            needs_review: true,
            disposition: "escalated",
            suggested_action: "转交并核实授权。",
            summary: "企业策略建议。",
          },
        ]}
      />,
    );
    for (const text of [
      "模型初判",
      "企业策略",
      "处置方案",
      "未要求复核",
      "要求复核",
      "转交复核",
      "已应用到决策",
      "转交并核实授权。",
    ])
      expect(html).toContain(text);
    expect(html).not.toContain("已经转交");
  });
});
