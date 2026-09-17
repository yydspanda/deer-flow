"""PingAn adapter identifier namespaces; no LLM text or cross-log inference."""

from collections.abc import Sequence

from soc_agent.contracts import ParsedRawMessageEvidence


def source_detection_identifiers(messages: Sequence[ParsedRawMessageEvidence]) -> dict[str, list[dict[str, str]]]:
    result = {}
    for message in messages:
        fields = message.fields
        origin = fields.get("_origin")
        identifiers = []
        for kind, field, value in (
            ("rule", "rule_id", fields.get("rule_id")),
            ("signature", "_origin.sig_id", origin.get("sig_id") if isinstance(origin, dict) else None),
            ("malware", "virus_id", fields.get("virus_id")),
        ):
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                identifiers.append({"kind": kind, "source_field": field, "value": str(value).strip()})
        if identifiers:
            result[message.source_path] = identifiers
    return result
