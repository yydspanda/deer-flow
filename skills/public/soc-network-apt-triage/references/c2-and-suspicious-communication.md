# C2 And Suspicious Communication

## Callback And Beaconing

- Compare interval regularity, duration, volume, byte symmetry, protocol behavior, destination rarity, DNS history, TLS metadata, and user agent.
- Tie network activity to an endpoint process when possible. A suspicious destination without host/process context may remain suspicious but not confirmed compromise.
- Reverse-shell and callback semantics can make the affected internal host the wire source and the controller the wire destination. Preserve wire direction separately from security roles.

## Malicious Outbound And Tunnels

- Check whether the destination is expected for the host, user, application, environment, and time window.
- For proxy, VPN, remote-control, DNS tunnel, or covert channel hypotheses, identify the protocol feature that supports the claim; product names and ports alone are weak evidence.
- Distinguish an authorized scanner, red-team source, maintenance process, or security service by returned governed context, not by a global allowlist in the Skill.

## DNS And Tooling Signals

- Separate ordinary lookup, rare-domain lookup, DNS-based callback, DNS tunneling, and confirmed controller communication. A `dnslog` label or one query is a hypothesis, not proof.
- Evaluate query shape, entropy, label length, record type, repetition, response, resolver path, destination history, and endpoint process when available.
- Scanner, exploit-framework, proxy, and remote-control signatures are useful scenario evidence, but product names, ports, or user agents do not establish authorization or successful impact.

## Correlation

Look for repeated entities, rule/detection identity, temporal proximity, matching process or user, and shared infrastructure. Similar alerts improve context but do not prove that the current event has the same disposition.
