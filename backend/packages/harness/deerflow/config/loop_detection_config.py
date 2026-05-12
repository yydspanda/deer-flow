# yyds: 循环检测中间件配置，防止 agent 陷入重复工具调用的死循环
# yyds: 双层阈值设计：warn_threshold 注入警告提示，hard_limit 强制中断执行
# yyds: 支持按工具名单独配置频率阈值（tool_freq_overrides），如 bash 可设置更高上限
"""Configuration for loop detection middleware."""

from pydantic import BaseModel, Field, model_validator


class ToolFreqOverride(BaseModel):
    """yyds: 单个工具的频率阈值覆盖 — 让特定工具使用不同的警告/停止阈值。

    为什么需要？某些工具天然就会高频调用（如 bash 在批处理流水线里），
    如果用全局的 30/50 阈值会误杀。通过 tool_freq_overrides 给它单独设更高的值。

    示例（config.yaml）：
      loop_detection:
        tool_freq_overrides:
          bash:
            warn: 100
            hard_limit: 150
    """

    warn: int = Field(ge=1)
    hard_limit: int = Field(ge=1)

    @model_validator(mode="after")
    def _validate(self) -> "ToolFreqOverride":
        if self.hard_limit < self.warn:
            raise ValueError("hard_limit must be >= warn")
        return self


class LoopDetectionConfig(BaseModel):
    """yyds: 循环检测中间件的完整配置 — 对应 config.yaml 的 loop_detection 节。

    参数分两组：
      精确匹配组（第一层检测）：
        - warn_threshold(3): 同一组工具调用重复 N 次后警告
        - hard_limit(5): 重复 N 次后强制停止
        - window_size(20): 滑动窗口大小（只看最近 N 次调用）
      频率统计组（第二层检测）：
        - tool_freq_warn(30): 同一工具（不管参数）调用 N 次后警告
        - tool_freq_hard_limit(50): 调用 N 次后强制停止
        - tool_freq_overrides: 按工具名单独覆盖频率阈值

    校验规则（model_validator）：
      - hard_limit 必须 ≥ warn_threshold（不能还没警告就停了）
      - tool_freq_hard_limit 必须 ≥ tool_freq_warn
    """

    enabled: bool = Field(
        default=True,
        description="Whether to enable repetitive tool-call loop detection",
    )
    warn_threshold: int = Field(
        default=3,
        ge=1,
        description="Number of identical tool-call sets before injecting a warning",
    )
    hard_limit: int = Field(
        default=5,
        ge=1,
        description="Number of identical tool-call sets before forcing a stop",
    )
    window_size: int = Field(
        default=20,
        ge=1,
        description="Number of recent tool-call sets to track per thread",
    )
    max_tracked_threads: int = Field(
        default=100,
        ge=1,
        description="Maximum number of thread histories to keep in memory",
    )
    tool_freq_warn: int = Field(
        default=30,
        ge=1,
        description="Number of calls to the same tool type before injecting a frequency warning",
    )
    tool_freq_hard_limit: int = Field(
        default=50,
        ge=1,
        description="Number of calls to the same tool type before forcing a stop",
    )
    tool_freq_overrides: dict[str, ToolFreqOverride] = Field(
        default_factory=dict,
        description=("Per-tool overrides for tool_freq_warn / tool_freq_hard_limit, keyed by tool name. Values can be higher or lower than the global defaults. Commonly used to raise thresholds for high-frequency tools like bash."),
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> "LoopDetectionConfig":
        """yyds: 确保强制停止阈值 ≥ 警告阈值 — 不能还没警告就直接停了。"""
        if self.hard_limit < self.warn_threshold:
            raise ValueError("hard_limit must be greater than or equal to warn_threshold")
        if self.tool_freq_hard_limit < self.tool_freq_warn:
            raise ValueError("tool_freq_hard_limit must be greater than or equal to tool_freq_warn")
        return self
