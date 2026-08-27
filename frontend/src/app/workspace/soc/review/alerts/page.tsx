import { SocHumanInterventionInbox } from "@/components/workspace/soc/soc-human-intervention-inbox";

export default async function SocAlertReviewPage({
  searchParams,
}: {
  searchParams: Promise<{ queue_id?: string | string[] }>;
}) {
  const params = await searchParams;
  const queueId = Array.isArray(params.queue_id)
    ? params.queue_id[0]
    : params.queue_id;
  return <SocHumanInterventionInbox initialQueueId={queueId} />;
}
