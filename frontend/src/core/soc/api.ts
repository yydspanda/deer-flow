import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  SocAgentActionResult,
  SocAgentApprovalGrant,
  SocAgentApprovalRequest,
  SocAgentApprovedActionCommand,
  SocAnalysisRun,
  SocDispositionOutcomeApplyResult,
  SocDispositionOutcomeRecordRequest,
  SocDispositionSampleManifestListResponse,
  SocDispositionSampleReviewInbox,
  SocApprovalGrantRequest,
  SocApprovalRequestListResponse,
  SocInvestigationContext,
  SocMemoryCandidate,
  SocMemoryCandidateListResponse,
  SocMemoryCandidateReviewRequest,
  SocMemoryCandidateReviewResult,
  SocMemoryCandidateStatus,
  SocMemoryQuery,
  SocMemoryRecord,
  SocMemoryRecordListResponse,
  SocMemoryRecordStatus,
  SocMemoryRetrievalResult,
  SocNormalizationBaselineListResponse,
  SocNormalizationIssueListResponse,
  SocNormalizationIssueStatus,
  SocNormalizationIssueUpdateRequest,
  SocNormalizationMaintenanceIssue,
  SocNormalizationOperationsMetrics,
  SocNormalizationSchemaBaseline,
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

export async function listSocDispositionSampleCampaigns({
  limit = 50,
  context,
}: {
  limit?: number;
  context?: SocRequestContext;
} = {}): Promise<SocDispositionSampleManifestListResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  const url = `${getBackendBaseURL()}/api/soc/review/disposition-samples?${params.toString()}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  return readJson<SocDispositionSampleManifestListResponse>(
    response,
    "Failed to load SOC disposition sample campaigns",
  );
}

export async function getSocDispositionSampleReviewInbox(
  sampleId: string,
  {
    offset = 0,
    limit = 100,
    context,
  }: {
    offset?: number;
    limit?: number;
    context?: SocRequestContext;
  } = {},
): Promise<SocDispositionSampleReviewInbox> {
  const params = new URLSearchParams({
    offset: String(offset),
    limit: String(limit),
  });
  const url = `${getBackendBaseURL()}/api/soc/review/disposition-samples/${encodeURIComponent(sampleId)}/inbox?${params.toString()}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  return readJson<SocDispositionSampleReviewInbox>(
    response,
    "Failed to load SOC disposition sample review inbox",
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

export async function recordSocDispositionOutcome(
  request: SocDispositionOutcomeRecordRequest,
  context?: SocRequestContext,
): Promise<SocDispositionOutcomeApplyResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/disposition-outcomes`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify(request),
    },
  );
  return readJson<SocDispositionOutcomeApplyResult>(
    response,
    "Failed to record SOC disposition outcome",
  );
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

export async function listSocNormalizationIssues({
  status = "open",
  limit = 100,
  context,
}: {
  status?: SocNormalizationIssueStatus | null;
  limit?: number;
  context?: SocRequestContext;
} = {}): Promise<SocNormalizationMaintenanceIssue[]> {
  const params = new URLSearchParams();
  if (status !== null) params.set("status", status);
  params.set("limit", String(limit));
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/normalization/issues?${params.toString()}`,
    { headers: buildSocHeaders(context) },
  );
  const data = await readJson<SocNormalizationIssueListResponse>(
    response,
    "Failed to load SOC normalization issues",
  );
  return data.items;
}

export async function updateSocNormalizationIssue(
  issueId: string,
  request: SocNormalizationIssueUpdateRequest,
  context?: SocRequestContext,
): Promise<SocNormalizationMaintenanceIssue> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/normalization/issues/${encodeURIComponent(issueId)}`,
    {
      method: "PATCH",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify(request),
    },
  );
  return readJson<SocNormalizationMaintenanceIssue>(
    response,
    "Failed to update SOC normalization issue",
  );
}

export async function listSocNormalizationBaselines(
  context?: SocRequestContext,
): Promise<SocNormalizationSchemaBaseline[]> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/normalization/baselines?status=active&limit=200`,
    { headers: buildSocHeaders(context) },
  );
  const data = await readJson<SocNormalizationBaselineListResponse>(
    response,
    "Failed to load SOC normalization baselines",
  );
  return data.items;
}

