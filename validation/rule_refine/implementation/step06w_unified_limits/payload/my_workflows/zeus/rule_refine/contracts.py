"""第一步：新增排除建议的格式校验；不执行表达式或应用规则。"""

import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Text = Annotated[
    str,
    StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=512),
]
Description = Annotated[
    str,
    StringConstraints(
        strict=True, strip_whitespace=True, min_length=1, max_length=4096
    ),
]
ALIAS = re.compile(r"[A-Z][A-Z0-9_]{0,31}\Z")
TOKEN = re.compile(r"\s*([A-Z][A-Z0-9_]*|&&|\|\||[()])")


def validate_expression(expression: str, aliases: set[str]) -> None:
    """只检查完整语法和条件引用，不进行求值。"""
    expression = expression.strip()
    pos, depth, token_count, expect_operand = 0, 0, 0, True
    while pos < len(expression):
        match = TOKEN.match(expression, pos)
        if match is None:
            raise ValueError("表达式含不支持的字符")
        token = match.group(1)
        pos = match.end()
        token_count += 1
        if token_count > 256:
            raise ValueError("表达式过长")
        if token == "(":
            if not expect_operand or depth >= 32:
                raise ValueError("括号位置不正确或嵌套过深")
            depth += 1
        elif token == ")":
            if expect_operand or depth == 0:
                raise ValueError("括号不匹配或缺少条件")
            depth -= 1
        elif token in {"&&", "||"}:
            if expect_operand:
                raise ValueError("运算符前缺少条件")
            expect_operand = True
        else:
            if not expect_operand or token not in aliases:
                raise ValueError("缺少运算符或引用了未定义条件")
            expect_operand = False
    if expect_operand or depth:
        raise ValueError("表达式不完整")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FilterCondition(StrictModel):
    name: Text
    value: str = Field(strict=True, max_length=4096)
    operator: Literal["equal", "contain", "notcontain"]

    @model_validator(mode="after")
    def check_value(self):
        if self.operator != "equal" and self.value == "":
            raise ValueError("包含/不包含条件的值不能为空")
        return self


class FilterDefinition(BaseModel):
    model_config = ConfigDict(extra="allow")
    __pydantic_extra__: dict[str, FilterCondition] = Field(init=False)
    topic: Text
    expression: str = Field(strict=True, min_length=1, max_length=2048)

    @model_validator(mode="after")
    def check_expression(self):
        aliases = set(self.__pydantic_extra__ or {})
        if not 1 <= len(aliases) <= 32 or any(
            not ALIAS.fullmatch(key) for key in aliases
        ):
            raise ValueError(
                "需要1至32个条件，别名使用大写字母开头的字母、数字或下划线"
            )
        validate_expression(self.expression, aliases)
        return self


class Evidence(StrictModel):
    window_start: AwareDatetime
    window_end: AwareDatetime
    alert_count: int = Field(strict=True, ge=1)
    count_basis: Literal["received_unique_alerts"] = "received_unique_alerts"
    sample_alert_ids: list[Text] = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def check_window_and_samples(self):
        if self.window_end <= self.window_start:
            raise ValueError("统计结束时间必须晚于开始时间")
        if len(set(self.sample_alert_ids)) != len(self.sample_alert_ids):
            raise ValueError("样本告警ID不能重复")
        if len(self.sample_alert_ids) > self.alert_count:
            raise ValueError("样本数不能大于统计告警数")
        return self


class RuleIdentity(StrictModel):
    company_code: Text
    rule_code: Text
    rule_name: Description
    rule_config_version: Text


class RuleRecommendation(RuleIdentity):
    schema_version: Literal["sec_know.rule_filter_recommendation.v1"] = (
        "sec_know.rule_filter_recommendation.v1"
    )
    recommendation_id: UUID = Field(default_factory=uuid4)
    issue: Description
    recommendation: Description
    certainty: Description
    evidence: Evidence
    change_type: Literal["add_exclusion"] = "add_exclusion"
    filter: FilterDefinition
    automatic_apply: Literal[False] = False

    @field_validator("automatic_apply", mode="before")
    @classmethod
    def forbid_automatic_apply(cls, value):
        if value is not False:
            raise ValueError("automatic_apply必须为布尔值false")
        return value
