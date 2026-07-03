import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  SocAgentActionResult,
  SocAgentApprovalGrant,
  SocAgentApprovalRequest,
  SocAgentApprovedActionCommand,
  SocAnalysisRun,
  SocApprovalGrantRequest,
  SocApprovalRequestListResponse,
  SocInvestigationContext,
  SocRequestContext,
  SocReviewCloseRequest,
  SocReviewCorrectionRequest,
  SocReviewQueueItem,
  SocReviewQueueListResponse,
  SocReviewQueueStatus,
} from "./types";

function createRequestId(prefix: string) {
  const randomId = globalThis.crypto?.randomUUID?.();
  if (randomId) return `${prefix}-${randomId}`;
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function buildSocHeaders(
  context: SocRequestContext | undefined,
  {
    json = false,
    stateChanging = false,
  }: {
    json?: boolean;
    stateChanging?: boolean;
  } = {},
) {
  const headers = new Headers();
  if (json) {
    headers.set("Content-Type", "application/json");
  }
  if (!context) {
    return headers;
  }
  if (context.actorId) {
    headers.set("x-soc-actor-id", context.actorId);
  }
  if (context.surface) {
    headers.set("x-soc-surface", context.surface);
  }
  headers.set("x-trace-id", context.traceId ?? createRequestId("soc-trace"));
  if (stateChanging) {
    headers.set(
      "idempotency-key",
      context.idempotencyKey ?? createRequestId("soc-idem"),
    );
  }
  return headers;
}

async function readJson<T>(response: Response, fallbackMessage: string) {
  if (!response.ok) {
    const body = (await response.json().catch(() => ({}))) as {
      detail?: unknown;
    };
    const detail = typeof body.detail === "string" ? body.detail : null;
    throw new Error(detail ?? `${fallbackMessage}: ${response.statusText}`);
  }
  return response.json() as Promise<T>;
}

export async function listSocReviewItems({
  status = "open",
  limit = 50,
  context,
}: {
  status?: SocReviewQueueStatus | null;
  limit?: number;
  context?: SocRequestContext;
} = {}): Promise<SocReviewQueueItem[]> {
  const params = new URLSearchParams();
  if (status !== null) {
    params.set("status", status);
  }
  params.set("limit", String(limit));

  const url = `${getBackendBaseURL()}/api/soc/review/items?${params.toString()}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  const data = await readJson<SocReviewQueueListResponse>(
    response,
    "Failed to load SOC review queue",
  );
  return data.items;
}

export async function getSocReviewContext(
  queueId: string,
  context?: SocRequestContext,
): Promise<SocInvestigationContext> {
  const url = `${getBackendBaseURL()}/api/soc/review/items/${encodeURIComponent(queueId)}/context`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  return readJson<SocInvestigationContext>(
    response,
    "Failed to load SOC review context",
  );
}

export async function closeSocReviewItem(
  queueId: string,
  request: SocReviewCloseRequest,
  context?: SocRequestContext,
): Promise<SocReviewQueueItem> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/items/${encodeURIComponent(queueId)}/close`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify(request),
    },
  );
  return readJson<SocReviewQueueItem>(
    response,
    "Failed to close SOC review item",
  );
}

export async function correctSocReviewRun(
  runId: string,
  request: SocReviewCorrectionRequest,
  context?: SocRequestContext,
): Promise<SocAnalysisRun> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/runs/${encodeURIComponent(runId)}/correct`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify(request),
    },
  );
  return readJson<SocAnalysisRun>(response, "Failed to correct SOC review run");
}

export async function createSocApprovalGrant(
  request: SocApprovalGrantRequest,
  context?: SocRequestContext,
): Promise<SocAgentApprovalGrant> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/approvals/grants`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify(request),
    },
  );
  return readJson<SocAgentApprovalGrant>(
    response,
    "Failed to create SOC approval grant",
  );
}

export async function listSocApprovalRequests({
  status = "pending",
  limit = 50,
  context,
}: {
  status?: "pending" | null;
  limit?: number;
  context?: SocRequestContext;
} = {}): Promise<SocAgentApprovalRequest[]> {
  const params = new URLSearchParams();
  if (status !== null) {
    params.set("status", status);
  }
  params.set("limit", String(limit));

  const url = `${getBackendBaseURL()}/api/soc/approvals/requests?${params.toString()}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  const data = await readJson<SocApprovalRequestListResponse>(
    response,
    "Failed to load SOC approval requests",
  );
  return data.items;
}

export async function getSocApprovalRequest(
  approvalRequestId: string,
  context?: SocRequestContext,
): Promise<SocAgentApprovalRequest> {
  const url = `${getBackendBaseURL()}/api/soc/approvals/requests/${encodeURIComponent(approvalRequestId)}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  return readJson<SocAgentApprovalRequest>(
    response,
    "Failed to load SOC approval request",
  );
}

export async function dryRunSocApprovedAction(
  command: SocAgentApprovedActionCommand,
  context?: SocRequestContext,
): Promise<SocAgentActionResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/approvals/actions/dry-run`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true }),
      body: JSON.stringify({ ...command, dry_run: true }),
    },
  );
  return readJson<SocAgentActionResult>(
    response,
    "Failed to dry-run SOC approved action",
  );
}

export async function executeSocApprovedAction(
  command: SocAgentApprovedActionCommand,
  context?: SocRequestContext,
): Promise<SocAgentActionResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/approvals/actions/execute`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify({ ...command, dry_run: false }),
    },
  );
  return readJson<SocAgentActionResult>(
    response,
    "Failed to execute SOC approved action",
  );
}
