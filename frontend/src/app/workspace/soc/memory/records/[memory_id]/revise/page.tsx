import { SocMemoryRevisionWorkbench } from "@/components/workspace/soc/soc-memory-revision-workbench";

export default async function SocMemoryRevisionPage({
  params,
  searchParams,
}: {
  params: Promise<{ memory_id: string }>;
  searchParams: Promise<{ run_id?: string | string[] }>;
}) {
  const [{ memory_id: memoryId }, query] = await Promise.all([
    params,
    searchParams,
  ]);
  const runId = Array.isArray(query.run_id) ? query.run_id[0] : query.run_id;
  const normalizedRunId = runId?.trim();
  return (
    <SocMemoryRevisionWorkbench
      memoryId={memoryId}
      sourceRunId={normalizedRunId?.length ? normalizedRunId : null}
    />
  );
}
