import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import {
  memoryCandidate,
  memoryRecord,
  mockSocAPI,
} from "./utils/mock-soc-api";

// Frozen scope only, no alert payload or real governance writes.
const fixture = {
  applicability: {
    schema_version: "soc.memory_applicability.v1",
    profile_id: "pingan.soc",
    profile_version: "7",
    feature_schema_version: "pingan.soc.memory_features.v5",
    required_facets: {
      detection_key: ["sec_guard_apt:rule_code:rpaadm_000558"],
      detection_signature: [
        "cfcf75a28055fdc3f5e0306ad86b4da136bd704d77462aa30d0771df71bf671f",
      ],
      behavior_fingerprint: [
        "3cd30c2a0971d87700bb21d569ac3333cf033eefd0bf3f7429666a8c8aa791fd",
      ],
      behavior_strength: ["strong"],
      environment: ["dev-corpus-eval"],
    },
    optional_facets: {
      source_type: ["ndr"],
      source_system: ["sec_guard_apt"],
      product: ["360天眼APT"],
      scenario_key: ["web_attack"],
      behavior_component: [
        "attack_family:denial_of_service",
        "network_service:http/8080",
        "protocol:http",
        "scenario:web_attack",
        "technique:t1190",
      ],
      behavior_component_strong: ["technique:t1190"],
      entity: [
        "asset:未知资产组",
        "mitre:T1190",
        "mitre:TA0001",
        "rule:9d6ba27d4738e67e",
        "rule_code:RPAADM_000558",
        "rule_name:红队IP监控",
      ],
    },
    excluded_facets: {},
    minimum_optional_matches: 0,
    minimum_strong_anchor_matches: 3,
    context_only_required_facet_keys: [
      "behavior_strength",
      "detection_key",
      "detection_signature",
      "environment",
    ],
    context_only_missing_facet_keys: ["behavior_fingerprint"],
    context_only_similarity_facet_keys: ["behavior_component_strong"],
    policy_version: "soc.memory_applicability_policy.v1",
  },
  scope_view: {
    schema_version: "soc.memory_scope_view.v1",
    required_details: {
      detection_signature: {
        source_system: ["sec_guard_apt"],
        product: ["360天眼APT"],
        rule_name: ["红队IP监控"],
        entity: ["rule_name:红队IP监控"],
      },
      detection_key: {
        rule_code: ["RPAADM_000558"],
        source_system: ["sec_guard_apt"],
        entity: ["rule_code:RPAADM_000558", "rule:9d6ba27d4738e67e"],
      },
      behavior_fingerprint: {
        behavior_component: [
          "attack_family:denial_of_service",
          "network_service:http/8080",
          "protocol:http",
          "scenario:web_attack",
          "technique:t1190",
        ],
        scenario_key: ["web_attack"],
        network_service: ["http/8080"],
        attack_behavior_family: ["denial_of_service"],
        entity: ["mitre:t1190"],
      },
    },
    unresolved_fingerprint_keys: [],
    options: [
      {
        key: "source_type",
        values: ["ndr"],
        kind: "additional",
      },
      {
        key: "source_system",
        values: ["sec_guard_apt"],
        kind: "covered",
      },
      {
        key: "product",
        values: ["360天眼APT"],
        kind: "covered",
      },
      {
        key: "scenario_key",
        values: ["web_attack"],
        kind: "covered",
      },
      {
        key: "behavior_component",
        values: [
          "attack_family:denial_of_service",
          "network_service:http/8080",
          "protocol:http",
          "scenario:web_attack",
          "technique:t1190",
        ],
        kind: "covered",
      },
      {
        key: "behavior_component_strong",
        values: ["technique:t1190"],
        kind: "similarity",
      },
      {
        key: "entity",
        values: ["asset:未知资产组", "mitre:TA0001"],
        covered_values: [
          "mitre:T1190",
          "rule_code:RPAADM_000558",
          "rule_name:红队IP监控",
          "rule:9d6ba27d4738e67e",
        ],
        kind: "additional",
      },
    ],
  },
  facets: {
    candidate_source: ["repeated_pattern"],
    pattern_dimension: ["compound"],
    pattern_value: [
      "compound:30700630169d0706c653a9e48e13ed750ab17d1db56bb666f935b98711cdb766",
    ],
    pattern_origin: ["pingan.soc.memory_features.v5"],
    environment: ["dev-corpus-eval"],
    data_class: ["operational"],
    behavior_component: [
      "attack_family:denial_of_service",
      "network_service:http/8080",
      "protocol:http",
      "scenario:web_attack",
      "technique:t1190",
    ],
    behavior_component_strong: ["technique:t1190"],
    behavior_fingerprint: [
      "3cd30c2a0971d87700bb21d569ac3333cf033eefd0bf3f7429666a8c8aa791fd",
    ],
    behavior_strength: ["strong"],
    category: ["拒绝服务"],
    detection_key: ["sec_guard_apt:rule_code:rpaadm_000558"],
    detection_signature: [
      "cfcf75a28055fdc3f5e0306ad86b4da136bd704d77462aa30d0771df71bf671f",
    ],
    entity: [
      "asset:未知资产组",
      "mitre:T1190",
      "mitre:TA0001",
      "rule:9d6ba27d4738e67e",
      "rule_code:RPAADM_000558",
      "rule_name:红队IP监控",
    ],
    product: ["360天眼APT"],
    rule_code: ["RPAADM_000558"],
    rule_name: ["红队IP监控"],
    scenario_key: ["web_attack"],
    source_system: ["sec_guard_apt"],
    source_type: ["ndr"],
    pattern_signature: [
      "compound:30700630169d0706c653a9e48e13ed750ab17d1db56bb666f935b98711cdb766",
    ],
  },
};

