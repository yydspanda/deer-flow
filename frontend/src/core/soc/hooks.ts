"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { useAuth } from "@/core/auth/AuthProvider";

import {
  SocApiError,
  acceptSocLeadAgentConclusion,
  closeSocReviewItem,
  correctSocReviewRun,
  createSocApprovalGrant,
  dryRunSocApprovedAction,
  draftSocMemoryBusinessLesson,
  executeSocApprovedAction,
  expireSocApprovalRequest,
  getSocCorpusWorkbenchState,
  getSocDispositionSampleReviewInbox,
  getSocMemoryCandidate,
  getSocMemoryCenterOverview,
  getSocMemoryCenterPattern,
  getSocMemoryRecord,
  getSocMemoryWorkbenchState,
  getSocNormalizationMetrics,
  getSocOperationsSnapshot,
  getSocApprovalRequest,
  getSocReviewContext,
  listSocMemoryCandidates,
  listSocMemoryRecords,
  listSocNormalizationBaselines,
  listSocNormalizationIssues,
  listSocApprovalRequests,
  listSocDispositionSampleCampaigns,
  listSocReviewItems,
  processSocMemoryWorkbenchAlert,
  processSocCorpusWorkbenchAlert,
  recordSocDispositionOutcome,
  rejectSocApprovalRequest,
  reviewSocMemoryCandidate,
  searchSocMemoryRecords,
  supersedeSocMemoryCandidate,
  updateSocMemoryRetrievalActivation,
  updateSocNormalizationIssue,
} from "./api";
import type {
  SocAgentApprovedActionCommand,
  SocApprovalGrantRequest,
  SocApprovalResolutionRequest,
  SocAgentApprovalRequestStatus,
  SocDispositionOutcomeRecordRequest,
  SocLeadAgentConclusionAcceptanceRequest,
  SocMemoryCandidateReviewRequest,
  SocMemoryBusinessLessonDraftRequest,
  SocMemoryCandidateStatus,
  SocMemoryCandidateSupersessionRequest,
  SocMemoryQuery,
  SocMemoryRecordStatus,
  SocMemoryRetrievalActivationRequest,
  SocNormalizationIssueStatus,
  SocNormalizationIssueUpdateRequest,
  SocRequestContext,
  SocReviewCloseRequest,
  SocReviewCorrectionRequest,
  SocReviewQueueStatus,
} from "./types";

// Mutations invalidate their owner namespaces; this window only avoids route-switch refetch churn.
const SOC_NAVIGATION_STALE_TIME_MS = 30_000;

export const socReviewQueryKeys = {
  all: ["soc-review"] as const,
  items: (status: SocReviewQueueStatus | null, limit: number) =>
    [...socReviewQueryKeys.all, "items", status, limit] as const,
  context: (queueId: string | null | undefined) =>
    [...socReviewQueryKeys.all, "context", queueId] as const,
  sampleCampaigns: (limit: number) =>
    [...socReviewQueryKeys.all, "sample-campaigns", limit] as const,
  sampleInbox: (
    sampleId: string | null | undefined,
    offset: number,
    limit: number,
  ) =>
    [
      ...socReviewQueryKeys.all,
      "sample-inbox",
      sampleId,
      offset,
      limit,
    ] as const,
};

export const socApprovalQueryKeys = {
  all: ["soc-approval"] as const,
  requests: (status: SocAgentApprovalRequestStatus | null, limit: number) =>
    [...socApprovalQueryKeys.all, "requests", status, limit] as const,
  request: (approvalRequestId: string | null | undefined) =>
    [...socApprovalQueryKeys.all, "request", approvalRequestId] as const,
};

