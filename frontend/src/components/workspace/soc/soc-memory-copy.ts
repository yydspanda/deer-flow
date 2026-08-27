import type {
  SocMemoryFutureUseState,
  SocMemoryPatternLifecycleState,
  SocMemoryProfileState,
} from "@/core/soc";

export const MEMORY_PATTERN_STAGE_LABELS: Record<
  SocMemoryPatternLifecycleState,
  string
> = {
  collecting: "正在积累同类样本",
  candidate_pending: "已生成候选，等待审核",
  candidate_intermediate: "审核已完成，正在生成经验",
  memory_inactive: "经验已沉淀",
  memory_active: "经验已沉淀",
  terminal_history: "已结束，不再沉淀",
};

export const MEMORY_PATTERN_STAGE_DETAILS: Record<
  SocMemoryPatternLifecycleState,
  string
> = {
  collecting: "同类样本仍在积累，尚未进入专家审核。",
  candidate_pending: "系统已经生成经验候选，等待专家确认是否值得沉淀。",
  candidate_intermediate: "专家审核已经完成，系统正在生成正式经验记录。",
  memory_inactive:
    "专家确认的经验记录已经保存；是否供新告警使用由下方使用状态决定。",
  memory_active:
    "专家确认的经验记录已经保存；是否供新告警使用由下方使用状态决定。",
  terminal_history: "该候选已被拒绝、替代或结束，仅保留历史审计。",
};

export const MEMORY_MATCHING_RULE_STATE_LABELS: Record<
  SocMemoryProfileState,
  string
> = {
  current: "匹配规则正常",
  legacy: "匹配规则待升级",
  unregistered: "匹配规则不可用",
};

export interface MemoryRunUsageCopy {
  label: string;
  detail: string;
  tone: "neutral" | "reference" | "decision";
}

export interface MemoryFutureUseCopy {
  label: string;
  detail: string;
  tone: "neutral" | "paused" | "reference" | "decision" | "blocked";
}

export function memoryFutureUseStateCopy(
  state: SocMemoryFutureUseState,
): MemoryFutureUseCopy {
  switch (state) {
    case "not_ready":
      return {
        label: "尚未形成经验",
        detail: "当前只有同类样本或待审候选，还不能供新告警使用。",
        tone: "neutral",
      };
    case "paused":
      return {
        label: "尚未开放",
        detail: "经验已经沉淀，但新告警不会检索到它。",
        tone: "paused",
      };
    case "blocked":
      return {
        label: "匹配规则待处理",
        detail:
          "经验虽已开放，但匹配规则或有效期需要处理，完成前不会用于新告警。",
        tone: "blocked",
      };
    case "exact_match_decision":
      return {
        label: "精确匹配可复用结论",
        detail:
          "新告警完整满足审核条件时可复用专家结论；只有部分条件相似时，仅供研判参考。",
        tone: "decision",
      };
    case "reference_only":
      return {
        label: "仅供研判参考",
        detail:
          "新告警匹配后可将它作为历史经验交给模型，但不会直接改变最终结论。",
        tone: "reference",
      };
  }
}

export function memoryFutureUseCopy({
  hasRecord,
  retrievalEnabled,
  decisionDirectiveReady,
  matchingRuleState = "current",
}: {
  hasRecord: boolean;
  retrievalEnabled: boolean;
  decisionDirectiveReady: boolean;
  matchingRuleState?: SocMemoryProfileState;
}): MemoryFutureUseCopy {
  if (!hasRecord) {
    return memoryFutureUseStateCopy("not_ready");
  }
  if (!retrievalEnabled) {
    return memoryFutureUseStateCopy("paused");
  }
  if (matchingRuleState !== "current") {
    return memoryFutureUseStateCopy("blocked");
  }
  if (decisionDirectiveReady) {
    return memoryFutureUseStateCopy("exact_match_decision");
  }
  return memoryFutureUseStateCopy("reference_only");
}

export function memoryRunUsageCopy(
  referencedCount: number,
  reviewedConclusionApplied: boolean,
): MemoryRunUsageCopy {
  if (referencedCount <= 0) {
    return {
      label: "未使用历史经验",
      detail: "本次没有找到可用于研判的已审核经验。",
      tone: "neutral",
    };
  }
  if (reviewedConclusionApplied) {
    return {
      label: "已复用审核结论",
      detail: `本次参考 ${referencedCount} 条历史经验，其中精确匹配的经验参与了最终结论。`,
      tone: "decision",
    };
  }
  return {
    label: "仅作研判参考",
    detail: `本次参考 ${referencedCount} 条相似经验，但没有直接改变最终结论。`,
    tone: "reference",
  };
}

export function memoryAvailabilityCopy(retrievalEnabled: boolean) {
  return retrievalEnabled
    ? {
        label: "已开放给新告警",
        detail:
          "新告警可以找到这条经验；只有完整满足审核范围时，已审核结论才可能参与最终判断。",
      }
    : {
        label: "暂停用于新告警",
        detail:
          "新告警不会找到这条经验，因此它既不会作为研判参考，也不会参与最终判断。",
      };
}

export function memoryDecisionCapabilityCopy(hasDecisionDirective: boolean) {
  return hasDecisionDirective
    ? {
        label: "精确匹配可复用结论",
        detail:
          "新告警完整满足审核过的匹配条件时，可以复用专家结论；只有部分条件相似时，仅供模型参考。",
      }
    : {
        label: "仅供研判参考",
        detail:
          "这条经验可以帮助模型理解当前告警，但不会直接改变最终结论或授权外部动作。",
      };
}
