import { type SocCorpusWorkbenchGroup } from "./types";

const VALUE_LABELS: Record<string, string> = {
  command_execution: "命令执行",
  denial_of_service: "拒绝服务",
  outbound_c2: "C2 外联",
  proxy_tunnel_activity: "代理隧道",
  reverse_connection: "反向连接",
  vulnerability_exploitation: "漏洞利用",
  web_attack: "Web 攻击",
  webshell: "WebShell",
};

const FACET_PRIORITY: Record<string, number> = {
  vulnerability: 0,
  attack_family: 1,
  network_service: 2,
  scenario: 3,
  process_image: 4,
  process: 5,
  target_class: 6,
  http_method: 7,
  protocol: 8,
  technique: 9,
};

function humanizeValue(value: string): string {
  return VALUE_LABELS[value.toLocaleLowerCase()] ?? value.replaceAll("_", " ");
}

export function formatCorpusBehaviorFacet(component: string): string {
  const [facet, ...parts] = component.split(":");
  const rawValue = parts.join(":");
  if (!rawValue) return component;

  if (facet === "attack_family" && rawValue.startsWith("source_category:")) {
    return humanizeValue(rawValue.slice("source_category:".length));
  }
  if (facet === "network_service") return rawValue.toUpperCase();
  if (facet === "vulnerability" || facet === "technique") {
    return rawValue.toUpperCase();
  }
  if (facet === "process" || facet === "process_image") {
    return `进程 ${rawValue}`;
  }
  if (facet === "http_method") return `HTTP ${rawValue.toUpperCase()}`;
  if (facet === "target_class") return humanizeValue(rawValue);
  return humanizeValue(rawValue);
}

export function summarizeCorpusGroupBehavior(
  behaviorComponents: string[],
  limit = 3,
): string {
  const ordered = [...behaviorComponents].sort((left, right) => {
    const leftFacet = left.split(":", 1)[0] ?? "";
    const rightFacet = right.split(":", 1)[0] ?? "";
    return (
      (FACET_PRIORITY[leftFacet] ?? 99) - (FACET_PRIORITY[rightFacet] ?? 99) ||
      left.localeCompare(right)
    );
  });
  return Array.from(new Set(ordered.map(formatCorpusBehaviorFacet)))
    .filter(Boolean)
    .slice(0, limit)
    .join(" / ");
}

export function formatCorpusGroupOption(
  group: SocCorpusWorkbenchGroup,
): string {
  const rule = group.rule_name ?? group.detection_key ?? "未命名规则";
  const behavior = summarizeCorpusGroupBehavior(group.behavior_components);
  const suffix = group.group_id.replace(/^CG-/, "").slice(-6);
  return [
    rule,
    behavior || group.source_type.toUpperCase(),
    `组 ${suffix}`,
    `${group.alert_count} 条`,
  ].join(" · ");
}
