import { beforeEach, describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({
  fetch: rs.fn(),
}));

rs.mock("@/core/config", () => ({
  getBackendBaseURL: () => "",
}));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  SocApiError,
  closeSocReviewItem,
  correctSocReviewRun,
  createSocApprovalGrant,
  dryRunSocApprovedAction,
  expireSocApprovalRequest,
  executeSocApprovedAction,
  getSocApprovalRequest,
  getSocDispositionSampleReviewInbox,
  getSocNormalizationMetrics,
  getSocReviewContext,
  listSocApprovalRequests,
  listSocDispositionSampleCampaigns,
  listSocMemoryCandidates,
  listSocMemoryRecords,
  listSocNormalizationBaselines,
  listSocNormalizationIssues,
  listSocReviewItems,
  recordSocDispositionOutcome,
  rejectSocApprovalRequest,
  reviewSocMemoryCandidate,
  searchSocMemoryRecords,
  updateSocMemoryRetrievalActivation,
  updateSocNormalizationIssue,
} from "@/core/soc/api";

const mockedFetch = rs.mocked(fetcher);

function jsonResponse(
  status: number,
  body: unknown,
  headers: Record<string, string> = {},
): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
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
    expect(headers.get("x-request-id")).toMatch(/^soc-req-/);
    expect(headers.get("x-trace-id")).toBe("trace-1");
  });

  test("uses caller request id when provided", async () => {
    mockedFetch.mockResolvedValueOnce(jsonResponse(200, { items: [] }));

    await listSocReviewItems({
      context: { requestId: "req-web-1", traceId: "trace-web-1" },
    });

    const headers = firstFetchInit().headers as Headers;
    expect(headers.get("x-request-id")).toBe("req-web-1");
  });

  test("surfaces structured SOC problem details", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(
        409,
        {
          schema_version: "soc.api.problem.v1",
          code: "soc.conflict",
          detail: "The command conflicts with current state",
          request_id: "req-conflict-1",
          trace_id: "trace-conflict-1",
          retryable: false,
        },
        { "X-SOC-API-Version": "1" },
      ),
    );

    const error = await listSocReviewItems().catch((cause: unknown) => cause);

    expect(error).toBeInstanceOf(SocApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "soc.conflict",
      requestId: "req-conflict-1",
      traceId: "trace-conflict-1",
      retryable: false,
      message: "The command conflicts with current state",
    });
  });

  test("rejects an unsupported declared SOC API version", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { items: [] }, { "X-SOC-API-Version": "2" }),
    );

    await expect(listSocReviewItems()).rejects.toMatchObject({
      name: "SocApiError",
      code: "soc.unsupported_api_version",
    });
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

  test("posts governed memory retrieval activation with optimistic version", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { record: { memory_id: "MEM-1", version: 2 } }),
    );

    await updateSocMemoryRetrievalActivation(
      "MEM/1",
      {
        action: "enable",
        expected_record_version: 1,
        reason: "Memory reviewer approved bounded retrieval.",
        activation_valid_until: "2026-10-01T00:00:00Z",
        review_after_days: 30,
      },
      { idempotencyKey: "memory-enable-1", surface: "web" },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/memory/records/MEM%2F1/retrieval",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "enable",
          expected_record_version: 1,
          reason: "Memory reviewer approved bounded retrieval.",
          activation_valid_until: "2026-10-01T00:00:00Z",
          review_after_days: 30,
        }),
      }),
    );
    const headers = firstFetchInit().headers as Headers;
    expect(headers.get("idempotency-key")).toBe("memory-enable-1");
  });

  test("posts explicit structured disposition outcome", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { outcome: { outcome_id: "DOUT-1" } }),
    );

    await recordSocDispositionOutcome(
      {
        proposal_id: "DPROP-1",
        observed_disposition: "closed_benign_true_positive",
        review_kind: "analyst_resolution",
        reason: "Analyst confirmed the authorized activity.",
        evidence_refs: ["review_queue:REV-1"],
      },
      {
        actorId: "analyst-1",
        surface: "web",
        idempotencyKey: "outcome:web:1",
      },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/review/disposition-outcomes",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          proposal_id: "DPROP-1",
          observed_disposition: "closed_benign_true_positive",
          review_kind: "analyst_resolution",
          reason: "Analyst confirmed the authorized activity.",
          evidence_refs: ["review_queue:REV-1"],
        }),
      }),
    );
    const init = firstFetchInit();
    const headers = init.headers as Headers;
    expect(headers.get("idempotency-key")).toBe("outcome:web:1");
  });

  test("loads disposition sample campaigns and authenticated reviewer inbox", async () => {
    mockedFetch
      .mockResolvedValueOnce(jsonResponse(200, { items: [], has_more: false }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          manifest: { sample_id: "DSAMPLE-1" },
          reviewer_actor_id: "qa-reviewer-1",
          items: [],
        }),
      );

    await listSocDispositionSampleCampaigns({
      limit: 25,
      context: { actorId: "qa-reviewer-1", surface: "web" },
    });
    await getSocDispositionSampleReviewInbox("DSAMPLE/1", {
      offset: 20,
      limit: 10,
      context: { actorId: "qa-reviewer-1", surface: "web" },
    });

    expect(mockedFetch).toHaveBeenNthCalledWith(
      1,
      "/api/soc/review/disposition-samples?limit=25",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/api/soc/review/disposition-samples/DSAMPLE%2F1/inbox?offset=20&limit=10",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    const secondHeaders = mockedFetch.mock.calls[1]?.[1]?.headers as Headers;
    expect(secondHeaders.get("x-soc-actor-id")).toBe("qa-reviewer-1");
    expect(secondHeaders.get("idempotency-key")).toBeNull();
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
    approval_request_id: "APR-1",
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
    status: "pending" as const,
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
        approval_request_id: "APR-1",
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
          approval_request_id: "APR-1",
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

  test("rejects and expires approval requests by immutable request id", async () => {
    mockedFetch
      .mockResolvedValueOnce(
        jsonResponse(200, { ...approvalRequest, status: "rejected" }),
      )
      .mockResolvedValueOnce(
        jsonResponse(200, { ...approvalRequest, status: "expired" }),
      );

    await rejectSocApprovalRequest(
      "APR/1",
      { reason: "scope rejected" },
      {
        actorId: "approver-1",
        surface: "web",
        idempotencyKey: "reject-1",
      },
    );
    await expireSocApprovalRequest(
      "APR/2",
      { reason: "request expired" },
      {
        actorId: "approver-1",
        surface: "web",
        idempotencyKey: "expire-1",
      },
    );

    expect(mockedFetch).toHaveBeenNthCalledWith(
      1,
      "/api/soc/approvals/requests/APR%2F1/reject",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "scope rejected" }),
      }),
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/api/soc/approvals/requests/APR%2F2/expire",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ reason: "request expired" }),
      }),
    );
    const firstHeaders = mockedFetch.mock.calls[0]?.[1]?.headers as Headers;
    const secondHeaders = mockedFetch.mock.calls[1]?.[1]?.headers as Headers;
    expect(firstHeaders.get("idempotency-key")).toBe("reject-1");
    expect(secondHeaders.get("idempotency-key")).toBe("expire-1");
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

