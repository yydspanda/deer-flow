import { describe, expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocCaseOutcomePanel } from "@/components/workspace/soc/soc-case-outcome-panel";
import { SocHandlingBadge } from "@/components/workspace/soc/soc-handling-badge";
import type { SocCaseOutcomeView } from "@/core/soc";

function outcome(
  overrides: Partial<SocCaseOutcomeView> = {},
): SocCaseOutcomeView {
  return {
    schema_version: "soc.case_outcome_view.v1",
    event_summary: "检测到目标主机发起反向连接。",
    security_verdict: "true_positive",
    base_verdict: "true_positive",
    confidence: 0.86,
    decision_usable: true,
    recommended_handling: "ignore",
    handling_reason: "已授权活动按企业规则忽略。",
    decision_reason: "当前告警事实支持该安全判断。",
    decision_change: "unchanged",
    operational_disposition: "ignored",
    handling_recommendation: "忽略并保留审计。",
    closure_status: "closed",
    closure_reason_codes: ["handling_applied"],
    evidence_gap_impact: "none",
    evidence_gaps: [],
    blocked_capabilities: [],
    next_steps: [],
    basis: [
      {
        kind: "current_analysis",
        summary: "当前告警事实支持该安全判断。",
        source_id: "RUN-1",
      },
      {
        kind: "tenant_policy",
        summary: "已授权活动按企业规则忽略。",
        source_id: "TPD-1",
      },
    ],
    contributions: [
      {
        kind: "tenant_policy_applied",
        count: 1,
        summary: "企业处置规则已映射为本次运营动作。",
      },
    ],
    memory_context_count: 0,
    memory_directive_applied: false,
    tenant_policy_applied: true,
    ...overrides,
  };
}

function render(value: Partial<SocCaseOutcomeView> = {}) {
  const html = renderToStaticMarkup(
    <SocCaseOutcomePanel outcome={outcome(value)} />,
  );
  return { html, primary: html.split("<details")[0]! };
}

describe("SocCaseOutcomePanel", () => {
  test("direct policy and Memory results name their authority instead of a failed model", () => {
    const policy = render({
      processing_path: "tenant_policy",
      security_verdict: "unknown",
      base_verdict: null,
      confidence: null,
    });
    expect(policy.primary).toContain("命中企业规则 · 直接忽略，未调用主模型");
    expect(policy.primary).not.toContain("企业策略直接处理");
    expect(policy.html).toContain("未进行模型风险研判");
    expect(policy.html).not.toContain("暂无法判断");
    const memory = render({
      processing_path: "memory",
      base_verdict: null,
      confidence: null,
    });
    expect(memory.primary).toContain("直接复用结论，未调用主模型");
    expect(memory.primary).not.toContain("命中企业规则");
  });
  test("names the server-selected direct transfer without implying Memory reuse", () => {
    const policy = render({
      processing_path: "tenant_policy",
      recommended_handling: "transfer",
      operational_disposition: "escalated",
      security_verdict: "unknown",
      base_verdict: null,
      confidence: null,
    });
    expect(policy.primary).toContain("命中企业规则 · 直接转交，未调用主模型");
    expect(policy.primary).not.toContain("直接忽略");
    expect(policy.primary).not.toContain("直接复用结论");
  });
  test("has one handling conclusion and keeps technical verdict and progress collapsed", () => {
    const { html, primary } = render({
      progress_label: "已确定处置方案",
      progress_detail: "尚无外部处置回执。",
    });
    expect(primary).toContain("处理结论");
    expect(primary).toContain("忽略");
    expect(primary).toContain("已授权活动按企业规则忽略");
    for (const text of [
      "最终安全判断",
      "运营处置",
      "处理进度",
      "真实攻击",
      "研判置信度",
      "下一步",
    ])
      expect(primary).not.toContain(text);
    expect(html).toContain("真实攻击");
    expect(html).toContain("尚无外部处置回执");
    expect(html).toContain("研判与处置详情");
    expect(html).not.toContain("<details open");
  });
  test("uses the same transfer label in list and detail despite a false-positive technical verdict", () => {
    const { primary } = render({
      security_verdict: "false_positive",
      recommended_handling: "transfer",
      operational_disposition: "escalated",
      handling_reason: "当前倾向误报，但命中企业指定转交规则。",
      handling_recommendation: "核实业务授权。",
      next_steps: ["按企业规则转交。"],
    });
    expect(primary).toContain("转交");
    expect(primary).toContain("当前倾向误报，但命中企业指定转交规则。");
    expect(primary).toContain("下一步");
    expect(primary).toContain("核实业务授权。");
    expect(primary).not.toContain("忽略");
    expect(
      renderToStaticMarkup(<SocHandlingBadge value="transfer" />),
    ).toContain("转交");
  });
  test("keeps nonblocking evidence and superseded Memory questions in details", () => {
    const { html, primary } = render({
      security_verdict: "false_positive",
      evidence_gap_impact: "advisory",
      evidence_gaps: ["缺少 CMDB 资产负责人。"],
      prior_analysis_gaps: ["原先怀疑反弹连接。"],
      blocked_capabilities: ["attacker_targeting"],
      action_limits_relevant: false,
      conclusion_support: {
        context_refs: ["M-1"],
        resolved_questions: ["经验已确认正常内部调用。"],
        optional_checks: ["可补充负责人。"],
        reassessment_triggers: ["出现新的恶意载荷。"],
      },
    });
    expect(primary).toContain("经验已确认正常内部调用。");
    expect(primary).not.toContain("CMDB");
    expect(primary).not.toContain("原先怀疑");
    expect(primary).not.toContain("能力检查");
    expect(html).toContain("补充信息，不影响当前结论");
    expect(html).toContain("何时需要重新研判");
  });
  test("does not hide a material conflict behind disclosure", () => {
    const { primary } = render({
      recommended_handling: "transfer",
      evidence_gap_impact: "decision_blocking",
      closure_status: "follow_up_required",
      progress_label: "待解决决策分歧",
      progress_detail: "两条经验对当前行为有相反判断。",
      next_steps: ["确认两条经验的适用范围。"],
    });
    expect(primary).toContain("两条经验对当前行为有相反判断。");
    expect(primary).toContain("确认两条经验的适用范围。");
    expect(primary).not.toContain("关键事实待确认");
  });
  test("runtime failure is not displayed as a completed business decision", () => {
    const { primary } = render({
      closure_status: "failed",
      recommended_handling: "undetermined",
      progress_detail: "模型连接失败。",
      next_steps: ["恢复连接后重试。"],
    });
    expect(primary).toContain("运行失败");
    expect(primary).toContain("模型连接失败");
    expect(primary).not.toContain("忽略");
    expect(primary).not.toContain("转交");
    expect(primary).not.toContain("已完成");
  });
  test("unknown does not become false positive or a fabricated transfer", () => {
    const { primary } = render({
      recommended_handling: "undetermined",
      security_verdict: "unknown",
      handling_reason: "本次没有生成可用判断。",
    });
    expect(primary).toContain("未形成处理结论");
    expect(primary).not.toContain("忽略");
    expect(primary).not.toContain("转交");
  });
  test("execution failure stays visible without changing the security conclusion", () => {
    const { primary } = render({
      closure_status: "handling_pending",
      closure_reason_codes: ["action_execution_failed"],
      progress_label: "动作执行失败",
      progress_detail: "接口暂不可用。",
      next_steps: ["检查接口，按原任务重试。"],
    });
    expect(primary).toContain("忽略");
    expect(primary).toContain("动作执行失败");
    expect(primary).toContain("检查接口，按原任务重试。");
  });
});
