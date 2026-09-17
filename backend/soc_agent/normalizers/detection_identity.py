"""Deterministic primary identity from typed, source-bound detector identifiers."""

from soc_agent.contracts.normalization import DetectionIdentifier


def resolve_detection_identity(proposed: list[DetectionIdentifier], declared: list[DetectionIdentifier], legacy: str | None) -> dict:
    # Source-declared fields cannot be reclassified by a model. Other source
    # fields remain available for future adapters and genuinely new formats.
    by_field = {item.source_field: item for item in declared}
    identifiers = set(declared)
    identifiers.update(item for item in proposed if item.source_field not in by_field)
    identifiers = sorted(identifiers, key=lambda item: (item.kind, item.source_field, item.value))
    basis = "adapter_declared" if declared else "model_typed" if identifiers else "legacy"
    primary = legacy
    # Retain the established rule identity encoding. Other namespaces are
    # explicit so a signature numbered 42 cannot impersonate rule 42.
    for kind in ("rule", "signature", "malware", "detector"):
        authoritative = {item.value for item in declared if item.kind == kind}
        candidates = authoritative or {item.value for item in identifiers if item.kind == kind}
        if candidates:
            if len(candidates) > 1:
                primary, basis = None, "ambiguous"
            else:
                value = next(iter(candidates))
                primary = value if kind == "rule" else kind + ":" + value
            break
    return {"identifiers": [item.model_dump() for item in identifiers], "detector_id": primary, "identity_basis": basis}
