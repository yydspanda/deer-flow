"""提取单次接收告警的规则观察；不连接服务，不进行跨请求计数。"""

import hashlib
import json
from datetime import datetime, timezone

from .csv_config_source import AmbiguousRuleError, RuleNotFoundError

MAX_HITS = 1000


def _text(value):
    if isinstance(value, str):
        value = value.strip()
        if 1 <= len(value) <= 512 and value != "待定":
            return value
    return None


def _identifier(value):
    if type(value) is int and value >= 0:
        value = str(value)
    return _text(value)


def normalize_received_alert(payload, rules, *, received_at, mode="demo", company_code=None):
    """按可信配置目录唯一匹配规则归属；显式公司只接受调用上下文，不读取请求或资产公司。"""
    if not isinstance(received_at, datetime) or received_at.utcoffset() is None:
        raise ValueError("received_at 必须是带时区的 datetime")
    if not isinstance(mode, str) or mode not in {"live", "replay", "demo"}:
        raise ValueError("mode 必须是 live、replay 或 demo")
    if company_code is not None:
        company_code = _text(company_code)
        if company_code is None:
            raise ValueError("company_code 必须是非空企业编码")
    timestamp = received_at.astimezone(timezone.utc).isoformat()
    result = {
        "schema_version": "sec_know.rule_observations.v1",
        "status": "skipped",
        "observations": [],
        "issues": [],
        "duplicate_hit_count": 0,
    }

    def issue(code, location):
        result["issues"].append({"code": code, "location": location})

    if not isinstance(payload, dict):
        issue("invalid_request", "request")
        return result
    source_app = _text(payload.get("app_code"))
    alert_id = _identifier(payload.get("alert_id"))
    if source_app is None or alert_id is None:
        issue(
            "invalid_app_code" if source_app is None else "invalid_alert_id",
            "app_code" if source_app is None else "alert_id",
        )
        return result
    data = payload.get("alert_data")
    if not isinstance(data, dict):
        issue("missing_alert_data", "alert_data")
        return result
    alert = data.get("alert")
    if not isinstance(alert, dict):
        issue("invalid_alert", "alert_data.alert")
        return result
    inner_id = _identifier(alert.get("alertId"))
    if inner_id is None:
        issue("invalid_alert_id", "alert_data.alert.alertId")
        return result
    if inner_id != alert_id:
        issue("alert_identity_mismatch", "alert_data.alert.alertId")
        return result
    hits = alert.get("hitLog")
    location = "alert_data.alert.hitLog"
    if hits is None or hits == []:
        issue("missing_hit_logs", location)
        return result
    if not isinstance(hits, list):
        issue("invalid_hit_logs", location)
        return result
    if len(hits) > MAX_HITS:
        issue("too_many_hit_logs", location)
        return result
    seen = set()
    for index, hit in enumerate(hits):
        item_location = f"{location}[{index}]"
        if not isinstance(hit, dict):
            issue("invalid_hit", item_location)
            continue
        topic = _text(hit.get("topic"))
        rule_code = _text(hit.get("ruleCode"))
        if topic is None or rule_code is None:
            issue("invalid_rule_identity", item_location)
            continue
        identity = (topic, rule_code)
        if identity in seen:
            result["duplicate_hit_count"] += 1
            continue
        seen.add(identity)
        try:
            rule = rules.get_rule(rule_code, company_code=company_code, topic=topic)
        except RuleNotFoundError:
            issue("rule_not_found", item_location)
            continue
        except AmbiguousRuleError:
            issue("rule_ambiguous", item_location)
            continue
        key_parts = ["sec_know.rule_occurrence.v1", mode, source_app, rule["company_code"], topic, rule_code, alert_id]
        key_bytes = json.dumps(key_parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        result["observations"].append(
            {
                "source_app": source_app,
                "company_code": rule["company_code"],
                "company_scope_source": ("explicit" if company_code is not None else "csv_unique_match"),
                "topic": topic,
                "rule_code": rule_code,
                "rule_name": rule["rule_name"],
                "rule_config_version": rule["rule_config_version"],
                "rule_source": dict(rule["source"]),
                "alert_id": alert_id,
                "received_at": timestamp,
                "mode": mode,
                "occurrence_key": "sha256:" + hashlib.sha256(key_bytes).hexdigest(),
            }
        )
    if result["observations"]:
        result["status"] = "partial" if result["issues"] else "ready"
    return result
