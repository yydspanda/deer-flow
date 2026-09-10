"use client";

import {
  CheckIcon,
  CodeIcon,
  LockKeyholeIcon,
  SlidersHorizontalIcon,
} from "lucide-react";

import type {
  SocMemoryApplicabilitySpec,
  SocMemoryScopeView,
} from "@/core/soc";

const labels: Record<string, string> = {
  detection_key: "检测规则",
  detection_signature: "规则与产品标识",
  behavior_fingerprint: "核心行为",
  behavior_strength: "行为特征强度",
  source_system: "来源系统",
  source_type: "来源类型",
  product: "安全产品",
  rule_name: "规则名称",
  rule_code: "规则编码",
  scenario_key: "攻击场景",
  behavior_component: "核心行为",
  behavior_component_core: "核心行为",
  behavior_component_strong: "相似行为特征",
  behavior_component_weak: "辅助行为特征",
  environment: "数据使用范围",
  entity: "关联实体",
  role_entity: "角色实体",
  network_service: "目标服务",
  vulnerability_id: "漏洞",
  attack_behavior_family: "攻击类型",
};

const terms: Record<string, string> = {
  web_attack: "Web 攻击",
  denial_of_service: "拒绝服务",
  command_and_control: "命令与控制",
  vulnerability_exploitation: "漏洞利用",
  reverse_shell: "反弹 Shell",
  ndr: "网络检测与响应（NDR）",
  nids: "网络入侵检测（NIDS）",
  edr: "终端检测与响应（EDR）",
  hids: "主机入侵检测（HIDS）",
  strong: "强特征",
  weak_only: "仅弱特征",
  "dev-corpus-eval": "告警演练数据",
  dev: "DEV 运行范围",
  stg: "STG 运行范围",
  prd: "PRD 运行范围",
};
const componentLabels: Record<string, string> = {
  attack_family: "攻击类型",
  network_service: "目标服务",
  protocol: "协议",
  scenario: "场景",
  technique: "攻击技术",
  process: "进程",
  process_path: "进程路径",
  process_image: "进程映像",
  parent_service: "父服务",
  command_module: "命令模块",
  command_switch: "命令参数",
  vulnerability: "漏洞",
  target_class: "目标类型",
  http_method: "请求方法",
  account: "账号",
  service_uri: "服务地址",
};

function label(key: string) {
  return labels[key] ?? key;
}
function valueLabel(key: string, value: string): string {
  if (key === "entity" || key === "role_entity") {
    const colon = value.indexOf(":");
    if (colon >= 0) {
      const prefix = value.slice(0, colon);
      const names: Record<string, string> = {
        asset: "资产组",
        ip: "IP",
        host: "主机",
        user: "账号",
        mitre: "MITRE 标记",
        rule: "规则内部标识",
        domain: "域名",
      };
      return `${names[prefix] ?? label(prefix)}：${value.slice(colon + 1)}`;
    }
  }
  if (key.startsWith("behavior_component")) {
    const colon = value.indexOf(":");
    if (colon >= 0) {
      const prefix = value.slice(0, colon),
        content = value.slice(colon + 1);
      return `${componentLabels[prefix] ?? prefix}：${terms[content] ?? content}`;
    }
  }
  return terms[value] ?? value;
}

