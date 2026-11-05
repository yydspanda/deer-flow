# -*- coding: utf-8 -*-
"""
小规则同步客户端

通过 ISEC 网关调用规则中心的 syncSmallRule 接口（/sapi/v1/smallrule/_sync），批量新增小规则。

请求体为规则列表，每条规则包含 topic、expression、executeType，以及若干规则条款（如 A、B）。
除 topic/expression/executeType 之外的字段均视为规则条款，条款需包含 name/value/operator。

使用示例：
    client = SmallRuleSyncClient()
    result = client.sync_small_rule(
        [
            {
                "topic": "SYNTHETIC_TEST_TOPIC",
                "A": {"name": "event_content", "value": "synthetic inventory operation", "operator": "Eq"},
                "expression": "A",
                "executeType": [1],
            }
        ]
    )
"""

import json
import re
from typing import Any, Dict, List, Optional

import aiohttp
import requests

from my_workflows.zeus.rule_refine.operators import OPERATORS
from util.log_config import get_logger
from util.root_config import ZEUS_APP_ID, ZEUS_APP_KEY, ZEUS_SYSTEM_URL
from util.util_tools import isec_sign

logger = get_logger()

SYNC_SMALL_RULE_PATH = "/public/syncSmallRule"

# 非条款的保留字段
RESERVED_FIELDS = {"topic", "expression", "executeType"}

# 合法的操作符
VALID_OPERATORS = OPERATORS

# executeType 合法取值: -1-全部; 0-生产; 1-测试; 2-验证; 3-告警
VALID_EXECUTE_TYPES = {-1, 0, 1, 2, 3}

# 表达式中的变量名（字母开头，字母数字下划线）
TERM_VAR_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# 响应码与含义
SMALL_RULE_ERROR_MESSAGES = {
    69204: "规则内容不是合法的JSON格式",
    69205: "缺少必填字段 topic 或 expression",
    69206: "表达式中的变量没有对应的条件定义",
    69207: "条件缺少 name/value/operator",
    69208: "operator 不合法，仅支持 Eq/Ne/Contains/NotContains/StartsWith/EndsWith",
    69211: "topic不能为空",
    69212: "executeType不能为空",
    69213: "executeType不合法",
    69214: "term.name不能为空",
    69215: "term.value不能为空",
    69216: "expression引用了不存在的条件term",
    69217: "expression未引用的多余term",
    69218: "小规则不存在",
    69219: "新增不允许携带id",
    69220: "同步规则的公司配置缺失",
}


def _validate_rule_operators(rule: Dict[str, Any]) -> None:
    """发送新接口枚举；关闭其余预校验也不能发送旧值或未知值。"""
    if not isinstance(rule, dict):
        raise ValueError("小规则必须是 JSON 对象")
    terms = {k: v for k, v in rule.items() if k not in RESERVED_FIELDS}
    if not terms:
        raise ValueError("至少需要一个规则条款（如 A）")
    for term_name, term in terms.items():
        if not isinstance(term, dict):
            raise ValueError(f"条款 {term_name} 必须是对象")
        operator = term.get("operator")
        if not isinstance(operator, str) or operator not in VALID_OPERATORS:
            raise ValueError(
                f"条款 {term_name} 的 operator 不合法，仅支持 {sorted(VALID_OPERATORS)}"
            )


def validate_small_rule(rule: Dict[str, Any]) -> None:
    """
    客户端侧校验单条小规则，避免无谓的网关调用。

    校验项：topic 非空、executeType 合法、expression 非空、条款完整、
    expression 与条款变量互相匹配、operator 合法。

    Raises:
        ValueError: 校验失败
    """
    if not isinstance(rule, dict):
        raise ValueError("小规则必须是 JSON 对象")

    topic = rule.get("topic")
    if not topic or not isinstance(topic, str):
        raise ValueError("topic 不能为空")

    execute_type = rule.get("executeType")
    if not execute_type or not isinstance(execute_type, list):
        raise ValueError("executeType 不能为空且必须为列表")
    invalid_types = [t for t in execute_type if t not in VALID_EXECUTE_TYPES]
    if invalid_types:
        raise ValueError(f"executeType 不合法: {invalid_types}，仅支持 -1/0/1/2/3")

    expression = rule.get("expression")
    if not expression or not isinstance(expression, str):
        raise ValueError("expression 不能为空")

    terms = {k: v for k, v in rule.items() if k not in RESERVED_FIELDS}
    if not terms:
        raise ValueError("至少需要一个规则条款（如 A）")

    for term_name, term in terms.items():
        if not isinstance(term, dict):
            raise ValueError(f"条款 {term_name} 必须是对象")
        for field in ("name", "value", "operator"):
            if not term.get(field):
                raise ValueError(f"条款 {term_name} 缺少 {field}")
    _validate_rule_operators(rule)

    # expression 引用的变量与条款定义必须一一对应
    expr_vars = set(TERM_VAR_PATTERN.findall(expression))
    term_names = set(terms.keys())
    undefined = expr_vars - term_names
    if undefined:
        raise ValueError(f"expression 引用了未定义的条款: {sorted(undefined)}")
    unreferenced = term_names - expr_vars
    if unreferenced:
        raise ValueError(f"expression 未引用的多余条款: {sorted(unreferenced)}")


