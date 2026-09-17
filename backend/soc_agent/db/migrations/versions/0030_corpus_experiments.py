"""Persist frozen corpus experiments and rounds, reusing processing jobs."""

import sqlalchemy as sa
from alembic import op

revision = "0030_corpus_experiments"
down_revision = "0029_processing_job_scope"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "soc_corpus_experiments",
        sa.Column("experiment_id", sa.String(64), primary_key=True),
        sa.Column("plan_id", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_soc_corpus_experiments_plan_id", "soc_corpus_experiments", ["plan_id"])
    op.create_table(
        "soc_corpus_experiment_members",
        sa.Column("experiment_id", sa.String(64), primary_key=True),
        sa.Column("alert_id", sa.String(128), primary_key=True),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("group_id", sa.String(512), nullable=False),
        sa.Column("batch", sa.String(32), nullable=False),
        sa.Column("validation_tier", sa.String(32)),
        sa.Column("rule_code", sa.String(256)),
        sa.Column("record_payload", sa.JSON(), nullable=False),
        sa.UniqueConstraint("experiment_id", "sequence_number", name="uq_soc_corpus_member_sequence"),
    )
    op.create_index("ix_soc_corpus_member_batch", "soc_corpus_experiment_members", ["experiment_id", "batch", "validation_tier", "sequence_number"])
    op.create_index("ix_soc_corpus_member_group", "soc_corpus_experiment_members", ["experiment_id", "group_id", "sequence_number"])
    op.create_table(
        "soc_corpus_rounds",
        sa.Column("round_id", sa.String(64), primary_key=True),
        sa.Column("experiment_id", sa.String(64), nullable=False),
        sa.Column("batch", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("record_payload", sa.JSON(), nullable=False),
    )
    op.create_index("ix_soc_corpus_rounds_experiment_id", "soc_corpus_rounds", ["experiment_id"])
    op.create_index("ix_soc_corpus_round_state", "soc_corpus_rounds", ["state", "created_at"])
    op.create_table(
        "soc_corpus_round_items",
        sa.Column("round_id", sa.String(64), primary_key=True),
        sa.Column("alert_id", sa.String(128), primary_key=True),
        sa.Column("group_id", sa.String(512), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=False, unique=True),
        sa.UniqueConstraint("round_id", "sequence_number", name="uq_soc_corpus_round_sequence"),
    )
    op.create_index("ix_soc_corpus_round_group", "soc_corpus_round_items", ["round_id", "group_id", "sequence_number"])


def downgrade():
    for table in ("soc_corpus_round_items", "soc_corpus_rounds", "soc_corpus_experiment_members", "soc_corpus_experiments"):
        op.drop_table(table)
