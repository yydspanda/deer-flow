import { SocReviewQueueWorkbench } from "@/components/workspace/soc/soc-review-queue-workbench";

export default async function SocMemoryCandidateReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ experiment?: string; return_batch?: string }>;
}) {
  const { experiment, return_batch } = await searchParams;
  return (
    <SocReviewQueueWorkbench
      initialView="memory"
      initialExperimentId={experiment}
      initialReturnBatch={
        return_batch === "validation" ? "validation" : "learning"
      }
    />
  );
}
