from __future__ import annotations

import hashlib
import json

import pytest

from soc_agent.integrations.pingan import zeus_signing


def test_isec_sign_matches_reviewed_legacy_wire_contract(monkeypatch) -> None:
    data = {
        "assetTypeList": ["IP"],
        "param": {"keyword": "192.0.2.8", "queryType": 1},
        "pageNum": 1,
        "pageSize": 10,
    }
    monkeypatch.setattr(zeus_signing.time, "time", lambda: 1_700_000_000.123)
    monkeypatch.setattr(zeus_signing.random, "randint", lambda _start, _end: 42)

    headers = zeus_signing.isec_sign(
        data=data,
        app_id="DEV-APP",
        app_key="local-secret",
    )

    request_body = json.dumps(data)
    expected_material = f"DEV-APP@@1700000000123@@42@@SHA256@@{request_body}@@local-secret"
    assert headers == {
        "x-sec-route-env": "gray",
        "App-Sign": hashlib.sha256(expected_material.encode("UTF-8")).hexdigest(),
        "App-Id": "DEV-APP",
        "App-Timestamp": "1700000000123",
        "App-Nonce": "42",
        "App-Signature-Method": "SHA256",
        "APP-key": "local-secret",
        "companyCode": "ada82427b0de4a1ab890f5ed1a557c14",
    }


def test_isec_sign_matches_legacy_empty_body_contract(monkeypatch) -> None:
    monkeypatch.setattr(zeus_signing.time, "time", lambda: 1.0)
    monkeypatch.setattr(zeus_signing.random, "randint", lambda _start, _end: 0)

    headers = zeus_signing.isec_sign(app_id="DEV-APP", app_key="local-secret")

    expected_material = "DEV-APP@@1000@@0@@SHA256@@@@local-secret"
    assert headers["App-Sign"] == hashlib.sha256(expected_material.encode("UTF-8")).hexdigest()


@pytest.mark.parametrize(
    ("app_id", "app_key"),
    [("", "secret"), ("DEV-APP", ""), (None, "secret"), ("DEV-APP", None)],
)
def test_isec_sign_requires_explicit_credentials(app_id, app_key) -> None:
    with pytest.raises(ValueError, match="app_id and app_key are required"):
        zeus_signing.isec_sign(data={}, app_id=app_id, app_key=app_key)
