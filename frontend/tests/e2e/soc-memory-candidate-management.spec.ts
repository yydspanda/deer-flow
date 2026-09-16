import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import {
  memoryCandidate,
  memoryRecord,
  mockSocAPI,
} from "./utils/mock-soc-api";

const candidatePath = "/workspace/soc/review/memory-candidates/MC-ALPHA-001";
const recordPath = "/workspace/soc/memory/records/MEM-ALPHA-001";

for (const width of [1440, 390]) {
  test(`confirmed review exposes modification before historical content at ${width}px`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      includeQueueItem: false,
      standaloneMemoryCandidate: true,
      candidateStatus: "confirmed",
    });
    await page.goto(candidatePath);
    await expect(
      page.getByRole("heading", { name: "经验确认记录" }),
    ).toBeVisible();
    const actions = page.getByRole("region", { name: "已通过经验的管理" });
    const modify = actions.getByRole("link", { name: "修改经验", exact: true });
    await expect(modify).toHaveAttribute("href", `${recordPath}/revise`);
    await expect(
      actions.getByRole("link", { name: "查看经验详情" }),
    ).toHaveAttribute("href", recordPath);
    await expect(
      actions.getByRole("button", { name: "废止这条经验", exact: true }),
    ).toBeVisible();
    await expect(page.locator("[data-memory-review-actions]")).toHaveCount(0);
    const box = await modify.boundingBox();
    expect(box!.y + box!.height).toBeLessThan(1000);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.screenshot({
      path: testInfo.outputPath("confirmed-actions.png"),
      animations: "disabled",
    });
    await modify.click();
    await expect(page).toHaveURL(`${recordPath}/revise`, { timeout: 30_000 });
    expect(
      state.requests.filter((r) =>
        ["POST", "PATCH", "DELETE"].includes(r.method),
      ),
    ).toHaveLength(0);
  });
}

test("pending revision links to existing work and does not offer another revision", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  const state = await mockSocAPI(page, {
    includeQueueItem: false,
    standaloneMemoryCandidate: true,
    candidateStatus: "confirmed",
  });
  await page.route("**/api/soc/memory/records?*", (route) =>
    route.fulfill({
      json: {
        items: [
          { ...memoryRecord(state), metadata: { revision_pending: true } },
        ],
      },
    }),
  );
  await page.route("**/api/soc/memory/candidates?*", async (route) => {
    expect(
      new URL(route.request().url()).searchParams.get("revision_of_memory_id"),
    ).toBe("MEM-ALPHA-001");
    await route.fulfill({
      json: {
        items: [
          {
            ...memoryCandidate(state),
            candidate_id: "MC-REVISION",
            status: "pending_review",
            revision_lineage: { reason: "更新业务依据" },
          },
        ],
      },
    });
  });
  await page.goto(candidatePath);
  await expect(
    page.getByRole("link", { name: "继续审核修订" }),
  ).toHaveAttribute(
    "href",
    "/workspace/soc/review/memory-candidates/MC-REVISION",
  );
  await expect(
    page.getByRole("link", { name: "修改经验", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "废止这条经验", exact: true }),
  ).toBeDisabled();
});

test("record lookup failure offers recovery instead of destructive-only actions", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  const state = await mockSocAPI(page, {
    includeQueueItem: false,
    standaloneMemoryCandidate: true,
    candidateStatus: "confirmed",
  });
  let failed = true;
  await page.route("**/api/soc/memory/records?*", (route) =>
    route.fulfill(
      failed
        ? { status: 503, json: { detail: "unavailable" } }
        : { json: { items: [memoryRecord(state)] } },
    ),
  );
  await page.goto(candidatePath);
  await expect(page.getByText("已确认经验加载失败，请重试。")).toBeVisible({
    timeout: 15_000,
  });
  await expect(
    page.getByRole("button", { name: "废止这条经验", exact: true }),
  ).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "重新加载经验" }).click();
  await expect(
    page.getByRole("link", { name: "修改经验", exact: true }),
  ).toBeVisible();
});

test("terminal review remains read-only and closed history is discoverable", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  await mockSocAPI(page, {
    includeQueueItem: false,
    standaloneMemoryCandidate: true,
    candidateStatus: "deprecated",
  });
  await page.goto("/workspace/soc/review/memory-candidates");
  await page.getByLabel("候选状态").click();
  await page.getByRole("option", { name: "已结束", exact: true }).click();
  await expect(page.getByText("已废止", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "查看治理记录" }).click();
  await expect(
    page.getByRole("heading", { name: "经验审核记录" }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: "修改经验", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "废止这条经验", exact: true }),
  ).toHaveCount(0);
});
