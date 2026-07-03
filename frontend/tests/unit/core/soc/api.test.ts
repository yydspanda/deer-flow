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
  createSocApprovalGrant,
  dryRunSocApprovedAction,
  executeSocApprovedAction,
  getSocApprovalRequest,
  getSocReviewContext,
  listSocApprovalRequests,
  listSocReviewItems,
} from "@/core/soc/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function firstFetchInit(): RequestInit {
  const init = mockedFetch.mock.calls[0]?.[1];
  if (!init) {
    throw new Error("expected fetch init");
  }
  return init;
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

  test("adds web actor headers when context is provided", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { items: [] }));

    await listSocReviewItems({
      status: "open",
      limit: 25,
      context: {
        actorId: "user-1",
        surface: "web",
        traceId: "trace-1",
      },
    });

    const init = firstFetchInit();
    const headers = init?.headers as Headers;
    expect(headers.get("x-soc-actor-id")).toBe("user-1");
    expect(headers.get("x-soc-surface")).toBe("web");
    expect(headers.get("x-trace-id")).toBe("trace-1");
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

  test("posts state-changing review request with idempotency key", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { queue_id: "REV-1" }));

    await closeSocReviewItem(
      "REV-1",
      { reason: "done" },
      {
        actorId: "user-1",
        surface: "web",
        traceId: "trace-1",
        idempotencyKey: "idem-1",
      },
    );

    const init = firstFetchInit();
    const headers = init?.headers as Headers;
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("x-soc-actor-id")).toBe("user-1");
    expect(headers.get("x-soc-surface")).toBe("web");
    expect(headers.get("x-trace-id")).toBe("trace-1");
    expect(headers.get("idempotency-key")).toBe("idem-1");
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

describe("SOC approval API", () => {
  const approvalRequest = {
    permission_decision_id: "PERM-1",
    route: "response.block_ip",
    action: "response.block_ip",
    risk_level: "high_risk" as const,
    reason: "requires approval",
    requested_by: {
      actor_id: "soc-agent",
      surface: "web",
      roles: ["analyst"],
    },
  };

  test("lists approval requests from inbox", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { items: [approvalRequest] }),
    );

    await expect(
      listSocApprovalRequests({
        status: "pending",
        limit: 25,
        context: {
          actorId: "approver-1",
          surface: "web",
          traceId: "trace-inbox-1",
        },
      }),
    ).resolves.toEqual([approvalRequest]);

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/approvals/requests?status=pending&limit=25",
      expect.any(Object),
    );
    const init = firstFetchInit();
    const headers = init.headers as Headers;
    expect(headers.get("x-soc-actor-id")).toBe("approver-1");
    expect(headers.get("x-soc-surface")).toBe("web");
    expect(headers.get("x-trace-id")).toBe("trace-inbox-1");
  });

  test("loads approval request detail by encoded id", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, approvalRequest));

    await expect(getSocApprovalRequest("APR/1")).resolves.toEqual(
      approvalRequest,
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/approvals/requests/APR%2F1",
    );
  });

  test("creates approval grant through gateway API", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        approval_grant_id: "APG-1",
        execution_token_id: "SAT-1",
      }),
    );

    await createSocApprovalGrant(
      {
        approval_request: approvalRequest,
        reason: "approved",
        expires_in_seconds: 900,
      },
      {
        actorId: "approver-1",
        surface: "web",
        traceId: "trace-approve-1",
        idempotencyKey: "idem-approve-1",
      },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/approvals/grants",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          approval_request: approvalRequest,
          reason: "approved",
          expires_in_seconds: 900,
        }),
      }),
    );
    const init = firstFetchInit();
    const headers = init.headers as Headers;
    expect(headers.get("content-type")).toBe("application/json");
    expect(headers.get("x-soc-actor-id")).toBe("approver-1");
    expect(headers.get("x-soc-surface")).toBe("web");
    expect(headers.get("x-trace-id")).toBe("trace-approve-1");
    expect(headers.get("idempotency-key")).toBe("idem-approve-1");
  });

  test("dry-runs approved action without idempotency header", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        route: "response.block_ip",
        action: "response.block_ip",
        status: "success",
        message: "dry-run",
        payload: {},
      }),
    );

    await dryRunSocApprovedAction(
      {
        execution_token_id: "SAT-1",
        route: "response.block_ip",
        action: "response.block_ip",
        dry_run: false,
      },
      {
        actorId: "analyst-1",
        surface: "web",
        traceId: "trace-dry-run-1",
        idempotencyKey: "idem-ignored",
      },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/approvals/actions/dry-run",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          execution_token_id: "SAT-1",
          route: "response.block_ip",
          action: "response.block_ip",
          dry_run: true,
        }),
      }),
    );
    const init = firstFetchInit();
    const headers = init.headers as Headers;
    expect(headers.get("x-trace-id")).toBe("trace-dry-run-1");
    expect(headers.get("idempotency-key")).toBeNull();
  });

  test("executes approved action with idempotency header", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, {
        route: "response.block_ip",
        action: "response.block_ip",
        status: "success",
        message: "consumed",
        payload: {},
      }),
    );

    await executeSocApprovedAction(
      {
        execution_token_id: "SAT-1",
        route: "response.block_ip",
        action: "response.block_ip",
        dry_run: true,
        payload: { ip: "203.0.113.8" },
      },
      {
        actorId: "analyst-1",
        surface: "web",
        traceId: "trace-execute-1",
        idempotencyKey: "idem-execute-1",
      },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/approvals/actions/execute",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          execution_token_id: "SAT-1",
          route: "response.block_ip",
          action: "response.block_ip",
          dry_run: false,
          payload: { ip: "203.0.113.8" },
        }),
      }),
    );
    const init = firstFetchInit();
    const headers = init.headers as Headers;
    expect(headers.get("x-soc-actor-id")).toBe("analyst-1");
    expect(headers.get("x-soc-surface")).toBe("web");
    expect(headers.get("x-trace-id")).toBe("trace-execute-1");
    expect(headers.get("idempotency-key")).toBe("idem-execute-1");
  });
});
