"""小规则当前操作符；旧写法仅用于有版本依据的历史材料投影。"""

import copy
from typing import Literal, get_args

Operator = Literal["Eq", "Ne", "Contains", "NotContains", "StartsWith", "EndsWith"]
OPERATORS = frozenset(get_args(Operator))
OPERATOR_CONTRACT_VERSION = "small-rule-operators-v2"

# 不用于新回答校验或提交：历史原文保留，在明确的历史读取入口复制转换。
LEGACY_OPERATORS = {
    "equal": "Eq",
    "notequal": "Ne",
    "contain": "Contains",
    "notcontain": "NotContains",
    "startWith": "StartsWith",
    "endWith": "EndsWith",
}


def convert_legacy_filter(definition):
    """只转换条款 operator，不递归改写日志、条件 value、说明或原对象。"""
    result = copy.deepcopy(definition)
    if result is None:
        return None
    if not isinstance(result, dict):
        raise ValueError("invalid_saved_filter")
    for alias, condition in result.items():
        if alias in {"topic", "expression", "executeType"}:
            continue
        if not isinstance(condition, dict):
            raise ValueError("invalid_saved_condition")
        operator = condition.get("operator")
        if not isinstance(operator, str):
            raise ValueError("invalid_saved_operator")
        if operator in LEGACY_OPERATORS:
            condition["operator"] = LEGACY_OPERATORS[operator]
        elif operator not in OPERATORS:
            raise ValueError("unsupported_saved_operator")
    return result
