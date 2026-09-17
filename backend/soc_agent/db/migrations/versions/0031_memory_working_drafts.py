"""Persist shared editable Memory drafts without granting retrieval authority."""

import sqlalchemy as sa
from alembic import op

revision = "0031_memory_working_drafts"
down_revision = "0030_corpus_experiments"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "soc_memory_working_drafts",
        sa.Column("candidate_id", sa.String(64), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("candidate_revision", sa.String(64), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("draft_payload", sa.JSON(), nullable=False),
    )


def downgrade():
    op.drop_table("soc_memory_working_drafts")
