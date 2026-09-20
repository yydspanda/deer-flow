import { expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { ExecutionMonitor } from "@/components/workspace/soc/soc-corpus-validation-workbench";
import type {
  SocCorpusWorkbenchExecution,
  SocCorpusWorkbenchExecutionPhase,
} from "@/core/soc";

function execution(
  memoryStatus: SocCorpusWorkbenchExecutionPhase["status"],
  summary: string,
): SocCorpusWorkbenchExecution {
  const active = memoryStatus === "running";
  return {
    schema_version: "soc.corpus_dev_execution.v1",
    alert_id: "2506195",
    status: active ? "running" : "analysis_complete",
    current_phase: active ? "memory" : null,
    run_id: "RUN-ANALYSIS-ONLY",
    run_status: "success",
    elapsed_ms: 1200,
    total_duration_ms: 1200,
    model_name: "fixture-model",
    provider_attempt_count: 1,
    observation_id: null,
    phases: [
      {
        phase: "reasoning",
        label: "模型研判",
        status: "success",
        summary: "研判结果已保存。",
        duration_ms: 1200,
        metrics: {},
        steps: [],
      },
      {
        phase: "memory",
        label: "模式积累",
        status: memoryStatus,
        summary,
        duration_ms: null,
        metrics: {},
        steps: [],
      },
    ],
  };
}

for (const [status, summary] of [
  ["skipped", "本次研判已完成，未生成模式积累记录。"],
  ["failed", "研判结果已保存，模式积累失败，可重新运行。"],
] as const) {
  test(`analysis-only completion with ${status} memory stops waiting`, () => {
    const html = renderToStaticMarkup(
      <ExecutionMonitor
        execution={execution(status, summary)}
        isLoading={false}
      />,
    );
    expect(html).toContain("研判已完成");
    expect(html).toContain(summary);
    expect(html).not.toContain("animate-spin");
    expect(html).not.toContain("等待 Pattern 写入");
  });
}

test("an active memory write keeps its running indicator", () => {
  const summary = "研判已完成，正在积累模式记录。";
  const html = renderToStaticMarkup(
    <ExecutionMonitor
      execution={execution("running", summary)}
      isLoading={false}
    />,
  );
  expect(html).toContain("运行中");
  expect(html).toContain(summary);
  expect(html).toContain("animate-spin");
});
