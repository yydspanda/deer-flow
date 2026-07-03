import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  closeSocReviewItem,
  correctSocReviewRun,
  getSocReviewContext,
  listSocReviewItems,
} from "@/core/soc/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  mockedFetch.mockReset();
});

describe("SOC review API", () => {
  test("lists review queue items with status and limit", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { items: [] }));

    await expect(
      listSocReviewItems({ status: "open", limit: 25 }),
    ).resolves.toEqual([]);
    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/review/items?status=open&limit=25",
    );
  });

  test("omits status when requesting all queue items", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { items: [] }));

    await listSocReviewItems({ status: null, limit: 10 });

    expect(mockedFetch).toHaveBeenCalledWith("/api/soc/review/items?limit=10");
  });

  test("loads context by encoded queue id", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        queue_item: {},
        run: {},
        audit_records: [],
        similar_alerts: [],
      }),
    );

    await getSocReviewContext("REV/1");

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/review/items/REV%2F1/context",
    );
  });

  test("posts close request body", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { queue_id: "REV-1" }));

    await closeSocReviewItem("REV-1", { reason: "done" });

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/review/items/REV-1/close",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "done" }),
      }),
    );
  });

  test("posts correction request body", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { run_id: "RUN-1" }));

    await correctSocReviewRun("RUN-1", {
      verdict: "false_positive",
      reason: "known false positive",
    });

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/review/runs/RUN-1/correct",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          verdict: "false_positive",
          reason: "known false positive",
        }),
      }),
    );
  });

  test("surfaces backend detail", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(503, { detail: "no database" }),
    );

    await expect(listSocReviewItems()).rejects.toThrow("no database");
  });
});
