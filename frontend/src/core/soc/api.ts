import { fetch } from "@/core/api/fetcher";
import { getBackendBaseURL } from "@/core/config";

import type {
  SocAnalysisRun,
  SocInvestigationContext,
  SocReviewCloseRequest,
  SocReviewCorrectionRequest,
  SocReviewQueueItem,
  SocReviewQueueListResponse,
  SocReviewQueueStatus,
} from "./types";

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
}: {
  status?: SocReviewQueueStatus | null;
  limit?: number;
} = {}): Promise<SocReviewQueueItem[]> {
  const params = new URLSearchParams();
  if (status !== null) {
    params.set("status", status);
  }
  params.set("limit", String(limit));

  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/items?${params.toString()}`,
  );
  const data = await readJson<SocReviewQueueListResponse>(
    response,
    "Failed to load SOC review queue",
  );
  return data.items;
}

export async function getSocReviewContext(
  queueId: string,
): Promise<SocInvestigationContext> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/items/${encodeURIComponent(queueId)}/context`,
  );
  return readJson<SocInvestigationContext>(
    response,
    "Failed to load SOC review context",
  );
}

export async function closeSocReviewItem(
  queueId: string,
  request: SocReviewCloseRequest,
): Promise<SocReviewQueueItem> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/items/${encodeURIComponent(queueId)}/close`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
): Promise<SocAnalysisRun> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/soc/review/runs/${encodeURIComponent(runId)}/correct`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(request),
    },
  );
  return readJson<SocAnalysisRun>(response, "Failed to correct SOC review run");
}
