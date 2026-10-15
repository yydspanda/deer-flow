"""Keep immutable Pattern history while replacing effective source observations."""

import sqlalchemy as sa
from alembic import op

revision = "0033_pattern_revisions"
down_revision = "0032_corpus_revision_index"
branch_labels = None
depends_on = None

_TABLE = "soc_memory_pattern_observations"


def upgrade() -> None:
    with op.batch_alter_table(_TABLE) as batch:
        batch.add_column(sa.Column("is_current", sa.Boolean(), server_default=sa.true(), nullable=False))
        batch.add_column(sa.Column("superseded_by_observation_id", sa.String(64), nullable=True))
        batch.drop_constraint("uq_soc_memory_pattern_aggregation_source", type_="unique")
        batch.drop_constraint("uq_soc_memory_pattern_aggregation_occurrence", type_="unique")
    op.create_index("ix_soc_memory_pattern_observations_superseded_by_observation_id", _TABLE, ["superseded_by_observation_id"])
    for suffix, column in (("source", "source_id"), ("occurrence", "occurrence_key")):
        op.create_index(
            f"uq_soc_memory_pattern_current_{suffix}",
            _TABLE,
            ["aggregation_key", column],
            unique=True,
            sqlite_where=sa.text("is_current = 1"),
            postgresql_where=sa.text("is_current = true"),
        )


def downgrade() -> None:
    # Restoring the legacy uniqueness would discard or collide with revision
    # history. Require an explicit archival decision outside schema downgrade.
    if op.get_bind().scalar(sa.text(f"SELECT COUNT(*) FROM {_TABLE} WHERE is_current = false")):
        raise RuntimeError("cannot downgrade Pattern observations while immutable revision history exists")
    for suffix in ("source", "occurrence"):
        op.drop_index(f"uq_soc_memory_pattern_current_{suffix}", table_name=_TABLE)
    op.drop_index("ix_soc_memory_pattern_observations_superseded_by_observation_id", table_name=_TABLE)
    with op.batch_alter_table(_TABLE) as batch:
        batch.drop_column("superseded_by_observation_id")
        batch.drop_column("is_current")
        batch.create_unique_constraint("uq_soc_memory_pattern_aggregation_source", ["aggregation_key", "source_id"])
        batch.create_unique_constraint("uq_soc_memory_pattern_aggregation_occurrence", ["aggregation_key", "occurrence_key"])
