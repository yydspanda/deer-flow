import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { mockSocAPI } from "./utils/mock-soc-api";

for (const viewport of [
  { width: 1440, height: 1000 },
  { width: 390, height: 844 },
]) {
  test(`candidate comparison and replacement choice at ${viewport.width}px`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize(viewport);
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
    });
    await page.route(
      "**/api/soc/memory/candidates/MC-ALPHA-001/governance-preview",
      (route) =>
        route.fulfill({
          json: {
            candidate_id: "MC-ALPHA-001",
            recommendation: "revise",
            related_count: 1,
            explanation:
              "模拟对照：本次审核与已有经验不同，适用条件相同。请核对业务事实后修订原经验。",
            related_memories: [
              {
                memory_id: "MEM-OLD-FIXTURE",
                version: 2,
                conclusion:
                  "已审核的旧经验：该反连行为属于内部服务访问，并非真实反弹 Shell。",
                reviewed_verdict: "false_positive",
                scope_relation: "same",
                conclusion_relation: "differs",
                retrieval_enabled: true,
                directive_enabled: true,
                retrieved_in_source_run: true,
                differences: [],
              },
            ],
          },
        }),
    );
    await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");
    const comparison = page.getByRole("region", { name: "与已有经验对照" });
    await expect(comparison).toBeVisible({ timeout: 45_000 });
    await comparison.getByLabel("以本次审核修订这条经验").check();
    await expect(
      page.getByRole("button", { name: "确认修订并替换旧经验" }),
    ).toBeDisabled();
    await expect(
      comparison.getByText("现在尚未修改原经验", { exact: false }),
    ).toBeVisible();
    expect(
      state.requests.some((request) => request.path.endsWith("/review")),
    ).toBe(false);
    await comparison.scrollIntoViewIfNeeded();
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-governance-20260909/review-${viewport.width}.png`,
    });
    const box = await comparison.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(viewport.width + 1);
    await comparison.getByLabel("保留为独立经验").check();
    await expect(
      page.getByRole("button", { name: "确认并启用经验" }),
    ).toBeVisible();
  });
}
