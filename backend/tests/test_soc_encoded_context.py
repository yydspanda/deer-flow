from __future__ import annotations

from soc_agent.pipeline.encoded_context import compact_encoded_spans


def test_encoded_context_compacts_embedded_blob_without_mutating_input() -> None:
    blob = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/" * 5
    source = {
        "payload": f"prefix={blob};suffix=kept",
        "short_id": "QbJK/jZFu",
    }

    compacted, omissions = compact_encoded_spans(source)

    assert source["payload"] == f"prefix={blob};suffix=kept"
    marker = f"<ENCODED:base64_like:320:sha256={omissions[0].sha256[:12]}:OMITTED>"
    assert compacted == {
        "payload": f"prefix={marker};suffix=kept",
        "short_id": "QbJK/jZFu",
    }
    assert len(omissions) == 1
    assert omissions[0].path == "$.payload"
    assert omissions[0].kind == "base64_like"
    assert omissions[0].original_chars == 320
    assert len(omissions[0].sha256) == 64


def test_encoded_context_keeps_low_entropy_and_short_values() -> None:
    source = {
        "repeated": "A" * 320,
        "hex_digest": "8b046b92886dfc7418569b7b9f8e6328",
        "business_id": "QbJK/jZFu",
    }

    compacted, omissions = compact_encoded_spans(source)

    assert compacted == source
    assert omissions == []
