"use client";

import dynamic from "next/dynamic";

import SocMemoryLoading from "@/app/workspace/soc/memory/loading";

const SocMemoryCenter = dynamic(
  () =>
    import("@/components/workspace/soc/soc-memory-center").then(
      (module) => module.SocMemoryCenter,
    ),
  {
    loading: () => <SocMemoryLoading />,
    ssr: false,
  },
);

const SocMemoryRecordInventory = dynamic(
  () =>
    import("@/components/workspace/soc/soc-memory-record-inventory").then(
      (module) => module.SocMemoryRecordInventory,
    ),
  { loading: () => <SocMemoryLoading />, ssr: false },
);

export function SocMemoryCenterLoader() {
  return <SocMemoryRecordInventory />;
}

export function SocMemoryPatternsLoader() {
  return <SocMemoryCenter />;
}
