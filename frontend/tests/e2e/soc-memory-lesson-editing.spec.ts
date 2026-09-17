import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { memoryCandidate, mockSocAPI } from "./utils/mock-soc-api";

const lesson = {
  schema_version: "soc.memory_business_lesson.v2",
  detection_scenario: "监控规则报告与红队监控地址相关的网络活动。",
  observed_event: "当前样本包含相关网络访问，运营判断该模式存在真实攻击风险。",
  conclusion: "在已审核的行为范围内，该类活动应判定为真实攻击。",
  business_rationale: ["运营已选择真实攻击，当前告警中存在对应网络活动。"],
  applicability_conditions: ["必须满足当前候选的全部必需匹配条件。"],
  generalization_boundaries: ["未被适用范围约束的告警时间可以变化。"],
  invalidation_conditions: ["发现新的授权业务依据时，应重新评估该经验。"],
  handling_guidance: ["按攻击事件开展后续处置并记录业务反馈。"],
};

const fields = [
  [
    "检测场景：规则报告了什么",
    "detection_scenario",
    "监控规则报告了对指定服务的异常访问行为。",
  ],
  [
    "实际事件：业务上发生了什么",
    "observed_event",
    "运营核对后确认该组行为不是已登记的授权验证。",
  ],
  ["审核结论", "conclusion", "这组网络活动在当前适用范围内构成真实攻击。"],
  [
    "判断依据（每行一条）",
    "business_rationale",
    "业务登记中没有本次授权记录。\n目标服务出现与攻击行为对应的访问记录。",
  ],
  [
    "泛化边界（每行一条）",
    "generalization_boundaries",
    "相同服务和行为模式下，源地址允许变化。",
  ],
  [
    "失效条件（每行一条）",
    "invalidation_conditions",
    "出现可核实的授权验证记录时停止复用并修订。",
  ],
  [
    "处置建议（每行一条）",
    "handling_guidance",
    "转交运营处置，并跟踪受影响服务。",
  ],
] as const;

function draftResponse(verdict: string) {
  return {
    schema_version: "soc.memory_business_lesson_draft.v1",
    candidate_id: "MC-ALPHA-001",
    reviewer_verdict: verdict,
    lesson,
    supporting_source_refs: ["D-001"],
    rationale_sources: [],
    uncertainties: [],
    provenance: {
      model_name: "fixture-model",
      prompt_version: "fixture-prompt",
      provider_call_count: 1,
      output_repair_call_count: 0,
    },
  };
}

