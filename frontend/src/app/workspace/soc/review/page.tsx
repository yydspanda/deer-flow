import { SocReviewQueueWorkbench } from "@/components/workspace/soc/soc-review-queue-workbench";

export default async function SocReviewQueuePage({
  searchParams,
}: {
  searchParams: Promise<{
    queue_id?: string | string[];
    candidate_id?: string | string[];
  }>;
}) {
  const params = await searchParams;
  const queueId = Array.isArray(params.queue_id)
    ? params.queue_id[0]
    : params.queue_id;
  const candidateId = Array.isArray(params.candidate_id)
    ? params.candidate_id[0]
    : params.candidate_id;
  return (
    <SocReviewQueueWorkbench
      initialQueueId={queueId}
      initialCandidateId={candidateId}
    />
  );
}
