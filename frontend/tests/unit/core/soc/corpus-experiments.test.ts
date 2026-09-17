import { describe, expect, test, rs } from "@rstest/core";

rs.mock("@/core/api/fetcher", () => ({ fetch: rs.fn() }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));

import { fetch as fetcher } from "@/core/api/fetcher";
import {
  createSocCorpusRound,
  getSocCorpusWorkbenchAudit,
  startSocCorpusRound,
} from "@/core/soc/api";
import {
  corpusRoundSelection,
  corpusDuration,
} from "@/core/soc/corpus-experiments";

describe("SOC experiment boundaries", () => {
  test("duration formatting distinguishes missing measurements from zero", () => {
    expect(corpusDuration(null)).toBe("未记录");
    expect(corpusDuration(-1)).toBe("未记录");
    expect(corpusDuration(0)).toBe("0 秒");
    expect(corpusDuration(80000)).toBe("1 分 20 秒");
    expect(corpusDuration(3660000)).toBe("1 小时 1 分");
  });
  test("keeps sparse exploration separate and scopes manual alert selection", () => {
    expect(corpusRoundSelection("learning", "all", "G-1", " 42 ")).toEqual({
      batch: "learning",
      scope: "reuse",
      group_ids: ["G-1"],
      rule_codes: [],
      alert_ids: ["42"],
    });
    expect(
      corpusRoundSelection("validation", "supplementary", "all").scope,
    ).toBe("explore");
  });

  test("preparing a round does not silently start it; retry preserves the key", async () => {
    const mock = rs.mocked(fetcher);
    mock
      .mockReset()
      .mockResolvedValue(
        new Response(JSON.stringify({ round_id: "R-1" }), { status: 201 }),
      );
    await createSocCorpusRound(
      {
        experiment_id: "EXP-1",
        selection: corpusRoundSelection("learning", "main", "all"),
        purpose: "memory",
        memory_mode: "snapshot",
        execution_limit: 5,
        concurrency: 3,
        options: {
          normalization_review_mode: "apply",
          tenant_policy_enabled: false,
          tenant_policy_advisor_enabled: false,
          tenant_policy_signal_providers_enabled: false,
        },
      },
      "stable-key",
    );
    expect(mock).toHaveBeenCalledTimes(1);
    expect(
      new Headers(mock.mock.calls[0]?.[1]?.headers).get("idempotency-key"),
    ).toBe("stable-key");
    mock.mockResolvedValue(new Response("{}"));
    await startSocCorpusRound("R-1", { execution_limit: 50 });
    expect(mock.mock.calls[1]?.[0]).toContain("/rounds/R-1/start");
    expect(JSON.parse(mock.mock.calls[1]?.[1]?.body as string)).toEqual({
      execution_limit: 50,
    });
  });

  test("audit URL pins the historical run instead of asking for the latest", async () => {
    const mock = rs.mocked(fetcher);
    mock.mockReset().mockResolvedValue(new Response("{}"));
    await getSocCorpusWorkbenchAudit("123", undefined, "RUN-old");
    expect(mock.mock.calls[0]?.[0]).toBe(
      "/api/soc/dev/corpus-workbench/alerts/123/audit?run_id=RUN-old",
    );
  });
});