export function SocMemoryScope({
  spec,
  view,
  promoted = {},
  onPromote,
  disabled = false,
  compact = false,
}: {
  spec?: SocMemoryApplicabilitySpec | null;
  view?: SocMemoryScopeView | null;
  promoted?: Record<string, string[]>;
  onPromote?: (values: Record<string, string[]>) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  if (!spec)
    return (
      <p className="text-muted-foreground text-sm">
        这条经验尚未保存结构化匹配范围。
      </p>
    );
  const required = { ...spec.required_facets, ...promoted };
  const rows = new Map<string, string[]>();
  for (const [key, values] of Object.entries(
    onPromote ? spec.required_facets : required,
  )) {
    const expansion = view?.required_details[key];
    if (expansion) {
      const visible =
        key === "behavior_fingerprint"
          ? { behavior_component: expansion.behavior_component ?? [] }
          : expansion;
      for (const [detailKey, detailValues] of Object.entries(visible)) {
        if (detailKey !== "entity")
          rows.set(detailKey, [
            ...new Set([...(rows.get(detailKey) ?? []), ...detailValues]),
          ]);
      }
    } else if (key.includes("fingerprint") || key.includes("signature")) {
      rows.set(key, ["按已保存指纹匹配，组成明细未能核验"]);
    } else if (
      key !== "behavior_strength" ||
      !view?.required_details.behavior_fingerprint
    ) {
      rows.set(key, values);
    }
  }
  const options = (
    view?.options ??
    Object.entries(spec.optional_facets).map(([key, values]) => ({
      key,
      values,
      kind: spec.context_only_similarity_facet_keys.includes(key)
        ? "similarity"
        : "additional",
    }))
  ).filter((option) => option.kind === "additional");
  return (
    <section aria-label="经验适用范围" data-memory-scope className="min-w-0">
      {!compact && (
        <div className="flex items-center gap-2 text-sm font-semibold">
          <LockKeyholeIcon className="size-4 shrink-0 text-emerald-700" />
          适用告警
        </div>
      )}
      {!compact && (
        <h4 className="text-muted-foreground mt-4 text-xs font-medium">
          必需匹配条件
        </h4>
      )}
      <dl className="mt-2 divide-y text-sm">
        {[...rows].map(([key, values]) => (
          <div
            key={key}
            className="grid min-w-0 gap-1 py-2.5 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-4"
          >
            <dt className="text-muted-foreground text-xs leading-6">
              {label(key)}
            </dt>
            <dd className="min-w-0 leading-6 [overflow-wrap:anywhere]">
              {values.map((value) => (
                <span
                  key={value}
                  className="mr-4 inline-block max-w-full align-top"
                >
                  {valueLabel(key, value)}
                </span>
              ))}
              {values.length > 1 && key !== "behavior_component" && (
                <span className="text-muted-foreground ml-1 text-xs">
                  （该条件接受任意一项）
                </span>
              )}
            </dd>
          </div>
        ))}
      </dl>
      {!compact && (
        <>
          <div className="mt-2 flex items-start gap-2 text-xs leading-5 text-emerald-800 dark:text-emerald-300">
            <CheckIcon className="mt-0.5 size-3.5 shrink-0" />
            <span>
              精确匹配需满足以上范围；是否直接复用结论，以已审核的使用方式为准。
            </span>
          </div>
          {spec.context_only_missing_facet_keys.includes(
            "behavior_fingerprint",
          ) && (
            <p className="text-muted-foreground mt-1 text-xs leading-5">
              核心行为不完全一致时，仅符合相似召回条件的经验可供模型参考，不直接复用结论。
            </p>
          )}
          {Object.keys(spec.excluded_facets).length > 0 && (
            <div className="mt-3 border-l-2 border-red-500 pl-3 text-sm">
              <div className="font-medium text-red-700">不适用范围</div>
              {Object.entries(spec.excluded_facets).map(([key, values]) => (
                <p key={key} className="mt-1 [overflow-wrap:anywhere]">
                  {label(key)}：
                  {values.map((value) => valueLabel(key, value)).join(" / ")}
                </p>
              ))}
            </div>
          )}
          {spec.minimum_optional_matches > 0 && (
            <p className="mt-3 text-sm font-medium">
              此外，至少需满足 {spec.minimum_optional_matches}{" "}
              组附加条件，具体条件见技术详情。
            </p>
          )}
          {options.length > 0 && (
            <div className="mt-5 border-t pt-4" data-memory-scope-options>
              <h4 className="flex items-center gap-2 text-sm font-medium">
                <SlidersHorizontalIcon className="size-4 shrink-0" />
                可选匹配条件
                {!onPromote && (
                  <span className="text-muted-foreground text-xs font-normal">
                    未增加为限制
                  </span>
                )}
              </h4>
              <div className="mt-2 divide-y">
                {options.map((option) => (
                  <fieldset
                    key={option.key}
                    aria-label={label(option.key)}
                    className="grid min-w-0 gap-2 py-3 sm:grid-cols-[8rem_minmax(0,1fr)] sm:gap-4"
                  >
                    <div className="text-muted-foreground text-xs leading-6">
                      {label(option.key)}
                    </div>
                    <div className="grid min-w-0 gap-2">
                      {option.values.map((value) => (
                        <label
                          key={value}
                          className="flex min-w-0 items-start gap-2 text-sm leading-6"
                        >
                          {onPromote && (
                            <input
                              type="checkbox"
                              className="mt-1 size-4 shrink-0 accent-emerald-700"
                              aria-label={`增加匹配条件 ${label(option.key)} ${valueLabel(option.key, value)}`}
                              disabled={disabled}
                              checked={
                                promoted[option.key]?.includes(value) ?? false
                              }
                              onChange={(event) => {
                                const values = promoted[option.key] ?? [];
                                const next = { ...promoted };
                                const selected = event.target.checked
                                  ? [...values, value]
                                  : values.filter((item) => item !== value);
                                if (selected.length)
                                  next[option.key] = selected;
                                else delete next[option.key];
                                onPromote(next);
                              }}
                            />
                          )}
                          <span className="min-w-0 [overflow-wrap:anywhere]">
                            {valueLabel(option.key, value)}
                          </span>
                          {promoted[option.key]?.includes(value) && (
                            <span className="shrink-0 text-xs leading-6 font-medium text-emerald-700">
                              已增加
                            </span>
                          )}
                        </label>
                      ))}
                      {onPromote && option.values.length > 1 && (
                        <span className="text-muted-foreground text-xs">
                          本组多选时，命中所选任意一项即可。
                        </span>
                      )}
                    </div>
                  </fieldset>
                ))}
              </div>
            </div>
          )}
          <details
            className="mt-3 border-t pt-3 text-xs"
            data-memory-scope-audit
          >
            <summary className="text-muted-foreground w-fit cursor-pointer">
              <CodeIcon className="mr-2 inline size-3.5" />
              技术详情：指纹与完整匹配条件
            </summary>
            <div className="mt-3 space-y-3">
              <p className="text-muted-foreground">
                不同必需条件之间全部满足；同一条件内多个候选值命中任意一个即可。
              </p>
              <pre className="bg-muted/50 max-h-80 overflow-auto p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
                {JSON.stringify(
                  {
                    ...spec,
                    required_facets: required,
                    optional_facets: Object.fromEntries(
                      Object.entries(spec.optional_facets).filter(
                        ([key]) => !Object.hasOwn(promoted, key),
                      ),
                    ),
                    context_only_required_facet_keys: spec
                      .context_only_required_facet_keys.length
                      ? [
                          ...new Set([
                            ...spec.context_only_required_facet_keys,
                            ...Object.keys(promoted),
                          ]),
                        ].sort()
                      : [],
                  },
                  null,
                  2,
                )}
              </pre>
            </div>
          </details>
        </>
      )}
    </section>
  );
}
