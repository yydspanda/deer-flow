import type { Message } from "@langchain/langgraph-sdk";
import { expect, test } from "@rstest/core";

import { DEFAULT_LOCAL_SETTINGS } from "@/core/settings/local";
import {
  latestSocAssistantConclusion,
  resolveSocAgentReview,
} from "@/core/soc/agent-review";
import { buildRunContext } from "@/core/threads/hooks";

test("stored review binding takes precedence over the navigation hint", () => {
  expect(
    resolveSocAgentReview(
      "soc-triage",
      { soc_lead_agent_review_thread_binding: { queue_id: " saved/id " } },
      "other",
    ),
  ).toEqual({
    transportAssistantId: "lead_agent",
    reviewQueueId: "saved/id",
    reviewQueueQuery: "?queue_id=saved%2Fid",
    reviewContext: { soc_review_queue_id: "saved/id" },
  });
});

test("new SOC chat carries the trimmed review hint", () => {
  expect(resolveSocAgentReview("soc-triage", undefined, " REV-1 ")).toEqual({
    transportAssistantId: "lead_agent",
    reviewQueueId: "REV-1",
    reviewQueueQuery: "?queue_id=REV-1",
    reviewContext: { soc_review_queue_id: "REV-1" },
  });
});

test("generic run submission retains the SOC binding alongside upstream context", () => {
  const { reviewContext } = resolveSocAgentReview("soc-triage", {}, "REV-1");
  expect(
    buildRunContext({
      settings: {
        ...DEFAULT_LOCAL_SETTINGS.context,
        agent_name: "soc-triage",
        ...reviewContext,
      },
      threadId: "thread-1",
      locale: "zh-CN",
      conversationReferences: ["project-source"],
    }),
  ).toMatchObject({
    agent_name: "soc-triage",
    soc_review_queue_id: "REV-1",
    thread_id: "thread-1",
    locale: "zh-CN",
    conversation_references: ["project-source"],
  });
});

test("ordinary agents never receive the SOC review context", () => {
  expect(
    resolveSocAgentReview(
      "researcher",
      { soc_lead_agent_review_thread_binding: { queue_id: "saved" } },
      "requested",
    ),
  ).toEqual({
    transportAssistantId: "researcher",
    reviewQueueId: null,
    reviewQueueQuery: "",
    reviewContext: {},
  });
  expect(resolveSocAgentReview("soc-triage", {}, " ").reviewQueueId).toBeNull();
});

test("hidden control messages do not replace the last visible conclusion", () => {
  expect(
    latestSocAssistantConclusion([
      { type: "ai", id: "answer", content: " Reviewed conclusion " },
      {
        type: "ai",
        id: "hidden",
        content: "internal control",
        additional_kwargs: { hide_from_ui: true },
      },
    ]),
  ).toEqual({ messageId: "answer", text: "Reviewed conclusion" });
});

for (const last of [
  { type: "ai", id: "opt-pending", content: "optimistic" },
  { type: "ai", id: "empty", content: " " },
  {
    type: "ai",
    id: "tool-step",
    content: "Investigating",
    tool_calls: [{ id: "tool-1", name: "task", args: {} }],
  },
] satisfies Message[]) {
  test(`does not offer an old conclusion while latest reply ${last.id} is incomplete`, () => {
    expect(
      latestSocAssistantConclusion([
        { type: "ai", id: "old", content: "Old answer" },
        last,
      ]),
    ).toBeNull();
  });
}
