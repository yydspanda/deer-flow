import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";

const AGGREGATION_KEY = "a".repeat(64);
const LINEAGE_KEY = "b".repeat(64);
const LEGACY_LINEAGE_KEY = "c".repeat(64);

const pattern = {
  schema_version: "soc.memory_center_pattern.v1",
  lineage_key: LINEAGE_KEY,
  tenant_id: "pingan",
  environment: "dev",
  data_class: "operational",
  pattern_dimension: "detection_behavior",
  pattern_value: "sliver-heartbeat",
  pattern_label: "Sliver 远控木马心跳",
  profile_id: "pingan.alert_memory",
  profile_version: "4",
  feature_schema_version: "4",
  current_profile_version: "4",
  current_feature_schema_version: "4",
  profile_state: "current",
  lifecycle_state: "memory_active",
  future_use_state: "exact_match_decision",
  attention_reasons: [],
  support_count: 8,
  distinct_source_count: 8,
  aggregation_window_count: 3,
  candidate_snapshot_count: 5,
  reinforcement_count: 3,
  first_observed_at: "2026-04-27T10:00:00Z",
  last_observed_at: "2026-04-27T18:00:00Z",
  first_window_start: "2026-04-27T00:00:00Z",
  last_window_end: "2026-04-30T00:00:00Z",
  candidate: {
    candidate_id: "MC-93413A392B09",
    status: "confirmed",
    summary: "Sliver 远控木马心跳重复模式",
    support_count_at_creation: 5,
    distinct_source_count_at_creation: 5,
    superseded_by_candidate_id: null,
  },
  memory_record: {
    memory_id: "MEM-F374BEA6E7B6",
    version: 2,
    status: "confirmed",
    summary: "Sliver 心跳在该适用范围内已由运营确认",
    retrieval_enabled: true,
    decision_directive_ready: true,
    retrieval_valid_until: "2026-10-27T18:00:00Z",
    retrieval_review_due_at: "2026-09-27T18:00:00Z",
  },
};

const legacyPattern = {
  ...pattern,
  lineage_key: LEGACY_LINEAGE_KEY,
  pattern_value: "galaxylab-t1003-v3",
  pattern_label: "GalaxyLab T1003 旧 Profile",
  profile_version: "3",
  feature_schema_version: "3",
  profile_state: "legacy",
  lifecycle_state: "terminal_history",
  future_use_state: "not_ready",
  attention_reasons: ["candidate_superseded"],
  support_count: 5,
  distinct_source_count: 5,
  aggregation_window_count: 1,
  candidate_snapshot_count: 5,
  reinforcement_count: 0,
  candidate: {
    candidate_id: "MC-206BBCE75A96",
    status: "superseded",
    summary: "GalaxyLab T1003 V3 历史候选",
    support_count_at_creation: 5,
    distinct_source_count_at_creation: 5,
    superseded_by_candidate_id: "MC-B6839162885A",
  },
  memory_record: null,
};