export async function getSocNormalizationMetrics(
  context?: SocRequestContext,
): Promise<SocNormalizationOperationsMetrics> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/normalization/metrics`,
    { headers: buildSocHeaders(context) },
  );
  return readJson<SocNormalizationOperationsMetrics>(
    response,
    "Failed to load SOC normalization metrics",
  );
}

export async function listSocMemoryCandidates({
  status = "pending_review",
  tenantScope,
  tenantId,
  runId,
  alertId,
  queueId,
  limit = 50,
  context,
}: {
  status?: SocMemoryCandidateStatus | null;
  tenantScope?: string | null;
  tenantId?: string | null;
  runId?: string | null;
  alertId?: string | null;
  queueId?: string | null;
  limit?: number;
  context?: SocRequestContext;
} = {}): Promise<SocMemoryCandidate[]> {
  const params = new URLSearchParams();
  if (status !== null) {
    params.set("status", status);
  }
  if (tenantScope) {
    params.set("tenant_scope", tenantScope);
  }
  if (tenantId) {
    params.set("tenant_id", tenantId);
  }
  if (runId) {
    params.set("run_id", runId);
  }
  if (alertId) {
    params.set("alert_id", alertId);
  }
  if (queueId) {
    params.set("queue_id", queueId);
  }
  params.set("limit", String(limit));

  const url = `${getBackendBaseURL()}/api/soc/memory/candidates?${params.toString()}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  const data = await readJson<SocMemoryCandidateListResponse>(
    response,
    "Failed to load SOC memory candidates",
  );
  return data.items;
}

export async function getSocMemoryCandidate(
  candidateId: string,
  context?: SocRequestContext,
): Promise<SocMemoryCandidate> {
  const url = `${getBackendBaseURL()}/api/soc/memory/candidates/${encodeURIComponent(candidateId)}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  return readJson<SocMemoryCandidate>(
    response,
    "Failed to load SOC memory candidate",
  );
}

export async function reviewSocMemoryCandidate(
  candidateId: string,
  request: SocMemoryCandidateReviewRequest,
  context?: SocRequestContext,
): Promise<SocMemoryCandidateReviewResult> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/memory/candidates/${encodeURIComponent(candidateId)}/review`,
    {
      method: "POST",
      headers: buildSocHeaders(context, { json: true, stateChanging: true }),
      body: JSON.stringify(request),
    },
  );
  return readJson<SocMemoryCandidateReviewResult>(
    response,
    "Failed to review SOC memory candidate",
  );
}

export async function listSocMemoryRecords({
  status = "confirmed",
  tenantScope,
  tenantId,
  sourceCandidateId,
  retrievalEnabled,
  limit = 50,
  context,
}: {
  status?: SocMemoryRecordStatus | null;
  tenantScope?: string | null;
  tenantId?: string | null;
  sourceCandidateId?: string | null;
  retrievalEnabled?: boolean | null;
  limit?: number;
  context?: SocRequestContext;
} = {}): Promise<SocMemoryRecord[]> {
  const params = new URLSearchParams();
  if (status !== null) {
    params.set("status", status);
  }
  if (tenantScope) {
    params.set("tenant_scope", tenantScope);
  }
  if (tenantId) {
    params.set("tenant_id", tenantId);
  }
  if (sourceCandidateId) {
    params.set("source_candidate_id", sourceCandidateId);
  }
  if (retrievalEnabled !== undefined && retrievalEnabled !== null) {
    params.set("retrieval_enabled", String(retrievalEnabled));
  }
  params.set("limit", String(limit));

  const url = `${getBackendBaseURL()}/api/soc/memory/records?${params.toString()}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  const data = await readJson<SocMemoryRecordListResponse>(
    response,
    "Failed to load SOC memory records",
  );
  return data.items;
}

export async function searchSocMemoryRecords(
  query: SocMemoryQuery,
  context?: SocRequestContext,
): Promise<SocMemoryRetrievalResult> {
  const response = await fetch(`${getBackendBaseURL()}/api/soc/memory/search`, {
    method: "POST",
    headers: buildSocHeaders(context, { json: true }),
    body: JSON.stringify(query),
  });
  return readJson<SocMemoryRetrievalResult>(
    response,
    "Failed to search SOC memory records",
  );
}

export async function getSocMemoryRecord(
  memoryId: string,
  context?: SocRequestContext,
): Promise<SocMemoryRecord> {
  const url = `${getBackendBaseURL()}/api/soc/memory/records/${encodeURIComponent(memoryId)}`;
  const response = context
    ? await fetch(url, { headers: buildSocHeaders(context) })
    : await fetch(url);
  return readJson<SocMemoryRecord>(
    response,
    "Failed to load SOC memory record",
  );
}
