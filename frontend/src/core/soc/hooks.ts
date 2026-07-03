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
  getSocReviewContext,
  listSocReviewItems,
} from "./api";
import type {
  SocAgentApprovedActionCommand,
  SocApprovalGrantRequest,
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

export function useCreateSocApprovalGrant() {
  const context = useSocWebRequestContext();
  return useMutation({
    mutationFn: (request: SocApprovalGrantRequest) =>
      createSocApprovalGrant(request, context),
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
