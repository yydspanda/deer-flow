import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { mockSocAPI } from "./utils/mock-soc-api";

test.describe("SOC review workbench", () => {
  test("resolves a critical fact conflict without mixing other workflows", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page);

    await page.goto("/workspace/soc/review");

    await expect(
      page.getByRole("heading", { name: "需人工介入" }),
    ).toBeVisible();
    await expect(page.getByText("关键事实冲突").first()).toBeVisible();
    await expect(
      page.getByText("Reverse shell activity").first(),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "返回完整研判" }),
    ).toHaveAttribute("href", "/workspace/soc/alerts?run_id=RUN-ALPHA-001");
    await expect(
      page.getByRole("link", { name: "交给 Lead Agent 调查" }),
    ).toHaveAttribute(
      "href",
      "/workspace/agents/soc-triage/chats/new?queue_id=REV-ALPHA-001",
    );
    await expect(
      page.getByRole("button", { name: "提交并完成介入" }),
    ).toBeDisabled();
    await expect(page.getByText("候选记忆")).toHaveCount(0);
    await expect(page.getByText("当前告警的动作审批")).toHaveCount(0);

    await page
      .getByLabel("最终判断依据")
      .fill("Fixture resolves the conflicting current fact.");
    await page.getByRole("button", { name: "提交并完成介入" }).click();
    await expect(
      page.getByText("最终判断已记录，人工介入任务已完成"),
    ).toBeVisible();

    const mutationRequests = state.requests.filter((request) =>
      ["POST", "PATCH"].includes(request.method),
    );
    expect(
      mutationRequests.find((request) => request.path.endsWith("/correct"))
        ?.body,
    ).toMatchObject({ verdict: "suspicious" });
    expect(
      mutationRequests.some(
        (request) =>
          request.path.includes("/memory/") ||
          request.path.includes("/approvals/"),
      ),
    ).toBe(false);
    expect(mutationRequests.every((request) => request.idempotencyKey)).toBe(
      true,
    );
  });

  test("opens only manifest-selected sample work and records an explicit outcome", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, { queueStatus: "closed" });

    await page.goto("/workspace/soc/review/samples");

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

  test("lists every Memory Candidate status before opening governance detail", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      includeQueueItem: false,
      standaloneMemoryCandidate: true,
      candidateStatus: "confirmed",
    });

    await page.goto("/workspace/soc/review/memory-candidates");

    await expect(
      page.getByRole("heading", { name: "Candidate 治理台账" }),
    ).toBeVisible();
    await expect(page.getByLabel("候选状态")).toContainText("全部状态");
    await expect(page.getByText("Authorized scanner pattern")).toBeVisible();
    await expect(
      page.getByText(
        "Confirm the change window before suppressing this pattern.",
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "查看治理记录" }),
    ).toHaveAttribute(
      "href",
      "/workspace/soc/review/memory-candidates/MC-ALPHA-001",
    );
    await expect(
      page.getByRole("link", { name: "查看治理记录" }),
    ).toHaveAttribute("data-variant", "secondary");
    expect(
      state.requests.filter((request) =>
        [
          "/api/soc/review/items",
          "/api/soc/memory/records",
          "/api/soc/approvals/requests",
        ].includes(request.path),
      ),
    ).toEqual([]);
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

    await expect(page.getByRole("heading", { name: "经验审核" })).toBeVisible();
    await expect(
      page.getByRole("link", { name: "返回经验中心" }),
    ).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "经验候选审核" }),
    ).toBeVisible();
    await expect(page.getByText("MC-ALPHA-001", { exact: true })).toBeVisible();
    await expect(
      page.getByText("Authorized scanner pattern", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText("候选记忆")).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "本次审核对象 / Candidate Proposal",
      }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Confirm the change window before suppressing this pattern.",
      ),
    ).toBeVisible();
    const candidateSection = page.locator("section").filter({
      has: page.getByRole("heading", { name: "候选记忆" }),
    });
    const generateDraftButton = candidateSection.getByRole("button", {
      name: "AI 生成 Business Lesson",
    });
    await expect(generateDraftButton).toBeDisabled();
    await candidateSection
      .getByRole("combobox", { name: "最终业务判断" })
      .click();
    await page.getByRole("option", { name: "误报" }).click();
    await expect(generateDraftButton).toBeEnabled();
    await generateDraftButton.click();
    await expect(
      page.getByRole("link", { name: "返回候选台账" }),
    ).toHaveAttribute("href", "/workspace/soc/review/memory-candidates");
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
    await expect(
      candidateSection.getByText("3. 选择未来用途", { exact: true }),
    ).toBeVisible();
    await expect(
      candidateSection.getByText("仅供研判参考，不改判", { exact: true }),
    ).toBeVisible();
    await candidateSection
      .getByRole("switch", { name: "允许精确匹配时参与最终结论" })
      .click();
    await expect(
      candidateSection.getByText("精确匹配时复用审核结论", { exact: true }),
    ).toBeVisible();
    await expect(
      candidateSection.getByRole("button", { name: "确认并沉淀 Memory" }),
    ).toHaveAttribute("data-variant", "default");
    await expect(
      candidateSection.getByRole("button", { name: "放弃沉淀此候选" }),
    ).toHaveAttribute("data-variant", "destructive");
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
      page.getByRole("heading", { name: "经验候选审核" }),
    ).toBeVisible();
    await expect(
      page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          document.documentElement.clientWidth,
      ),
    ).resolves.toBe(false);
  });

  test("shows the reviewed Business Lesson instead of an empty form for a confirmed Candidate", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      includeQueueItem: false,
      standaloneMemoryCandidate: true,
      candidateStatus: "confirmed",
    });

    await page.goto("/workspace/soc/review/memory-candidates/MC-ALPHA-001");

    await expect(
      page.getByRole("heading", {
        name: "本次审核对象 / Candidate Proposal",
      }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "Confirm the change window before suppressing this pattern.",
      ),
    ).toBeVisible();
    await expect(page.getByText("当前治理状态")).toBeVisible();
    await expect(
      page.getByRole("heading", {
        name: "已沉淀 Memory / 检索治理",
      }),
    ).toBeVisible();
    await expect(
      page.getByText(
        "该模式是已确认的内部服务调用，应按审核范围复用误报结论。",
      ),
    ).toBeVisible();
    await expect(
      page.getByRole("combobox", { name: "最终业务判断" }),
    ).toHaveCount(0);
    expect(
      state.requests.filter((request) =>
        ["/api/soc/review/items", "/api/soc/approvals/requests"].includes(
          request.path,
        ),
      ),
    ).toEqual([]);
    expect(
      state.requests.filter(
        (request) => request.path === "/api/soc/memory/records",
      ),
    ).toHaveLength(1);
  });

  test("reopens a rejected standalone Memory Candidate before editing", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    const state = await mockSocAPI(page, {
      includeQueueItem: false,
      standaloneMemoryCandidate: true,
      candidateStatus: "rejected",
    });

    await page.goto("/workspace/soc/review?candidate_id=MC-ALPHA-001");

    const candidateSection = page.locator("section").filter({
      has: page.getByRole("heading", { name: "候选记忆" }),
    });
    await expect(
      candidateSection.getByText("已放弃沉淀").first(),
    ).toBeVisible();
    await expect(
      candidateSection.getByRole("combobox", { name: "最终业务判断" }),
    ).toHaveCount(0);
    await expect(candidateSection.getByLabel("业务事实（可选）")).toHaveCount(
      0,
    );

    await candidateSection
      .getByRole("button", { name: "重新打开审核" })
      .click();

    await expect(page.getByText("候选已重新打开，可以继续审核")).toBeVisible();
    await expect(candidateSection.getByText("待审核")).toBeVisible();
    await expect(
      candidateSection.getByRole("combobox", { name: "最终业务判断" }),
    ).toBeEnabled();
    await expect(candidateSection.getByLabel("业务事实（可选）")).toBeEnabled();
    expect(
      state.requests.find((request) =>
        request.path.endsWith("/MC-ALPHA-001/review"),
      )?.body,
    ).toMatchObject({
      decision: "reopen",
      reason: "审核人重新打开此前被放弃的候选，返回待审核状态。",
    });
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
      page.getByRole("heading", { name: "SOC 运营总览" }),
    ).toBeVisible();
    await expect(page.getByTestId("operations-data-nature")).toContainText(
      "SQLite 本地或仿真证据",
    );
    await expect(page.getByText("Analysis runs / 分析运行")).toBeVisible();
    await expect(page.getByText("18", { exact: true })).toBeVisible();
    await expect(
      page.getByRole("heading", { name: "Effectiveness / 研判效能" }),
    ).toBeVisible();
    await expect(page.getByText("RC-ALPHA-001")).toBeVisible();
    await expect(page.getByText("评估受治理快速路径")).toBeVisible();
    await page.getByRole("button", { name: "展开规则详情" }).click();
    await expect(page.getByText("OpenVPN / UDP 1194")).toBeVisible();
    await expect(page.getByText("CVE-2017-7924 / UDP 44818")).toBeVisible();
    await expect(
      page.getByText("内部 OpenVPN 服务访问的稳定误报经验"),
    ).toBeVisible();
    await expect(page.getByText("直接复用 6")).toBeVisible();
    await expect(page.getByText("错误覆盖 0")).toBeVisible();
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
    expect(
      state.requests.filter(
        (request) => request.path === "/api/soc/effectiveness/snapshot",
      ),
    ).toEqual([expect.objectContaining({ method: "GET", body: null })]);
    expect(
      state.requests.filter(
        (request) =>
          request.path === "/api/soc/effectiveness/rules/0123456789abcdef",
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
      page.getByRole("heading", { name: "SOC 运营总览" }),
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
