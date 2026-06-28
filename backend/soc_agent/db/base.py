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

    Production deployments should use migrations; this helper keeps the Phase 1
    repository testable before the migration chain is finalized.
    """

    SocBase.metadata.create_all(engine)
