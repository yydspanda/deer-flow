from __future__ import annotations

from soc_agent.contracts import (
    AlertEntitySet,
    AlertInput,
    HttpEntityRef,
    HttpObservationRef,
    ParsedRawMessageEvidence,
)
from soc_agent.pipeline.field_importance import EvidenceFieldImportanceRegistry


def _decoded_user_agent_evidence() -> ParsedRawMessageEvidence:
    return ParsedRawMessageEvidence(
        source_path="alert.hitLog[0].zeusRawLogs[1].message",
        parser_name="test",
        parser_version="v1",
        message_hash="hash",
        original_length=1,
        decoded_fields={
            "payload": {
                "req_header": {
                    "headers": {
                        "user-agent": ["Nmap Scripting Engine"],
                    }
                }
            }
        },
    )


def test_observation_value_satisfies_high_value_canonical_target() -> None:
    alert = AlertInput(
        alert_id="ALT-OBSERVATION-COVERAGE",
        entities=AlertEntitySet(
            http=HttpEntityRef(
                observations=[
                    HttpObservationRef(
                        observation_id="http:1",
                        evidence_path="alert.hitLog[0].zeusRawLogs[1].message#parsed",
                        user_agent="Nmap Scripting Engine",
                    )
                ]
            )
        ),
    )
    parsed = _decoded_user_agent_evidence()

    gaps = EvidenceFieldImportanceRegistry.for_alert(alert).find_gaps(
        alert,
        {parsed.source_path: parsed},
    )

    assert not any(item.rule_id == "http.user_agent" for item in gaps)


def test_missing_aggregate_and_observation_value_remains_a_gap() -> None:
    alert = AlertInput(alert_id="ALT-MISSING-OBSERVATION-COVERAGE")
    parsed = _decoded_user_agent_evidence()

    gaps = EvidenceFieldImportanceRegistry.for_alert(alert).find_gaps(
        alert,
        {parsed.source_path: parsed},
    )

    assert any(item.rule_id == "http.user_agent" for item in gaps)
