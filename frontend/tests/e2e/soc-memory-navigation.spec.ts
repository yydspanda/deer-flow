import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { memoryRecord, mockSocAPI } from "./utils/mock-soc-api";

for (const width of [1440, 390]) {
  test(`one memory center supports browse, review and accumulation at ${width}px`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
    });
    const record = memoryRecord(state);
    const requests: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/soc/")) requests.push(request.url());
    });
    await page.route("**/api/soc/memory/records?*", (route) =>
      route.fulfill({
        json: { items: [record], limit: 50, offset: 0, has_more: false },
      }),
    );
    await page.route("**/api/soc/memory/records/MEM-ALPHA-001", (route) =>
      route.fulfill({ json: record }),
    );
    await page.route(
      "**/api/soc/memory/records/MEM-ALPHA-001/lineage",
      (route) =>
        route.fulfill({
          json: {
            record,
            uses: [],
            feedback: [],
            health: [],
            revision_proposals: [],
          },
        }),
    );
    await page.route("**/api/soc/memory/center**", (route) =>
      route.fulfill({
        json: {
          metrics: {
            pattern_count: 0,
            aggregation_window_count: 0,
            observation_count: 0,
            pending_candidate_count: 1,
            confirmed_memory_count: 1,
            retrieval_enabled_memory_count: 0,
            superseded_candidate_count: 0,
            legacy_profile_pattern_count: 0,
            unregistered_profile_pattern_count: 0,
          },
          items: [],
          total: 0,
          terminal_history_count: 0,
          limit: 50,
          offset: 0,
        },
      }),
    );
    await page.goto("/workspace/soc/memory");
    const navigation = page.getByRole("navigation", { name: "经验中心视图" });
    await expect(
      page.getByRole("heading", { name: "经验中心", exact: true }),
    ).toBeVisible({ timeout: 45_000 });
    await expect(
      navigation.getByRole("link", { name: "已确认经验" }),
    ).toHaveAttribute("aria-current", "page");
    await expect(page.getByLabel("搜索已确认经验")).toBeVisible();
    await expect(page.getByText(record.summary, { exact: true })).toBeVisible();
    expect(
      requests.some((url) => /\/center|\/candidates|\/records\/MEM-/.test(url)),
    ).toBe(false);
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-center-navigation-20260909/records-${width}.png`,
    });

    await page.getByText(record.summary, { exact: true }).click();
    await expect(
      page.getByRole("link", { name: "返回已确认经验" }),
    ).toBeVisible();
    await page.getByRole("link", { name: "返回已确认经验" }).click();
    await expect(page).toHaveURL(/\/soc\/memory$/);
    await navigation.getByRole("link", { name: "经验审核" }).click();
    await expect(
      page.getByRole("heading", { name: "待审核与历史记录" }),
    ).toBeVisible();
    await expect(
      navigation.getByRole("link", { name: "经验审核" }),
    ).toHaveAttribute("aria-current", "page");
    await page.getByRole("link", { name: "审核并决定" }).click();
    await expect(
      page.getByRole("heading", { name: "审核这条经验" }),
    ).toBeVisible();
    await page.getByRole("link", { name: "返回审核列表" }).click();
    await expect(
      page.getByRole("heading", { name: "待审核与历史记录" }),
    ).toBeVisible();
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-center-navigation-20260909/review-${width}.png`,
    });

    await navigation.getByRole("link", { name: "同类告警积累" }).click();
    await expect(page).toHaveURL(/\/memory\/patterns$/);
    await expect(
      navigation.getByRole("link", { name: "同类告警积累" }),
    ).toHaveAttribute("aria-current", "page");
    await expect(page.getByLabel("按沉淀阶段筛选")).toBeVisible();
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-center-navigation-20260909/patterns-${width}.png`,
    });
    const bounds = await navigation.getByRole("link").evaluateAll((links) =>
      links.map((link) => {
        const r = link.getBoundingClientRect();
        return { left: r.left, right: r.right };
      }),
    );
    expect(bounds.every((r) => r.left >= 0 && r.right <= width)).toBe(true);
    await page.goBack();
    await expect(
      navigation.getByRole("link", { name: "经验审核" }),
    ).toHaveAttribute("aria-current", "page");
    await page.goto("/workspace/soc/memory/records");
    await expect(
      page.getByRole("heading", { name: "经验中心", exact: true }),
    ).toBeVisible();
    await expect(
      navigation.getByRole("link", { name: "已确认经验" }),
    ).toHaveAttribute("aria-current", "page");
    expect(
      state.requests.filter(
        (r) => r.method === "POST" && !r.path.endsWith("/governance-preview"),
      ),
    ).toHaveLength(0);
  });
}
