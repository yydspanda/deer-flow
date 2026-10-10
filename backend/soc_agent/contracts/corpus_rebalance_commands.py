"""Strict transport commands for explicitly versioned learning supplementation."""

from pydantic import BaseModel, ConfigDict, Field

from soc_agent.contracts.analysis_options import SocAnalysisExecutionOptions
from soc_agent.contracts.corpus_rebalance import CorpusRebalancePolicy


class CorpusRebalanceFactsCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    options: SocAnalysisExecutionOptions | None = None


class CorpusRebalancePlanCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts_experiment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    policy: CorpusRebalancePolicy = Field(default_factory=CorpusRebalancePolicy)


class CorpusRebalanceApplyCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts_experiment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    experiment_id: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    name: str = Field(min_length=1, max_length=160)
    plan_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    policy: CorpusRebalancePolicy = Field(default_factory=CorpusRebalancePolicy)