for (const revision of [false, true]) {
  test(`edits every generated lesson field before confirming (revision=${revision})`, async ({
    page,
  }) => {
    test.setTimeout(90_000);
    const width = revision ? 390 : 1440;
    await page.setViewportSize({ width, height: 1000 });
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      standaloneMemoryCandidate: true,
      includeQueueItem: false,
    });
    if (revision) {
      await page.route("**/api/soc/memory/candidates/MC-ALPHA-001", (route) =>
        route.fulfill({
          json: {
            ...memoryCandidate(state),
            revision_lineage: {
              predecessor_memory_id: "MEM-ALPHA-001",
              predecessor_memory_version: 1,
              suspended_record_version: 2,
              predecessor_content_hash: "a".repeat(64),
              predecessor_facets_hash: "b".repeat(64),
              revision_origin: "operator_direct",
              source_run_id: "RUN-ALPHA-001",
              source_alert_id: "ALT-ALPHA-001",
              issue_type: "lesson_incomplete",
              reason: "运营需要补充该经验的事件说明和业务依据。",
              requested_at: "2026-09-09T08:00:00Z",
            },
          },
        }),
      );
    }
    const drafts: Record<string, unknown>[] = [];
    await page.route(
      "**/api/soc/memory/candidates/MC-ALPHA-001/lesson-draft",
      (route) => {
        const body = route.request().postDataJSON();
        drafts.push(body);
        return route.fulfill({ json: draftResponse(body.reviewer_verdict) });
      },
    );
    await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");
    const verdict = page.getByRole("combobox", { name: "最终业务判断" });
    await expect(verdict).toBeVisible({ timeout: 45_000 });
    await verdict.click();
    await page.getByRole("option", { name: "真实攻击", exact: true }).click();
    await page
      .getByRole("button", { name: "AI 生成研判经验", exact: true })
      .click();
    for (const [label, , value] of fields) {
      const input = page.getByRole("textbox", { name: label, exact: true });
      await expect(input).toBeEditable();
      await input.click();
      await input.fill(value);
    }
    expect(drafts).toHaveLength(1);
    expect(drafts[0]).toMatchObject({
      reviewer_verdict: "true_positive",
      reviewer_context: null,
    });
    const businessContext = page.getByRole("textbox", {
      name: "业务事实（可选）",
    });
    await businessContext.fill("运营补充的业务事实，刷新后应保留。");
    const reuse = page.getByRole("switch", {
      name: "允许精确匹配时参与最终结论",
    });
    await reuse.check();
    await page.reload();
    await expect(verdict).toContainText("真实攻击");
    await expect(businessContext).toHaveValue(
      "运营补充的业务事实，刷新后应保留。",
    );
    await expect(reuse).toBeChecked();
    for (const [label, , value] of fields) {
      await expect(
        page.getByRole("textbox", { name: label, exact: true }),
      ).toHaveValue(value);
    }
    expect(drafts).toHaveLength(1);
    await expect(
      page.getByRole("textbox", { name: "业务事实（可选）" }),
    ).toHaveValue("运营补充的业务事实，刷新后应保留。");
    await page.getByRole("button", { name: "预览经验", exact: true }).click();
    await expect(page.getByText(fields[1][2], { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "逐项修改", exact: true }).click();
    for (const [label, , value] of fields) {
      await expect(
        page.getByRole("textbox", { name: label, exact: true }),
      ).toHaveValue(value);
    }
    const editor = page.getByRole("group", { name: "研判经验草稿" });
    await expect(editor.getByRole("textbox")).toHaveCount(7);
    await expect(
      editor.getByText("适用条件（系统生成）", { exact: true }),
    ).toBeVisible();
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - innerWidth,
    );
    expect(overflow).toBeLessThanOrEqual(1);
    await page
      .getByRole("textbox", { name: fields[0][0], exact: true })
      .click();
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-lesson-editing-20260909/editor-top-${width}.png`,
    });
    const lastInput = page.getByRole("textbox", {
      name: fields[6][0],
      exact: true,
    });
    await lastInput.click();
    const inputBounds = await lastInput.boundingBox();
    const actionBounds = await page
      .locator("[data-memory-review-actions]")
      .boundingBox();
    expect(inputBounds!.y + inputBounds!.height).toBeLessThanOrEqual(
      actionBounds!.y + 1,
    );
    await page.screenshot({
      path: `../backend/.deer-flow/soc-validation/memory-lesson-editing-20260909/editor-bottom-${width}.png`,
    });
    await page
      .getByRole("button", { name: "确认并启用经验", exact: true })
      .click();
    await expect
      .poll(
        () =>
          state.requests.filter((request) =>
            request.path.endsWith("/MC-ALPHA-001/review"),
          ).length,
      )
      .toBe(1);
    const submitted = state.requests.find((request) =>
      request.path.endsWith("/MC-ALPHA-001/review"),
    )!.body as Record<string, unknown>;
    expect(submitted).toMatchObject({
      decision: "confirm",
      confirmed_verdict: "true_positive",
      record_lesson: {
        ...Object.fromEntries(
          fields.map(([, key, value]) => [
            key,
            ["detection_scenario", "observed_event", "conclusion"].includes(key)
              ? value
              : value.split("\n"),
          ]),
        ),
        schema_version: "soc.memory_business_lesson.v2",
      },
    });
    await expect
      .poll(() =>
        page.evaluate(() =>
          Object.keys(sessionStorage).filter((key) =>
            key.startsWith("soc-memory-review-draft:"),
          ),
        ),
      )
      .toEqual([]);
  });
}

test("regeneration requires confirmation, preserves edits on failure and locks in-flight fields", async ({
  page,
}) => {
  test.setTimeout(90_000);
  mockLangGraphAPI(page, { threads: [] });
  const state = await mockSocAPI(page, {
    standaloneMemoryCandidate: true,
    includeQueueItem: false,
  });
  let draftCount = 0;
  let release: (() => void) | undefined;
  await page.route(
    "**/api/soc/memory/candidates/MC-ALPHA-001/lesson-draft",
    async (route) => {
      draftCount++;
      if (draftCount === 2) {
        await new Promise<void>((resolve) => {
          release = resolve;
        });
        return route.fulfill({
          status: 503,
          json: { detail: "模拟模型暂时不可用" },
        });
      }
      return route.fulfill({
        json: draftResponse(route.request().postDataJSON().reviewer_verdict),
      });
    },
  );
  await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");
  await page.getByRole("combobox", { name: "最终业务判断" }).click();
  await page.getByRole("option", { name: "真实攻击", exact: true }).click();
  await page
    .getByRole("button", { name: "AI 生成研判经验", exact: true })
    .click();
  const event = page.getByRole("textbox", { name: fields[1][0], exact: true });
  await event.fill(fields[1][2]);
  const regenerate = page.getByRole("button", {
    name: "重新生成研判经验",
    exact: true,
  });
  await regenerate.click();
  const dialog = page.getByRole("dialog", { name: "替换当前经验草稿？" });
  await expect(dialog).toBeVisible();
  expect(draftCount).toBe(1);
  await dialog.getByRole("button", { name: "保留当前草稿" }).click();
  await expect(event).toHaveValue(fields[1][2]);
  await regenerate.click();
  await dialog.getByRole("button", { name: "替换并重新生成" }).click();
  await expect.poll(() => draftCount).toBe(2);
  await expect(event).toBeDisabled();
  await expect(
    page.getByRole("combobox", { name: "最终业务判断" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "确认并启用经验", exact: true }),
  ).toBeDisabled();
  release!();
  await expect(event).toBeEditable();
  await expect(event).toHaveValue(fields[1][2]);
  await regenerate.click();
  await dialog.getByRole("button", { name: "替换并重新生成" }).click();
  await expect(event).toHaveValue(lesson.observed_event);
  await expect(event).toBeEditable();
  expect(draftCount).toBe(3);
  expect(
    state.requests.filter(
      (request) =>
        request.path.endsWith("/review") && request.method === "POST",
    ),
  ).toHaveLength(0);
});
