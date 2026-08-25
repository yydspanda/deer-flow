import { describe, expect, it } from "@rstest/core";

import {
  formatCorpusBehaviorFacet,
  formatCorpusGroupOption,
  summarizeCorpusGroupBehavior,
} from "@/core/soc/corpus-presentation";

describe("SOC corpus group presentation", () => {
  it("turns canonical facets into concise analyst labels", () => {
    expect(
      formatCorpusBehaviorFacet("attack_family:proxy_tunnel_activity"),
    ).toBe("代理隧道");
    expect(formatCorpusBehaviorFacet("network_service:udp/1194")).toBe(
      "UDP/1194",
    );
    expect(formatCorpusBehaviorFacet("vulnerability:cve-2017-7924")).toBe(
      "CVE-2017-7924",
    );
  });

  it("prioritizes behavior facets that distinguish same-rule groups", () => {
    expect(
      summarizeCorpusGroupBehavior([
        "protocol:udp",
        "technique:t1190",
        "network_service:udp/44818",
        "attack_family:vulnerability_exploitation",
        "vulnerability:cve-2017-7924",
      ]),
    ).toBe("CVE-2017-7924 / 漏洞利用 / UDP/44818");
  });

  it("keeps a stable group suffix when vendor names repeat", () => {
    expect(
      formatCorpusGroupOption({
        group_id: "CG-541A6F83A997",
        source_type: "ndr",
        detection_key: "sec_guard_apt:rule_code:rpaadm_000558",
        rule_code: "RPAADM_000558",
        rule_name: "红队IP监控",
        behavior_fingerprint: "fingerprint",
        behavior_components: [
          "attack_family:vulnerability_exploitation",
          "network_service:udp/44818",
          "vulnerability:cve-2017-7924",
        ],
        decision_eligible: true,
        alert_count: 8,
        window_count: 2,
        max_window_alert_count: 7,
        candidate_window_count: 1,
        processed_count: 0,
        memory_hit_count: 0,
      }),
    ).toBe(
      "红队IP监控 · CVE-2017-7924 / 漏洞利用 / UDP/44818 · 组 83A997 · 8 条",
    );
  });
});
