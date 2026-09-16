"""Read-only next action for the shared experience learning lifecycle."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


class SocMemoryLearningView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["accumulating", "pending_review", "confirmed", "revision_pending", "closed"]
    label: str
    detail: str
    action: Literal["promote", "review", "view_memory", "view_history"]
    action_label: str
    candidate_id: str | None = None
    memory_id: str | None = None
    use_mode: Literal["reference", "exact", "paused", "expired", "retired"] | None = None
