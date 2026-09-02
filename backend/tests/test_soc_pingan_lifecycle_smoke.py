from __future__ import annotations

from soc_agent.integrations.pingan.legacy_compat import (
    PingAnAlertLifecycleService,
    StaticPingAnZeusAlertLifecyclePort,
)
from soc_agent.integrations.pingan.legacy_compat.lifecycle_smoke import (
    run_pingan_zeus_lifecycle_smoke,
)


def test_lifecycle_smoke_accepts_only_real_pending_alert() -> None:
    report = run_pingan_zeus_lifecycle_smoke(
        "2567610",
        service=_real_service(
            {"code": 200, "data": {"status": 1}},
        ),
    )

    assert report.schema_version == "soc.pingan_zeus_lifecycle_smoke.v1"
    assert report.outcome == "pending"
    assert report.passed is True
    assert report.ready_for_live_acceptance is True
    assert report.provider_code == "200"
    assert report.provider_status == "1"
    assert report.alert_id_sha256 != "2567610"
    assert "2567610" not in report.model_dump_json()


def test_lifecycle_smoke_reports_signature_business_error_without_message() -> None:
    report = run_pingan_zeus_lifecycle_smoke(
        "2567610",
        service=_real_service(
            {
                "code": 40100,
                "message": "签名验证失败-private-detail",
            }
        ),
    )

    assert report.outcome == "provider_business_error"
    assert report.passed is False
    assert report.ready_for_live_acceptance is False
    assert report.provider_code == "40100"
    assert report.provider_status is None
    assert report.response_sha256 is not None
    assert "签名验证失败" not in report.model_dump_json()


def test_lifecycle_smoke_rejects_pending_response_without_explicit_success_code() -> None:
    report = run_pingan_zeus_lifecycle_smoke(
        "2567610",
        service=_real_service({"data": {"status": 1}}),
    )

    assert report.outcome == "invalid_response"
    assert report.passed is False
    assert report.ready_for_live_acceptance is False
    assert report.provider_code is None
    assert report.provider_status == "1"


def test_lifecycle_smoke_rejects_fake_provider_in_live_mode() -> None:
    report = run_pingan_zeus_lifecycle_smoke(
        "2567610",
        service=PingAnAlertLifecycleService(port=StaticPingAnZeusAlertLifecyclePort({"2567610": {"code": 200, "data": {"status": 1}}})),
    )

    assert report.outcome == "fake_provider"
    assert report.passed is False
    assert report.ready_for_live_acceptance is False


def _real_service(response: dict) -> PingAnAlertLifecycleService:
    port = StaticPingAnZeusAlertLifecyclePort({"2567610": response})
    port.mocked = False
    return PingAnAlertLifecycleService(port=port)
