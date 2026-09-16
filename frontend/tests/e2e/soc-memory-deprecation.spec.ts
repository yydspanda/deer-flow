import { expect, test, type Page } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { memoryRecord, mockSocAPI } from "./utils/mock-soc-api";

const recordPath = "/workspace/soc/memory/records/MEM-ALPHA-001";
const reviewPath = "/api/soc/memory/candidates/MC-ALPHA-001/review";
const reason = "业务用途已经变更，这条经验不再适合后续告警，决定废止而不修订。";

async function mockRecord(page: Page, status = "confirmed") {
  mockLangGraphAPI(page, { threads: [] });
  const state = await mockSocAPI(page, {
    includeQueueItem: false,
    standaloneMemoryCandidate: true,
    candidateStatus: status,
  });
  await page.route(
    "**/api/soc/memory/records/MEM-ALPHA-001{,/lineage}",
    async (route) => {
      const deprecated = state.candidateStatus === "deprecated";
      const record = {
        ...memoryRecord(state),
        status: state.candidateStatus,
        version: deprecated ? 2 : 1,
        retrieval_enabled: !deprecated,
        deprecation_reason: deprecated ? reason : null,
      };
      await route.fulfill({
        json: route.request().url().endsWith("/lineage")
          ? {
              record,
              uses: [],
              feedback: [],
              health: [],
              revision_proposals: [],
            }
          : record,
      });
    },
  );
  return state;
}

for (const suffix of ["", "/revise"]) {
  for (const width of [1440, 390]) {
    test(`deprecates Memory from ${suffix || "record"} at ${width}px without creating a revision`, async ({
      page,
    }, testInfo) => {
      await page.setViewportSize({ width, height: 1000 });
      const state = await mockRecord(page);
      await page.goto(`${recordPath}${suffix}`);
      const trigger = page.getByRole("button", {
        name: "废止这条经验",
        exact: true,
      });
      await trigger.scrollIntoViewIfNeeded();
      const box = await trigger.boundingBox();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(width);
      await page.screenshot({
        path: testInfo.outputPath("actions.png"),
        animations: "disabled",
      });
      await trigger.click();
      const dialog = page.getByRole("dialog", {
        name: "废止这条经验",
        exact: true,
      });
      await expect(dialog.getByText(/不是临时暂停/)).toBeVisible();
      await expect(
        dialog.getByRole("button", { name: "确认废止" }),
      ).toBeDisabled();
      await dialog.getByLabel("废止原因").fill("太短");
      await expect(
        dialog.getByRole("button", { name: "确认废止" }),
      ).toBeDisabled();
      await dialog.getByRole("button", { name: "取消", exact: true }).click();
      expect(state.requests.filter((r) => r.method === "POST")).toHaveLength(0);
      await trigger.click();
      await dialog.getByLabel("废止原因").fill(reason);
      await page.screenshot({
        path: testInfo.outputPath("confirmation.png"),
        animations: "disabled",
      });
      expect(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= window.innerWidth,
        ),
      ).toBe(true);
      await dialog.getByRole("button", { name: "确认废止" }).click();
      await expect(dialog).not.toBeVisible();
      await expect(page).toHaveURL(recordPath);
      await expect(
        page.getByText("已废止", { exact: true }).first(),
      ).toBeVisible();
      await expect(page.getByText(reason, { exact: true })).toBeVisible();
      await expect(
        page.getByRole("link", { name: "创建修订版本" }),
      ).toHaveCount(0);
      await expect(
        page.getByRole("button", { name: "开放给新告警" }),
      ).toHaveCount(0);
      await expect(trigger).toHaveCount(0);
      await expect(
        page.getByText("已开放给新告警", { exact: true }),
      ).toHaveCount(0);
      await expect(
        page.getByText("未来告警如何使用", { exact: true }),
      ).toHaveCount(0);
      const writes = state.requests.filter((r) => r.method === "POST");
      expect(writes).toHaveLength(1);
      expect(writes[0]).toMatchObject({
        path: reviewPath,
        body: { decision: "deprecate", reason },
      });
      expect(writes[0]?.idempotencyKey).toBeTruthy();
      await page.screenshot({
        path: testInfo.outputPath("deprecated.png"),
        fullPage: true,
        animations: "disabled",
      });
    });
  }
}

test("keeps deprecation reason and current state after a failed request", async ({
  page,
}) => {
  const state = await mockRecord(page);
  let attempts = 0;
  await page.route(`**${reviewPath}`, async (route) => {
    if (attempts++ === 0) {
      await route.fulfill({
        status: 409,
        contentType: "application/problem+json",
        json: {
          title: "Conflict",
          detail: "经验状态已变化，请确认后重试。",
          status: 409,
        },
      });
    } else {
      await route.fallback();
    }
  });
  await page.goto(recordPath);
  await page.getByRole("button", { name: "废止这条经验", exact: true }).click();
  const dialog = page.getByRole("dialog", {
    name: "废止这条经验",
    exact: true,
  });
  await dialog.getByLabel("废止原因").fill(reason);
  await dialog.getByRole("button", { name: "确认废止" }).click();
  await expect(dialog.getByRole("alert")).toContainText(
    "经验状态已变化，请确认后重试。",
  );
  await expect(dialog.getByLabel("废止原因")).toHaveValue(reason);
  expect(state.candidateStatus).toBe("confirmed");
  await dialog.getByRole("button", { name: "确认废止" }).click();
  await expect(dialog).not.toBeVisible();
  expect(state.candidateStatus).toBe("deprecated");
});

test("does not offer another revision for a deprecated Memory deep link", async ({
  page,
}) => {
  const state = await mockRecord(page, "deprecated");
  await page.goto(`${recordPath}/revise`);
  await expect(page.getByText("这条经验已废止", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "暂停旧经验并创建修订候选" }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "废止这条经验", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("未来告警如何使用", { exact: true })).toHaveCount(
    0,
  );
  expect(state.requests.filter((r) => r.method === "POST")).toHaveLength(0);
});
