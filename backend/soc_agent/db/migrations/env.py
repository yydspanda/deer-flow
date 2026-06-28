"""Alembic environment for SOC Agent business tables."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine

# Import models so SocBase.metadata is populated.
import soc_agent.db.models  # noqa: F401
from soc_agent.db.base import SocBase

config = context.config
target_metadata = SocBase.metadata


def include_object(object_, name, type_, reflected, compare_to):  # noqa: ANN001, ARG001
    """Keep this migration scope limited to SOC-owned tables."""

    if type_ == "table":
        return str(name).startswith("soc_")
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        render_as_batch=True,
        include_object=include_object,
        version_table="soc_alembic_version",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(config.get_main_option("sqlalchemy.url"), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection,
                target_metadata=target_metadata,
                render_as_batch=True,
                include_object=include_object,
                version_table="soc_alembic_version",
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
