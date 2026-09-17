import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { memoryCandidate, mockSocAPI } from "./utils/mock-soc-api";

for (const width of [1440, 390]) {
  test(`source-backed scope picker and refinement at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
    });
    const base = memoryCandidate(state);
    const components = ["network_service:udp/1194", "protocol:udp"];
    const candidate = {
      ...base,
      applicability: {
        ...base.applicability,
        profile_version: "9",
        feature_schema_version: "pingan.soc.memory_features.v7",
        policy_version: "soc.memory_applicability_policy.v4",
        selected_behavior_components: components,
        covered_behavior_components: components,
        reuse_conditions: [],
      },
      scope_view: {
        schema_version: "soc.memory_scope_view.v1",
        unresolved_fingerprint_keys: [],
        required_details: {
          behavior_fingerprint: { behavior_component: components },
        },
        options: [],
      },
    };
    await page.route("**/api/soc/memory/candidates/MC-ALPHA-001", (route) =>
      route.fulfill({ json: candidate }),
    );
    let optionsCalls = 0;
    await page.route(
      "**/api/soc/memory/candidates/MC-ALPHA-001/scope-options**",
      (route) => {
        optionsCalls++;
        const url = new URL(route.request().url());
        const selected = url.searchParams.get("prefix") === "destination";
        const search = url.searchParams.get("search") ?? "";
        const offset = Number(url.searchParams.get("offset") ?? 0);
        const values = Array.from(
          { length: 25 },
          (_, n) => `192.0.2.${n + 1}`,
        ).filter((value) => value.includes(search));
        return route.fulfill({
          json: {
            groups: [{ facet_key: "role_entity", value_prefix: "destination" }],
            items: selected
              ? values.slice(offset, offset + 10).map((value) => ({
                  facet_key: "role_entity",
                  value_prefix: "destination",
                  value: `destination:${value}`,
                  sample_count: 1,
                  from_current_alert: value.endsWith(".1"),
                  source_alert_ids: ["fixture"],
                }))
              : [],
            total: selected ? values.length : 0,
            offset,
            limit: 10,
            source_sample_count: 25,
          },
        });
      },
    );
    await page.route(
      "**/api/soc/memory/candidates/MC-ALPHA-001/governance-preview",
      (route) => {
        const body = route.request().postDataJSON();
        return route.fulfill({
          json: {
            schema_version: "soc.memory_governance_preview.v1",
            candidate_id: base.candidate_id,
            recommendation: "new",
            explanation: "所选范围可独立审核。",
            related_count: 0,
            related_memories: [],
            decision_impact: "none",
            sample_coverage: {
              total: 25,
              applicable: body.promoted_facet_values?.role_entity?.length
                ? 1
                : 25,
            },
          },
        });
      },
    );
    await page.route(
      "**/api/soc/memory/candidates/MC-ALPHA-001/refinements",
      (route) =>
        route.fulfill({
          json: { ...candidate, candidate_id: "MC-SCOPED-FIXTURE" },
        }),
    );
    await page.route(
      "**/api/soc/memory/candidates/MC-SCOPED-FIXTURE",
      (route) =>
        route.fulfill({
          json: { ...candidate, candidate_id: "MC-SCOPED-FIXTURE" },
        }),
    );
    await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");
    await expect(
      page.getByRole("button", { name: "为部分告警建立细分经验" }),
    ).toBeVisible();
    expect(optionsCalls).toBe(0);
    await page.getByRole("button", { name: "为部分告警建立细分经验" }).click();
    const editor = page.getByRole("region", { name: "细分经验范围" });
    await editor.getByRole("button", { name: "添加适用范围限制" }).click();
    await editor
      .getByRole("combobox", { name: "限制维度" })
      .selectOption("role_entity/destination");
    const picker = editor.locator("[data-memory-entity-limits]");
    await expect(picker.getByRole("checkbox")).toHaveCount(10);
    for (const checkbox of await picker.getByRole("checkbox").all()) {
      await checkbox.check();
    }
    await picker.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(picker.getByText("192.0.2.11", { exact: true })).toBeVisible();
    for (const checkbox of await picker.getByRole("checkbox").all()) {
      await checkbox.check();
    }
    await picker.getByRole("button", { name: "下一页", exact: true }).click();
    await expect(picker.getByRole("checkbox")).toHaveCount(5);
    await expect(picker.getByRole("checkbox").first()).toBeDisabled();
    await picker.getByRole("button", { name: "上一页", exact: true }).click();
    await expect(picker.getByRole("checkbox").first()).toBeChecked();
    for (const checkbox of await picker.getByRole("checkbox").all()) {
      await checkbox.uncheck();
    }
    await picker.getByRole("button", { name: "上一页", exact: true }).click();
    await expect(picker.getByText("192.0.2.1", { exact: true })).toBeVisible();
    for (const checkbox of await picker.getByRole("checkbox").all()) {
      await checkbox.uncheck();
    }
    await picker
      .getByRole("textbox", { name: "搜索来源样本实体" })
      .fill("192.0.2.25");
    await expect(picker.getByRole("checkbox")).toHaveCount(1);
    await picker.getByRole("checkbox").check();
    await expect(editor.getByText("覆盖 1 / 25 条来源样本")).toBeVisible();
    await editor.screenshot({
      path: test.info().outputPath(`refinement-${width}.png`),
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const submitted = page.waitForRequest(
      (request) =>
        request.url().endsWith("/refinements") && request.method() === "POST",
    );
    await editor.getByRole("button", { name: "创建细分候选并审核" }).click();
    const request = await submitted;
    expect(request.postDataJSON().promoted_facet_values).toEqual({
      role_entity: ["destination:192.0.2.25"],
    });
    expect(request.headers()["idempotency-key"]).toBeTruthy();
    await expect(page).toHaveURL(/MC-SCOPED-FIXTURE/);
  });
}
