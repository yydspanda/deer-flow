import { afterEach, expect, rs, test } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";

import { SocApprovalInbox } from "@/components/workspace/soc/soc-approval-inbox";
import { fetch as fetcher } from "@/core/api/fetcher";
import { socApprovalQueryKeys } from "@/core/soc/hooks";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({ user: { id: "reviewer" } }),
}));
rs.mock("@/components/workspace/soc/soc-workspace-header", () => ({
  SocWorkspaceHeader: ({ actions }: { actions: ReactNode }) => (
    <header>{actions}</header>
  ),
}));

afterEach(() => cleanup());

function requestUrl(input: RequestInfo | URL) {
  return typeof input === "string"
    ? input
    : input instanceof URL
      ? input.href
      : input.url;
}

for (const anotherRequest of [false, true]) {
  test(`approved operation survives pending-list removal (another request: ${anotherRequest})`, async () => {
    let approved = false;
    let listReads = 0;
    const request = {
      approval_request_id: "AR-1",
      permission_decision_id: "PD-1",
      route: "response",
      action: "block",
      risk_level: "high_risk",
      reason: "Reviewed proposal",
      requested_by: { actor_id: "analyst" },
      status: "pending",
      action_payload: { target: "192.0.2.1" },
    };
    const nextRequest = {
      ...request,
      approval_request_id: "AR-2",
      action: "isolate",
    };
    const grant = {
      ...request,
      approval_grant_id: "AG-1",
      execution_token_id: "TOKEN-1",
      approval_reason: "approved reason",
      status: "approved",
    };
    const mock = rs.mocked(fetcher);
    mock.mockReset().mockImplementation(async (input) => {
      const url = requestUrl(input);
      const response = (body: unknown) => new Response(JSON.stringify(body));
      if (url.includes("/requests?")) {
        listReads += 1;
        return response({
          items: [
            ...(!approved ? [request] : []),
            ...(anotherRequest ? [nextRequest] : []),
          ],
        });
      }
      // The independently refreshed detail can still deliver its older read.
      if (url.endsWith("/requests/AR-1")) return response(request);
      if (url.endsWith("/requests/AR-2")) return response(nextRequest);
      if (url.endsWith("/grants")) {
        approved = true;
        return response(grant);
      }
      if (url.includes("/actions/"))
        return response({ status: "success", message: "done", payload: {} });
      throw new Error(`Unexpected request ${url}`);
    });
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    render(
      <QueryClientProvider client={client}>
        <SocApprovalInbox />
      </QueryClientProvider>,
    );
    fireEvent.change(
      await screen.findByPlaceholderText("填写批准、驳回或过期的依据"),
      { target: { value: "approved reason" } },
    );
    fireEvent.click(screen.getByRole("button", { name: "批准一次执行" }));
    await waitFor(() => expect(listReads).toBeGreaterThan(1));
    await screen.findByRole("button", { name: "Dry-run" });
    expect(screen.queryByRole("button", { name: "批准一次执行" })).toBeNull();
    expect(
      screen.getByRole("heading", { name: "response / block" }),
    ).toBeTruthy();
    const payload =
      screen.getByLabelText<HTMLTextAreaElement>("动作 payload JSON");
    expect(JSON.parse(payload.value)).toEqual(request.action_payload);
    fireEvent.change(payload, { target: { value: '{"target":"192.0.2.2"}' } });
    await act(async () => {
      await client.invalidateQueries({ queryKey: socApprovalQueryKeys.all });
    });
    expect(
      screen.getByLabelText<HTMLTextAreaElement>("动作 payload JSON").value,
    ).toContain("192.0.2.2");
    fireEvent.click(screen.getByRole("button", { name: "Dry-run" }));
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([url]) =>
          requestUrl(url).endsWith("/actions/dry-run"),
        ),
      ).toBe(true),
    );
    fireEvent.click(screen.getByRole("button", { name: "执行" }));
    await waitFor(() =>
      expect(
        mock.mock.calls.some(([url]) =>
          requestUrl(url).endsWith("/actions/execute"),
        ),
      ).toBe(true),
    );
    for (const [, options] of mock.mock.calls.filter(([url]) =>
      requestUrl(url).includes("/actions/"),
    )) {
      expect(JSON.parse(options?.body as string)).toMatchObject({
        execution_token_id: "TOKEN-1",
        route: "response",
        action: "block",
        payload: { target: "192.0.2.2" },
      });
    }
    if (anotherRequest) {
      fireEvent.click(
        screen.getByRole("button", { name: /response \/ isolate/ }),
      );
      await screen.findByRole("heading", { name: "response / isolate" });
      expect(screen.queryByRole("button", { name: "Dry-run" })).toBeNull();
    }
    client.clear();
  });
}