test("shows operational Sliver memory outside the fixed GalaxyLab DEV cohort", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  const detailRequests: URL[] = [];
  const overviewRequests: URL[] = [];
  await page.route("**/api/soc/memory/center**", async (route) => {
    const url = new URL(route.request().url());
    const pathname = url.pathname;
    if (pathname.endsWith(`/patterns/${LINEAGE_KEY}`)) {
      detailRequests.push(url);
      const includeObservations =
        url.searchParams.get("include_observations") === "true";
      const observationLimit = Number(
        url.searchParams.get("observation_limit") ?? "20",
      );
      const observationOffset = Number(
        url.searchParams.get("observation_offset") ?? "0",
      );
      const observations = Array.from({ length: 8 }, (_, index) => ({
        schema_version: "soc.memory_pattern_observation.v1",
        observation_id: `MPO-SLIVER-${index + 1}`,
        aggregation_key: AGGREGATION_KEY,
        lineage_key: pattern.lineage_key,
        tenant_id: "pingan",
        environment: "dev",
        data_class: "operational",
        profile_id: "pingan.alert_memory",
        profile_version: "4",
        feature_schema_version: "4",
        source: {
          source_type: "analysis_run",
          source_id: `RUN-SLIVER-${index + 1}`,
          transport_ref: `alert-${1979525 + index}`,
          run_id: `RUN-SLIVER-${index + 1}`,
          alert_id: String(1979525 + index),
          observed_at: `2026-04-27T1${index}:00:00Z`,
        },
        signature: {
          dimension: "detection_behavior",
          value: "sliver-heartbeat",
          label: "Sliver 远控木马心跳",
          origin: "canonical_alert",
          facets: {},
        },
        lesson: {
          verdict: "false_positive",
          risk_class: "benign",
          needs_review: false,
          summary: "运营确认的同类行为",
          reason: "业务事实与当前模式一致",
          recommended_action: "ignore",
        },
        window_start: pattern.first_window_start,
        window_end: pattern.last_window_end,
        created_at: `2026-04-27T1${index}:00:00Z`,
      }));
      await route.fulfill({
        json: {
          schema_version: "soc.memory_center_pattern_detail.v1",
          pattern,
          candidates: [
            {
              candidate_id: "MC-93413A392B09",
              status: "confirmed",
            },
          ],
          memory_records: [
            {
              memory_id: "MEM-F374BEA6E7B6",
              retrieval_enabled: true,
            },
          ],
          observations: includeObservations
            ? observations.slice(
                observationOffset,
                observationOffset + observationLimit,
              )
            : [],
          observation_total: 8,
          observation_limit: observationLimit,
          observation_offset: observationOffset,
          suggested_successor_candidate_id: null,
        },
      });
      return;
    }
    overviewRequests.push(url);
    await route.fulfill({
      json: {
        schema_version: "soc.memory_center_overview.v1",
        metrics: {
          pattern_count: 2,
          aggregation_window_count: 5,
          observation_count: 22,
          pending_candidate_count: 0,
          confirmed_memory_count: 2,
          retrieval_enabled_memory_count: 2,
          superseded_candidate_count: 1,
          legacy_profile_pattern_count: 1,
          unregistered_profile_pattern_count: 0,
        },
        items:
          new URL(route.request().url()).searchParams.get(
            "include_terminal_history",
          ) === "true"
            ? [pattern, legacyPattern]
            : [pattern],
        terminal_history_count: 1,
        total:
          new URL(route.request().url()).searchParams.get(
            "include_terminal_history",
          ) === "true"
            ? 2
            : 1,
        limit: 50,
        offset: 0,
        generated_at: "2026-08-18T08:00:00Z",
      },
    });
  });

  await page.goto("/workspace/soc/memory");

  await expect(
    page.getByRole("heading", { name: "SOC 经验中心" }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: /待审核经验/ })).toHaveAttribute(
    "data-variant",
    "default",
  );
  await expect(page.getByText("沉淀阶段", { exact: true })).toBeVisible();
  await expect(page.getByText("新告警使用", { exact: true })).toBeVisible();
  await expect(
    page.getByText("Sliver 远控木马心跳重复模式").first(),
  ).toBeVisible();
  const listStatuses = page.getByTestId("memory-pattern-statuses").first();
  await expect(listStatuses).toHaveCSS("flex-wrap", "nowrap");
  await expect(listStatuses.locator('[data-slot="badge"]')).toHaveCount(2);
  const listRowCenters = await page
    .locator(
      '[data-testid="memory-pattern-title"], [data-testid="memory-pattern-statuses"], [data-testid="memory-pattern-count"]',
    )
    .evaluateAll((parts) =>
      parts.slice(0, 3).map((part) => {
        const bounds = part.getBoundingClientRect();
        return bounds.y + bounds.height / 2;
      }),
    );
  expect(
    Math.max(...listRowCenters) - Math.min(...listRowCenters),
  ).toBeLessThan(1);
  await expect(page.getByText("选择一组同类行为查看详情。")).toBeVisible();
  expect(detailRequests).toHaveLength(0);

  await page.getByRole("link", { name: /Sliver 远控木马心跳重复模式/ }).click();

  await expect(
    page.getByText("8 条告警 / 8 个独立来源 / 3 个时间窗"),
  ).toBeVisible();
  await expect(
    page.getByText("生成时包含 5 条样本，后续新增 3 条"),
  ).toBeVisible();
  await expect(page.getByText("沉淀进度", { exact: true })).toBeVisible();
  await expect(
    page.getByText("MEM-F374BEA6E7B6", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByText("精确匹配可复用结论", { exact: true }).first(),
  ).toBeVisible();
  await expect(page.getByText("GalaxyLab Cohort")).toHaveCount(0);
  await expect(
    page.getByRole("link", { name: "查看 / 修订经验" }),
  ).toHaveAttribute("href", "/workspace/soc/memory/records/MEM-F374BEA6E7B6");
  await expect(
    page.getByRole("link", { name: "查看 / 修订经验" }),
  ).toHaveAttribute("data-variant", "default");
  expect(detailRequests).toHaveLength(1);
  expect(detailRequests[0]?.searchParams.get("include_observations")).toBe(
    "false",
  );
  expect(detailRequests[0]?.searchParams.get("observation_limit")).toBe("20");
  await expect(page.getByText("Alert 1979525")).toHaveCount(0);
  await page.getByRole("button", { name: "加载来源告警" }).click();
  await expect(page.getByText("Alert 1979525")).toBeVisible();
  expect(detailRequests).toHaveLength(2);
  expect(detailRequests[1]?.searchParams.get("include_observations")).toBe(
    "true",
  );

  await expect(page.getByText("GalaxyLab T1003 V3 历史候选")).toHaveCount(0);
  await page.getByLabel("按沉淀阶段筛选").click();
  await page.getByRole("option", { name: "经验已沉淀" }).click();
  await expect
    .poll(() => overviewRequests.at(-1)?.searchParams.get("stage"))
    .toBe("persisted");
  await page.getByLabel("按新告警使用方式筛选").click();
  await page.getByRole("option", { name: "精确匹配可复用结论" }).click();
  await expect
    .poll(() => overviewRequests.at(-1)?.searchParams.get("future_use"))
    .toBe("exact_match_decision");
  await page.getByRole("switch", { name: "包含已结束模式 (1)" }).click();
  await expect(page.getByText("GalaxyLab T1003 V3 历史候选")).toBeVisible();

  await page.setViewportSize({ width: 1920, height: 1080 });
  const wideLayoutRatio = await page
    .getByTestId("memory-center-layout")
    .evaluate((element) => {
      const workspace = element.closest("main");
      return (
        element.getBoundingClientRect().width /
        (workspace?.getBoundingClientRect().width ?? innerWidth)
      );
    });
  expect(wideLayoutRatio).toBeGreaterThan(0.92);

  const desktopOverflow = await page
    .getByTestId("memory-center-layout")
    .evaluate((element) => element.scrollWidth - element.clientWidth);
  expect(desktopOverflow).toBeLessThanOrEqual(1);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel("按沉淀阶段筛选")).toBeVisible();
  await expect(page.getByLabel("按新告警使用方式筛选")).toBeVisible();
  const mobileOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  expect(mobileOverflow).toBeLessThanOrEqual(1);
});