export const socMemoryQueryKeys = {
  all: ["soc-memory"] as const,
  candidates: ({
    status,
    tenantScope,
    tenantId,
    runId,
    alertId,
    queueId,
    limit,
  }: {
    status: SocMemoryCandidateStatus | null;
    tenantScope: string | null | undefined;
    tenantId: string | null | undefined;
    runId: string | null | undefined;
    alertId: string | null | undefined;
    queueId: string | null | undefined;
    limit: number;
  }) =>
    [
      ...socMemoryQueryKeys.all,
      "candidates",
      status,
      tenantScope,
      tenantId,
      runId,
      alertId,
      queueId,
      limit,
    ] as const,
  candidate: (candidateId: string | null | undefined) =>
    [...socMemoryQueryKeys.all, "candidate", candidateId] as const,
  center: (filters: Record<string, unknown>) =>
    [...socMemoryQueryKeys.all, "center", filters] as const,
  centerPattern: (
    lineageKey: string | null | undefined,
    includeObservations: boolean,
    observationLimit: number,
    observationOffset: number,
  ) =>
    [
      ...socMemoryQueryKeys.all,
      "center-pattern",
      lineageKey,
      includeObservations,
      observationLimit,
      observationOffset,
    ] as const,
  records: ({
    status,
    tenantScope,
    tenantId,
    sourceCandidateId,
    retrievalEnabled,
    limit,
  }: {
    status: SocMemoryRecordStatus | null;
    tenantScope: string | null | undefined;
    tenantId: string | null | undefined;
    sourceCandidateId: string | null | undefined;
    retrievalEnabled: boolean | null | undefined;
    limit: number;
  }) =>
    [
      ...socMemoryQueryKeys.all,
      "records",
      status,
      tenantScope,
      tenantId,
      sourceCandidateId,
      retrievalEnabled,
      limit,
    ] as const,
  record: (memoryId: string | null | undefined) =>
    [...socMemoryQueryKeys.all, "record", memoryId] as const,
  search: (query: SocMemoryQuery | null | undefined) =>
    [...socMemoryQueryKeys.all, "search", query] as const,
};

export const socMemoryWorkbenchQueryKeys = {
  all: ["soc-memory-workbench"] as const,
  state: () => [...socMemoryWorkbenchQueryKeys.all, "state"] as const,
};

export const socCorpusWorkbenchQueryKeys = {
  all: ["soc-corpus-workbench"] as const,
  state: () => [...socCorpusWorkbenchQueryKeys.all, "state"] as const,
};

export const socNormalizationQueryKeys = {
  all: ["soc-normalization"] as const,
  issues: (status: SocNormalizationIssueStatus | null, limit: number) =>
    [...socNormalizationQueryKeys.all, "issues", status, limit] as const,
  baselines: () => [...socNormalizationQueryKeys.all, "baselines"] as const,
  metrics: () => [...socNormalizationQueryKeys.all, "metrics"] as const,
};

export const socOperationsQueryKeys = {
  all: ["soc-operations"] as const,
  snapshot: () => [...socOperationsQueryKeys.all, "snapshot"] as const,
};

function useSocWebRequestContext(): SocRequestContext {
  const { user } = useAuth();
  return useMemo(
    () => ({
      actorId: user?.id ?? user?.email ?? "soc-web",
      surface: "web",
    }),
    [user?.email, user?.id],
  );
}

export function useSocReviewItems({
  status = "open",
  limit = 50,
  enabled = true,
}: {
  status?: SocReviewQueueStatus | null;
  limit?: number;
  enabled?: boolean;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socReviewQueryKeys.items(status, limit),
    queryFn: () => listSocReviewItems({ status, limit, context }),
    enabled,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { items: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocReviewContext(queueId: string | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socReviewQueryKeys.context(queueId),
    queryFn: () => getSocReviewContext(queueId!, context),
    enabled: !!queueId,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { context: data ?? null, isLoading, isFetching, error };
}

export function useSocDispositionSampleCampaigns({ limit = 50 } = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching, refetch } = useQuery({
    queryKey: socReviewQueryKeys.sampleCampaigns(limit),
    queryFn: () => listSocDispositionSampleCampaigns({ limit, context }),
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return {
    campaigns: data?.items ?? [],
    hasMore: data?.has_more ?? false,
    isLoading,
    isFetching,
    error,
    refetch,
  };
}

export function useSocDispositionSampleReviewInbox(
  sampleId: string | null | undefined,
  { offset = 0, limit = 100 } = {},
) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching, refetch } = useQuery({
    queryKey: socReviewQueryKeys.sampleInbox(sampleId, offset, limit),
    queryFn: () =>
      getSocDispositionSampleReviewInbox(sampleId!, {
        offset,
        limit,
        context,
      }),
    enabled: !!sampleId,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return {
    inbox: data ?? null,
    isLoading,
    isFetching,
    error,
    refetch,
  };
}

export function useCloseSocReviewItem() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      request,
    }: {
      queueId: string;
      request: SocReviewCloseRequest;
    }) => closeSocReviewItem(queueId, request, context),
    onSuccess: (_data, { queueId }) => {
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: socReviewQueryKeys.context(queueId),
      });
    },
  });
}

