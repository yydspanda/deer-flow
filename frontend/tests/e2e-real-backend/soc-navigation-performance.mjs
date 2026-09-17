// Read-only live navigation acceptance; never run alerts or mutate Memory.
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { chromium } from "@playwright/test";

const label = process.argv[2] ?? "after";
const output = path.resolve(
  process.env.SOC_PERF_OUTPUT ??
    "../backend/.deer-flow/soc-validation/navigation-performance-20260917",
);
const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? "http://localhost:2026";
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({
  baseURL,
  viewport: { width: 1920, height: 1080 },
  locale: "zh-CN",
});
const page = await context.newPage();
page.setDefaultTimeout(60_000);
const errors = [];
const requests = [];
const pending = [];
await context.route("**/api/soc/**", async (route) => {
  assert.equal(
    route.request().method(),
    "GET",
    "Performance acceptance must not mutate SOC state",
  );
  await route.continue();
});
page.on("pageerror", (error) => errors.push(error.message));
page.on("response", (response) => {
  if (!response.url().includes("/api/soc/")) return;
  pending.push(
    (async () => {
      const body = await response.body().catch(() => Buffer.alloc(0));
      const timing = response.request().timing();
      requests.push({
        path: new URL(response.url()).pathname + new URL(response.url()).search,
        status: response.status(),
        bytes: body.length,
        duration_ms: timing.responseEnd,
      });
    })(),
  );
});
async function ready(name) {
  if (name === "corpus")
    await page.locator("tbody tr[data-alert-id]").first().waitFor();
  else {
    await page.getByRole("textbox", { name: "搜索已确认经验" }).waitFor();
    await page
      .getByText("正在读取已确认经验...", { exact: true })
      .waitFor({ state: "hidden" });
    await page
      .locator('a[href^="/workspace/soc/memory/records/"]')
      .first()
      .waitFor();
  }
  await page.evaluate(
    () =>
      new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve)),
      ),
  );
}
const measurements = [];
let accepted = false;
try {
  for (const [name, route] of [
    ["memory", "/workspace/soc/memory"],
    ["corpus", "/workspace/soc/corpus-validation"],
  ]) {
    const start = performance.now();
    await page.goto(route, { waitUntil: "domcontentloaded" });
    await ready(name);
    measurements.push({
      kind: "first_visit",
      page: name,
      ms: Math.round(performance.now() - start),
    });
  }
  for (let index = 0; index < 20; index++) {
    const name = index % 2 === 0 ? "memory" : "corpus";
    const navigation = page.getByRole("navigation", { name: "SOC 运营导航" });
    const start = performance.now();
    await navigation
      .getByRole("link", { name: name === "memory" ? "经验中心" : /告警演练/ })
      .click();
    await ready(name);
    measurements.push({
      kind: "navigation",
      page: name,
      ms: Math.round(performance.now() - start),
    });
  }
  await page.screenshot({
    path: path.join(output, `${label}-corpus-desktop.png`),
  });
  assert.equal(await page.getByText("推荐演练", { exact: true }).count(), 0);
  assert.equal(
    await page
      .getByRole("navigation", { name: "SOC 运营导航" })
      .getByRole("link", { name: "归一化运维" })
      .count(),
    0,
  );
  await page.getByLabel("行为模式组", { exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "查找行为模式组" });
  const search = dialog.getByRole("combobox", { name: "搜索分组" });
  const groupSearchResponse = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.pathname.endsWith("/corpus-workbench/groups") &&
      url.searchParams.get("search") === "2448932"
    );
  });
  await search.fill("2448932");
  const groupPage = await (await groupSearchResponse).json();
  assert.equal(groupPage.total, 1);
  await dialog
    .getByText(groupPage.groups[0].rule_name, { exact: true })
    .waitFor();
  await page.screenshot({
    path: path.join(output, `${label}-groups-desktop.png`),
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: path.join(output, `${label}-groups-mobile.png`),
  });
  const bounds = await dialog.boundingBox();
  assert(bounds.x >= 0 && bounds.x + bounds.width <= 390);
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page
    .getByRole("navigation", { name: "SOC 运营导航" })
    .getByRole("link", { name: "经验中心" })
    .click();
  await ready("memory");
  await page.screenshot({
    path: path.join(output, `${label}-memory-desktop.png`),
  });
} finally {
  await Promise.all(pending);
  const warm = measurements
    .filter((item) => item.kind === "navigation")
    .map((item) => item.ms)
    .sort((a, b) => a - b);
  const report = {
    label,
    created_at: new Date().toISOString(),
    baseURL,
    hardware: {
      platform: os.platform(),
      arch: os.arch(),
      cpu: os.cpus()[0]?.model,
      logical_cpus: os.cpus().length,
      memory_gib: Math.round(os.totalmem() / 1024 ** 3),
    },
    model_calls: 0,
    writes: 0,
    measurements,
    warm_p95_ms: warm[Math.ceil(warm.length * 0.95) - 1],
    requests,
    errors,
  };
  accepted =
    warm.length === 20 &&
    report.warm_p95_ms <= 1500 &&
    measurements.filter((item) => item.kind === "first_visit").length === 2 &&
    measurements
      .filter((item) => item.kind === "first_visit")
      .every((item) => item.ms <= 2000) &&
    requests.every((item) => item.status < 400) &&
    requests
      .filter((item) => item.path.startsWith("/api/soc/dev/corpus-workbench?"))
      .every((item) => item.bytes <= 150000) &&
    errors.length === 0;
  report.accepted = accepted;
  await writeFile(
    path.join(output, `${label}.json`),
    JSON.stringify(report, null, 2),
  );
  console.log(
    JSON.stringify(
      { ...report, requests: `${requests.length} (see report)` },
      null,
      2,
    ),
  );
  await browser.close();
}
assert.equal(errors.length, 0, "Unexpected browser errors");
if (label !== "before")
  assert(accepted, "Navigation performance budget failed; see saved report");
