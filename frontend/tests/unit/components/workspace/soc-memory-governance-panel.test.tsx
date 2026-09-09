import { expect, test, rs } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryGovernancePanel } from "@/components/workspace/soc/soc-memory-governance-panel";
import { useSocMemoryGovernancePreview } from "@/core/soc/hooks";

rs.mock("@/core/soc/hooks", () => ({ useSocMemoryGovernancePreview: rs.fn() }));

function panel(
  replacement: { memoryId: string; version: number } | null = null,
) {
  return renderToStaticMarkup(
    <SocMemoryGovernancePanel
      candidateId="MC-test"
      verdict="true_positive"
      promotedFacets={[]}
      replacement={replacement}
      onReplace={() => undefined}
      facetLabel={(value) => value}
    />,
  );
}

function preview(scope: string) {
  rs.mocked(useSocMemoryGovernancePreview).mockReturnValue({
    data: {
      related_count: 1,
      explanation: "本次审核与已有经验不同，请核对业务事实。",
      related_memories: [
        {
          memory_id: "MEM-old",
          version: 2,
          conclusion: "已确认访问内部服务，并非反弹 Shell。",
          reviewed_verdict: "false_positive",
          scope_relation: scope,
          conclusion_relation: "differs",
          retrieval_enabled: true,
          directive_enabled: true,
          retrieved_in_source_run: true,
          differences: [
            {
              facet: "environment",
              candidate_values: ["dev"],
              memory_values: ["prd"],
            },
          ],
        },
      ],
    },
    isLoading: false,
  } as ReturnType<typeof useSocMemoryGovernancePreview>);
}

test("shows the old business conclusion and explicit replacement without a second reason field", () => {
  preview("same");
  const html = panel();
  expect(html).toContain("已确认访问内部服务，并非反弹 Shell。");
  expect(html).toContain("以本次审核修订这条经验");
  expect(html).toContain("适用条件相同");
  expect(html).toContain("/workspace/soc/memory/records/MEM-old");
  expect(html).not.toContain("textarea");
  expect(html).not.toContain("治理理由");
});

test("different scopes keep independent lessons and cannot accidentally replace each other", () => {
  preview("disjoint");
  const html = panel();
  expect(html).toContain("精确适用条件不同");
  expect(html).toContain("保留为独立经验");
  expect(html).not.toContain("以本次审核修订这条经验");
});

test("selection is a proposal until the existing confirmation command is submitted", () => {
  preview("same");
  expect(panel({ memoryId: "MEM-old", version: 2 })).toContain(
    "现在尚未修改原经验",
  );
});
