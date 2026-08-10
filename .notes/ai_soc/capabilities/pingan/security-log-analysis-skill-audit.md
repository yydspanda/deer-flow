# PingAn Security Log Analysis Skill Audit

> Updated: 2026-08-10
> Source: `validation/original_works/security-log-analysis/security-log-analysis/`
> Source ID: `PA-SKILL-DEMO-20260805`

## 1. Decision

The colleague-provided package is useful source material, but it must not be installed or copied wholesale as a public SOC Skill.

It combines five different artifact types in one package:

1. vendor-neutral triage method;
2. PingAn environment facts and authorized-activity lists;
3. PingAn/vendor field aliases;
4. historical false-positive cases and rule-family lessons;
5. direct disposition and response instructions.

The current SOC architecture deliberately separates those concerns. Public Skills teach a reusable method; governed context and memory hold tenant knowledge; normalizers own vendor aliases; Providers return external facts; evaluation corpora test behavior; Runtime, decision policy, review, and approval own operational outcomes.

## 2. Source Inventory

| Source area | Useful content | Unsafe if copied directly | Correct destination |
|---|---|---|---|
| `SKILL.md` | source-first classification, four-question review, scenario navigation | mandatory loading of all common knowledge, PingAn field tables, custom output JSON, direct no-action/block decisions | generic method to public Skills; output remains `AnalysisResult.v2` |
| `common/研判公共知识.md` | demonstrates the need for environment context | internal ranges, domains, systems, products, scanners, and static benign assumptions | governed context / authorized activity / tenant memory with owner and validity |
| `references/apt&nids/` | direction, proxy/CDN, dual-use tooling, payload completeness, attempt/effect distinction | static scanner lists, environment-name safety shortcuts, PingAn blocking instructions | network/web Skills plus tenant facts, policy candidates, and eval fixtures |
| `references/edr/` | process-chain, installer/deployment, persistence, signer/hash, command semantics | specific internal products, paths, departments, rules, and automatic false-positive conclusions | endpoint Skill plus tenant memory and rule-family eval data |
| `references/hids/` | host command context, parent process, user/session, completeness checks | named internal tools/accounts and fixed benign decisions | endpoint Skill now; a separate HIDS Skill only if generic content later outgrows it |
| historical cases | realistic competing explanations and evidence gaps | treating one historical disposition as universal truth | desensitized, analyst-labeled eval fixtures and pending memory candidates |

## 3. Reusable Method Extracted

The following cross-tenant lessons are valid and have been folded into the existing public Skill packages:

- Before a verdict, ask about direct harm, attack-sequence context, legitimate operational explanations, and material evidence gaps.
- Dual-use services and tools do not establish authorization, malicious intent, execution, or impact by themselves.
- A truncated packet/body cannot be replaced by a severe rule name or vendor score.
- Wire endpoints, proxies/CDNs, relays, scanners, attacker, and victim are separate roles.
- Installers, deployment systems, login scripts, IDEs, remote administration clients, and build agents can generate attack-like endpoint behavior; the complete process and execution context matters.
- A broad custom rule family is detection provenance, not a verdict.
- SQL, shell, source code, or webshell-like strings may be transported as inert business data; distinguish transport, storage, interpretation, execution, and observed effect.

These additions remain advisory. They cannot change Runtime control flow, create evidence, close a review, write confirmed memory, or execute an action.

## 4. Routing Defect And Fix

The previous D6 audit had already recorded a concrete defect: some HIDS alerts selected Network/APT from the generic word `恶意` and Web from `命令执行`. In the saved 212-row report, 17 HIDS rows had Network/APT or Web selected; 13 were caused only by those broad keywords, while the remaining cross-domain selections had typed network evidence.

The corrected routing contract is:

```text
source type + typed canonical evidence  -> strong route signal
explicit domain terminology            -> fallback route signal
ambiguous behavior wording             -> may reinforce an existing compatible route
ambiguous behavior wording alone       -> must not cross a known source-domain boundary
```

