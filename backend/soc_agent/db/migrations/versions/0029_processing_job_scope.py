"""Fence concurrent jobs for the same execution scope.

Revision ID: 0029_processing_job_scope
Revises: 0028_corpus_list
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_processing_job_scope"
down_revision = "0028_corpus_list"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("soc_processing_jobs", sa.Column("concurrency_key", sa.String(256)))
    active = sa.text("concurrency_key IS NOT NULL AND status IN ('claimed', 'prechecking', 'analyzing', 'projecting')")
    op.create_index("uq_soc_processing_jobs_active_scope", "soc_processing_jobs", ["concurrency_key"], unique=True, sqlite_where=active, postgresql_where=active)


def downgrade() -> None:
    op.drop_index("uq_soc_processing_jobs_active_scope", table_name="soc_processing_jobs")
    op.drop_column("soc_processing_jobs", "concurrency_key")