describe("SOC memory API", () => {
  test("lists scoped candidates and records without losing filters", async () => {
    mockedFetch
      .mockResolvedValueOnce(jsonResponse(200, { items: [] }))
      .mockResolvedValueOnce(jsonResponse(200, { items: [] }));

    await listSocMemoryCandidates({
      status: "pending_review",
      tenantScope: "tenant",
      tenantId: "tenant-1",
      runId: "RUN-1",
      alertId: "ALT-1",
      queueId: "REV-1",
      limit: 25,
    });
    await listSocMemoryRecords({
      status: "confirmed",
      tenantScope: "tenant",
      tenantId: "tenant-1",
      sourceCandidateId: "MC-1",
      retrievalEnabled: true,
      limit: 10,
    });

    expect(mockedFetch).toHaveBeenNthCalledWith(
      1,
      "/api/soc/memory/candidates?status=pending_review&tenant_scope=tenant&tenant_id=tenant-1&run_id=RUN-1&alert_id=ALT-1&queue_id=REV-1&limit=25",
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/api/soc/memory/records?status=confirmed&tenant_scope=tenant&tenant_id=tenant-1&source_candidate_id=MC-1&retrieval_enabled=true&limit=10",
    );
  });

  test("reviews a candidate through a state-changing idempotent request", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { candidate: { candidate_id: "MC-1" } }),
    );

    await reviewSocMemoryCandidate(
      "MC/1",
      {
        decision: "confirm",
        reason: "Reviewer confirmed the bounded lesson.",
      },
      {
        actorId: "memory-reviewer-1",
        surface: "web",
        idempotencyKey: "memory-review-1",
      },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/memory/candidates/MC%2F1/review",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          decision: "confirm",
          reason: "Reviewer confirmed the bounded lesson.",
        }),
      }),
    );
    const headers = firstFetchInit().headers as Headers;
    expect(headers.get("x-soc-actor-id")).toBe("memory-reviewer-1");
    expect(headers.get("idempotency-key")).toBe("memory-review-1");
  });

  test("searches confirmed memory as a read-only bounded request", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { matches: [], returned_count: 0 }),
    );

    await searchSocMemoryRecords(
      {
        tenant_scope: "tenant",
        tenant_id: "tenant-1",
        text_terms: ["authorized scanner"],
        require_retrieval_enabled: true,
        max_tokens: 800,
      },
      { actorId: "analyst-1", surface: "web" },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/memory/search",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          tenant_scope: "tenant",
          tenant_id: "tenant-1",
          text_terms: ["authorized scanner"],
          require_retrieval_enabled: true,
          max_tokens: 800,
        }),
      }),
    );
    const headers = firstFetchInit().headers as Headers;
    expect(headers.get("idempotency-key")).toBeNull();
  });
});