class SmallRuleSyncClient:
    """小规则同步客户端（ISEC 网关鉴权与既有接口一致）"""

    def __init__(self, system_url: Optional[str] = None):
        self.system_url = system_url or ZEUS_SYSTEM_URL

    def _build_request(
        self, rules: List[Dict[str, Any]], validate: bool = True
    ) -> Dict[str, Any]:
        """校验并组装请求头（请求体为规则列表，签名基于列表的 JSON 序列化）"""
        if not rules:
            raise ValueError("请求体不能为空列表")
        for i, rule in enumerate(rules):
            try:
                if validate:
                    validate_small_rule(rule)
                else:
                    _validate_rule_operators(rule)
            except ValueError as e:
                raise ValueError(f"第 {i + 1} 条规则校验失败: {e}") from e

        # 与 isec_sign 内部 json.dumps 保持一致的序列化方式，保证签名与报文一致
        payload_str = json.dumps(rules)
        headers = isec_sign(data=rules, app_id=ZEUS_APP_ID, app_key=ZEUS_APP_KEY)
        headers["Content-Type"] = "application/json"
        return {"payload_str": payload_str, "headers": headers}

    @staticmethod
    def _parse_response(full_response: Dict[str, Any]) -> Dict[str, Any]:
        """解析网关响应，标准化返回"""
        code = full_response.get("code")
        message = full_response.get("message", "")
        if code == 200:
            return {
                "success": True,
                "code": code,
                "message": message,
                "rule_id": full_response.get("data"),
                "raw": full_response,
            }

        known = SMALL_RULE_ERROR_MESSAGES.get(code if isinstance(code, int) else -1)
        if known:
            message = f"{message}（{known}）" if known not in message else message
        logger.error(f"同步小规则失败: code={code}, message={message}")
        return {
            "success": False,
            "code": code,
            "message": message,
            "rule_id": None,
            "raw": full_response,
        }

    def sync_small_rule(
        self,
        rules: List[Dict[str, Any]],
        validate: bool = True,
        debug: bool = False,
        timeout: int = 10,
    ) -> Dict[str, Any]:
        """
        同步新增小规则（批量）

        Args:
            rules: 规则列表，每项包含 topic、expression、executeType 及条款（A/B/...）
            validate: 是否完整客户端侧预校验，默认 True；operator 始终校验
            debug: 是否打印调试信息
            timeout: 请求超时时间（秒）

        Returns:
            标准化结果: {"success": bool, "code": int, "message": str, "rule_id": 规则Id, "raw": 原始响应}
        """
        request = self._build_request(rules, validate=validate)

        if debug:
            print(
                f"\n{'=' * 50}\n[同步小规则] 请求报文:\n{request['payload_str']}\n{'=' * 50}"
            )

        # data 使用与签名一致的字符串，避免 JSON 二次序列化导致签名不一致
        response = requests.post(
            f"{self.system_url}/public/syncSmallRule",
            data=request["payload_str"].encode("utf-8"),
            headers=request["headers"],
            timeout=timeout,
        )
        response.raise_for_status()
        full_response = response.json()

        if debug:
            print(
                f"[同步小规则] 原始响应:\n{json.dumps(full_response, ensure_ascii=False, indent=2)}"
            )

        result = self._parse_response(full_response)
        logger.info(
            f"同步小规则完成: {rules[0].get('topic') if rules else ''} -> code={result['code']}, rule_id={result['rule_id']}"
        )
        return result

    async def async_small_rule(
        self, rules: List[Dict[str, Any]], validate: bool = True, timeout: int = 10
    ) -> Dict[str, Any]:
        """异步新增小规则（批量），参数说明同 sync_small_rule"""
        request = self._build_request(rules, validate=validate)

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.post(
                f"{self.system_url}/public/syncSmallRule",
                data=request["payload_str"].encode("utf-8"),
                headers=request["headers"],
            ) as response:
                response.raise_for_status()
                full_response = await response.json()

        result = self._parse_response(full_response)
        logger.info(
            f"异步同步小规则完成: code={result['code']}, rule_id={result['rule_id']}"
        )
        return result


if __name__ == "__main__":
    # 人工合成示例，仅在手工运行本文件时提交；不是历史业务材料。
    client = SmallRuleSyncClient()
    DEMO_RULES = [
        {
            "topic": "SYNTHETIC_TEST_TOPIC",
            "A": {
                "name": "synthetic_event_content",
                "value": "synthetic inventory operation",
                "operator": operator,
            },
            "expression": "A",
            "executeType": [1],
        }
        for operator in (
            "Eq",
            "Ne",
            "Contains",
            "NotContains",
            "StartsWith",
            "EndsWith",
        )
    ]

    for i, rule in enumerate(DEMO_RULES, start=1):
        print(
            f"\n>>> 测试{i}: 新增小规则 topic={rule['topic']} expression={rule['expression']}"
        )
        result = client.sync_small_rule([rule], debug=True)
        print(json.dumps(result, ensure_ascii=False, indent=2))
