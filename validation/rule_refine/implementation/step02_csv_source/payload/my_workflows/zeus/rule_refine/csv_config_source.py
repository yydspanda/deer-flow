"""从 CSV 快照读取规则，后续可由 ZEUS 查询实现替换。"""

import copy
import csv
import hashlib
import io
import json
import math
from pathlib import Path


class CsvRuleConfigError(ValueError):
    pass


class RuleNotFoundError(CsvRuleConfigError):
    pass


class AmbiguousRuleError(CsvRuleConfigError):
    pass


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON存在重复字段")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError("JSON包含非标准数值")


def _finite_float(text):
    value = float(text)
    if not math.isfinite(value):
        raise ValueError("JSON数值超出可表示范围")
    return value


def _parse_json(raw, expected_type, location, nullable=False):
    if nullable and raw in (None, ""):
        return None
    try:
        if not isinstance(raw, str):
            raise ValueError("需要JSON文本")
        value = json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_invalid_constant, parse_float=_finite_float)
        if value is None and nullable:
            return None
        if not isinstance(value, expected_type):
            raise ValueError("JSON容器类型不正确")
        return value
    except (ValueError, TypeError, RecursionError):
        raise CsvRuleConfigError(f"{location}：JSON格式或类型不正确") from None


def _configuration(row, ordinal):
    result = dict(row)
    location = f"第{ordinal}条记录"
    terms = _parse_json(row["terms"], list, location + ".terms")
    for index, term in enumerate(terms):
        if not isinstance(term, dict):
            raise CsvRuleConfigError(f"{location}.terms[{index}]：必须为对象")
        term_location = f"{location}.terms[{index}]"
        term["config"] = _parse_json(term.get("config"), dict, term_location + ".config")
        if "standard_field_config" in term:
            term["standard_field_config"] = _parse_json(term["standard_field_config"], dict, term_location + ".standard_field_config", nullable=True)
    result["terms"] = terms
    result["standard_field_config"] = _parse_json(row["standard_field_config"], dict, location + ".standard_field_config", nullable=True)
    return result


class CsvRuleConfigSource:
    def __init__(self, path):
        path = Path(path)
        raw = path.read_bytes()
        self.file_sha256 = hashlib.sha256(raw).hexdigest()
        self._entries = []
        self._by_code = {}
        required = {"company_code", "topic", "rule_code", "rule_name", "terms", "standard_field_config"}
        try:
            reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
            headers = reader.fieldnames or []
            if len(headers) != len(set(headers)) or not required.issubset(headers):
                raise CsvRuleConfigError("CSV缺少必需列或存在重复列名")
            for ordinal, row in enumerate(reader, 1):
                if None in row or any(value is None for value in row.values()):
                    raise CsvRuleConfigError(f"第{ordinal}条记录：列数不正确")
                if any(not row[key].strip() for key in ("company_code", "topic", "rule_code")):
                    raise CsvRuleConfigError(f"第{ordinal}条记录：企业、topic或规则编号为空")
                canonical = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                entry = {
                    "company_code": row["company_code"],
                    "topic": row["topic"],
                    "rule_code": row["rule_code"],
                    "rule_name": row["rule_name"],
                    "rule_config_version": "csv-row-v1:sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
                    "raw_record": dict(row),
                    "configuration": _configuration(row, ordinal),
                    "source": {
                        "kind": "csv_snapshot",
                        "file_name": path.name,
                        "file_sha256": self.file_sha256,
                        "data_record_1based": ordinal,
                    },
                }
                self._entries.append(entry)
                self._by_code.setdefault(row["rule_code"], []).append(entry)
        except (UnicodeDecodeError, csv.Error):
            raise CsvRuleConfigError("文件不是有效的UTF-8 CSV") from None
        if not self._entries:
            raise CsvRuleConfigError("CSV没有规则记录")

    @property
    def count(self):
        return len(self._entries)

    def list_rules(self):
        return [{key: entry[key] for key in ("company_code", "topic", "rule_code", "rule_name")} for entry in self._entries]

    def get_rule(self, rule_code, *, company_code=None, topic=None):
        for value in (rule_code, company_code, topic):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise CsvRuleConfigError("查询条件必须是非空字符串")
        if rule_code is None:
            raise CsvRuleConfigError("必须提供规则编号")
        matches = [entry for entry in self._by_code.get(rule_code, []) if (company_code is None or entry["company_code"] == company_code) and (topic is None or entry["topic"] == topic)]
        if not matches:
            raise RuleNotFoundError("未找到符合指定范围的规则")
        if len(matches) != 1:
            raise AmbiguousRuleError("规则不唯一：请明确企业和topic；若已指定，检查重复配置记录")
        return copy.deepcopy(matches[0])


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="只读检查CSV规则配置")
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rule")
    parser.add_argument("--company")
    parser.add_argument("--topic")
    args = parser.parse_args()
    try:
        source = CsvRuleConfigSource(args.csv)
        summary = {"loaded_rules": source.count, "source": "csv_snapshot", "file_sha256": source.file_sha256}
        if args.rule:
            rule = source.get_rule(args.rule, company_code=args.company, topic=args.topic)
            summary["selected_rule"] = {key: rule[key] for key in ("rule_code", "rule_name", "topic", "rule_config_version")}
            summary["selected_rule"]["term_count"] = len(rule["configuration"]["terms"])
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    except (CsvRuleConfigError, OSError) as exc:
        parser.exit(1, f"error: {exc}\n")
