"""Local Kafka/Redpanda smoke test for SOC daemon ingestion.

This script expects a Kafka-compatible broker to already be reachable. It
creates the SOC topics, publishes one alert sample, runs ``soc daemon consume``
through the real CLI path, and verifies the resulting SOC summary.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from soc_agent.cli import main as soc_main  # noqa: E402
from soc_agent.daemon.kafka_mapper import DEFAULT_ALERT_TOPICS, DEFAULT_APPROVAL_REQUEST_TOPICS  # noqa: E402

DEFAULT_DEAD_LETTER_TOPIC = "soc.alerts.dead_letter.v1"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        from confluent_kafka import Consumer, Producer
        from confluent_kafka.admin import AdminClient, NewTopic
    except ImportError as exc:
        raise SystemExit("Install the Kafka extra first: uv sync --extra kafka") from exc

    database_url = args.database_url or f"sqlite+pysqlite:///{Path(tempfile.gettempdir()) / 'soc_kafka_smoke.db'}"
    sample_path = Path(args.sample)
    payload = json.loads(sample_path.read_text(encoding="utf-8"))
    alert_id = _alert_id(payload)
    group_id = args.group_id or f"soc-smoke-{int(time.time())}"

    admin = AdminClient({"bootstrap.servers": args.bootstrap_servers})
    _wait_for_broker(admin, timeout_seconds=args.timeout_seconds)
    _ensure_topics(
        admin,
        NewTopic,
        [
            args.alert_topic,
            args.approval_request_topic,
            args.dead_letter_topic,
        ],
        timeout_seconds=args.timeout_seconds,
    )

    _run_soc_cli(["db", "upgrade", "--database-url", database_url], env={})
    _produce_json(
        Producer({"bootstrap.servers": args.bootstrap_servers}),
        topic=args.alert_topic,
        key=f"smoke-alert-{alert_id}",
        payload=payload,
        timeout_seconds=args.timeout_seconds,
    )

    consume_env = _consume_env(args, group_id)
    consume = _run_soc_cli(
        ["daemon", "consume", "--database-url", database_url, "--max-records", "1", "--pretty"],
        env=consume_env,
    )
    consume_payload = json.loads(consume.stdout)
    first_result = consume_payload["results"][0]
    if first_result["status"] != "processed":
        raise SystemExit(f"Expected processed Kafka result, got: {first_result}")

    summaries = json.loads(_run_soc_cli(["list", "--database-url", database_url, "--limit", "20"], env={}).stdout)
    if not any(summary.get("alert_id") == alert_id for summary in summaries):
        raise SystemExit(f"Expected alert_id {alert_id} in persisted summaries")

    dead_letter_payload: dict[str, Any] | None = None
    if args.include_dead_letter:
        bad_key = f"smoke-bad-{int(time.time())}"
        producer = Producer({"bootstrap.servers": args.bootstrap_servers})
        producer.produce(args.alert_topic, key=bad_key, value=b"{bad-json")
        if producer.flush(args.timeout_seconds):
            raise SystemExit("Failed to flush bad JSON smoke message")

        dead_letter_consumer = Consumer(
            {
                "bootstrap.servers": args.bootstrap_servers,
                "group.id": f"{group_id}-dead-letter",
                "auto.offset.reset": "earliest",
                "enable.auto.commit": False,
            }
        )
        dead_letter_consumer.subscribe([args.dead_letter_topic])
        try:
            _run_soc_cli(
                ["daemon", "consume", "--database-url", database_url, "--max-records", "1", "--pretty"],
                env=consume_env,
            )
            dead_letter_payload = _wait_for_dead_letter(
                dead_letter_consumer,
                expected_key=bad_key,
                timeout_seconds=args.timeout_seconds,
            )
        finally:
            dead_letter_consumer.close()

    result = {
        "schema_version": "soc.kafka_smoke_result.v1",
        "bootstrap_servers": args.bootstrap_servers,
        "database_url": database_url,
        "group_id": group_id,
        "alert_id": alert_id,
        "consume_result": first_result,
        "summary_count": len(summaries),
        "dead_letter": dead_letter_payload,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a local SOC Kafka daemon smoke test")
    parser.add_argument("--bootstrap-servers", default="localhost:9092", help="Kafka bootstrap servers")
    parser.add_argument("--database-url", help="SOC database URL; defaults to /tmp/soc_kafka_smoke.db")
    parser.add_argument(
        "--sample",
        default=str(Path(__file__).resolve().parents[1] / "samples" / "alerts" / "approved_scanner.json"),
        help="Alert JSON sample to publish",
    )
    parser.add_argument("--group-id", help="Kafka consumer group id; defaults to a timestamped smoke group")
    parser.add_argument("--alert-topic", default=next(iter(DEFAULT_ALERT_TOPICS)), help="SOC alert input topic")
    parser.add_argument(
        "--approval-request-topic",
        default=next(iter(DEFAULT_APPROVAL_REQUEST_TOPICS)),
        help="SOC approval request input topic",
    )
    parser.add_argument("--dead-letter-topic", default=DEFAULT_DEAD_LETTER_TOPIC, help="SOC dead-letter topic")
    parser.add_argument("--timeout-seconds", type=float, default=15.0, help="Broker operation timeout")
    parser.add_argument("--include-dead-letter", action="store_true", help="Also verify bad JSON -> dead-letter")
    return parser


def _wait_for_broker(admin: Any, *, timeout_seconds: float) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            admin.list_topics(timeout=2)
            return
        except Exception as exc:  # noqa: BLE001 - broker readiness loop
            last_error = exc
            time.sleep(0.5)
    raise SystemExit(f"Kafka broker is not reachable: {last_error}")


def _ensure_topics(admin: Any, new_topic_cls: type[Any], topics: list[str], *, timeout_seconds: float) -> None:
    futures = admin.create_topics([new_topic_cls(topic, num_partitions=1, replication_factor=1) for topic in topics])
    for topic, future in futures.items():
        try:
            future.result(timeout=timeout_seconds)
        except Exception as exc:  # noqa: BLE001 - provider-specific error types
            if "already exists" not in str(exc).lower():
                raise SystemExit(f"Failed to create topic {topic}: {exc}") from exc


def _produce_json(producer: Any, *, topic: str, key: str, payload: dict[str, Any], timeout_seconds: float) -> None:
    producer.produce(topic, key=key, value=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    if producer.flush(timeout_seconds):
        raise SystemExit(f"Failed to flush smoke message to topic {topic}")


def _run_soc_cli(args: list[str], *, env: dict[str, str]) -> CliResult:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with _patched_env(env), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = soc_main(args)
    result = CliResult(code=code, stdout=stdout.getvalue(), stderr=stderr.getvalue())
    if result.code != 0:
        raise SystemExit(f"SOC CLI failed ({result.code}): {' '.join(args)}\n{result.stderr}")
    return result


def _consume_env(args: argparse.Namespace, group_id: str) -> dict[str, str]:
    return {
        "SOC_KAFKA_ENABLED": "true",
        "SOC_KAFKA_BOOTSTRAP_SERVERS": args.bootstrap_servers,
        "SOC_KAFKA_ALERT_TOPICS": args.alert_topic,
        "SOC_KAFKA_APPROVAL_REQUEST_TOPICS": args.approval_request_topic,
        "SOC_KAFKA_DEAD_LETTER_TOPIC": args.dead_letter_topic,
        "SOC_KAFKA_GROUP_ID": group_id,
        "SOC_KAFKA_MAX_POLL_RECORDS": "1",
    }


def _wait_for_dead_letter(consumer: Any, *, expected_key: str, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        message = consumer.poll(1.0)
        if message is None:
            continue
        if message.error():
            raise SystemExit(f"Dead-letter consumer error: {message.error()}")
        key = message.key().decode("utf-8", errors="replace") if isinstance(message.key(), bytes) else message.key()
        if key != expected_key:
            continue
        payload = json.loads(message.value().decode("utf-8"))
        if payload.get("schema_version") != "soc.kafka_dead_letter.v1":
            raise SystemExit(f"Unexpected dead-letter payload: {payload}")
        return payload
    raise SystemExit(f"Timed out waiting for dead-letter key {expected_key}")


def _alert_id(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("alert_id"), str):
        return payload["alert_id"]
    alert = payload.get("alert")
    if isinstance(alert, dict) and isinstance(alert.get("alertId"), str):
        return alert["alertId"]
    raise SystemExit("Smoke sample must contain alert_id or alert.alertId")


@contextlib.contextmanager
def _patched_env(env: dict[str, str]):
    old = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    try:
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class CliResult:
    def __init__(self, *, code: int, stdout: str, stderr: str) -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


if __name__ == "__main__":
    raise SystemExit(main())
