import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  closeSocReviewItem,
  correctSocReviewRun,
  getSocReviewContext,
  listSocReviewItems,
} from "./api";
import type {
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

export function useSocReviewItems({
  status = "open",
  limit = 50,
}: {
  status?: SocReviewQueueStatus | null;
  limit?: number;
} = {}) {
  const { data, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: socReviewQueryKeys.items(status, limit),
    queryFn: () => listSocReviewItems({ status, limit }),
  });
  return { items: data ?? [], isLoading, isFetching, error, refetch };
}

export function useSocReviewContext(queueId: string | null | undefined) {
  const { data, isLoading, error, isFetching } = useQuery({
    queryKey: socReviewQueryKeys.context(queueId),
    queryFn: () => getSocReviewContext(queueId!),
    enabled: !!queueId,
  });
  return { context: data ?? null, isLoading, isFetching, error };
}

export function useCloseSocReviewItem() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      queueId,
      request,
    }: {
      queueId: string;
      request: SocReviewCloseRequest;
    }) => closeSocReviewItem(queueId, request),
    onSuccess: (_data, { queueId }) => {
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
      void queryClient.invalidateQueries({
        queryKey: socReviewQueryKeys.context(queueId),
      });
    },
  });
}

export function useCorrectSocReviewRun() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      runId,
      request,
    }: {
      runId: string;
      request: SocReviewCorrectionRequest;
    }) => correctSocReviewRun(runId, request),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: socReviewQueryKeys.all });
    },
  });
}
