import { SocReviewQueueWorkbench } from "@/components/workspace/soc/soc-review-queue-workbench";

export default async function SocMemoryCandidateDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ candidate_id: string }>;
  searchParams: Promise<{ experiment?: string; return_batch?: string }>;
}) {
  const { candidate_id: candidateId } = await params;
  const { experiment, return_batch } = await searchParams;
  return (
    <SocReviewQueueWorkbench
      initialCandidateId={candidateId}
      initialExperimentId={experiment}
      initialReturnBatch={
        return_batch === "validation" ? "validation" : "learning"
      }
      initialView="memory"
    />
  );
}
