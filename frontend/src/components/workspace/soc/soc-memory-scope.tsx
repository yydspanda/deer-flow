"use client";

import {
  BookOpenIcon,
  CodeIcon,
  LockKeyholeIcon,
  PlusIcon,
  XIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import type {
  SocMemoryApplicabilitySpec,
  SocMemoryScopeView,
} from "@/core/soc";
import { withMemoryReuseConditions } from "@/core/soc/memory-reuse-scope";

const labels: Record<string, string> = {
  detection_key: "检测规则",
  detection_signature: "规则与产品标识",
  behavior_fingerprint: "行为标识",
  behavior_strength: "行为特征强度",
  source_system: "来源系统",
  source_type: "来源类型",
  product: "安全产品",
  rule_name: "规则名称",
  rule_code: "规则编码",
  scenario_key: "攻击场景",
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
  proxy_tunnel_activity: "代理 / 隧道通信",
  reverse_shell: "反弹 Shell",
  red_team_probe: "红队探测",
  windows_protected_registry_hive: "Windows 受保护注册表配置单元",
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
  process: "涉及进程",
  process_path: "程序路径",
  process_image: "进程映像",
  parent_service: "父服务",
  command_module: "命令模块",
  command_switch: "命令参数",
  vulnerability: "漏洞",
  target_class: "目标类型",
  target_file: "目标文件",
  http_method: "请求方法",
  account: "账号",
  service_uri: "服务地址",
};
const entityLabels: Record<string, string> = {
  asset: "资产组",
  ip: "IP（未区分角色）",
  host: "主机",
  user: "账号",
  account: "账号",
  mitre: "MITRE 标记",
  rule: "规则内部标识",
  domain: "域名",
  source: "来源 IP",
  destination: "目标 IP",
  attacker: "攻击者",
  victim: "受害者",
};
const internalKeys = new Set([
  "behavior_component_core",
  "behavior_component_strong",
  "behavior_component_weak",
]);

function splitValue(value: string): [string, string] {
  const index = value.indexOf(":");
  return index < 0
    ? ["", value]
    : [value.slice(0, index), value.slice(index + 1)];
}
function valueLabel(value: string): string {
  return terms[value] ?? value.replace(/^source_category:/, "");
}
function conditionLabel(key: string, prefix?: string | null): string {
  if (prefix) return entityLabels[prefix] ?? componentLabels[prefix] ?? prefix;
  return labels[key] ?? key;
}
function displayValues(values: string[], prefixed = false): string[] {
  return values.map((value) =>
    valueLabel(prefixed ? splitValue(value)[1] : value),
  );
}
function ScopeRows({ rows }: { rows: Map<string, string[]> }) {
  return (
    <dl className="divide-y text-sm">
      {[...rows].map(([key, values]) => (
        <div
          key={key}
          className="grid min-w-0 gap-1 py-2 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4"
        >
          <dt className="text-muted-foreground text-xs leading-6">{key}</dt>
          <dd className="min-w-0 leading-6 [overflow-wrap:anywhere]">
            {values.join("、")}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function SocMemoryScope({
  spec,
  view,
  promoted = {},
  onPromote,
  selectedBehavior,
  onSelectBehavior,
  disabled = false,
  compact = false,
}: {
  spec?: SocMemoryApplicabilitySpec | null;
  view?: SocMemoryScopeView | null;
  promoted?: Record<string, string[]>;
  onPromote?: (values: Record<string, string[]>) => void;
  selectedBehavior?: string[] | null;
  onSelectBehavior?: (values: string[]) => void;
  disabled?: boolean;
  compact?: boolean;
}) {
  if (!spec)
    return (
      <p className="text-muted-foreground text-sm">
        这条经验尚未保存结构化匹配范围。
      </p>
    );
  const reviewed = withMemoryReuseConditions(spec, promoted, selectedBehavior);
  const rows = new Map<string, string[]>();
  const behaviors = new Map<string, string[]>();
  const checkedBehavior =
    reviewed.selected_behavior_components ??
    view?.required_details.behavior_fingerprint?.behavior_component ??
    [];
  const append = (
    map: Map<string, string[]>,
    key: string,
    values: string[],
  ) => {
    map.set(key, [...new Set([...(map.get(key) ?? []), ...values])]);
  };
  for (const [key, values] of Object.entries(spec.required_facets)) {
    const expansion = view?.required_details[key];
    if (key === "behavior_fingerprint" && expansion) {
      for (const value of expansion.behavior_component ?? []) {
        const [prefix] = splitValue(value);
        append(behaviors, componentLabels[prefix] ?? prefix, [value]);
      }
    } else if (expansion) {
      for (const [detailKey, detailValues] of Object.entries(expansion)) {
        if (detailKey !== "entity")
          append(rows, conditionLabel(detailKey), displayValues(detailValues));
      }
    } else if (key.includes("fingerprint") || key.includes("signature")) {
      rows.set(conditionLabel(key), ["沿用已审核标识，组成明细待核验"]);
    } else if (
      key !== "behavior_strength" ||
      !view?.required_details.behavior_fingerprint
    ) {
      append(rows, conditionLabel(key), displayValues(values));
    }
  }
  const options = new Map<
    string,
    { key: string; prefix: string | null; values: string[] }
  >();
  // Without a verified projection, expose no guesses about hash contents.
  for (const option of view?.options ?? []) {
    if (
      option.kind !== "additional" ||
      internalKeys.has(option.key) ||
      spec.context_only_similarity_facet_keys.includes(option.key)
    )
      continue;
    for (const value of option.values) {
      const prefix = ["entity", "role_entity", "behavior_component"].includes(
        option.key,
      )
        ? splitValue(value)[0]
        : null;
      const id = option.key + "/" + (prefix ?? "*");
      if (
        spec.reuse_conditions?.some(
          (item) => item.facet_key + "/" + (item.value_prefix ?? "*") === id,
        )
      )
        continue;
      const group = options.get(id) ?? { key: option.key, prefix, values: [] };
      group.values.push(value);
      options.set(id, group);
    }
  }
  const removeCondition = (key: string, prefix?: string | null) => {
    if (!onPromote) return;
    const next = { ...promoted };
    const remaining = (next[key] ?? []).filter(
      (value) => prefix && !value.startsWith(prefix + ":"),
    );
    if (remaining.length) next[key] = remaining;
    else delete next[key];
    onPromote(next);
  };
  const legacyLimits = Object.keys(spec.required_facets).filter(
    (key) =>
      ["entity", "role_entity", "behavior_component", "source_type"].includes(
        key,
      ) && spec.context_only_required_facet_keys.includes(key),
  );
  return (
    <section aria-label="经验适用范围" data-memory-scope className="min-w-0">
      {!compact && (
        <h4 className="flex items-center gap-2 text-sm font-semibold">
          <LockKeyholeIcon className="size-4 text-emerald-700" />
          直接复用条件
        </h4>
      )}
      <div className="mt-2">
        <ScopeRows rows={rows} />
      </div>
      {behaviors.size > 0 && (
        <div className="mt-3 border-t pt-3" data-memory-scope-behavior>
          <h5 className="mb-1 text-xs font-semibold">核心行为</h5>
          <div className="divide-y">
            {[...behaviors].map(([name, values]) => (
              <div
                key={name}
                className="grid min-w-0 gap-2 py-3 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4"
              >
                <div className="text-muted-foreground text-xs leading-6">
                  {name}
                </div>
                <div className="grid min-w-0 gap-2">
                  {values.map((value) => {
                    const checked = checkedBehavior.includes(value);
                    const label = valueLabel(splitValue(value)[1]);
                    return (
                      <label
                        key={value}
                        className="flex min-w-0 items-start gap-2 text-sm leading-6"
                      >
                        <input
                          type="checkbox"
                          className="mt-1 size-4 shrink-0 accent-emerald-700"
                          aria-label={`要求核心行为 ${name} ${label}`}
                          checked={checked}
                          disabled={
                            disabled ||
                            !onSelectBehavior ||
                            (checked && checkedBehavior.length === 1)
                          }
                          onChange={(event) =>
                            onSelectBehavior?.(
                              event.target.checked
                                ? [...checkedBehavior, value]
                                : checkedBehavior.filter(
                                    (item) => item !== value,
                                  ),
                            )
                          }
                        />
                        <span className="min-w-0 [overflow-wrap:anywhere]">
                          {label}
                          {!checked && (
                            <span className="text-muted-foreground ml-2 text-xs">
                              不限定
                            </span>
                          )}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
          {onSelectBehavior && (
            <p className="text-muted-foreground mt-1 text-xs leading-5">
              勾选项必须逐项满足；取消后不再要求该项。至少保留一项核心行为。
            </p>
          )}
        </div>
      )}
      {!compact && (
        <>
          <div className="mt-4 border-t pt-3" data-memory-scope-limits>
            <h5 className="text-xs font-semibold">直接复用的附加条件</h5>
            {reviewed.reuse_conditions?.length ? (
              <div className="mt-2 divide-y">
                {reviewed.reuse_conditions.map((item) => {
                  const saved = spec.reuse_conditions?.some(
                    (old) =>
                      old.facet_key === item.facet_key &&
                      old.value_prefix === item.value_prefix,
                  );
                  const name = conditionLabel(
                    item.facet_key,
                    item.value_prefix,
                  );
                  return (
                    <div
                      key={item.facet_key + "/" + (item.value_prefix ?? "*")}
                      className="flex min-w-0 items-start gap-2 py-2 text-sm"
                    >
                      <span className="text-muted-foreground w-28 shrink-0 text-xs leading-6">
                        {name}
                      </span>
                      <span className="min-w-0 flex-1 leading-6 [overflow-wrap:anywhere]">
                        {displayValues(item.values, !!item.value_prefix).join(
                          " / ",
                        )}
                        {item.values.length > 1 && (
                          <span className="text-muted-foreground text-xs">
                            （任意一个）
                          </span>
                        )}
                      </span>
                      {onPromote && !saved && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-7 shrink-0"
                          disabled={disabled}
                          title={"移除" + name + "限制"}
                          aria-label={"移除" + name + "限制"}
                          onClick={() =>
                            removeCondition(item.facet_key, item.value_prefix)
                          }
                        >
                          <XIcon className="size-4" />
                        </Button>
                      )}
                      {saved && (
                        <span className="text-muted-foreground text-xs leading-6">
                          已保存
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className="text-muted-foreground mt-2 text-sm">
                未额外限定 IP、主机或账号。
              </p>
            )}
            {onPromote && options.size > 0 && (
              <details className="mt-3" data-memory-scope-options>
                <summary className="w-fit cursor-pointer text-sm font-medium text-emerald-800 dark:text-emerald-300">
                  <PlusIcon className="mr-1 inline size-4" />
                  增加直接复用限制
                </summary>
                <div className="mt-2 divide-y">
                  {[...options].map(([id, option]) => (
                    <fieldset
                      key={id}
                      aria-label={conditionLabel(option.key, option.prefix)}
                      className="grid min-w-0 gap-2 py-3 sm:grid-cols-[7rem_minmax(0,1fr)] sm:gap-4"
                    >
                      <div className="text-muted-foreground text-xs leading-6">
                        {conditionLabel(option.key, option.prefix)}
                      </div>
                      <div className="grid min-w-0 gap-2">
                        {option.values.map((value) => (
                          <label
                            key={value}
                            className="flex min-w-0 items-start gap-2 text-sm leading-6"
                          >
                            <input
                              type="checkbox"
                              className="mt-1 size-4 shrink-0 accent-emerald-700"
                              disabled={disabled}
                              aria-label={
                                "限制直接复用 " +
                                conditionLabel(option.key, option.prefix) +
                                " " +
                                displayValues([value], !!option.prefix)[0]
                              }
                              checked={
                                promoted[option.key]?.includes(value) ?? false
                              }
                              onChange={(event) => {
                                const next = { ...promoted };
                                const values = next[option.key] ?? [];
                                const selected = event.target.checked
                                  ? [...values, value]
                                  : values.filter((item) => item !== value);
                                if (selected.length)
                                  next[option.key] = selected;
                                else delete next[option.key];
                                onPromote(next);
                              }}
                            />
                            <span className="min-w-0 [overflow-wrap:anywhere]">
                              {displayValues([value], !!option.prefix)[0]}
                            </span>
                          </label>
                        ))}
                      </div>
                    </fieldset>
                  ))}
                </div>
              </details>
            )}
          </div>
          <div className="mt-4 border-l-2 border-emerald-600 pl-3 text-xs leading-6">
            <p className="font-medium">
              直接复用：固定范围、勾选的核心行为和附加条件全部满足，且经验已开启结论复用。
            </p>
            <p className="text-muted-foreground">
              <BookOpenIcon className="mr-1 inline size-3.5" />
              相似参考：附加条件未命中，仍可按相关性召回；由模型结合当前告警重新判断，可以采纳经验结论。
            </p>
          </div>
          {legacyLimits.length > 0 && (
            <p className="mt-3 text-xs text-amber-800 dark:text-amber-300">
              历史审核限制：
              {legacyLimits.map((key) => conditionLabel(key)).join("、")}
              同时限制直接复用和相似参考，仍按原审核范围执行。
            </p>
          )}
          {Object.keys(spec.excluded_facets).length > 0 && (
            <div className="mt-3 border-l-2 border-red-500 pl-3 text-sm">
              <h5 className="font-medium text-red-700">不适用范围</h5>
              {Object.entries(spec.excluded_facets).map(([key, values]) => (
                <p key={key} className="mt-1 [overflow-wrap:anywhere]">
                  {conditionLabel(key)}：{displayValues(values).join(" / ")}
                </p>
              ))}
            </div>
          )}
          {spec.minimum_optional_matches > 0 && (
            <p className="mt-3 text-xs">
              还需满足 {spec.minimum_optional_matches}{" "}
              组基础附加条件，见技术详情。
            </p>
          )}
          <details
            className="mt-4 border-t pt-3 text-xs"
            data-memory-scope-audit
          >
            <summary className="text-muted-foreground w-fit cursor-pointer">
              <CodeIcon className="mr-2 inline size-3.5" />
              技术详情：指纹与完整匹配条件
            </summary>
            <pre className="bg-muted/50 mt-3 max-h-80 overflow-auto p-3 font-mono text-xs [overflow-wrap:anywhere] whitespace-pre-wrap">
              {JSON.stringify(reviewed, null, 2)}
            </pre>
          </details>
        </>
      )}
    </section>
  );
}
