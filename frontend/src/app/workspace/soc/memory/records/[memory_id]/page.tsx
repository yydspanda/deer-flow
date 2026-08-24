import { SocMemoryRecordWorkbench } from "@/components/workspace/soc/soc-memory-record-workbench";

export default async function SocMemoryRecordPage({
  params,
}: {
  params: Promise<{ memory_id: string }>;
}) {
  const { memory_id: memoryId } = await params;
  return <SocMemoryRecordWorkbench memoryId={memoryId} />;
}