for (const width of [1440, 390]) {
  test(`readable candidate scope and meaningful narrowing at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
    });
    await page.route("**/api/soc/memory/candidates/MC-ALPHA-001", (route) =>
      route.fulfill({
        json: { ...memoryCandidate(state), ...fixture },
      }),
    );
    await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");
    const scope = page.getByRole("region", { name: "经验适用范围" }).first();
    await expect(
      scope.getByText("RPAADM_000558", { exact: true }),
    ).toBeVisible();
    await expect(
      scope.getByText("目标服务：http/8080", { exact: true }),
    ).toBeVisible();
    await expect(
      scope.getByText("告警演练数据", { exact: true }),
    ).toBeVisible();
    await expect(scope.locator("pre")).not.toBeVisible();
    await expect(
      scope.getByText("可选匹配条件", { exact: true }),
    ).toBeVisible();
    await expect(scope.getByRole("checkbox")).toHaveCount(3);
    await expect(
      scope.locator("[data-memory-scope-options]"),
    ).not.toContainText("规则内部标识");
    const checkbox = scope.getByRole("checkbox", {
      name: "增加匹配条件 来源类型 网络检测与响应（NDR）",
    });
    await checkbox.check();
    await expect(scope.getByText("已增加", { exact: true })).toHaveCount(1);
    await checkbox.uncheck();
    await expect(scope.getByText("已增加", { exact: true })).toHaveCount(0);
    await expect(
      scope.getByText("网络检测与响应（NDR）", { exact: true }),
    ).toBeVisible();
    const asset = scope.getByRole("checkbox", {
      name: "增加匹配条件 关联实体 资产组：未知资产组",
    });
    await asset.check();
    await scope
      .getByText("技术详情：指纹与完整匹配条件", { exact: true })
      .click();
    await expect(scope.locator("pre")).toContainText(
      fixture.applicability.required_facets.behavior_fingerprint[0]!,
    );
    await expect(scope.locator("pre")).toContainText("asset:未知资产组");
    const audited = JSON.parse(await scope.locator("pre").innerText());
    expect(audited.required_facets.entity).toEqual(["asset:未知资产组"]);
    expect(audited.optional_facets.entity).toBeUndefined();
    expect(audited.context_only_required_facet_keys).toContain("entity");
    await asset.uncheck();
    await asset.check();
    await scope
      .getByText("技术详情：指纹与完整匹配条件", { exact: true })
      .click();
    await scope.screenshot({
      path: test.info().outputPath(`scope-${width}.png`),
    });
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
    const verdict = page.getByRole("combobox", { name: "最终业务判断" });
    await verdict.click();
    await page.getByRole("option", { name: "误报", exact: true }).click();
    await checkbox.check();
    const request = page.waitForRequest(
      (r) => r.url().endsWith("/lesson-draft") && r.method() === "POST",
    );
    await page
      .getByRole("button", { name: "AI 生成研判经验", exact: true })
      .click();
    expect((await request).postDataJSON().promoted_facet_keys).toEqual([
      "entity",
      "source_type",
    ]);
    const payload = (await request).postDataJSON();
    expect(payload.promoted_facet_values).toEqual({
      entity: ["asset:未知资产组"],
      source_type: ["ndr"],
    });
    const reviewRequest = page.waitForRequest(
      (r) => r.url().endsWith("/MC-ALPHA-001/review") && r.method() === "POST",
    );
    await page
      .getByRole("button", { name: "确认并启用经验", exact: true })
      .click();
    const reviewed = (await reviewRequest).postDataJSON().record_applicability;
    expect(reviewed.required_facets.entity).toEqual(["asset:未知资产组"]);
    expect(reviewed.required_facets.source_type).toEqual(["ndr"]);
    expect(reviewed.optional_facets.entity).toBeUndefined();
    expect(reviewed.context_only_required_facet_keys).toContain("entity");
  });
}

test("confirmed record uses the same scope without editable restrictions", async ({
  page,
}) => {
  mockLangGraphAPI(page, { threads: [] });
  const state = await mockSocAPI(page, {
    standaloneMemoryCandidate: true,
    includeQueueItem: false,
  });
  await page.route("**/api/soc/memory/records/MEM-ALPHA-001/lineage", (route) =>
    route.fulfill({
      json: {
        schema_version: "soc.memory_lineage_report.v1",
        record: {
          ...memoryRecord(state),
          applicability: fixture.applicability,
          facets: fixture.facets,
        },
        scope_view: fixture.scope_view,
        uses: [],
        feedback: [],
        health: [],
        revision_proposals: [],
      },
    }),
  );
  await page.goto("/workspace/soc/memory/records/MEM-ALPHA-001");
  const scope = page.getByRole("region", { name: "经验适用范围" });
  await expect(
    scope.getByText("攻击类型：拒绝服务", { exact: true }),
  ).toBeVisible();
  await expect(scope.getByRole("checkbox")).toHaveCount(0);
  await expect(
    scope.getByText("网络检测与响应（NDR）", { exact: true }),
  ).toBeVisible();
  await expect(
    scope.getByText("资产组：未知资产组", { exact: true }),
  ).toBeVisible();
  await expect(scope.getByText("未增加为限制", { exact: true })).toBeVisible();
  await expect(scope.locator("pre")).not.toBeVisible();
  await expect(
    page.getByText(
      "Required canonical facet behavior_fingerprint: behavior-alpha",
      { exact: true },
    ),
  ).not.toBeVisible();
});
