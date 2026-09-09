import { expect, test, rs } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryPendingRevision } from "@/components/workspace/soc/soc-memory-pending-revision";
import { useSocMemoryCandidates } from "@/core/soc/hooks";

rs.mock("@/core/soc/hooks", () => ({ useSocMemoryCandidates: rs.fn() }));

function panel(count = 1, error = false) {
  rs.mocked(useSocMemoryCandidates).mockReturnValue({
    candidates: Array.from({ length: count }, (_, i) => ({
      candidate_id: `MC-revision-${i}`,
      revision_lineage: { reason: "补充业务依据" },
    })),
    error: error ? new Error("offline") : null,
    isLoading: false,
    refetch: rs.fn(),
  } as unknown as ReturnType<typeof useSocMemoryCandidates>);
  return renderToStaticMarkup(<SocMemoryPendingRevision memoryId="MEM-old" />);
}

test("links to the persisted pending revision instead of creating another", () => {
  const html = panel();
  expect(html).toContain("继续审核修订");
  expect(html).toContain(
    "/workspace/soc/review/memory-candidates/MC-revision-0",
  );
  expect(html).toContain("补充业务依据");
  expect(useSocMemoryCandidates).toHaveBeenCalledWith({
    revisionOfMemoryId: "MEM-old",
    limit: 2,
  });
});

test("missing or ambiguous pending lineage never picks an arbitrary candidate", () => {
  for (const count of [0, 2]) {
    const html = panel(count);
    expect(html).toContain("重新查找");
    expect(html).not.toContain("继续审核修订");
  }
});

test("read failure exposes recovery without enabling another revision", () => {
  const html = panel(1, true);
  expect(html).toContain("加载失败");
  expect(html).toContain("查看经验治理");
  expect(html).not.toContain("继续审核修订");
});
