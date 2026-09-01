"""Add durable SOC processing jobs and append-only events.

Revision ID: 0027_processing_jobs
Revises: 0026_effectiveness
Create Date: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_processing_jobs"
down_revision: str | Sequence[str] | None = "0026_effectiveness"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "soc_processing_jobs",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.String(length=128)),
        sa.Column("workload_kind", sa.String(length=64), nullable=False),
        sa.Column("queue_name", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("submission_sha256", sa.String(length=64), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("external_ref", sa.String(length=256)),
        sa.Column("alert_id", sa.String(length=128)),
        sa.Column("detection_key", sa.String(length=256)),
        sa.Column("execution_type", sa.String(length=64)),
        sa.Column("model_name", sa.String(length=128)),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("metadata_payload", sa.JSON(), nullable=False),
        sa.Column("run_id", sa.String(length=64)),
        sa.Column("result_payload", sa.JSON()),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("job_id"),
    )
    for name, columns, unique in (
        ("ix_soc_processing_jobs_tenant_id", ["tenant_id"], False),
        ("ix_soc_processing_jobs_workload_kind", ["workload_kind"], False),
        ("ix_soc_processing_jobs_queue_name", ["queue_name"], False),
        ("ix_soc_processing_jobs_status", ["status"], False),
        ("ix_soc_processing_jobs_idempotency_key", ["idempotency_key"], True),
        ("ix_soc_processing_jobs_payload_sha256", ["payload_sha256"], False),
        ("ix_soc_processing_jobs_external_ref", ["external_ref"], False),
        ("ix_soc_processing_jobs_alert_id", ["alert_id"], False),
        ("ix_soc_processing_jobs_detection_key", ["detection_key"], False),
        ("ix_soc_processing_jobs_execution_type", ["execution_type"], False),
        ("ix_soc_processing_jobs_model_name", ["model_name"], False),
        ("ix_soc_processing_jobs_run_id", ["run_id"], False),
        ("ix_soc_processing_jobs_error_code", ["error_code"], False),
        ("ix_soc_processing_jobs_available_at", ["available_at"], False),
        ("ix_soc_processing_jobs_expires_at", ["expires_at"], False),
        ("ix_soc_processing_jobs_lease_owner", ["lease_owner"], False),
        ("ix_soc_processing_jobs_lease_expires_at", ["lease_expires_at"], False),
        ("ix_soc_processing_jobs_created_at", ["created_at"], False),
        ("ix_soc_processing_jobs_updated_at", ["updated_at"], False),
        ("ix_soc_processing_jobs_started_at", ["started_at"], False),
        ("ix_soc_processing_jobs_completed_at", ["completed_at"], False),
    ):
        op.create_index(name, "soc_processing_jobs", columns, unique=unique)
    op.create_index(
        "ix_soc_processing_jobs_claim",
        "soc_processing_jobs",
        ["queue_name", "status", "available_at", "priority", "created_at"],
    )
    op.create_index(
        "ix_soc_processing_jobs_lease",
        "soc_processing_jobs",
        ["status", "lease_expires_at"],
    )
    op.create_index(
        "ix_soc_processing_jobs_alert_created",
        "soc_processing_jobs",
        ["alert_id", "created_at"],
    )

    op.create_table(
        "soc_processing_job_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_status", sa.String(length=32)),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=128)),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("details_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint(
            "job_id",
            "sequence",
            name="uq_soc_processing_job_event_sequence",
        ),
    )
    for name, columns in (
        ("ix_soc_processing_job_events_job_id", ["job_id"]),
        ("ix_soc_processing_job_events_event_type", ["event_type"]),
        ("ix_soc_processing_job_events_from_status", ["from_status"]),
        ("ix_soc_processing_job_events_to_status", ["to_status"]),
        ("ix_soc_processing_job_events_worker_id", ["worker_id"]),
        ("ix_soc_processing_job_events_occurred_at", ["occurred_at"]),
    ):
        op.create_index(name, "soc_processing_job_events", columns)
    op.create_index(
        "ix_soc_processing_job_events_job_time",
        "soc_processing_job_events",
        ["job_id", "occurred_at"],
    )

    op.create_table(
        "soc_callback_outbox",
        sa.Column("outbox_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("destination", sa.String(length=256), nullable=False),
        sa.Column("idempotency_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_error_code", sa.String(length=128)),
        sa.Column("last_error_message", sa.Text()),
        sa.Column("response_metadata", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("outbox_id"),
    )
    for name, columns, unique in (
        ("ix_soc_callback_outbox_job_id", ["job_id"], True),
        ("ix_soc_callback_outbox_destination", ["destination"], False),
        ("ix_soc_callback_outbox_idempotency_key", ["idempotency_key"], True),
        ("ix_soc_callback_outbox_status", ["status"], False),
        ("ix_soc_callback_outbox_available_at", ["available_at"], False),
        ("ix_soc_callback_outbox_lease_owner", ["lease_owner"], False),
        ("ix_soc_callback_outbox_lease_expires_at", ["lease_expires_at"], False),
        ("ix_soc_callback_outbox_last_error_code", ["last_error_code"], False),
        ("ix_soc_callback_outbox_created_at", ["created_at"], False),
        ("ix_soc_callback_outbox_updated_at", ["updated_at"], False),
        ("ix_soc_callback_outbox_delivered_at", ["delivered_at"], False),
    ):
        op.create_index(name, "soc_callback_outbox", columns, unique=unique)
    op.create_index(
        "ix_soc_callback_outbox_claim",
        "soc_callback_outbox",
        ["destination", "status", "available_at", "created_at"],
    )
    op.create_index(
        "ix_soc_callback_outbox_lease",
        "soc_callback_outbox",
        ["status", "lease_expires_at"],
    )

    op.create_table(
        "soc_callback_attempts",
        sa.Column("attempt_id", sa.String(length=64), nullable=False),
        sa.Column("outbox_id", sa.String(length=64), nullable=False),
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("destination", sa.String(length=256), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("dispatcher_id", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column("error_message", sa.Text()),
        sa.Column("response_metadata", sa.JSON()),
        sa.PrimaryKeyConstraint("attempt_id"),
        sa.UniqueConstraint(
            "outbox_id",
            "attempt_number",
            name="uq_soc_callback_attempt_number",
        ),
    )
    for name, columns in (
        ("ix_soc_callback_attempts_outbox_id", ["outbox_id"]),
        ("ix_soc_callback_attempts_job_id", ["job_id"]),
        ("ix_soc_callback_attempts_destination", ["destination"]),
        ("ix_soc_callback_attempts_dispatcher_id", ["dispatcher_id"]),
        ("ix_soc_callback_attempts_outcome", ["outcome"]),
        ("ix_soc_callback_attempts_completed_at", ["completed_at"]),
        ("ix_soc_callback_attempts_error_code", ["error_code"]),
    ):
        op.create_index(name, "soc_callback_attempts", columns)
    op.create_index(
        "ix_soc_callback_attempts_outbox_time",
        "soc_callback_attempts",
        ["outbox_id", "completed_at"],
    )


def downgrade() -> None:
    op.drop_table("soc_callback_attempts")
    op.drop_table("soc_callback_outbox")
    op.drop_table("soc_processing_job_events")
    op.drop_table("soc_processing_jobs")
