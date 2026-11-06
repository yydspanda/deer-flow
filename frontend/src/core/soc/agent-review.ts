import type { Message } from "@langchain/langgraph-sdk";

import { isHiddenFromUIMessage } from "@/core/messages/utils";
import { textOfMessage } from "@/core/threads/utils";

/** A navigation hint only; the Gateway verifies the persisted review binding. */
export function resolveSocAgentReview(
  agentName: string,
  metadata: Record<string, unknown> | null | undefined,
  requestedQueueId: string | null,
) {
  const binding = metadata?.soc_lead_agent_review_thread_binding as
    | Record<string, unknown>
    | undefined;
  const storedQueueId =
    typeof binding?.queue_id === "string" ? binding.queue_id.trim() : "";
  const requested = requestedQueueId?.trim() ?? "";
  const reviewQueueId =
    agentName === "soc-triage" ? storedQueueId || requested || null : null;
  return {
    // SOC review admission is registered on the lead_agent graph. The selected
    // custom Agent remains agent_name in the context, as before the merge.
    transportAssistantId: agentName === "soc-triage" ? "lead_agent" : agentName,
    reviewQueueId,
    reviewQueueQuery: reviewQueueId
      ? `?queue_id=${encodeURIComponent(reviewQueueId)}`
      : "",
    reviewContext: reviewQueueId ? { soc_review_queue_id: reviewQueueId } : {},
  };
}

export function latestSocAssistantConclusion(messages: Message[]) {
  const message = [...messages]
    .reverse()
    .find((item) => item.type === "ai" && !isHiddenFromUIMessage(item));
  if (
    !message ||
    message.type !== "ai" ||
    !message.id ||
    message.id.startsWith("opt-") ||
    (message.tool_calls?.length ?? 0) > 0
  ) {
    return null;
  }
  const text = textOfMessage(message)?.trim();
  return text ? { messageId: message.id, text } : null;
}
