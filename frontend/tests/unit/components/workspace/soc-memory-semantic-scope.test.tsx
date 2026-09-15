import { expect, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryScope } from "@/components/workspace/soc/soc-memory-scope";
import type { SocMemoryApplicabilitySpec } from "@/core/soc";

test("semantic matching ingredients have Chinese labels and remain selectable", () => {
  const spec: SocMemoryApplicabilitySpec = {
    schema_version: "soc.memory_applicability.v1",
    profile_id: "pingan.soc",
    profile_version: "8",
    feature_schema_version: "pingan.soc.memory_features.v6",
    required_facets: { behavior_fingerprint: ["frozen-hash"] },
    optional_facets: {},
    excluded_facets: {},
    minimum_optional_matches: 0,
    minimum_strong_anchor_matches: 1,
    context_only_required_facet_keys: [],
    context_only_missing_facet_keys: [],
    context_only_similarity_facet_keys: [],
    policy_version: "soc.memory_applicability_policy.v3",
  };
  const html = renderToStaticMarkup(
    <SocMemoryScope
      spec={spec}
      view={{
        schema_version: "soc.memory_scope_view.v1",
        required_details: {
          behavior_fingerprint: {
            behavior_component: [
              "detected_file:yak.exe",
              "observed_process:bash.exe",
            ],
          },
        },
        unresolved_fingerprint_keys: [],
        options: [],
      }}
      onSelectBehavior={() => undefined}
    />,
  );
  expect(html).toContain("要求核心行为 被检测文件 yak.exe");
  expect(html).toContain("要求核心行为 日志中的进程 bash.exe");
  expect(html.match(/checked=""/g)).toHaveLength(2);
});