export function useAcceptSocLeadAgentConclusion() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      threadId,
      request,
    }: {
      queueId: string;
      threadId: string;
      request: SocLeadAgentConclusionAcceptanceRequest;
    }) => acceptSocLeadAgentConclusion(queueId, threadId, request, context),
    onSuccess: (_data, { queueId }) => {
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: socReviewQueryKeys.context(queueId),
      });
      void queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all });
    },
  });
}

export function useCorrectSocReviewRun() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      request,
    }: {
      runId: string;
      request: SocReviewCorrectionRequest;
    }) => correctSocReviewRun(runId, request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
    },
  });
}

export function useRecordSocDispositionOutcome() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: SocDispositionOutcomeRecordRequest) =>
      recordSocDispositionOutcome(request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
    },
  });
}

export function useSocApprovalRequests({
  status = "pending",
  limit = 50,
  enabled = true,
}: {
  status?: "pending" | null;
  limit?: number;
  enabled?: boolean;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socApprovalQueryKeys.requests(status, limit),
    queryFn: () => listSocApprovalRequests({ status, limit, context }),
    enabled,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { requests: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocApprovalRequest(
  approvalRequestId: string | null | undefined,
) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socApprovalQueryKeys.request(approvalRequestId),
    queryFn: () => getSocApprovalRequest(approvalRequestId!, context),
    enabled: !!approvalRequestId,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { request: data ?? null, isLoading, isFetching, error };
}

export function useSocNormalizationIssues({
  status = "open",
  limit = 100,
}: {
  status?: SocNormalizationIssueStatus | null;
  limit?: number;
} = {}) {
  const context = useSocWebRequestContext();
  const query = useQuery({
    queryKey: socNormalizationQueryKeys.issues(status, limit),
    queryFn: () => listSocNormalizationIssues({ status, limit, context }),
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { issues: query.data ?? [], ...query };
}

export function useSocNormalizationBaselines() {
  const context = useSocWebRequestContext();
  const query = useQuery({
    queryKey: socNormalizationQueryKeys.baselines(),
    queryFn: () => listSocNormalizationBaselines(context),
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { baselines: query.data ?? [], ...query };
}

export function useSocNormalizationMetrics() {
  const context = useSocWebRequestContext();
  const query = useQuery({
    queryKey: socNormalizationQueryKeys.metrics(),
    queryFn: () => getSocNormalizationMetrics(context),
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { metrics: query.data ?? null, ...query };
}

export function useSocOperationsSnapshot() {
  const context = useSocWebRequestContext();
  const query = useQuery({
    queryKey: socOperationsQueryKeys.snapshot(),
    queryFn: () => getSocOperationsSnapshot(context),
    refetchInterval: 30_000,
    staleTime: 15_000,
  });
  return { snapshot: query.data ?? null, ...query };
}

export function useSocMemoryWorkbench() {
  const context = useSocWebRequestContext();
  const query = useQuery({
    queryKey: socMemoryWorkbenchQueryKeys.state(),
    queryFn: () => getSocMemoryWorkbenchState(context),
    retry: false,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { state: query.data ?? null, ...query };
}

export function useProcessSocMemoryWorkbenchAlert() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alertId: string) =>
      processSocMemoryWorkbenchAlert(alertId, context),
    onSuccess: (result) => {
      queryClient.setQueryData(
        socMemoryWorkbenchQueryKeys.state(),
        result.state,
      );
      void queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all });
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: socOperationsQueryKeys.all,
      });
    },
  });
}

export function useSocCorpusWorkbench() {
  const context = useSocWebRequestContext();
  const query = useQuery({
    queryKey: socCorpusWorkbenchQueryKeys.state(),
    queryFn: () => getSocCorpusWorkbenchState(context),
    retry: false,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { state: query.data ?? null, ...query };
}

export function useProcessSocCorpusWorkbenchAlert() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (alertId: string) =>
      processSocCorpusWorkbenchAlert(alertId, context),
    onSuccess: (result) => {
      queryClient.setQueryData(
        socCorpusWorkbenchQueryKeys.state(),
        result.state,
      );
      void queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all });
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: socOperationsQueryKeys.all,
      });
    },
  });
}

