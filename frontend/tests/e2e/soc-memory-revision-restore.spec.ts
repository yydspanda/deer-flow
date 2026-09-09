import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import {
  memoryCandidate,
  memoryRecord,
  mockSocAPI,
} from "./utils/mock-soc-api";

for (const scenario of [
  { status: "pending_review", width: 1440, fail: false },
  { status: "rejected", width: 390, fail: false },
  { status: "pending_review", width: 1440, fail: true },
]) {
  test(`restore ${scenario.status} revision at ${scenario.width}px (conflict=${scenario.fail})`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: scenario.width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
      candidateStatus: scenario.status,
    });
    let candidate = {
      ...memoryCandidate(state),
      revision_lineage: {
        predecessor_memory_id: "MEM-ALPHA-001",
        predecessor_memory_version: 1,
        suspended_record_version: 2,
        predecessor_content_hash: "a".repeat(64),
        predecessor_facets_hash: "b".repeat(64),
        revision_origin: "operator_direct",
        source_memory_use_id: null,
        source_run_id: "RUN-ALPHA-001",
        source_alert_id: "ALT-ALPHA-001",
        issue_type: "lesson_incomplete",
        reason: "考虑补充业务依据，现决定继续使用原经验。",
        requested_at: "2026-09-09T08:00:00Z",
      },
    };
    let record = {
      ...memoryRecord(state),
      version: scenario.status === "rejected" ? 3 : 2,
      metadata: {
        revision_pending: scenario.status !== "rejected",
        revision_resolution_candidate_id: "MC-ALPHA-001",
        revision_resolution: "rejected",
      },
    };
    const expectedVersion = record.version;
    const mutations: Record<string, unknown>[] = [];
    await page.route("**/api/soc/memory/candidates/MC-ALPHA-001", (route) =>
      route.fulfill({ json: candidate }),
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
    await page.route(
      "**/api/soc/memory/candidates/MC-ALPHA-001/review",
      async (route) => {
        const body = route.request().postDataJSON() as Record<string, unknown>;
        mutations.push(body);
        if (scenario.fail)
          return route.fulfill({
            status: 409,
            json: { detail: "旧经验已发生变化，请刷新后重试。" },
          });
        candidate = { ...candidate, status: "rejected" };
        record = {
          ...record,
          version: record.version + 1,
          retrieval_enabled: true,
          metadata: { ...record.metadata, revision_pending: false },
        };
        return route.fulfill({
          json: {
            candidate,
            memory_record: null,
            restored_predecessor_record: record,
            previous_status: scenario.status,
            decision: "reject",
            reviewed_at: "2026-09-09T08:10:00Z",
          },
        });
      },
    );

    await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");
    const recovery = page.locator("[data-memory-revision-recovery]");
    await expect(
      recovery.getByRole("link", { name: "查看旧经验" }),
    ).toHaveAttribute("href", "/workspace/soc/memory/records/MEM-ALPHA-001", {
      timeout: 45_000,
    });
    await expect(
      page.getByRole("link", { name: "返回审核列表" }),
    ).toHaveAttribute("href", "/workspace/soc/review/memory-candidates");
    const button = recovery.getByRole("button", {
      name:
        scenario.status === "rejected" ? "恢复旧经验" : "取消修订并恢复旧经验",
      exact: true,
    });
    await button.click();
    const dialog = page.getByRole("dialog");
    await expect(
      dialog.locator('[data-memory-use-mode="reference_only"]'),
    ).toBeVisible();
    await expect(
      dialog.getByText("本次开放至", { exact: false }),
    ).toBeVisible();
    expect(mutations).toHaveLength(0);
    await dialog.getByRole("button", { name: "暂不恢复" }).click();
    expect(mutations).toHaveLength(0);
    await button.click();
    await expect(dialog).toHaveCSS("opacity", "1");
    const box = await dialog.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(scenario.width + 1);
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-revision-restore-20260909/${scenario.status}-${scenario.width}-${scenario.fail}.png`,
    });
    await dialog.getByRole("button", { name: "确认恢复使用" }).click();
    if (scenario.fail) {
      await expect(
        page.getByText("旧经验已发生变化，请刷新后重试。"),
      ).toBeVisible();
      await expect(page).toHaveURL(
        /\/review\/memory-candidates\/MC-ALPHA-001$/,
      );
      expect(record.retrieval_enabled).toBe(false);
    } else {
      await expect(page).toHaveURL(/\/memory\/records\/MEM-ALPHA-001$/);
      await expect(
        page.locator('[data-memory-use-mode="reference_only"]'),
      ).toBeVisible();
    }
    expect(mutations).toHaveLength(1);
    expect(mutations[0]).toMatchObject({
      decision: "reject",
      restore_predecessor: true,
      expected_predecessor_version: expectedVersion,
      activation_review_after_days: 30,
    });
    expect(mutations[0]).not.toHaveProperty("decision_directive");
    expect(mutations[0]).not.toHaveProperty("record_lesson");
    expect(state.requests.filter((r) => r.method === "POST")).toHaveLength(0);
  });
}
