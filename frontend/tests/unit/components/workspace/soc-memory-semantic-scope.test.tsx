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

test("network observations show request and response meaning without exposing profile versions", () => {
  const behaviors = [
    "http_observation:request=command_execution,directory_traversal,file_upload;response=command_output,directory_listing,file_content;server=simplehttp",
    "http_observation:response=directory_listing;server=nginx",
  ];
  const html = renderToStaticMarkup(
    <SocMemoryScope
      compact
      spec={{
        schema_version: "soc.memory_applicability.v1",
        profile_id: "pingan.soc",
        profile_version: "11",
        feature_schema_version: "pingan.soc.memory_features.v9",
        required_facets: { behavior_fingerprint: ["opaque-server-hash"] },
        optional_facets: {},
        excluded_facets: {},
        minimum_optional_matches: 0,
        minimum_strong_anchor_matches: 1,
        context_only_required_facet_keys: [],
        context_only_missing_facet_keys: [],
        context_only_similarity_facet_keys: [],
        policy_version: "soc.memory_applicability_policy.v4",
      }}
      view={{
        schema_version: "soc.memory_scope_view.v1",
        required_details: {
          behavior_fingerprint: { behavior_component: behaviors },
        },
        unresolved_fingerprint_keys: [],
        options: [],
      }}
      onSelectBehavior={() => undefined}
    />,
  );
  for (const label of [
    "请求包含命令执行载荷",
    "请求包含目录穿越载荷",
    "请求上传文件",
    "响应包含命令输出",
    "响应返回目录列表",
    "响应返回文件内容",
    "Server 头标识 SimpleHTTP",
    "Server 头标识 nginx",
    "同一 HTTP 事务",
  ])
    expect(html).toContain(label);
  expect(html.match(/type="checkbox"/g)).toHaveLength(2);
  expect(html).not.toContain("profile_version");
  expect(html).not.toContain("pingan.soc.memory_features.v9");
  expect(html).not.toContain("opaque-server-hash");
});
