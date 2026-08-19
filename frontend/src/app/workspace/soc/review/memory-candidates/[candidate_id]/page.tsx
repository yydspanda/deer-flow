import { SocReviewQueueWorkbench } from "@/components/workspace/soc/soc-review-queue-workbench";

export default async function SocMemoryCandidateDetailPage({
  params,
}: {
  params: Promise<{ candidate_id: string }>;
}) {
  const { candidate_id: candidateId } = await params;
  return (
    <SocReviewQueueWorkbench
      initialCandidateId={candidateId}
      initialView="memory"
    />
  );
}
