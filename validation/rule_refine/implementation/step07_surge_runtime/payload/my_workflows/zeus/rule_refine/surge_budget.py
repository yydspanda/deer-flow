"""6Z input budget based on the internal route's reported context capacity.

The provider rejected 336423 input tokens against a 262144-token context on
2026-11-03. This observation describes that internal route, not an official
model specification. UTF-8 bytes remain a conservative proxy, not a tokenizer.
"""

from dataclasses import dataclass

OBSERVED_MODEL = "Pub_DeepSeek_V4_Flash"
REPORTED_CONTEXT_TOKENS = 262144
REJECTED_INPUT_TOKENS = 336423
OBSERVED_AT = "2026-11-03T16:43:21"
TOKEN_ESTIMATION_BASIS = "utf8_message_content_bytes_plus_chat_allowance_v1"


@dataclass(frozen=True)
class ContextBudget:
    context_tokens: int = REPORTED_CONTEXT_TOKENS
    input_fraction: float = 0.8
    timeout_seconds: int = 600

    def __post_init__(self):
        if type(self.context_tokens) is not int or self.context_tokens < 1:
            raise ValueError("invalid_context_tokens")
        if (
            type(self.input_fraction) not in (int, float)
            or not 0 < self.input_fraction < 1
            or self.input_token_budget < 1
        ):
            raise ValueError("invalid_input_fraction")
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 600
        ):
            raise ValueError("invalid_timeout_seconds")

    @classmethod
    def for_model(cls, model):
        if model != OBSERVED_MODEL:
            raise ValueError(
                "本次已确认的上下文容量对应Pub_DeepSeek_V4_Flash；请核对当前模型配置。"
            )
        return cls()

    @property
    def input_token_budget(self):
        return int(self.context_tokens * self.input_fraction)

    @property
    def input_bytes(self):
        return 32 * 1024 * 1024

    @property
    def output_bytes(self):
        return 40 * 1024 * 1024

    def audit(self):
        return {
            "context_tokens": self.context_tokens,
            "input_fraction": self.input_fraction,
            "input_token_budget": self.input_token_budget,
            "model_output_token_limit": None,
            "context_capacity_basis": (
                "observed_internal_service_error"
                if self.context_tokens == REPORTED_CONTEXT_TOKENS
                else "operator_configuration"
            ),
            "context_capacity_scope": "internal_route_at_observation_time",
            "reported_context_tokens": REPORTED_CONTEXT_TOKENS,
            "reported_capacity_model": OBSERVED_MODEL,
            "reported_capacity_observed_at": OBSERVED_AT,
            "reported_capacity_time_zone": "not_provided_in_log",
            "reported_rejected_input_tokens": REJECTED_INPUT_TOKENS,
            "context_capacity_probe_performed": False,
            "token_estimation_basis": TOKEN_ESTIMATION_BASIS,
            "token_count_exact": False,
            "token_estimate_is_verified_upper_bound": False,
            "structural_max_input_bytes": self.input_bytes,
            "structural_max_response_bytes": self.output_bytes,
        }
