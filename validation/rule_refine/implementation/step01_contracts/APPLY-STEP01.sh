bash <<'SOC_STEP01'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
from importlib.metadata import version
from pathlib import Path

root = Path.cwd()
if not (root / "service/io_models.py").is_file() or not (root / "my_workflows/zeus").is_dir():
    raise SystemExit("停止：请在 sec_know_model 根目录执行")
if version("pydantic").split(".")[0] != "2":
    raise SystemExit("停止：此步骤需要 Pydantic 2")
files = {
    'my_workflows/zeus/rule_refine/__init__.py': r'''"""规则突增分析与过滤建议。"""
''',
    'my_workflows/zeus/rule_refine/contracts.py': r'''"""第一步：新增排除建议的格式校验；不执行表达式或应用规则。"""

import re
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=512)]
Description = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=4096)]
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
        if not 1 <= len(aliases) <= 32 or any(not ALIAS.fullmatch(key) for key in aliases):
            raise ValueError("需要1至32个条件，别名使用大写字母开头的字母、数字或下划线")
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


class RuleRecommendation(StrictModel):
    schema_version: Literal["sec_know.rule_filter_recommendation.v1"] = "sec_know.rule_filter_recommendation.v1"
    recommendation_id: UUID = Field(default_factory=uuid4)
    company_code: Text
    rule_code: Text
    rule_name: Text
    rule_config_version: Text
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
''',
    'test/rule_refine/test_contracts.py': r'''"""仅测试规则建议契约，不加载原应用、数据库或模型。"""

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

from pydantic import ValidationError

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine/contracts.py"
spec = importlib.util.spec_from_file_location("_rule_refine_contracts", source)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
RuleRecommendation = module.RuleRecommendation
FilterDefinition = module.FilterDefinition


def sample():
    return {
        "company_code": "demo",
        "rule_code": "DEMO_RULE",
        "rule_name": "人工测试规则",
        "rule_config_version": "demo-v1",
        "issue": "人工构造的重复场景，用于格式测试",
        "recommendation": "确认正常业务后，评估此组合排除条件",
        "certainty": "人工示例，不是实际告警结论",
        "evidence": {
            "window_start": "2026-10-27T09:00:00+08:00",
            "window_end": "2026-10-27T10:00:00+08:00",
            "alert_count": 2,
            "sample_alert_ids": ["DEMO-1", "DEMO-2"],
        },
        "filter": {
            "topic": "demo_topic",
            "A": {"name": "host", "value": "example.invalid", "operator": "equal"},
            "B": {"name": "api", "value": "/health", "operator": "contain"},
            "C": {"name": "method", "value": "GET", "operator": "equal"},
            "expression": "A&&(B||C)",
        },
    }


class ContractTests(unittest.TestCase):
    def test_flat_json_roundtrip(self):
        record = RuleRecommendation.model_validate(sample())
        data = json.loads(record.model_dump_json())
        self.assertEqual(data["filter"]["B"]["value"], "/health")
        self.assertFalse(data["automatic_apply"])
        self.assertTrue(data["recommendation_id"])
        self.assertEqual(RuleRecommendation.model_validate_json(record.model_dump_json()), record)

    def test_condition_value_is_not_trimmed(self):
        data = sample()
        data["filter"]["A"]["value"] = " value with spaces "
        self.assertEqual(RuleRecommendation.model_validate(data).model_dump()["filter"]["A"]["value"], " value with spaces ")

    def test_bad_expressions(self):
        for expression in ("", "()", "A B", "A&&", "||A", "(A", "A)", "!A", "A;foo()", "A||Z", "A & B", "A(B)", "(" * 33 + "A" + ")" * 33):
            with self.subTest(expression=expression):
                data = sample()["filter"]
                data["expression"] = expression
                with self.assertRaises(ValidationError):
                    FilterDefinition.model_validate(data)

    def test_bad_condition(self):
        for update in ({"operator": "execute"}, {"value": 123}, {"name": " "}, {"value": "", "operator": "contain"}, {"value": "", "operator": "notcontain"}, {"unexpected": True}):
            with self.subTest(update=update):
                data = sample()["filter"]
                data["A"].update(update)
                with self.assertRaises(ValidationError):
                    FilterDefinition.model_validate(data)

    def test_bad_alias_and_missing_conditions(self):
        for data in ({"topic": "demo", "expression": "A"}, {**sample()["filter"], "bad_key": sample()["filter"]["A"]}):
            with self.subTest(data=data):
                with self.assertRaises(ValidationError):
                    FilterDefinition.model_validate(data)

    def test_automatic_apply_must_be_false(self):
        for value in (True, 0, "false", None):
            with self.subTest(value=value):
                data = sample()
                data["automatic_apply"] = value
                with self.assertRaises(ValidationError):
                    RuleRecommendation.model_validate(data)

    def test_other_change_type_is_not_silently_accepted(self):
        data = sample()
        data["change_type"] = "modify_conditions"
        with self.assertRaises(ValidationError):
            RuleRecommendation.model_validate(data)

    def test_bad_evidence(self):
        changes = ({"alert_count": -1}, {"alert_count": True}, {"alert_count": "2"}, {"alert_count": 1}, {"sample_alert_ids": []}, {"sample_alert_ids": ["DEMO-1", "DEMO-1"]}, {"window_end": "2026-10-27T08:00:00+08:00"}, {"window_start": "2026-10-27T09:00:00"})
        for update in changes:
            with self.subTest(update=update):
                data = sample()
                data["evidence"].update(copy.deepcopy(update))
                with self.assertRaises(ValidationError):
                    RuleRecommendation.model_validate(data)

    def test_missing_identity_and_extra_fields(self):
        for key in ("company_code", "rule_code", "rule_config_version", "evidence"):
            with self.subTest(key=key):
                data = sample()
                del data[key]
                with self.assertRaises(ValidationError):
                    RuleRecommendation.model_validate(data)
        with self.assertRaises(ValidationError):
            RuleRecommendation.model_validate({**sample(), "unexpected": True})

    def test_schema_can_be_exported(self):
        schema = RuleRecommendation.model_json_schema()
        self.assertIn("FilterDefinition", schema["$defs"])
        json.dumps(schema)


if __name__ == "__main__":
    unittest.main(verbosity=2)
''',
}
for name, content in files.items():
    target = root / name
    if target.exists() and (not target.is_file() or target.read_text(encoding="utf-8") != content):
        raise SystemExit("停止：存在不同内容，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("内容一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_contracts.py
SOC_STEP01
