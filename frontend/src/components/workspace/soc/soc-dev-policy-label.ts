export function formatSocDevPolicyLabel({
  tenantPolicy,
  softwarePathFastPolicy,
}: {
  tenantPolicy: "disabled" | "deterministic" | "deterministic_and_llm";
  softwarePathFastPolicy: boolean;
}) {
  if (tenantPolicy === "disabled") return "关闭";
  const capabilities = ["确定性"];
  if (softwarePathFastPolicy) capabilities.push("安全路径");
  if (tenantPolicy === "deterministic_and_llm") capabilities.push("LLM");
  return `全开（${capabilities.join(" + ")}）`;
}
