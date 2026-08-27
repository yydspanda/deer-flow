import { SocAlertResultsWorkbench } from "@/components/workspace/soc/soc-alert-results-workbench";

export default async function SocAlertResultsPage({
  searchParams,
}: {
  searchParams: Promise<{ run_id?: string | string[] }>;
}) {
  const params = await searchParams;
  const runId = Array.isArray(params.run_id) ? params.run_id[0] : params.run_id;
  return <SocAlertResultsWorkbench initialRunId={runId} />;
}
