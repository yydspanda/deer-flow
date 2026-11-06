import { afterEach, beforeEach, expect, rs, test } from "@rstest/core";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import type { ReactNode } from "react";

import { SocAgentReviewActions } from "@/components/workspace/soc/soc-agent-review-actions";

const api = rs.hoisted(() => ({
  accept: rs.fn(),
  success: rs.fn(),
  error: rs.fn(),
}));
rs.mock("@/core/soc", () => ({
  useAcceptSocLeadAgentConclusion: () => ({
    mutateAsync: api.accept,
    isPending: false,
  }),
}));
rs.mock("sonner", () => ({
  toast: { success: api.success, error: api.error },
}));
rs.mock("@/components/workspace/tooltip", () => ({
  Tooltip: ({ children }: { children: ReactNode }) => <>{children}</>,
}));

beforeEach(() => {
  api.accept.mockReset().mockResolvedValue({ memory_candidate: null });
  api.success.mockReset();
  api.error.mockReset();
});
afterEach(() => cleanup());

function actions(isLoading = false) {
  return (
    <SocAgentReviewActions
      reviewQueueId="REV-1"
      threadId="thread-1"
      messages={[{ type: "ai", id: "answer-1", content: "Current conclusion" }]}
      isNewThread={false}
      isMock={false}
      isLoading={isLoading}
    />
  );
}

test("submits only the selected reply identity and explicit operator reason", async () => {
  render(actions());
  fireEvent.click(screen.getByRole("button", { name: "采纳结论" }));
  const submit = screen.getByRole<HTMLButtonElement>("button", {
    name: "提交候选",
  });
  expect(submit.disabled).toBe(true);
  fireEvent.change(
    screen.getByPlaceholderText("填写你核验并采纳该结论的理由"),
    {
      target: { value: "  verified evidence  " },
    },
  );
  fireEvent.click(submit);
  await waitFor(() =>
    expect(api.accept).toHaveBeenCalledWith({
      queueId: "REV-1",
      threadId: "thread-1",
      request: {
        message_id: "answer-1",
        acceptance_reason: "verified evidence",
      },
    }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(api.success).toHaveBeenCalledWith("Lead Agent 结论已记录");
});

test("does not offer a conclusion while the current reply is streaming", () => {
  render(actions(true));
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "采纳结论" })
      .disabled,
  ).toBe(true);
  expect(api.accept).not.toHaveBeenCalled();
});

test("server rejection retains the operator draft and shows the error", async () => {
  api.accept.mockRejectedValue(new Error("review binding changed"));
  render(actions());
  fireEvent.click(screen.getByRole("button", { name: "采纳结论" }));
  const reason =
    screen.getByPlaceholderText<HTMLTextAreaElement>(
      "填写你核验并采纳该结论的理由",
    );
  fireEvent.change(reason, { target: { value: "checked" } });
  fireEvent.click(screen.getByRole("button", { name: "提交候选" }));
  await waitFor(() =>
    expect(api.error).toHaveBeenCalledWith("review binding changed"),
  );
  expect(reason.value).toBe("checked");
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(api.success).not.toHaveBeenCalled();
});
