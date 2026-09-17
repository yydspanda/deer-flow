"""Detection identity follows its typed subject, not an LLM's enum choice."""

from types import SimpleNamespace

from soc_agent.contracts import AlertEntitySet
from soc_agent.integrations.pingan.memory.semantic_features import semantic_behavior_components


def request(kind, name="Tool P detected", detector_id="event-42"):
    entities = AlertEntitySet.model_validate(
        {
            "network": {"protocol": "udp", "dst_port": 1194},
            "detections": [{"observation_id": "test", "evidence_path": "message", "event_scope_id": "message", "kind": kind, "name": name, "detector_id": detector_id, "category": "proxy", "subject_refs": ["entities.network"]}],
        }
    )
    return SimpleNamespace(canonical_entities=entities, source=SimpleNamespace(source_system="vendor", product="network-sensor"))


def test_network_detection_classification_and_caption_do_not_split_identity():
    a = semantic_behavior_components(request("network_access"), stable=True)
    b = semantic_behavior_components(request("other_detection", "Tool P communication"), stable=True)
    assert a == b
    assert a[1]
    assert not any(c.startswith("target_port:") for c in a[0])


def test_other_detector_or_service_remains_distinct():
    a = request("network_access")
    b = request("configuration_detection", detector_id="event-43")
    assert semantic_behavior_components(a, stable=True) != semantic_behavior_components(b, stable=True)
    b = request("other_detection")
    b.canonical_entities.network.dst_port = 443
    assert semantic_behavior_components(a, stable=True) != semantic_behavior_components(b, stable=True)


def test_old_projection_is_replayable():
    assert semantic_behavior_components(request("network_access"), stable=False) != semantic_behavior_components(request("other_detection"), stable=False)
