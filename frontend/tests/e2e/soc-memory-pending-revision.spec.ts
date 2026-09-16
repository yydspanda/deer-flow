import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { memoryRecord, mockSocAPI } from "./utils/mock-soc-api";

for (const width of [1440, 390]) {
  test(`pending revision is reachable from record and revision pages at ${width}px`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
    });
    const record = {
      ...memoryRecord(state),
      retrieval_enabled: false,
      metadata: { revision_pending: true },
    };
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
    await page.route("**/api/soc/memory/candidates?*", (route) => {
      expect(
        new URL(route.request().url()).searchParams.get(
          "revision_of_memory_id",
        ),
      ).toBe("MEM-ALPHA-001");
      return route.fulfill({
        json: {
          items: [
            {
              candidate_id: "MC-ALPHA-001",
              revision_lineage: { reason: "补充业务依据与适用边界" },
            },
          ],
        },
      });
    });
    for (const suffix of ["", "/revise"]) {
      await page.goto(`/workspace/soc/memory/records/MEM-ALPHA-001${suffix}`);
      const link = page.getByRole("link", { name: "继续审核修订" });
      await expect(link).toBeVisible({ timeout: 45_000 });
      await expect(link).toHaveAttribute(
        "href",
        "/workspace/soc/review/memory-candidates/MC-ALPHA-001",
      );
      await expect(
        page.getByRole("link", { name: "创建修订版本" }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "暂停旧经验并创建修订候选" }),
      ).toHaveCount(0);
      if (!suffix) {
        await expect(
          page.getByRole("button", { name: "废止这条经验", exact: true }),
        ).toBeDisabled();
        await expect(
          page.getByText(
            "已有待审核的修订，请先完成或取消该修订，再决定是否废止旧经验。",
          ),
        ).toBeVisible();
      }
      await link.scrollIntoViewIfNeeded();
      const box = await link.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: `../backend/.deer-flow/soc-validation/memory-revision-navigation-20260909/${width}-${suffix ? "revise" : "record"}.png`,
      });
    }
    await page.getByRole("link", { name: "继续审核修订" }).click();
    await expect(page).toHaveURL(/\/review\/memory-candidates\/MC-ALPHA-001/);
    expect(
      state.requests.filter(
        (r) => r.method === "POST" && !r.path.endsWith("/governance-preview"),
      ),
    ).toHaveLength(0);
  });
}
