import { beforeEach, expect, rs, test } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryCenter } from "@/components/workspace/soc/soc-memory-center";
import { SocMemoryRecordWorkbench } from "@/components/workspace/soc/soc-memory-record-workbench";
import {
  useSocMemoryCenterOverview,
  useSocMemoryCenterPattern,
  useSocMemoryLineage,
  useTestSocMemoryRecordMatch,
} from "@/core/soc";

rs.mock("next/link", () => ({ default: "a" }));
rs.mock("@/components/workspace/soc/soc-workspace-header", () => ({
  SocWorkspaceHeader: () => null,
}));
rs.mock("@/components/workspace/soc/soc-memory-pending-revision", () => ({
  SocMemoryPendingRevision: () => null,
}));
rs.mock("@/components/workspace/soc/soc-memory-deprecation-action", () => ({
  SocMemoryDeprecationAction: () => null,
}));
rs.mock("@/components/workspace/soc/soc-memory-scope-boundaries", () => ({
  SocMemoryScopeBoundaries: () => null,
}));
rs.mock("@/core/soc", () => ({
  useSocMemoryLineage: rs.fn(),
  useTestSocMemoryRecordMatch: rs.fn(),
  useUpdateSocMemoryRetrievalActivation: () => ({ isPending: false }),
  useSocMemoryCenterOverview: rs.fn(),
  useSocMemoryCenterPattern: rs.fn(),
  useSupersedeSocMemoryCandidate: () => ({ isPending: false }),
}));

beforeEach(() => {
  rs.mocked(useSocMemoryCenterOverview).mockReturnValue({
    overview: null,
    isLoading: false,
    isFetching: false,
    error: null,
    refetch: rs.fn(),
  } as unknown as ReturnType<typeof useSocMemoryCenterOverview>);
});

function recordHtml(profileVersion: string) {
  rs.mocked(useSocMemoryLineage).mockReturnValue({
    lineage: {
      record: {
        memory_id: "MEM-display",
        version: 2,
        memory_type: "detection_lesson",
        status: "confirmed",
        tenant_id: "tenant-test",
        source_candidate_id: "MC-source",
        source: { alert_id: "alert-source", run_id: "RUN-source" },
        summary: "已审核的业务经验",
        content: "审核结论原文",
        facets: {},
        metadata: {},
        validity: { valid_from: "2026-09-20T00:00:00Z" },
        retrieval_enabled: false,
        created_at: "2026-09-20T00:00:00Z",
        updated_at: "2026-09-20T00:00:00Z",
        applicability: {
          schema_version: "soc.memory_applicability.v1",
          profile_id: "pingan.soc",
          profile_version: profileVersion,
          feature_schema_version: `pingan.soc.memory_features.v${profileVersion}`,
          required_facets: { environment: ["dev"] },
          optional_facets: {},
          excluded_facets: {},
          minimum_optional_matches: 0,
          minimum_strong_anchor_matches: 1,
          context_only_required_facet_keys: [],
          context_only_missing_facet_keys: [],
          context_only_similarity_facet_keys: [],
        },
      },
      uses: [],
      feedback: [],
      health: [],
      revision_proposals: [],
    },
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useSocMemoryLineage>);
  rs.mocked(useTestSocMemoryRecordMatch).mockReturnValue({
    isPending: false,
    data: {
      matched: true,
      profile_id: "pingan.soc",
      profile_version: profileVersion,
      exclusion_reasons: [],
      match: { score: 112.7, match_reasons: ["业务范围一致"] },
    },
  } as unknown as ReturnType<typeof useTestSocMemoryRecordMatch>);
  return renderToStaticMarkup(
    <SocMemoryRecordWorkbench memoryId="MEM-display" />,
  );
}

for (const profileVersion of ["7", "9"]) {
  test(`record and match test hide internal profile ${profileVersion} while preserving the actual Memory revision and audit`, () => {
    const html = recordHtml(profileVersion);
    expect(html).not.toContain(`pingan.soc v${profileVersion}`);
    expect(html).not.toContain("匹配规则版本");
    expect(html).not.toContain("匹配规则待升级");
    expect(html).toContain("MEM-display · v2");
    expect(html).toContain("新告警会找到这条经验");
    expect(html).toContain("相关度 112.7");
    expect(html).toContain("业务范围一致");
    expect(html).toContain("profile_version");
    expect(html).toContain(`pingan.soc.memory_features.v${profileVersion}`);
  });
}

for (const state of ["current", "legacy", "unregistered"] as const) {
  test(`pattern detail keeps actionable ${state} state without internal profile identifiers`, () => {
    rs.mocked(useSocMemoryCenterPattern).mockReturnValue({
      detail: {
        pattern: {
          lineage_key: "lineage-display",
          pattern_label: "Windows 安装行为",
          pattern_value: "installer",
          profile_id: "pingan.soc",
          profile_version: "7",
          current_profile_version: "9",
          profile_state: state,
          lifecycle_state: "collecting",
          future_use_state: "not_ready",
          attention_reasons: [],
          support_count: 1,
          distinct_source_count: 1,
          aggregation_window_count: 1,
          candidate_snapshot_count: 0,
          reinforcement_count: 0,
          candidate: null,
          memory_record: null,
        },
        candidates: [],
        memory_records: [],
        observations: [],
        observation_total: 0,
      },
      isLoading: false,
      isFetching: false,
      error: null,
    } as unknown as ReturnType<typeof useSocMemoryCenterPattern>);
    const html = renderToStaticMarkup(
      <SocMemoryCenter initialLineageKey="lineage-display" />,
    );
    expect(html).not.toContain("pingan.soc v7");
    expect(html).not.toContain("当前规则版本 v9");
    expect(html).not.toContain("匹配规则版本");
    expect(html).toContain("同类识别状态");
    if (state === "legacy") expect(html).toContain("需要重新校验");
    if (state === "unregistered") expect(html).toContain("不能用于新告警");
  });
}
