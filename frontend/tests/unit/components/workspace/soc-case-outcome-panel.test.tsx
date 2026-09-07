import { describe, expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocCaseOutcomePanel } from "@/components/workspace/soc/soc-case-outcome-panel";
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

describe("SocCaseOutcomePanel", () => {
  test("separates technical truth from tenant handling without conflict copy", () => {
    const html = renderToStaticMarkup(
      <SocCaseOutcomePanel outcome={outcome()} />,
    );

    expect(html).toContain("安全判断：真实攻击");
    expect(html).toContain("处置：忽略");
    expect(html).toContain("处理完成");
    expect(html).toContain("企业处置规则");
    expect(html).not.toContain("Effective Decision");
    expect(html).not.toContain("Base：");
  });

  test("states explicitly when an evidence gap does not block the result", () => {
    const html = renderToStaticMarkup(
      <SocCaseOutcomePanel
        outcome={outcome({
          security_verdict: "false_positive",
          base_verdict: "suspicious",
          closure_status: "closed_with_limitations",
          evidence_gap_impact: "advisory",
          evidence_gaps: ["缺少 CMDB 资产负责人。"],
          decision_change: "memory_overridden",
          change_summary: "已审核经验将模型初判调整为当前最终安全判断。",
        })}
      />,
    );

    expect(html).toContain("安全判断：误报 / 无风险");
    expect(html).toContain("补充信息，不影响当前结论");
    expect(html).toContain("缺少 CMDB 资产负责人");
    expect(html).toContain("已审核经验将模型初判调整");
  });

  test("turns a material gap into a concrete unfinished state", () => {
    const html = renderToStaticMarkup(
      <SocCaseOutcomePanel
        outcome={outcome({
          security_verdict: "suspicious",
          operational_disposition: null,
          closure_status: "follow_up_required",
          evidence_gap_impact: "decision_blocking",
          evidence_gaps: ["缺少命令执行结果。"],
          next_steps: ["补查目标主机的命令执行记录。"],
        })}
      />,
    );

    expect(html).toContain("关键事实待确认");
    expect(html).toContain("关键问题影响闭环");
    expect(html).toContain("补查目标主机的命令执行记录");
    expect(html).not.toContain("处置：忽略");
  });

  test("explains policy-required transfer for a usable false-positive decision", () => {
    const html = renderToStaticMarkup(
      <SocCaseOutcomePanel
        outcome={outcome({
          security_verdict: "false_positive",
          base_verdict: "false_positive",
          operational_disposition: "escalated",
          handling_reason: "研判为误报；企业规则要求此类告警仍须转交复核。",
          closure_status: "handling_pending",
          closure_reason_codes: ["tenant_policy_handoff_pending"],
          evidence_gap_impact: "capability_limited",
          evidence_gaps: ["缺少资产归属信息。"],
          blocked_capabilities: ["attacker_targeting"],
          next_steps: ["按企业规则转交复核，并记录接收方及处理结果。"],
        })}
      />,
    );

    expect(html).toContain("研判为误报；企业规则要求此类告警仍须转交复核。");
    expect(html).toContain("研判完成，待按规则转交");
    expect(html).toContain("按企业规则转交复核，并记录接收方及处理结果。");
    expect(html).not.toContain("关键事实待确认");
    expect(html).not.toContain("关键问题影响闭环");
  });
});