export function useUpdateSocNormalizationIssue() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      issueId,
      request,
    }: {
      issueId: string;
      request: SocNormalizationIssueUpdateRequest;
    }) => updateSocNormalizationIssue(issueId, request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: socNormalizationQueryKeys.all,
      });
    },
  });
}

export function useSocMemoryCandidates({
  status = "pending_review",
  tenantScope,
  tenantId,
  runId,
  alertId,
  queueId,
  limit = 50,
  enabled = true,
}: {
  status?: SocMemoryCandidateStatus | null;
  tenantScope?: string | null;
  tenantId?: string | null;
  runId?: string | null;
  alertId?: string | null;
  queueId?: string | null;
  limit?: number;
  enabled?: boolean;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.candidates({
      status,
      tenantScope,
      tenantId,
      runId,
      alertId,
      queueId,
      limit,
    }),
    queryFn: () =>
      listSocMemoryCandidates({
        status,
        tenantScope,
        tenantId,
        runId,
        alertId,
        queueId,
        limit,
        context,
      }),
    enabled,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { candidates: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocMemoryCandidate(candidateId: string | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.candidate(candidateId),
    queryFn: () => getSocMemoryCandidate(candidateId!, context),
    enabled: !!candidateId,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { candidate: data ?? null, isLoading, isFetching, error };
}

export function useSocMemoryCenterOverview({
  tenantId,
  environment,
  dataClass,
  profileId,
  search,
  includeTerminalHistory = false,
  limit = 50,
  offset = 0,
}: {
  tenantId?: string | null;
  environment?: string | null;
  dataClass?: "simulation" | "operational" | null;
  profileId?: string | null;
  search?: string | null;
  includeTerminalHistory?: boolean;
  limit?: number;
  offset?: number;
} = {}) {
  const context = useSocWebRequestContext();
  const filters = {
    tenantId,
    environment,
    dataClass,
    profileId,
    search,
    includeTerminalHistory,
    limit,
    offset,
  };
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.center(filters),
    queryFn: () => getSocMemoryCenterOverview(filters, context),
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { overview: data ?? null, isLoading, isFetching, error, refetch };
}

export function useSocMemoryCenterPattern(
  lineageKey: string | null | undefined,
  {
    includeObservations = false,
    observationLimit = 20,
    observationOffset = 0,
  }: {
    includeObservations?: boolean;
    observationLimit?: number;
    observationOffset?: number;
  } = {},
) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.centerPattern(
      lineageKey,
      includeObservations,
      observationLimit,
      observationOffset,
    ),
    queryFn: () =>
      getSocMemoryCenterPattern(
        lineageKey!,
        { includeObservations, observationLimit, observationOffset },
        context,
      ),
    enabled: !!lineageKey,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { detail: data ?? null, isLoading, isFetching, error, refetch };
}

export function useReviewSocMemoryCandidate() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      candidateId,
      request,
    }: {
      candidateId: string;
      request: SocMemoryCandidateReviewRequest;
    }) => reviewSocMemoryCandidate(candidateId, request, context),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all });
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: socMemoryQueryKeys.candidate(result.candidate.candidate_id),
      });
    },
  });
}