describe("SOC normalization API", () => {
  test("loads issues, active baselines, and operations metrics", async () => {
    mockedFetch
      .mockResolvedValueOnce(jsonResponse(200, { items: [] }))
      .mockResolvedValueOnce(jsonResponse(200, { items: [] }))
      .mockResolvedValueOnce(
        jsonResponse(200, {
          schema_version: "soc.normalization_operations_metrics.v1",
          open_issue_count: 0,
        }),
      );

    await listSocNormalizationIssues({ status: null, limit: 40 });
    await listSocNormalizationBaselines();
    await getSocNormalizationMetrics();

    expect(mockedFetch).toHaveBeenNthCalledWith(
      1,
      "/api/soc/normalization/issues?limit=40",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      2,
      "/api/soc/normalization/baselines?status=active&limit=200",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
    expect(mockedFetch).toHaveBeenNthCalledWith(
      3,
      "/api/soc/normalization/metrics",
      expect.objectContaining({ headers: expect.any(Headers) }),
    );
  });

  test("updates one encoded normalization issue with an idempotency key", async () => {
    mockedFetch.mockResolvedValueOnce(
      jsonResponse(200, { issue_id: "NORM-1", status: "resolved" }),
    );

    await updateSocNormalizationIssue(
      "NORM/1",
      { status: "resolved", reason: "Parser mapping and regression reviewed." },
      {
        actorId: "normalizer-1",
        surface: "web",
        idempotencyKey: "normalization-resolve-1",
      },
    );

    expect(mockedFetch).toHaveBeenCalledWith(
      "/api/soc/normalization/issues/NORM%2F1",
      expect.objectContaining({
        method: "PATCH",
        body: JSON.stringify({
          status: "resolved",
          reason: "Parser mapping and regression reviewed.",
        }),
      }),
    );
    const headers = firstFetchInit().headers as Headers;
    expect(headers.get("idempotency-key")).toBe("normalization-resolve-1");
  });
});
