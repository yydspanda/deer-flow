"""ORM models for SOC Agent persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from soc_agent.db.base import SocBase


class SocAnalysisRunRow(SocBase):
    """Persisted SOC analysis run.

    The full Pydantic run is stored in ``run_payload`` so schema evolution can
    proceed at the contract layer while indexed columns support common lookups.
    """

    __tablename__ = "soc_analysis_runs"
    __table_args__ = (
        Index("ix_soc_analysis_runs_alert_status", "alert_id", "status"),
        Index("ix_soc_analysis_runs_replay_source", "replay_of_run_id"),
    )

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    alert_id: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    input_hash: Mapped[str | None] = mapped_column(String(128), index=True)
    replay_of_run_id: Mapped[str | None] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    input_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    run_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
