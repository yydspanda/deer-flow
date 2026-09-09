import { expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocRunMemoryContext } from "@/components/workspace/soc/soc-run-memory-context";

test("actual retrieved memory is visible without a cohort candidate and links by record ID", () => {
  const html = renderToStaticMarkup(
    <SocRunMemoryContext
      runId="RUN-1"
      memories={[
        {
          context_ref: "M-123",
          label: "已审核业务通信",
          source_id: "MEM-123@v4",
          memory_id: "MEM-123",
          memory_version: 4,
          summary: "审核确认正常服务。",
          decision_cited: true,
          applicability_status: "partial",
          reviewed_verdict: "false_positive",
          use_mode: "context_only",
        },
      ]}
    />,
  );
  expect(html).toContain("本次研判读取的经验");
  expect(html).toContain("已被模型引用");
  expect(html).toContain("部分相似，需结合当前行为");
  expect(html).toContain('href="/workspace/soc/memory/records/MEM-123"');
  expect(html).toContain(
    'href="/workspace/soc/memory/records/MEM-123/revise?run_id=RUN-1"',
  );
  expect(html).not.toContain("MEM-123%40v4/revise");
  expect(html).not.toContain("同类组");
});

test("retrieval alone is not described as model adoption", () => {
  const html = renderToStaticMarkup(
    <SocRunMemoryContext
      memories={[
        {
          context_ref: "M-1",
          label: "历史业务",
          source_id: "MEM-1@v1",
          summary: "背景。",
          decision_cited: false,
        },
      ]}
    />,
  );
  expect(html).toContain("已提供给模型参考");
  expect(html).not.toContain("已被模型引用");
});
