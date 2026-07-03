from __future__ import annotations

import pytest
from pydantic import ValidationError

from soc_agent.daemon.kafka_adapter import KafkaAdapterNotConfiguredError, NullKafkaConsumerPort
from soc_agent.daemon.kafka_config import KafkaConsumerSettings, KafkaSecurityProtocol
from soc_agent.daemon.kafka_mapper import KafkaRecord


def test_kafka_consumer_settings_defaults_disabled_local_broker() -> None:
    settings = KafkaConsumerSettings()

    assert settings.enabled is False
    assert settings.bootstrap_servers == ["localhost:9092"]
    assert settings.alert_topics == ["soc.alerts.raw.v1"]
    assert settings.approval_request_topics == ["soc.approvals.requests.v1"]
    assert settings.dead_letter_topic == "soc.alerts.dead_letter.v1"
    assert settings.security_protocol is KafkaSecurityProtocol.PLAINTEXT


def test_kafka_consumer_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOC_KAFKA_ENABLED", "true")
    monkeypatch.setenv("SOC_KAFKA_BOOTSTRAP_SERVERS", "kafka-1:9092,kafka-2:9092")
    monkeypatch.setenv("SOC_KAFKA_ALERT_TOPICS", "alerts.a,alerts.b")
    monkeypatch.setenv("SOC_KAFKA_APPROVAL_REQUEST_TOPICS", "approvals.a")
    monkeypatch.setenv("SOC_KAFKA_GROUP_ID", "soc-group")
    monkeypatch.setenv("SOC_KAFKA_CLIENT_ID", "soc-client")
    monkeypatch.setenv("SOC_KAFKA_DEAD_LETTER_TOPIC", "soc.dlq")
    monkeypatch.setenv("SOC_KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("SOC_KAFKA_SASL_MECHANISM", "PLAIN")
    monkeypatch.setenv("SOC_KAFKA_SASL_USERNAME", "soc-user")
    monkeypatch.setenv("SOC_KAFKA_SASL_PASSWORD_ENV", "SOC_KAFKA_PASSWORD")
    monkeypatch.setenv("SOC_KAFKA_PASSWORD", "secret")
    monkeypatch.setenv("SOC_KAFKA_SSL_CA_LOCATION", "/tmp/ca.pem")
    monkeypatch.setenv("SOC_KAFKA_POLL_TIMEOUT_MS", "250")
    monkeypatch.setenv("SOC_KAFKA_MAX_POLL_RECORDS", "10")

    settings = KafkaConsumerSettings.from_env()

    assert settings.enabled is True
    assert settings.bootstrap_servers == ["kafka-1:9092", "kafka-2:9092"]
    assert settings.alert_topics == ["alerts.a", "alerts.b"]
    assert settings.approval_request_topics == ["approvals.a"]
    assert settings.group_id == "soc-group"
    assert settings.client_id == "soc-client"
    assert settings.dead_letter_topic == "soc.dlq"
    assert settings.security_protocol is KafkaSecurityProtocol.SASL_SSL
    assert settings.sasl_mechanism == "PLAIN"
    assert settings.sasl_username == "soc-user"
    assert settings.sasl_password() == "secret"
    assert settings.ssl_ca_location == "/tmp/ca.pem"
    assert settings.poll_timeout_ms == 250
    assert settings.max_poll_records == 10


def test_kafka_consumer_settings_rejects_empty_topic_list() -> None:
    with pytest.raises(ValidationError):
        KafkaConsumerSettings(alert_topics=[])


def test_null_kafka_consumer_port_is_idle_when_disabled() -> None:
    port = NullKafkaConsumerPort(KafkaConsumerSettings(enabled=False))

    assert port.poll() is None
    port.commit(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"))
    port.send_dead_letter(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"), RuntimeError("bad"))
    port.close()

    assert port.closed is True


def test_null_kafka_consumer_port_fails_fast_when_enabled() -> None:
    port = NullKafkaConsumerPort(KafkaConsumerSettings(enabled=True))

    with pytest.raises(KafkaAdapterNotConfiguredError, match="enabled"):
        port.poll()
    with pytest.raises(KafkaAdapterNotConfiguredError, match="dead-letter"):
        port.send_dead_letter(KafkaRecord(topic="soc.alerts.raw.v1", partition=0, offset=1, value="{}"), RuntimeError("bad"))
