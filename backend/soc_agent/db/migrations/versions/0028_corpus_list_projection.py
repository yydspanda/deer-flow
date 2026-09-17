"""Add a rebuildable DEV corpus list index.

Revision ID: 0028_corpus_list
Revises: 0027_processing_jobs
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_corpus_list"
down_revision = "0027_processing_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "soc_corpus_list_projections",
        sa.Column("catalog_id", sa.String(64), nullable=False),
        sa.Column("alert_id", sa.String(128), nullable=False),
        sa.Column("input_hash", sa.String(128), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.String(64), nullable=False),
        sa.Column("search_text", sa.Text(), nullable=False),
        sa.Column("group_id", sa.String(128), nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("labeled", sa.Boolean(), nullable=False),
        sa.Column("projection_payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("catalog_id", "alert_id"),
    )
    op.create_index("ix_soc_corpus_list_order", "soc_corpus_list_projections", ["catalog_id", "sequence_number"])


def downgrade() -> None:
    op.drop_index("ix_soc_corpus_list_order", table_name="soc_corpus_list_projections")
    op.drop_table("soc_corpus_list_projections")
