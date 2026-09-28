import { afterEach, expect, test } from "@rstest/core";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";

import { SocMemoryScope } from "@/components/workspace/soc/soc-memory-scope";
import type {
  SocMemoryApplicabilitySpec,
  SocMemoryScopeView,
} from "@/core/soc";
import { withMemoryReuseConditions } from "@/core/soc/memory-reuse-scope";

const directory =
  "http_observation:response=directory_listing;server=simplehttp";
const request = "http_observation:request=directory_traversal";
const spec: SocMemoryApplicabilitySpec = {
  schema_version: "soc.memory_applicability.v1",
  profile_id: "pingan.soc",
  profile_version: "11",
  feature_schema_version: "pingan.soc.memory_features.v9",
  required_facets: { behavior_fingerprint: ["server-owned-fingerprint"] },
  optional_facets: {},
  excluded_facets: {},
  minimum_optional_matches: 0,
  minimum_strong_anchor_matches: 1,
  context_only_required_facet_keys: [],
  context_only_missing_facet_keys: [],
  context_only_similarity_facet_keys: [],
  policy_version: "soc.memory_applicability_policy.v4",
};
const view: SocMemoryScopeView = {
  schema_version: "soc.memory_scope_view.v1",
  required_details: {
    behavior_fingerprint: { behavior_component: [directory, request] },
  },
  unresolved_fingerprint_keys: [],
  options: [],
};

afterEach(cleanup);

test("network behavior choices preserve server tokens and same-response binding in the review command", () => {
  function ReviewScope() {
    const [selected, setSelected] = useState<string[]>([directory, request]);
    return (
      <>
        <SocMemoryScope
          spec={spec}
          view={view}
          selectedBehavior={selected}
          onSelectBehavior={setSelected}
        />
        <output data-testid="command">
          {JSON.stringify(
            withMemoryReuseConditions(spec, {}, selected, [directory, request]),
          )}
        </output>
      </>
    );
  }
  render(<ReviewScope />);
  const response = screen.getByRole<HTMLInputElement>("checkbox", {
    name: "要求核心行为 HTTP 请求与响应 响应返回目录列表；Server 头标识 SimpleHTTP（同一 HTTP 事务）",
  });
  const traversal = screen.getByRole<HTMLInputElement>("checkbox", {
    name: "要求核心行为 HTTP 请求与响应 请求包含目录穿越载荷（同一 HTTP 事务）",
  });
  expect(screen.getAllByRole("checkbox")).toHaveLength(2);
  expect(response.checked).toBe(true);
  expect(traversal.checked).toBe(true);

  fireEvent.click(traversal);
  expect(response.disabled).toBe(true);
  const submitted = JSON.parse(screen.getByTestId("command").textContent);
  expect(submitted.selected_behavior_components).toEqual([directory]);
  expect(submitted.covered_behavior_components).toEqual(
    [directory, request].sort(),
  );
  expect(submitted.required_facets).toEqual(spec.required_facets);

  fireEvent.click(traversal);
  expect(
    JSON.parse(screen.getByTestId("command").textContent)
      .selected_behavior_components,
  ).toEqual([directory, request].sort());
});

test("restores saved network choices while keeping unknown server components intact", () => {
  const future = "http_observation:future_observable";
  render(
    <SocMemoryScope
      spec={{ ...spec, selected_behavior_components: [directory] }}
      view={{
        ...view,
        required_details: {
          behavior_fingerprint: { behavior_component: [directory, future] },
        },
      }}
      onSelectBehavior={() => undefined}
    />,
  );
  const checkboxes = screen.getAllByRole<HTMLInputElement>("checkbox");
  expect(checkboxes.map((input) => input.checked)).toEqual([true, false]);
  expect(screen.getByText("future_observable（同一 HTTP 事务）")).toBeTruthy();
  expect(screen.getByText("允许有或没有")).toBeTruthy();
});
