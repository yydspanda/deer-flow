import type {
  SocMemoryApplicabilitySpec,
  SocMemoryReuseCondition,
} from "./types";

// Build the review command only. The server validates values and evaluates scope.
export function withMemoryReuseConditions(
  base: SocMemoryApplicabilitySpec,
  selected: Record<string, string[]>,
  selectedBehavior?: string[] | null,
  verifiedBehavior?: string[] | null,
): SocMemoryApplicabilitySpec {
  const conditions = new Map<string, SocMemoryReuseCondition>();
  for (const item of base.reuse_conditions ?? []) {
    conditions.set(`${item.facet_key}/${item.value_prefix ?? "*"}`, item);
  }
  for (const [key, values] of Object.entries(selected)) {
    for (const value of values) {
      const prefix = ["entity", "role_entity", "behavior_component"].includes(
        key,
      )
        ? value.split(":", 1)[0]!
        : null;
      const id = `${key}/${prefix ?? "*"}`;
      // Selections replace the values of a saved condition, never its key.
      const chosen = values.filter((item) =>
        prefix ? item.startsWith(`${prefix}:`) : true,
      );
      conditions.set(id, {
        facet_key: key,
        value_prefix: prefix,
        values: [...new Set(chosen)].sort(),
      });
    }
  }
  const behavior = selectedBehavior ?? base.selected_behavior_components;
  const coverage = base.covered_behavior_components ?? verifiedBehavior;
  const optional = { ...base.optional_facets };
  for (const key of ["entity", "role_entity"]) {
    if (selected[key]?.length)
      optional[key] = [
        ...new Set([...(optional[key] ?? []), ...selected[key]]),
      ].sort();
  }
  if (!conditions.size && behavior == null) return base;
  return {
    ...base,
    optional_facets: optional,
    ...(behavior != null && coverage != null
      ? { covered_behavior_components: [...new Set(coverage)].sort() }
      : {}),
    ...(behavior != null
      ? { selected_behavior_components: [...new Set(behavior)].sort() }
      : {}),
    reuse_conditions: [...conditions]
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([, item]) => item),
    policy_version:
      behavior != null
        ? coverage != null
          ? "soc.memory_applicability_policy.v4"
          : "soc.memory_applicability_policy.v3"
        : "soc.memory_applicability_policy.v2",
  };
}