Consequences:

- HIDS/EDR/XDR text such as `恶意命令执行` stays in endpoint triage unless typed HTTP/network evidence exists.
- NIDS/NDR text may add Web triage for a web-specific behavior, while endpoint routing still requires typed endpoint evidence.
- WAF/F5 text does not create endpoint/network routes by itself.
- Unknown-source text `恶意命令执行` remains on baseline triage instead of guessing a domain.
- Typed cross-domain evidence is preserved; the fix suppresses keyword-only guesses, not legitimate multi-Skill investigations.

D6 now has an explicit `ambiguous_keywords_do_not_cross_known_source_domains` acceptance check so this cannot silently regress.

## 5. PingAn Knowledge Candidates

The following candidate groups are discovered but are not automatically activated:

| Candidate group | Target artifact | Required governance |
|---|---|---|
| internal network/domain/system catalog | `GovernedContextFact` | tenant scope, source, owner, validity window, append-only version |
| scanner, red-team, test, and maintenance catalog | authorized-activity facts | actor/scope/time/purpose match; a name or IP match alone does not close an alert |
| internal software, path, process-chain, and admin-tool cases | `benign_pattern` / `environment_fact` candidate | `pending_review`, expiry/review date, evidence lineage, no automatic suppression |
| custom detection rule-family behavior | `detection_lesson` plus eval fixture | source-versioned, reviewed false-positive/true-positive counterexamples |
| `dev/stg/test` handling suggestions | tenant disposition policy v1 | implemented as isolated shadow data; hostname remains a hint, so no benign/exempt disposition without governed confirmation |
| historical APT/EDR/HIDS cases | desensitized labeled eval corpus | analyst truth, rationale, corpus manifest, supersession lineage |

Exact internal values remain in the source material until an operator deliberately onboards them through the corresponding governed service. They are not duplicated into public Skills or this audit.

## 6. Rejected Direct Migrations

The following source behaviors are intentionally not migrated:

- always load the entire common-knowledge document before every alert;
- route generic core logic by PingAn field names;
- declare an internal system, path, scanner, security product, or environment benign from a static Skill match;
- convert missing authorization data into malicious proof;
- use vendor confidence or a rule name as observed behavior;
- emit a second ad hoc verdict schema instead of `AnalysisResult.v2`;
- let a Skill decide suppression, blocking, isolation, case closure, or an empty action list;
- place exact customer facts in `skills/public/`.

## 7. Validation

This slice is accepted only when:

- focused resolver tests prove ambiguous HIDS wording does not cross-route;
- typed HTTP/network evidence still enables legitimate cross-domain Skills;
- Checkpoint D6 processes the full corpus with zero keyword-only cross-domain findings;
- every public `soc-*` package still passes DeerFlow Skill parsing and package projection;
- public Skill diffs contain no PingAn internal ranges, systems, accounts, rule IDs, or response IDs.

Future additions from this source must update this audit or a linked governed candidate/eval artifact. The original package remains reference evidence and is not a production Skill dependency.

## 8. Tenant Policy Extraction Result

The first non-Skill migration from this source is now implemented:

- generic contracts/evaluator: `backend/soc_agent/contracts/tenant_policy.py` and
  `backend/soc_agent/tenant_policy/`;
- post-persistence service: `backend/soc_agent/core/tenant_policy.py`;
- PingAn-owned policy data:
  `backend/soc_agent/integrations/pingan/policies/tenant-disposition-v1.json`;
- append-only persistence: migration `0022_tenant_policy_decisions`;
- replay/inspection: `soc tenant-policy evaluate|list|get`;
- real saved-result validation: `validation/compact_zeus/policy/`.

This closes only the `dev/stg/test` policy-candidate extraction. Internal scanners, red-team rosters,
maintenance windows, products, accounts, paths and historical false-positive cases still require the
governed fact/memory/eval onboarding named above; they were not silently copied into the policy pack.
