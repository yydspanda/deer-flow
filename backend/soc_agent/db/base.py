"""SQLAlchemy base for SOC-owned business tables."""

from __future__ import annotations

from sqlalchemy import Engine
from sqlalchemy.orm import DeclarativeBase


class SocBase(DeclarativeBase):
    """Declarative base for SOC Agent tables.

    SOC tables intentionally live outside DeerFlow harness persistence so this
    fork can keep business data separate from upstream runtime tables.
    """


def create_soc_tables(engine: Engine) -> None:
    """Create SOC tables for local development and tests.

    Production deployments must use migrations; this helper keeps local tests
    and isolated acceptance environments lightweight.
    """

    SocBase.metadata.create_all(engine)
