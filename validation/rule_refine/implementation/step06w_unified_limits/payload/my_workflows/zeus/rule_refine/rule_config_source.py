"""规则配置查询契约；CSV 与未来 ZEUS API 适配器共享，不绑定传输方式。"""

from typing import Protocol


class RuleConfigError(ValueError):
    pass


class RuleNotFoundError(RuleConfigError):
    pass


class AmbiguousRuleError(RuleConfigError):
    pass


class RuleConfigSource(Protocol):
    def get_rule(
        self,
        rule_code: str,
        *,
        company_code: str | None = None,
        topic: str | None = None,
    ) -> dict:
        """返回唯一匹配；零条/多条分别抛出上述异常，来源故障原样传播。

        结果包含 company_code、topic、rule_code、rule_name、rule_config_version
        和 source.kind；适配器保留原始配置及可核验来源，不冒充已查询 ZEUS。
        """
        ...


def validate_rule_match(rule, *, rule_code, topic, company_code=None):
    """在形成观察前校验提供器返回，错误响应不能混入其他公司或规则。"""
    if not isinstance(rule, dict):
        raise RuleConfigError("规则查询结果必须为对象")
    for key in ("company_code", "topic", "rule_code", "rule_config_version"):
        value = rule.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            raise RuleConfigError("规则查询结果的身份或版本字段无效：" + key)
    if rule["topic"] != topic or rule["rule_code"] != rule_code:
        raise RuleConfigError("规则查询结果与请求身份不一致")
    if company_code is not None and rule["company_code"] != company_code:
        raise RuleConfigError("规则查询结果超出指定企业范围")
    if (
        not isinstance(rule.get("rule_name"), str)
        or not 1 <= len(rule["rule_name"].strip()) <= 4096
    ):
        raise RuleConfigError("规则查询结果缺少规则名称")
    source = rule.get("source")
    if (
        not isinstance(source, dict)
        or not isinstance(source.get("kind"), str)
        or not source["kind"].strip()
    ):
        raise RuleConfigError("规则查询结果缺少配置来源")
