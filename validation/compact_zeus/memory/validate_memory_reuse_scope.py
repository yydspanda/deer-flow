"""Read a frozen candidate and inspect simulated scope variants, without writes or LLM."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from soc_agent.contracts import SocMemoryCandidate, SocMemoryQuery  # noqa: E402
from soc_agent.integrations.pingan.memory.profile import PingAnSocMemoryProfile  # noqa: E402
from soc_agent.memory.behavior_scope import select_memory_behavior_components  # noqa: E402
from soc_agent.memory.lessons import promote_memory_applicability_facets  # noqa: E402
from soc_agent.memory.profiles import SocMemoryProfileRegistry  # noqa: E402
from soc_agent.memory.scoring import evaluate_memory_scope  # noqa: E402
from soc_agent.utils.hashing import stable_hash  # noqa: E402


def inspect(candidate: SocMemoryCandidate, *, behavior_selection: bool = False) -> dict:
    spec = candidate.applicability
    if spec is None:
        raise ValueError("candidate has no typed scope")
    roles = spec.optional_facets.get("role_entity", [])
    selected = [v for v in roles if v.startswith(("source:", "destination:"))]
    if not any(v.startswith("source:") for v in selected) or not any(
        v.startswith("destination:") for v in selected
    ):
        raise ValueError("this inspection needs observed source and destination roles")
    narrowed = promote_memory_applicability_facets(spec, [], {"role_entity": selected})
    facets = {**candidate.facets, **spec.required_facets, **spec.optional_facets}
    variants = [
        ("all_conditions_match", facets),
        (
            "only_source_matches",
            {**facets, "role_entity": [v for v in selected if v.startswith("source:")]},
        ),
        (
            "same_behavior_different_ips",
            {**facets, "role_entity": ["source:192.0.2.1", "destination:192.0.2.2"]},
        ),
    ]
    changed = {
        key: [v.replace("udp/1194", "udp/443") for v in values]
        for key, values in facets.items()
    }
    components = sorted(
        changed.get("behavior_component_core") or changed.get("behavior_component", [])
    )
    if components == sorted(
        facets.get("behavior_component_core") or facets.get("behavior_component", [])
    ):
        raise ValueError("service variant requires an observed UDP/1194 component")
    version = spec.feature_schema_version.rsplit(".", 1)[-1]
    changed["behavior_fingerprint"] = [
        stable_hash(
            {
                "schema_version": f"pingan.soc.memory_behavior_fingerprint.{version}",
                "components": components,
            }
        )
    ]
    changed["role_entity"] = ["source:192.0.2.1", "destination:192.0.2.2"]
    variants.append(("shared_behavior_different_service_and_ips", changed))
    checks = []
    for name, query_facets in variants:
        query = SocMemoryQuery(
            facets=query_facets,
            metadata={
                "memory_profile_id": spec.profile_id,
                "memory_profile_version": spec.profile_version,
                "memory_feature_schema_version": spec.feature_schema_version,
            },
        )
        baseline = evaluate_memory_scope(spec, candidate.candidate_type, query, {})
        result = evaluate_memory_scope(narrowed, candidate.candidate_type, query, {})
        checks.append(
            {
                "variant": name,
                "before_extra_limits": baseline.model_dump(mode="json"),
                "after_extra_limits": result.model_dump(mode="json"),
            }
        )
    behavior_checks = []
    if behavior_selection:
        core = sorted(
            set(
                candidate.facets.get("behavior_component_core")
                or candidate.facets.get("behavior_component", [])
            )
        )
        if "network_service:sip/5060" not in core:
            raise ValueError(
                "behavior inspection requires the observed SIP/5060 component"
            )
        registry = SocMemoryProfileRegistry(
            [PingAnSocMemoryProfile(semantic_features=version == "v6")]
        )
        all_selected = select_memory_behavior_components(
            spec, candidate.facets, core, registry=registry
        )
        without_sip = select_memory_behavior_components(
            spec,
            candidate.facets,
            [v for v in core if v != "network_service:sip/5060"],
            registry=registry,
        )
        reordered = select_memory_behavior_components(
            spec, candidate.facets, [*reversed(core), core[0]], registry=registry
        )
        only_udp = {
            key: [
                v for v in values if v not in {"network_service:sip/5060", "sip/5060"}
            ]
            for key, values in facets.items()
        }
        only_udp["behavior_fingerprint"] = [
            stable_hash(
                {
                    "schema_version": f"pingan.soc.memory_behavior_fingerprint.{version}",
                    "components": sorted(
                        set(
                            only_udp.get("behavior_component_core")
                            or only_udp["behavior_component"]
                        )
                    ),
                }
            )
        ]
        for name, selection in (
            ("all_selected_but_query_has_no_sip", all_selected),
            ("sip_unchecked_and_query_has_no_sip", without_sip),
        ):
            query = SocMemoryQuery(
                facets=only_udp,
                metadata={
                    "memory_profile_id": spec.profile_id,
                    "memory_profile_version": spec.profile_version,
                    "memory_feature_schema_version": spec.feature_schema_version,
                },
            )
            result = evaluate_memory_scope(
                selection, candidate.candidate_type, query, {}
            )
            behavior_checks.append(
                {
                    "variant": name,
                    "source_fingerprint": spec.required_facets["behavior_fingerprint"],
                    "query_fingerprint": only_udp["behavior_fingerprint"],
                    "report": result.model_dump(mode="json"),
                }
            )
        assert all_selected == reordered
        assert behavior_checks[0]["report"]["status"] == "partial"
        assert behavior_checks[1]["report"]["status"] == "applicable"
    return {
        "schema_version": "soc.memory_reuse_scope_inspection.v1",
        "candidate_id": candidate.candidate_id,
        "candidate_hash": stable_hash(candidate.model_dump(mode="json")),
        "simulated_variants": True,
        "scope_only_not_top_k_or_verdict_test": True,
        "model_calls": 0,
        "memory_writes": 0,
        "reuse_conditions": [item.model_dump() for item in narrowed.reuse_conditions],
        "checks": checks,
        "behavior_selection_checks": behavior_checks,
        "selection_order_and_duplicates_equivalent": True
        if behavior_selection
        else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--behavior-selection", action="store_true")
    args = parser.parse_args()
    with sqlite3.connect(
        args.database.resolve().as_uri() + "?mode=ro", uri=True
    ) as connection:
        row = connection.execute(
            "SELECT candidate_payload FROM soc_memory_candidates WHERE candidate_id = ?",
            (args.candidate_id,),
        ).fetchone()
    if row is None:
        raise ValueError("candidate not found")
    report = inspect(
        SocMemoryCandidate.model_validate_json(row[0]),
        behavior_selection=args.behavior_selection,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        args.output.chmod(0o600)
        json.dump(report, stream, ensure_ascii=False, indent=2)
    print(
        json.dumps(
            {
                "report": str(args.output),
                "checks": [
                    {
                        "variant": c["variant"],
                        "status": c["after_extra_limits"]["status"],
                        "context_allowed": c["after_extra_limits"][
                            "context_only_allowed"
                        ],
                    }
                    for c in report["checks"]
                ],
                "behavior_checks": [
                    {"variant": check["variant"], "status": check["report"]["status"]}
                    for check in report["behavior_selection_checks"]
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
