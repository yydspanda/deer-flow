import { SocReviewQueueWorkbench } from "@/components/workspace/soc/soc-review-queue-workbench";

export default async function SocMemoryCandidateReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ experiment?: string }>;
}) {
  const { experiment } = await searchParams;
  return (
    <SocReviewQueueWorkbench
      initialView="memory"
      initialExperimentId={experiment}
    />
  );
}
