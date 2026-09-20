import { expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryLearningStatus } from "@/components/workspace/soc/soc-memory-learning-status";
import { memoryLearningHref } from "@/core/soc/memory-learning";
import type { SocMemoryLearningView } from "@/core/soc/types";

test("manual early review never claims the automatic threshold was met", () => {
  const view: SocMemoryLearningView = {
    state: "pending_review",
    label: "已有待审核经验",
    detail: "已由人工发起提炼，无需等待样本数量达标。",
    action: "review",
    action_label: "继续审核",
    candidate_id: "MC-manual",
  };
  const html = renderToStaticMarkup(<SocMemoryLearningStatus view={view} />);
  expect(html).toContain("无需等待样本数量达标");
  expect(html).toContain("/workspace/soc/review/memory-candidates/MC-manual");
  expect(html).not.toContain("达到沉淀质量门");
});

test("a pending revision links to review instead of the old Memory", () => {
  const view: SocMemoryLearningView = {
    state: "revision_pending",
    label: "经验正在修订",
    detail: "继续完善已有修订，不重复创建。",
    action: "review",
    action_label: "继续审核修订",
    candidate_id: "MC-revision",
    memory_id: "MEM-old",
  };
  expect(memoryLearningHref(view)).toBe(
    "/workspace/soc/review/memory-candidates/MC-revision",
  );
  const html = renderToStaticMarkup(<SocMemoryLearningStatus view={view} />);
  expect(html).toContain("继续审核修订");
  expect(html).not.toContain("records/MEM-old");
});

test("paused experience links to inventory, rejected candidate to its history", () => {
  expect(
    memoryLearningHref({
      state: "confirmed",
      label: "已暂停",
      detail: "",
      action: "view_memory",
      action_label: "查看 / 修订经验",
      candidate_id: "MC-old",
      memory_id: "MEM-paused",
      use_mode: "paused",
    }),
  ).toBe("/workspace/soc/memory/records/MEM-paused");
  expect(
    memoryLearningHref({
      state: "closed",
      label: "本轮已放弃沉淀",
      detail: "",
      action: "view_history",
      action_label: "查看记录",
      candidate_id: "MC-rejected",
    }),
  ).toBe("/workspace/soc/review/memory-candidates/MC-rejected");
  expect(
    memoryLearningHref({
      state: "accumulating",
      label: "",
      detail: "",
      action: "promote",
      action_label: "提炼经验",
    }),
  ).toBeNull();
});

test("closed learning without a candidate shows its explanation without a link", () => {
  const view: SocMemoryLearningView = {
    state: "closed",
    label: "本次未生成经验",
    detail: "关联实体超过经验条件的 512 字符上限，研判结果已保留。",
    action: "view_history",
    action_label: "查看记录",
  };
  const html = renderToStaticMarkup(<SocMemoryLearningStatus view={view} />);
  expect(html).toContain('role="status"');
  expect(html).toContain(view.label);
  expect(html).toContain(view.detail);
  expect(html).not.toContain("<a ");
  expect(html).not.toContain("<button");
  expect(html).not.toContain(view.action_label);
});

test("promotion without a destination remains hidden in the status band", () => {
  const view: SocMemoryLearningView = {
    state: "accumulating",
    label: "正在积累样本",
    detail: "尚未达到自动提炼条件。",
    action: "promote",
    action_label: "提炼经验",
  };
  expect(renderToStaticMarkup(<SocMemoryLearningStatus view={view} />)).toBe(
    "",
  );
});
