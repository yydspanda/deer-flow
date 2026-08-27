import { expect, test } from "@playwright/test";

import { mockLangGraphAPI } from "./utils/mock-api";
import { mockSocAPI } from "./utils/mock-soc-api";

test.describe("SOC alert results", () => {
  test("shows every alert result without requiring a ReviewQueue task", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockSocAPI(page);

    await page.goto("/workspace/soc/alerts?run_id=RUN-ALPHA-001");

    await expect(page.getByRole("heading", { name: "告警研判" })).toBeVisible();
    await expect(
      page.getByText("Potential reverse shell requires role verification."),
    ).toBeVisible();
    await expect(
      page.getByText("Missing confirmed asset ownership"),
    ).toBeVisible();
    await expect(
      page.getByText("Verify the destination host business owner"),
    ).toBeVisible();
    await expect(
      page.getByRole("link", { name: "处理人工介入" }),
    ).toHaveAttribute(
      "href",
      "/workspace/soc/review/alerts?queue_id=REV-ALPHA-001",
    );
    await expect(
      page.getByRole("link", { name: "查看待审核经验" }),
    ).toHaveAttribute(
      "href",
      "/workspace/soc/review/memory-candidates/MC-ALPHA-001",
    );
    await expect(page.getByText("技术详情与完整审计")).toBeVisible();
    await expect(
      page.getByText(/soc.alert_investigation_context.v1/),
    ).toBeHidden();
  });

  test("keeps high-risk action approval in a separate inbox", async ({
    page,
  }) => {
    mockLangGraphAPI(page, { threads: [] });
    await mockSocAPI(page);

    await page.goto("/workspace/soc/approvals");

    await expect(page.getByRole("heading", { name: "动作审批" })).toBeVisible();
    await expect(
      page.getByText("response.block_ip / response.block_ip").first(),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "批准一次执行" }),
    ).toBeDisabled();
    await page
      .getByPlaceholder("填写批准、驳回或过期的依据")
      .fill("已核对目标和处置范围");
    await expect(
      page.getByRole("button", { name: "批准一次执行" }),
    ).toBeEnabled();
  });
});
