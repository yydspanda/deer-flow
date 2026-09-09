import { expect, test, rs } from "@rstest/core";
import { renderToStaticMarkup } from "react-dom/server";

import { SocMemoryNavigation } from "@/components/workspace/soc/soc-memory-navigation";

rs.mock("next/link", () => ({ default: "a" }));

test("one experience center provides three named views", () => {
  const html = renderToStaticMarkup(
    <SocMemoryNavigation pathname="/workspace/soc/memory" />,
  );
  expect(html).toContain("已确认经验");
  expect(html).toContain("经验审核");
  expect(html).toContain("同类告警积累");
  expect(html).not.toContain("台账");
  expect(html.match(/aria-current="page"/g)).toHaveLength(1);
});

for (const [path, active] of [
  ["/memory", "/workspace/soc/memory"],
  ["/memory/records", "/workspace/soc/memory"],
  ["/memory/records/MEM-old/revise", "/workspace/soc/memory"],
  ["/memory/patterns/GROUP-1", "/workspace/soc/memory/patterns"],
  [
    "/review/memory-candidates/MC-old",
    "/workspace/soc/review/memory-candidates",
  ],
]) {
  test(`keeps the owning view active on ${path}`, () => {
    const html = renderToStaticMarkup(
      <SocMemoryNavigation pathname={`/workspace/soc${path}`} />,
    );
    expect(html).toContain(`href="${active}" aria-current="page"`);
    expect(html.match(/aria-current="page"/g)).toHaveLength(1);
  });
}

test("does not introduce experience tabs on other SOC workspaces", () => {
  expect(
    renderToStaticMarkup(
      <SocMemoryNavigation pathname="/workspace/soc/alerts" />,
    ),
  ).toBe("");
});
