import { describe, expect, test } from "@rstest/core";

import {
  memoryAvailabilityCopy,
  memoryDecisionCapabilityCopy,
  memoryFutureUseCopy,
  memoryFutureUseStateCopy,
  memoryRunUsageCopy,
  MEMORY_MATCHING_RULE_STATE_LABELS,
  MEMORY_PATTERN_STAGE_DETAILS,
  MEMORY_PATTERN_STAGE_LABELS,
} from "@/components/workspace/soc/soc-memory-copy";

describe("SOC Memory analyst-facing copy", () => {
  test("separates no use, reference use, and reviewed conclusion reuse", () => {
    expect(memoryRunUsageCopy(0, false).label).toBe("未使用历史经验");
    expect(memoryRunUsageCopy(2, false)).toMatchObject({
      label: "仅作研判参考",
      tone: "reference",
    });
    expect(memoryRunUsageCopy(2, true)).toMatchObject({
      label: "已复用审核结论",
      tone: "decision",
    });
  });

  test("does not describe a disabled record as reference-only", () => {
    expect(memoryAvailabilityCopy(false)).toEqual({
      label: "暂停用于新告警",
      detail:
        "新告警不会找到这条经验，因此它既不会作为研判参考，也不会参与最终判断。",
    });
  });

  test("separates record availability from exact-match decision capability", () => {
    expect(memoryAvailabilityCopy(true).label).toBe("已开放给新告警");
    expect(memoryDecisionCapabilityCopy(false).label).toBe("仅供研判参考");
    expect(memoryDecisionCapabilityCopy(true).label).toBe("精确匹配可复用结论");
  });

  test("describes the complete future-use state without conflating lifecycle", () => {
    expect(
      memoryFutureUseCopy({
        hasRecord: false,
        retrievalEnabled: false,
        decisionDirectiveReady: false,
      }).label,
    ).toBe("尚未形成经验");
    expect(
      memoryFutureUseCopy({
        hasRecord: true,
        retrievalEnabled: false,
        decisionDirectiveReady: true,
      }).label,
    ).toBe("尚未开放");
    expect(
      memoryFutureUseCopy({
        hasRecord: true,
        retrievalEnabled: true,
        decisionDirectiveReady: false,
      }).label,
    ).toBe("仅供研判参考");
    expect(
      memoryFutureUseCopy({
        hasRecord: true,
        retrievalEnabled: true,
        decisionDirectiveReady: true,
      }).label,
    ).toBe("精确匹配可复用结论");
    expect(memoryFutureUseStateCopy("blocked").label).toBe("匹配规则待处理");
  });

  test("uses analyst language for lifecycle and matching-rule states", () => {
    expect(MEMORY_PATTERN_STAGE_LABELS.memory_inactive).toBe("经验已沉淀");
    expect(MEMORY_PATTERN_STAGE_LABELS.memory_active).toBe("经验已沉淀");
    expect(MEMORY_PATTERN_STAGE_DETAILS.memory_active).toContain(
      "使用状态决定",
    );
    expect(MEMORY_MATCHING_RULE_STATE_LABELS.legacy).toBe("匹配规则待升级");
  });
});
