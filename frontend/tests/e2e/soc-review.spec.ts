import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { mockSocAPI } from "./utils/mock-soc-api";

test.describe("SOC review workbench", () => {
  test("renders the investigation and preserves mutation boundaries", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page);

    await page.goto("/workspace/soc/review");

    await expect(page.getByRole("heading", { name: "SOC 复核" })).toBeVisible();
    await expect(
      page.getByText("Reverse shell activity").first(),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "统一调查视图" }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Local fixture returned an explicit mock reputation result.",
      ),
    ).toBeVisible();
    await expect(page.getByText("只读调查附录")).toBeVisible();
    await expect(
      page.getByText(/Read-only investigation completed: 1 hit/),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "Lead Agent" }),
    ).toHaveAttribute(
      "href",
      "/workspace/agents/soc-triage/chats/new?queue_id=REV-ALPHA-001",
    );

    const memorySection = page.locator("section").filter({
      has: page.getByRole("heading", { name: "候选记忆" }),
    });
    await expect(memorySection.getByText("可形成决策经验")).toBeVisible();
    await memorySection.getByRole("button", { name: /高级匹配范围/ }).click();
    await memorySection
      .locator("#memory-scope-MC-ALPHA-001-source_type")
      .check();
    await memorySection.getByRole("combobox", { name: "最终业务判断" }).click();
    await page.getByRole("option", { name: "误报" }).click();
    await memorySection
      .getByLabel("业务事实（可选）")
      .fill("Alpha reviewer confirmed the bounded lesson.");
    await expect(
      memorySection.getByRole("button", { name: "确认候选", exact: true }),
    ).toBeDisabled();
    await memorySection.getByRole("button", { name: "AI 生成 Memory" }).click();
    await expect(
      page.getByText("AI 经验草稿已生成，请审核后确认"),
    ).toBeVisible();
    await expect(
      memorySection.getByText(
        "该模式是已确认的内部服务调用，应按审核范围复用误报结论。",
      ),
    ).toBeVisible();
    await expect(memorySection.getByText(/fixture-lesson-model/)).toBeVisible();
    await expect(
      memorySection.getByRole("textbox", { name: "经验结论" }),
    ).toHaveCount(0);
    await memorySection.getByRole("button", { name: "编辑" }).click();
    await expect(
      memorySection.getByRole("textbox", { name: "经验结论" }),
    ).toHaveValue("该模式是已确认的内部服务调用，应按审核范围复用误报结论。");
    await memorySection.getByRole("button", { name: "完成编辑" }).click();
    await memorySection
      .getByRole("switch", { name: "未来精确匹配改判" })
      .click();
    await memorySection
      .getByRole("button", { name: "确认候选", exact: true })
      .click();
    await expect(page.getByText("候选记忆已更新")).toBeVisible();

    const retrievalSection = page.locator("section").filter({
      has: page.getByRole("heading", { name: "确认记忆检索治理" }),
    });
    await retrievalSection
      .getByLabel("治理理由")
      .fill("Enable bounded retrieval for Alpha regression.");
    await retrievalSection.getByRole("button", { name: "启用检索" }).click();
    await expect(page.getByText("确认记忆检索已启用")).toBeVisible();

    const approvalSection = page.locator("section").filter({
      has: page.getByRole("heading", { name: "审批动作" }),
    });
    await approvalSection
      .getByRole("button", { name: "生成审批 token" })
      .click();
    await expect(approvalSection.getByText("SAT-ALPHA-001")).toBeVisible();
    await approvalSection.getByRole("button", { name: "Dry-run" }).click();
    await expect(
      approvalSection.getByText("Dry-run validated without side effects."),
    ).toBeVisible();

    await page
      .getByLabel("纠正研判")
      .fill("Fixture confirms this is an authorized test.");
    await page.getByRole("button", { name: "提交纠正" }).click();
    await expect(page.getByText("人工纠正已记录")).toBeVisible();

    await page.getByRole("button", { name: "关闭复核项" }).click();
    await expect(page.getByText("复核项已关闭")).toBeVisible();

    const mutationRequests = state.requests.filter((request) =>
      ["POST", "PATCH"].includes(request.method),
    );
    expect(
      mutationRequests.find((request) =>
        request.path.endsWith("/MC-ALPHA-001/lesson-draft"),
      )?.body,
    ).toMatchObject({
      reviewer_verdict: "false_positive",
      reviewer_context: "Alpha reviewer confirmed the bounded lesson.",
      promoted_facet_keys: ["source_type"],
    });
    expect(
      mutationRequests.find((request) =>
        request.path.endsWith("/MC-ALPHA-001/review"),
      )?.body,
    ).toMatchObject({
      decision: "confirm",
      apply_to_future_matches: true,
      confirmed_verdict: "false_positive",
      clear_review_on_match: true,
      record_lesson: {
        schema_version: "soc.memory_business_lesson.v1",
        conclusion: "该模式是已确认的内部服务调用，应按审核范围复用误报结论。",
        business_rationale: ["运营专家已核对当前告警证据和内部服务登记信息。"],
        applicability_conditions: [
          "Required canonical facet detection_key: pingan:ndr:reverse-shell",
          "Required canonical facet behavior_fingerprint: behavior-alpha",
          "Required canonical facet environment: prd",
          "Required canonical facet source_type: nids",
        ],
        generalization_boundaries: ["审核范围未约束的源和目的 IP 可以变化。"],
        invalidation_conditions: [
          "必需 facet 不匹配或当前证据出现反证时失效。",
        ],
        handling_guidance: ["全部适用条件命中时复用误报结论，否则重新研判。"],
      },
      record_applicability: {
        required_facets: {
          source_type: ["nids"],
          detection_key: ["pingan:ndr:reverse-shell"],
          behavior_fingerprint: ["behavior-alpha"],
          environment: ["prd"],
        },
      },
    });
    expect(
      mutationRequests.find((request) =>
        request.path.endsWith("/MEM-ALPHA-001/retrieval"),
      )?.body,
    ).toMatchObject({ action: "enable", expected_record_version: 1 });
    expect(
      mutationRequests.find((request) =>
        request.path.endsWith("/approvals/grants"),
      )?.body,
    ).toMatchObject({ approval_request_id: "APR-ALPHA-001" });
    expect(
      mutationRequests.find((request) => request.path.endsWith("/correct"))
        ?.body,
    ).toMatchObject({ verdict: "false_positive" });
    expect(
      mutationRequests
        .filter(
          (request) => request.path !== "/api/soc/approvals/actions/dry-run",
        )
        .every((request) => request.idempotencyKey),
    ).toBe(true);
  });

  test("opens only manifest-selected sample work and records an explicit outcome", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, { queueStatus: "closed" });

    await page.goto("/workspace/soc/review");
    await page.getByRole("radio", { name: "抽样复核批次" }).click();

    await expect(page.getByText("DSAMPLE-ALPHA-001").first()).toBeVisible();
    await expect(page.getByText("#1 DPROP-ALPHA-001")).toBeVisible();
    await page.getByRole("button", { name: "打开复核" }).click();
    await expect(
      page.getByRole("heading", { name: "结构化处置标签" }),
    ).toBeVisible();

    await page
      .getByLabel("标签理由")
      .fill("Independent sample reviewer confirmed the outcome.");
    await page.getByRole("button", { name: "记录标签" }).click();
    await expect(page.getByText("处置标签已记录")).toBeVisible();

    const request = state.requests.find(
      (item) => item.path === "/api/soc/review/disposition-outcomes",
    );
    expect(request?.body).toMatchObject({
      proposal_id: "DPROP-ALPHA-001",
      review_kind: "sampled_quality_review",
      sample_id: "DSAMPLE-ALPHA-001",
      observed_disposition: "closed_benign_true_positive",
    });
    expect(request?.idempotencyKey).toBeTruthy();
  });

  test("opens a standalone Memory Candidate without fabricating a ReviewQueue item", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      includeQueueItem: false,
      standaloneMemoryCandidate: true,
    });

    await page.goto("/workspace/soc/review?candidate_id=MC-ALPHA-001");

    await expect(page.getByRole("heading", { name: "SOC 复核" })).toBeVisible();
    await expect(
      page.getByRole("radio", { name: "候选经验审核" }),
    ).toBeChecked();
    await expect(
      page.getByRole("heading", { name: "Memory Candidate 治理" }),
    ).toBeVisible();
    await expect(page.getByText("MC-ALPHA-001", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Authorized scanner pattern", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("候选记忆")).toBeVisible();
    const candidateSection = page.locator("section").filter({
      has: page.getByRole("heading", { name: "候选记忆" }),
    });
    const generateDraftButton = candidateSection.getByRole("button", {
      name: "AI 生成 Memory",
    });
    await expect(generateDraftButton).toBeDisabled();
    await candidateSection
      .getByRole("combobox", { name: "最终业务判断" })
      .click();
    await page.getByRole("option", { name: "误报" }).click();
    await expect(generateDraftButton).toBeEnabled();
    await generateDraftButton.click();
    for (const lessonField of [
      "经验结论",
      "业务依据",
      "适用条件",
      "泛化边界",
      "失效条件",
      "处置建议",
    ]) {
      await expect(
        candidateSection.getByText(lessonField, { exact: true }),
      ).toBeVisible();
    }
    await expect(
      candidateSection.getByText(
        "该模式是已确认的内部服务调用，应按审核范围复用误报结论。",
      ),
    ).toBeVisible();
    expect(
      state.requests.find((request) =>
        request.path.endsWith("/MC-ALPHA-001/lesson-draft"),
      )?.body,
    ).toMatchObject({
      reviewer_verdict: "false_positive",
      reviewer_context: null,
      promoted_facet_keys: [],
    });

    expect(
      state.requests.some(
        (request) => request.path === "/api/soc/memory/candidates/MC-ALPHA-001",
      ),
    ).toBe(true);
    expect(
      state.requests.some((request) =>
        request.path.endsWith("/REV-ALPHA-001/context"),
      ),
    ).toBe(false);

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByRole("heading", { name: "Memory Candidate 治理" }),
    ).toBeVisible();
    await expect(
      page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth,
      ),
    ).resolves.toBe(false);
  });

  test("renders normalization drift and writes an explicit maintenance action", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page);

    await page.goto("/workspace/soc/normalization");

    await expect(
      page.getByRole("heading", { name: "归一化运维" }),
    ).toBeVisible();
    await expect(page.getByText("新 Schema").first()).toBeVisible();
    await expect(page.getByText("schema-alpha-001")).toBeVisible();
    await page
      .getByLabel("处理理由")
      .fill("Parser mapping reviewed and regression added.");
    await page.getByRole("button", { name: "标记解决" }).click();
    await expect(page.getByText("维护问题已更新")).toBeVisible();

    const request = state.requests.find(
      (item) => item.path === "/api/soc/normalization/issues/NORM-ALPHA-001",
    );
    expect(request?.method).toBe("PATCH");
    expect(request?.body).toEqual({
      status: "resolved",
      reason: "Parser mapping reviewed and regression added.",
    });
    expect(request?.idempotencyKey).toBeTruthy();
  });

  test("renders the passive operations snapshot without inventing health", async ({
    page,
  }, testInfo) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page);

    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/workspace/soc/operations");

    await expect(
      page.getByRole("heading", { name: "SOC 运营观察" }),
    ).toBeVisible();
    await expect(page.getByTestId("operations-data-nature")).toContainText(
      "SQLite 本地或仿真证据",
    );
    await expect(page.getByText("Analysis runs / 分析运行")).toBeVisible();
    await expect(page.getByText("18", { exact: true })).toBeVisible();
    await expect(page.getByText("kafka.consumer_lag")).toBeVisible();
    await expect(
      page.getByText("缺少测量不等于健康，也不等于故障。"),
    ).toBeVisible();
    await expect(
      page.getByText("Production SLO evidence: not available"),
    ).toBeVisible();

    expect(
      state.requests.filter(
        (request) => request.path === "/api/soc/operations/snapshot",
      ),
    ).toEqual([expect.objectContaining({ method: "GET", body: null })]);

    const horizontalOverflow = async () =>
      page.evaluate(() => {
        const main = document.querySelector(
          '[data-testid="soc-operations-scroll"]',
        );
        return {
          document: document.documentElement.scrollWidth > window.innerWidth,
          main: main ? main.scrollWidth > main.clientWidth : true,
        };
      });

    await expect(horizontalOverflow()).resolves.toEqual({
      document: false,
      main: false,
    });
    await page.screenshot({
      path: testInfo.outputPath("soc-operations-desktop.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await expect(
      page.getByRole("heading", { name: "SOC 运营观察" }),
    ).toBeVisible();
    await expect(horizontalOverflow()).resolves.toEqual({
      document: false,
      main: false,
    });
    await page.screenshot({
      path: testInfo.outputPath("soc-operations-mobile-top.png"),
      fullPage: false,
    });
    await page.getByTestId("soc-operations-scroll").evaluate((element) => {
      element.scrollTop = element.scrollHeight;
    });
    await page.screenshot({
      path: testInfo.outputPath("soc-operations-mobile-bottom.png"),
      fullPage: false,
    });
  });
});
