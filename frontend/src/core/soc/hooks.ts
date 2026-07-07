"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { useAuth } from "@/core/auth/AuthProvider";

import {
  closeSocReviewItem,
  correctSocReviewRun,
  createSocApprovalGrant,
  dryRunSocApprovedAction,
  executeSocApprovedAction,
  getSocMemoryCandidate,
  getSocMemoryRecord,
  getSocApprovalRequest,
  getSocReviewContext,
  listSocMemoryCandidates,
  listSocMemoryRecords,
  listSocApprovalRequests,
  listSocReviewItems,
  reviewSocMemoryCandidate,
} from "./api";
import type {
  SocAgentApprovedActionCommand,
  SocApprovalGrantRequest,
  SocMemoryCandidateReviewRequest,
  SocMemoryCandidateStatus,
  SocMemoryRecordStatus,
  SocRequestContext,
  SocReviewCloseRequest,
  SocReviewCorrectionRequest,
  SocReviewQueueStatus,
} from "./types";

export const socReviewQueryKeys = {
  all: ["soc-review"] as const,
  items: (status: SocReviewQueueStatus | null, limit: number) =>
    [...socReviewQueryKeys.all, "items", status, limit] as const,
  context: (queueId: string | null | undefined) =>
    [...socReviewQueryKeys.all, "context", queueId] as const,
};

export const socApprovalQueryKeys = {
  all: ["soc-approval"] as const,
  requests: (status: "pending" | null, limit: number) =>
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
  records: ({
    status,
    tenantScope,
    tenantId,
    sourceCandidateId,
    limit,
  }: {
    status: SocMemoryRecordStatus | null;
    tenantScope: string | null | undefined;
    tenantId: string | null | undefined;
    sourceCandidateId: string | null | undefined;
    limit: number;
  }) =>
    [
      ...socMemoryQueryKeys.all,
      "records",
      status,
      tenantScope,
      tenantId,
      sourceCandidateId,
      limit,
    ] as const,
  record: (memoryId: string | null | undefined) =>
    [...socMemoryQueryKeys.all, "record", memoryId] as const,
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
}: {
  status?: SocReviewQueueStatus | null;
  limit?: number;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socReviewQueryKeys.items(status, limit),
    queryFn: () => listSocReviewItems({ status, limit, context }),
  });
  return { items: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocReviewContext(queueId: string | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socReviewQueryKeys.context(queueId),
    queryFn: () => getSocReviewContext(queueId!, context),
    enabled: !!queueId,
  });
  return { context: data ?? null, isLoading, isFetching, error };
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

export function useSocApprovalRequests({
  status = "pending",
  limit = 50,
}: {
  status?: "pending" | null;
  limit?: number;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socApprovalQueryKeys.requests(status, limit),
    queryFn: () => listSocApprovalRequests({ status, limit, context }),
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
  });
  return { request: data ?? null, isLoading, isFetching, error };
}

export function useSocMemoryCandidates({
  status = "pending_review",
  tenantScope,
  tenantId,
  runId,
  alertId,
  queueId,
  limit = 50,
}: {
  status?: SocMemoryCandidateStatus | null;
  tenantScope?: string | null;
  tenantId?: string | null;
  runId?: string | null;
  alertId?: string | null;
  queueId?: string | null;
  limit?: number;
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
  });
  return { candidates: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocMemoryCandidate(candidateId: string | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.candidate(candidateId),
    queryFn: () => getSocMemoryCandidate(candidateId!, context),
    enabled: !!candidateId,
  });
  return { candidate: data ?? null, isLoading, isFetching, error };
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

export function useSocMemoryRecords({
  status = "confirmed",
  tenantScope,
  tenantId,
  sourceCandidateId,
  limit = 50,
}: {
  status?: SocMemoryRecordStatus | null;
  tenantScope?: string | null;
  tenantId?: string | null;
  sourceCandidateId?: string | null;
  limit?: number;
} = {}) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.records({
      status,
      tenantScope,
      tenantId,
      sourceCandidateId,
      limit,
    }),
    queryFn: () =>
      listSocMemoryRecords({
        status,
        tenantScope,
        tenantId,
        sourceCandidateId,
        limit,
        context,
      }),
  });
  return { records: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocMemoryRecord(memoryId: string | null | undefined) {
  const context = useSocWebRequestContext();
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socMemoryQueryKeys.record(memoryId),
    queryFn: () => getSocMemoryRecord(memoryId!, context),
    enabled: !!memoryId,
  });
  return { record: data ?? null, isLoading, isFetching, error };
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
