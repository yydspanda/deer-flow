import type { SocCorpusWorkbenchDecisionStage } from "@/core/soc";

const STAGES = {
  base: "模型初判",
  memory: "经验结论复用",
  tenant_policy: "企业策略",
  effective: "最终采用结果",
};
const STATUSES: Record<string, string> = {
  observed: "已记录",
  disabled: "未启用",
  no_input: "无可用输入",
  no_match: "未满足应用条件",
  shadow_matched: "模拟命中，未应用",
  unchanged: "未改变结果",
  reinforced: "沿用同向结论",
  overridden: "已调整结论",
  applied: "已应用到决策",
  conflicted: "存在分歧或应用限制",
};
const VERDICTS: Record<string, string> = {
  suspicious: "可疑",
  true_positive: "真实攻击",
  false_positive: "误报 / 无风险",
  unknown: "暂无法判断",
  needs_review: "需要确认",
};
const DISPOSITIONS: Record<string, string> = {
  escalated: "转交复核",
  ignored: "忽略",
  suppressed: "抑制",
  duplicate: "合并重复告警",
  closed_true_positive: "确认攻击并结案",
  closed_false_positive: "误报结案",
  closed_benign_true_positive: "授权活动结案",
  unknown: "未指定",
};

export function SocDecisionLineageTable({
  stages,
}: {
  stages: SocCorpusWorkbenchDecisionStage[];
}) {
  return (
    <div className="overflow-x-auto border-t">
      <table className="w-full min-w-[1100px] table-fixed text-left text-sm">
        <colgroup>
          <col className="w-32" />
          <col className="w-52" />
          <col className="w-36" />
          <col className="w-28" />
          <col className="w-28" />
          <col />
        </colgroup>
        <thead className="bg-muted/50 text-xs">
          <tr>
            {[
              "阶段",
              "本阶段结果",
              "安全判断 / 置信度",
              "处置方案",
              "复核要求",
              "处理建议与来源",
            ].map((label) => (
              <th
                key={label}
                className="px-4 py-3 font-medium whitespace-nowrap"
              >
                {label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {stages.map((stage) => (
            <tr key={stage.stage} className="border-t align-top">
              <td className="px-4 py-3">
                <div className="font-medium whitespace-nowrap">
                  {STAGES[stage.stage]}
                </div>
                <div className="text-muted-foreground mt-1 font-mono text-xs">
                  {stage.stage}
                </div>
              </td>
              <td className="px-4 py-3">
                {STATUSES[stage.status] ?? stage.status}
                {stage.stage === "memory" && stage.status === "no_input" ? (
                  <p className="text-muted-foreground mt-1 text-xs">
                    无直接复用结论的经验指令；不代表模型未读取参考经验。
                  </p>
                ) : null}
              </td>
              <td className="px-4 py-3">
                {VERDICTS[stage.verdict] ?? stage.verdict}
                <div className="text-muted-foreground mt-1 tabular-nums">
                  {Math.round(stage.confidence * 100)}%
                </div>
              </td>
              <td className="px-4 py-3 whitespace-nowrap">
                {stage.disposition
                  ? (DISPOSITIONS[stage.disposition] ?? stage.disposition)
                  : "未指定"}
              </td>
              <td className="px-4 py-3 whitespace-nowrap">
                {stage.needs_review ? "要求复核" : "未要求复核"}
              </td>
              <td className="max-w-lg px-4 py-3 break-words">
                <p className="leading-6">{stage.suggested_action}</p>
                <details className="mt-2">
                  <summary className="text-muted-foreground cursor-pointer text-xs">
                    查看阶段原始说明
                  </summary>
                  <p className="mt-2 leading-6">{stage.summary}</p>
                </details>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
