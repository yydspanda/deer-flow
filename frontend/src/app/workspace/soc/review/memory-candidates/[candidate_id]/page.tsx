import { SocReviewQueueWorkbench } from "@/components/workspace/soc/soc-review-queue-workbench";

export default async function SocMemoryCandidateDetailPage({
  params,
  searchParams,
}: {
  params: Promise<{ candidate_id: string }>;
  searchParams: Promise<{ experiment?: string }>;
}) {
  const { candidate_id: candidateId } = await params;
  const { experiment } = await searchParams;
  return (
    <SocReviewQueueWorkbench
      initialCandidateId={candidateId}
      initialExperimentId={experiment}
      initialView="memory"
    />
  );
}
