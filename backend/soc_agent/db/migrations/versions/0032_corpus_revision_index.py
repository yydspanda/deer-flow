"""Cover corpus revision reads without rewriting historical Runtime payloads."""

from alembic import op

revision = "0032_corpus_revision_index"
down_revision = "0031_memory_working_drafts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_soc_analysis_runs_corpus_revision",
        "soc_analysis_runs",
        ["alert_id", "input_hash", "run_id", "started_at", "updated_at", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_soc_analysis_runs_corpus_revision", table_name="soc_analysis_runs")
