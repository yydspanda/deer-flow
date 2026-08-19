import { redirect } from "next/navigation";

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
  if (candidateId) {
    redirect(
      `/workspace/soc/review/memory-candidates/${encodeURIComponent(candidateId)}`,
    );
  }
  redirect(
    queueId
      ? `/workspace/soc/review/alerts?queue_id=${encodeURIComponent(queueId)}`
      : "/workspace/soc/review/alerts",
  );
}
