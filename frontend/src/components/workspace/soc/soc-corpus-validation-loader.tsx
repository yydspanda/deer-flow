"use client";

import dynamic from "next/dynamic";

import SocCorpusValidationLoading from "@/app/workspace/soc/corpus-validation/loading";

const SocCorpusValidationWorkbench = dynamic(
  () =>
    import("@/components/workspace/soc/soc-corpus-validation-workbench").then(
      (module) => module.SocCorpusValidationWorkbench,
    ),
  {
    loading: () => <SocCorpusValidationLoading />,
    ssr: false,
  },
);

export function SocCorpusValidationLoader() {
  return <SocCorpusValidationWorkbench />;
}
