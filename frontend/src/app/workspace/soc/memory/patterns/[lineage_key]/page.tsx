import { SocMemoryCenter } from "@/components/workspace/soc/soc-memory-center";

export default async function SocMemoryPatternPage({
  params,
}: {
  params: Promise<{ lineage_key: string }>;
}) {
  const { lineage_key: lineageKey } = await params;
  return <SocMemoryCenter initialLineageKey={lineageKey} />;
}
