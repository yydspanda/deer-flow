// Read-only acceptance of batch membership and navigation against the local corpus.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import path from "node:path";
import { chromium } from "@playwright/test";

const output = path.resolve(
  process.env.SOC_BATCH_OUTPUT ??
    "../backend/.deer-flow/soc-validation/corpus-batch-ui-20260917",
);
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:2026";
await mkdir(output, { recursive: true });
const browser = await chromium.launch();
const errors = [];
const requests = [];
const measurements = [];
const pending = [];
let passed = false;
let writes = 0;
try {
  for (const width of [1920, 390]) {
    const context = await browser.newContext({
      baseURL,
      viewport: { width, height: width === 390 ? 844 : 1080 },
      locale: "zh-CN",
    });
    await context.route("**/api/soc/**", async (route) => {
      if (route.request().method() !== "GET") {
        writes++;
        await route.abort();
        return;
      }
      await route.continue();
    });
    const page = await context.newPage();
    page.setDefaultTimeout(30_000);
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("response", (response) => {
      if (!response.url().includes("/api/soc/")) return;
      pending.push(
        (async () => {
          const body = await response.body();
          requests.push({
            width,
            path:
              new URL(response.url()).pathname + new URL(response.url()).search,
            status: response.status(),
            bytes: body.length,
            duration_ms: response.request().timing().responseEnd,
          });
        })().catch((error) => errors.push(error.message)),
      );
    });
    const rows = page.locator("tbody tr[data-alert-id]");
    async function checkBatch(batch, tier, count, action) {
      const start = performance.now();
      const responsePromise = page.waitForResponse((response) => {
        const url = new URL(response.url());
        return (
          url.pathname.endsWith("/corpus-workbench") &&
          url.searchParams.get("batch") === batch &&
          url.searchParams.get("validation_tier") === tier
        );
      });
      await action();
      const response = await responsePromise;
      assert.equal(response.status(), 200);
      const body = await response.json();
      assert.equal(body.batch_selection.selected_count, count);
      assert.equal(body.alert_page.total, count);
      assert.equal(body.batch_selection.execution_enabled, false);
      assert.equal(body.batch_selection.existing_results_only, true);
      assert(body.alerts.length <= 20);
      assert(
        body.alerts.every(
          (alert) => alert.batch === batch && !alert.can_process,
        ),
      );
      if (tier)
        assert(body.alerts.every((alert) => alert.validation_tier === tier));
      await rows.first().waitFor();
      measurements.push({
        width,
        batch,
        tier,
        selected_count: count,
        group_count: body.batch_selection.group_count,
        plan_id: body.batch_selection.plan_id,
        list_ready_ms: Math.round(performance.now() - start),
      });
      await page.screenshot({
        path: path.join(output, `${batch}-${tier ?? "all"}-${width}.png`),
      });
      assert(
        await page.evaluate(
          () => document.documentElement.scrollWidth <= innerWidth,
        ),
      );
    }
    await checkBatch("learning", null, 3002, () =>
      page.goto("/workspace/soc/corpus-validation", {
        waitUntil: "domcontentloaded",
      }),
    );
    await page.getByLabel("行为模式组", { exact: true }).click();
    const dialog = page.getByRole("dialog", { name: "查找行为模式组" });
    const groupResponse = page.waitForResponse((response) => {
      const url = new URL(response.url());
      return (
        url.pathname.endsWith("/corpus-workbench/groups") &&
        url.searchParams.get("search") === "RPAADM_000558"
      );
    });
    await dialog
      .getByRole("combobox", { name: "搜索分组" })
      .fill("RPAADM_000558");
    const groups = await (await groupResponse).json();
    assert(groups.total > 0);
    assert(
      groups.groups.every(
        (group) => group.alert_count >= 5 && group.alert_count <= 10,
      ),
    );
    await page.screenshot({
      path: path.join(output, `learning-groups-${width}.png`),
    });
    await page.keyboard.press("Escape");
    await checkBatch("validation", "main", 8522, () =>
      page.getByRole("tab", { name: /第二批/ }).click(),
    );
    await page.getByLabel("验证样本范围").click();
    await checkBatch("validation", "supplementary", 3764, () =>
      page.getByRole("option", { name: /探索少样本告警/ }).click(),
    );
    assert(
      (await page
        .getByText(/少样本探索 · (单例|同类样本较少|时间待核验)/)
        .count()) > 0,
    );
    await checkBatch("validation", "supplementary", 3764, () => page.reload());
    assert.equal(
      await page
        .getByRole("tab", { name: /第二批/ })
        .getAttribute("aria-selected"),
      "true",
    );
    await context.close();
  }
  for (const request of pending) await request;
  assert.equal(writes, 0);
  assert.deepEqual(errors, []);
  assert(requests.every((request) => request.status === 200));
  passed = true;
} finally {
  for (const request of pending) await request;
  await browser.close();
  const report = {
    passed,
    created_at: new Date().toISOString(),
    measurements,
    requests,
    errors,
    writes,
    model_calls: 0,
  };
  await writeFile(
    path.join(output, "report.json"),
    JSON.stringify(report, null, 2) + "\n",
  );
  console.log(JSON.stringify(report, null, 2));
}
