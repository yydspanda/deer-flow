"""Kafka consumer settings for SOC daemon adapters."""

from __future__ import annotations

import os
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator

from soc_agent.daemon.kafka_mapper import DEFAULT_ALERT_TOPICS, DEFAULT_APPROVAL_REQUEST_TOPICS


class KafkaSecurityProtocol(StrEnum):
    PLAINTEXT = "PLAINTEXT"
    SSL = "SSL"
    SASL_PLAINTEXT = "SASL_PLAINTEXT"
    SASL_SSL = "SASL_SSL"


class KafkaConsumerSettings(BaseModel):
    """Config contract for SOC Kafka consumer adapters.

    This is intentionally adapter-neutral. Real broker clients should translate
    this contract into their own constructor kwargs outside core services.
    """

    enabled: bool = False
    bootstrap_servers: list[str] = Field(default_factory=lambda: ["localhost:9092"])
    alert_topics: list[str] = Field(default_factory=lambda: sorted(DEFAULT_ALERT_TOPICS))
    approval_request_topics: list[str] = Field(default_factory=lambda: sorted(DEFAULT_APPROVAL_REQUEST_TOPICS))
    group_id: str = "soc-agent-daemon"
    client_id: str = "soc-agent-consumer"
    dead_letter_topic: str = "soc.alerts.dead_letter.v1"
    security_protocol: KafkaSecurityProtocol = KafkaSecurityProtocol.PLAINTEXT
    sasl_mechanism: str | None = None
    sasl_username: str | None = None
    sasl_password_env: str | None = None
    ssl_ca_location: str | None = None
    poll_timeout_ms: int = Field(default=1000, ge=1)
    max_poll_records: int = Field(default=1, ge=1)

    @field_validator("bootstrap_servers", "alert_topics", "approval_request_topics")
    @classmethod
    def _non_empty_list(cls, value: list[str]) -> list[str]:
        if not value or any(not item.strip() for item in value):
            raise ValueError("must contain at least one non-empty value")
        return value

    def sasl_password(self) -> str | None:
        if not self.sasl_password_env:
            return None
        return os.environ.get(self.sasl_password_env)

    @classmethod
    def from_env(cls, prefix: str = "SOC_KAFKA_") -> KafkaConsumerSettings:
        """Build settings from environment variables without touching DeerFlow config."""

        data: dict[str, object] = {}
        if (enabled := os.environ.get(f"{prefix}ENABLED")) is not None:
            data["enabled"] = enabled.strip().lower() in {"1", "true", "yes", "on"}
        if servers := os.environ.get(f"{prefix}BOOTSTRAP_SERVERS"):
            data["bootstrap_servers"] = _split_csv(servers)
        if alert_topics := os.environ.get(f"{prefix}ALERT_TOPICS"):
            data["alert_topics"] = _split_csv(alert_topics)
        if approval_topics := os.environ.get(f"{prefix}APPROVAL_REQUEST_TOPICS"):
            data["approval_request_topics"] = _split_csv(approval_topics)
        if group_id := os.environ.get(f"{prefix}GROUP_ID"):
            data["group_id"] = group_id
        if client_id := os.environ.get(f"{prefix}CLIENT_ID"):
            data["client_id"] = client_id
        if dead_letter_topic := os.environ.get(f"{prefix}DEAD_LETTER_TOPIC"):
            data["dead_letter_topic"] = dead_letter_topic
        if security_protocol := os.environ.get(f"{prefix}SECURITY_PROTOCOL"):
            data["security_protocol"] = security_protocol
        if sasl_mechanism := os.environ.get(f"{prefix}SASL_MECHANISM"):
            data["sasl_mechanism"] = sasl_mechanism
        if sasl_username := os.environ.get(f"{prefix}SASL_USERNAME"):
            data["sasl_username"] = sasl_username
        if sasl_password_env := os.environ.get(f"{prefix}SASL_PASSWORD_ENV"):
            data["sasl_password_env"] = sasl_password_env
        if ssl_ca_location := os.environ.get(f"{prefix}SSL_CA_LOCATION"):
            data["ssl_ca_location"] = ssl_ca_location
        if poll_timeout_ms := os.environ.get(f"{prefix}POLL_TIMEOUT_MS"):
            data["poll_timeout_ms"] = int(poll_timeout_ms)
        if max_poll_records := os.environ.get(f"{prefix}MAX_POLL_RECORDS"):
            data["max_poll_records"] = int(max_poll_records)
        return cls.model_validate(data)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]
