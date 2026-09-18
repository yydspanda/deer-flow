import { afterEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  useCreateSocApprovalGrant,
  useDryRunSocApprovedAction,
  useExecuteSocApprovedAction,
  useExpireSocApprovalRequest,
  useRejectSocApprovalRequest,
} from "@/core/soc/hooks";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "reviewer" } }),
}));

afterEach(() => {
  cleanup();
  rs.unstubAllGlobals();
});

const cases: {
  name: string;
  useCommand: () => (reason: string) => Promise<unknown>;
}[] = [
  {
    name: "approve",
    useCommand: () => {
      const mutation = useCreateSocApprovalGrant();
      return (reason: string) =>
        mutation.mutateAsync({ approval_request_id: "AR-1", reason });
    },
  },
  {
    name: "reject",
    useCommand: () => {
      const mutation = useRejectSocApprovalRequest();
      return (reason: string) =>
        mutation.mutateAsync({
          approvalRequestId: "AR-1",
          request: { reason },
        });
    },
  },
  {
    name: "expire",
    useCommand: () => {
      const mutation = useExpireSocApprovalRequest();
      return (reason: string) =>
        mutation.mutateAsync({
          approvalRequestId: "AR-1",
          request: { reason },
        });
    },
  },
  {
    name: "dry-run",
    useCommand: () => {
      const mutation = useDryRunSocApprovedAction();
      return (reason: string) =>
        mutation.mutateAsync({
          execution_token_id: "TOKEN-1",
          route: "response",
          action: "block",
          payload: { reason },
        });
    },
  },
  {
    name: "execute",
    useCommand: () => {
      const mutation = useExecuteSocApprovedAction();
      return (reason: string) =>
        mutation.mutateAsync({
          execution_token_id: "TOKEN-1",
          route: "response",
          action: "block",
          payload: { reason },
        });
    },
  },
];

for (const { name, useCommand } of cases) {
  for (const uuidAvailable of [true, false]) {
    test(`${name} retries retain keys until acknowledgement (randomUUID: ${uuidAvailable})`, async () => {
      if (!uuidAvailable) rs.stubGlobal("crypto", {});
      const mock = rs.mocked(fetcher);
      mock.mockReset().mockRejectedValueOnce(new Error("response lost"));
      mock.mockImplementation(async () => new Response("{}"));
      const client = new QueryClient({
        defaultOptions: {
          queries: { retry: false },
          mutations: { retry: false },
        },
      });
      const wrapper = ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={client}>{children}</QueryClientProvider>
      );
      const { result } = renderHook(useCommand, { wrapper });
      await act(async () => {
        await expect(result.current("reviewed")).rejects.toThrow(
          "response lost",
        );
      });
      await act(async () => {
        await result.current("reviewed");
        await result.current("reviewed");
      });
      const key = (index: number) =>
        new Headers(mock.mock.calls[index]?.[1]?.headers).get(
          "idempotency-key",
        );
      expect(key(0)).toBeTruthy();
      expect(key(1)).toBe(key(0));
      expect(key(2)).not.toBe(key(1));

      mock.mockRejectedValueOnce(new Error("response lost again"));
      await act(async () => {
        await expect(result.current("old input")).rejects.toThrow(
          "response lost again",
        );
        await result.current("changed input");
        await result.current("old input");
      });
      expect(key(4)).not.toBe(key(3));
      expect(key(5)).toBe(key(3));
      client.clear();
    });
  }
}