export function useSupersedeSocMemoryCandidate() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      candidateId,
      request,
    }: {
      candidateId: string;
      request: SocMemoryCandidateSupersessionRequest;
    }) => supersedeSocMemoryCandidate(candidateId, request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all });
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
    },
  });
}

export function useDraftSocMemoryBusinessLesson() {
  const context = useSocWebRequestContext();
  return useMutation({
    mutationFn: ({
      candidateId,
      request,
    }: {
      candidateId: string;
      request: SocMemoryBusinessLessonDraftRequest;
    }) => draftSocMemoryBusinessLesson(candidateId, request, context),
  });
}

export function useSocMemoryRecords({
  status = "confirmed",
  tenantScope,
  tenantId,
  sourceCandidateId,
  retrievalEnabled,
  limit = 50,
  enabled = true,
}: {
  status?: SocMemoryRecordStatus | null;
  tenantScope?: string | null;
  tenantId?: string | null;
  sourceCandidateId?: string | null;
  retrievalEnabled?: boolean | null;
  limit?: number;
  enabled?: boolean;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.records({
      status,
      tenantScope,
      tenantId,
      sourceCandidateId,
      retrievalEnabled,
      limit,
    }),
    queryFn: () =>
      listSocMemoryRecords({
        status,
        tenantScope,
        tenantId,
        sourceCandidateId,
        retrievalEnabled,
        limit,
        context,
      }),
    enabled,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { records: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocMemorySearch(query: SocMemoryQuery | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.search(query),
    queryFn: () => searchSocMemoryRecords(query!, context),
    enabled: !!query,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { result: data ?? null, isLoading, isFetching, error, refetch };
}

export function useSocMemoryRecord(memoryId: string | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.record(memoryId),
    queryFn: () => getSocMemoryRecord(memoryId!, context),
    enabled: !!memoryId,
    staleTime: SOC_NAVIGATION_STALE_TIME_MS,
  });
  return { record: data ?? null, isLoading, isFetching, error };
}

export function useUpdateSocMemoryRetrievalActivation() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      memoryId,
      request,
    }: {
      memoryId: string;
      request: SocMemoryRetrievalActivationRequest;
    }) => updateSocMemoryRetrievalActivation(memoryId, request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all });
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
    },
    onError: async (error) => {
      if (error instanceof SocApiError && error.status === 409) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: socMemoryQueryKeys.all }),
          queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all }),
        ]);
      }
    },
  });
}

export function useCreateSocApprovalGrant() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: SocApprovalGrantRequest) =>
      createSocApprovalGrant(request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: socApprovalQueryKeys.all,
      });
    },
  });
}

export function useRejectSocApprovalRequest() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      approvalRequestId,
      request,
    }: {
      approvalRequestId: string;
      request: SocApprovalResolutionRequest;
    }) => rejectSocApprovalRequest(approvalRequestId, request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: socApprovalQueryKeys.all,
      });
    },
  });
}

export function useExpireSocApprovalRequest() {
  const context = useSocWebRequestContext();
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      approvalRequestId,
      request,
    }: {
      approvalRequestId: string;
      request: SocApprovalResolutionRequest;
    }) => expireSocApprovalRequest(approvalRequestId, request, context),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: socApprovalQueryKeys.all,
      });
    },
  });
}

export function useDryRunSocApprovedAction() {
  const context = useSocWebRequestContext();
  return useMutation({
    mutationFn: (command: SocAgentApprovedActionCommand) =>
      dryRunSocApprovedAction(command, context),
  });
}

export function useExecuteSocApprovedAction() {
  const context = useSocWebRequestContext();
  return useMutation({
    mutationFn: (command: SocAgentApprovedActionCommand) =>
      executeSocApprovedAction(command, context),
  });
}
