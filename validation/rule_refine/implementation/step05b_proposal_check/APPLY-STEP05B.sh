bash <<'SOC_STEP05B'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYWRITE'
import hashlib
from pathlib import Path

root = Path.cwd()
dependencies = {'my_workflows/zeus/rule_refine/contracts.py': '59986ced58a5c951d4fb2fdb1ffafee106a370da7e5e893dbee24a1db292356a', 'my_workflows/zeus/rule_refine/rule_config_source.py': 'dbe4abef7543403fa34e57edc19d9f1d1ddd153b84638e6b5332036f0acfe941', 'my_workflows/zeus/rule_refine/materials.py': '75b9b44e15737b6cf8a8b594ac077a1a9f5b8405d8797d068f1e3e5451dd88d9', 'test/rule_refine/test_materials.py': '3045b6ddccdbc53260887eb5b2efa8726e1553cc196eb0b5f1595102659c0e76', 'my_workflows/zeus/rule_refine/analysis.py': '1946b9c0828a38679655d67c1d28677441211b2edf576c15bda8396decee9117', 'test/rule_refine/test_analysis.py': 'a956151f1d3b2615cf745f1c879566af469fb18d8e8d1418c024fd2cf01d5f5f', 'my_workflows/zeus/rule_refine/filter_evaluation.py': 'e47ec1171bb6ca5948f5b15fc0a8eaa158782f1d3910dbcad08d262eb8a615fe'}
for name, digest in dependencies.items():
    target = root / name
    if not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != digest:
        raise SystemExit("停止：前序文件不存在或版本不同，未修改：" + name)
files = {
    'my_workflows/zeus/rule_refine/proposal_check.py': r'''"""关联建议、模型可见材料和重新查询的配置；只生成供运营审阅的校验结果。"""

import copy

from .analysis import PROMPT_VERSION, build_analysis_request
from .contracts import Evidence, RuleRecommendation
from .filter_evaluation import evaluate_filter
from .materials import _encode
from .rule_config_source import validate_rule_match

NONPROPOSAL = {"no_change", "insufficient_evidence", "needs_existing_condition_change"}


def _coverage(materials, context):
    requested = materials["requested_sample_count"]
    samples = materials["samples"]
    if type(requested) is not int or not len(samples) <= requested <= min(20, context["window"]["count"]):
        raise ValueError("请求样本数量无效")
    projected = {sample["alert_id"]: len(sample["raw_logs"]) for sample in context["samples"]}
    rows = []
    for sample in samples:
        identity, logs = sample["alert_id"], sample["raw_logs"]
        if not 1 <= len(identity) <= 512 or identity != identity.strip() or len(logs) > 100:
            raise ValueError("输入样本超出本步范围")
        if any(not isinstance(log, dict) for log in logs):
            raise ValueError("日志必须为对象")
        shown = projected.get(identity, 0)
        rows.append(
            {"alert_id": identity, "input_logs": len(logs), "projected_logs": shown, "omitted_logs": len(logs) - shown}
        )
    return {
        "requested_alert_count": requested,
        "input_alert_count": len(samples),
        "projected_alert_count": len(projected),
        "unavailable_alert_count": requested - len(samples),
        "input_log_count": sum(row["input_logs"] for row in rows),
        "projected_log_count": sum(row["projected_logs"] for row in rows),
        "omitted_log_count": sum(row["omitted_logs"] for row in rows),
        "per_alert": rows,
    }


def _config_bytes(rule):
    if not isinstance(rule["configuration"], dict):
        raise ValueError("规则配置必须为对象")
    view = {
        key: rule[key]
        for key in ("company_code", "topic", "rule_code", "rule_name", "rule_config_version", "configuration")
    }
    view["source_kind"] = rule["source"]["kind"]
    return _encode(view, 512 * 1024)


def check_proposal(materials: dict, analysis_result: dict, load_rules) -> dict:
    """load_rules() 必须重新读取配置；不得传入复用旧快照的工厂。

    本函数不生成建议或调用模型。输入分析结果来自本项目生产者；摘要不是数字签名。
    reviewable 仅表示可交运营审阅，不代表业务正常、已审批或引擎兼容。
    """
    outcome = {
        "schema_version": "sec_know.rule_proposal_check.v1",
        "status": "blocked",
        "reason": "invalid_analysis",
        "recommendation": None,
        "scope": "model_projected_logs_only",
        "engine_compatibility": "not_verified",
        "field_catalog_check": "not_performed",
        "automatic_apply": False,
    }

    def stop(reason, **details):
        return {**outcome, "reason": reason, **details}

    try:
        _encode(analysis_result, 128 * 1024)
        if (
            not isinstance(analysis_result, dict)
            or analysis_result.get("schema_version") != "sec_know.rule_analysis.v1"
        ):
            return outcome
        status = analysis_result.get("status")
        if status not in NONPROPOSAL | {"proposal", "analysis_failed"}:
            return outcome
        if analysis_result.get("response_source") not in ("model", "synthetic_reply"):
            return outcome
        outcome["analysis_status"] = status
        outcome["response_source"] = analysis_result["response_source"]
        if status == "analysis_failed":
            return stop("analysis_failed")
        if status != "proposal" and analysis_result.get("recommendation") is not None:
            return outcome
    except (ValueError, TypeError, RecursionError):
        return outcome
    try:
        request = build_analysis_request(materials)
        if request is None:
            if (
                status == "insufficient_evidence"
                and type(analysis_result.get("generation_calls")) is int
                and analysis_result["generation_calls"] == 0
                and analysis_result.get("error_code") == "no_usable_material_or_rule_over_budget"
            ):
                return stop(status, status="not_applicable")
            return stop("no_usable_material")
        context = request["context"]
        outcome["coverage"] = _coverage(materials, context)
        outcome["material_issues"] = copy.deepcopy(context["material_issues"])
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return stop("invalid_materials")
    expected = {
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": request["prompt_sha256"],
        "material_mode": context["mode"],
        "material_source": context["material_source"],
        "rule_source": context["rule"]["source"],
    }
    if any(analysis_result.get(key) != value for key, value in expected.items()):
        return stop("prompt_mismatch")
    outcome.update({key: copy.deepcopy(value) for key, value in expected.items()})
    if status != "proposal":
        return stop(status, status="not_applicable")
    try:
        saved = analysis_result["recommendation"]
        if not isinstance(saved, dict) or "recommendation_id" not in saved:
            return stop("recommendation_mismatch")
        record = RuleRecommendation.model_validate(saved)
        rule = context["rule"]
        identities = ("company_code", "rule_code", "rule_name", "rule_config_version")
        refs = record.evidence.sample_alert_ids
        projected_ids = {sample["alert_id"] for sample in context["samples"]}
        evidence = Evidence(
            window_start=context["window"]["start"],
            window_end=context["window"]["end"],
            alert_count=context["window"]["count"],
            sample_alert_ids=refs,
        )
        if (
            any(getattr(record, key) != rule[key] for key in identities)
            or record.filter.topic != rule["topic"]
            or record.evidence != evidence
            or not set(refs).issubset(projected_ids)
            or record.issue != analysis_result.get("issue")
            or record.recommendation != analysis_result.get("advice")
            or record.certainty != analysis_result.get("certainty")
        ):
            return stop("recommendation_mismatch")
        evaluation = evaluate_filter(record.filter.model_dump(mode="json"), context["samples"])
        outcome["evaluation"] = evaluation
        supported = {row["alert_id"] for row in evaluation["results"] if row["status"] == "matched"}
        unsupported = [ref for ref in refs if ref not in supported]
        if unsupported:
            return stop("cited_samples_not_supported", unsupported_sample_alert_ids=unsupported)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return stop("recommendation_mismatch")
    try:
        # 使用新提供器，先按 topic+编号唯一匹配，再验证企业，不以企业筛选隐藏歧义。
        fresh = load_rules().get_rule(rule["rule_code"], topic=rule["topic"])
        validate_rule_match(fresh, rule_code=rule["rule_code"], topic=rule["topic"], company_code=rule["company_code"])
        if _config_bytes(fresh) != _config_bytes(rule):
            return stop("configuration_changed")
        outcome["configuration_check"] = {
            "status": "same_provider_configuration",
            "source_kind": fresh["source"]["kind"],
            "rule_config_version": fresh["rule_config_version"],
            "online_zeus_verified": False,
        }
    except Exception as error:
        # 查询失败时不回退到旧配置；只暴露错误类型，不回显连接信息或异常原文。
        return stop("configuration_unavailable", error_type=type(error).__name__)
    return stop("local_checks_passed", status="reviewable", recommendation=record.model_dump(mode="json"))
''',
    'test/rule_refine/test_proposal_check.py': r'''"""建议、材料与当前配置串联校验；人工回复及假配置提供器，无外部调用。"""

import copy
import importlib
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "_rule_refine_check_fixtures", Path(__file__).with_name("test_analysis.py")
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
check = importlib.import_module(fixtures.fixtures.package.__name__ + ".proposal_check").check_proposal


def analyze(data, answer=None):
    return fixtures.analysis.analyze_materials(
        data, Mock(return_value=json.dumps(fixtures.draft() if answer is None else answer))
    )


def loader(data):
    return Mock(return_value=Mock(get_rule=Mock(return_value=copy.deepcopy(data["rule"]))))


class ProposalCheckTests(unittest.TestCase):
    def setUp(self):
        self.data = fixtures.materials()
        self.result = analyze(self.data)
        self.load = loader(self.data)

    def blocked(self, result, reason):
        self.assertEqual((result["status"], result["reason"]), ("blocked", reason))
        self.assertIsNone(result["recommendation"])

    def test_reviewable_preserves_id_uses_fresh_provider_and_does_not_mutate(self):
        before = copy.deepcopy((self.data, self.result))
        outcome = check(self.data, self.result, self.load)
        self.assertEqual(outcome["status"], "reviewable")
        self.assertEqual(outcome["recommendation"], self.result["recommendation"])
        self.assertEqual((self.data, self.result), before)
        self.assertIs(outcome["automatic_apply"], False)
        self.assertEqual(outcome["engine_compatibility"], "not_verified")
        self.assertEqual(
            [outcome[key] for key in ("response_source", "material_mode", "material_source")],
            ["synthetic_reply", "demo", "synthetic_fixture"],
        )
        self.assertEqual(outcome["evaluation"]["counts"], {"matched": 1, "not_matched": 0, "unknown": 0})
        self.load.assert_called_once_with()
        self.load.return_value.get_rule.assert_called_once_with("DEMO_RULE", topic="demo-topic")
        check(self.data, self.result, self.load)
        self.assertEqual(self.load.call_count, 2)

    def test_prompt_hash_version_and_provenance_mismatch_prevent_provider_lookup(self):
        for key, value in (
            ("prompt_sha256", "0" * 64),
            ("prompt_version", "old"),
            ("material_mode", "live"),
            ("material_source", "other"),
            ("rule_source", {"kind": "other"}),
        ):
            changed = {**self.result, key: value}
            with self.subTest(key=key):
                self.blocked(check(self.data, changed, self.load), "prompt_mismatch")
        self.data["samples"][0]["raw_logs"][0]["host"] = "changed.invalid"
        self.blocked(check(self.data, self.result, self.load), "prompt_mismatch")
        self.load.assert_not_called()

    def test_recommendation_identity_window_count_and_descriptions_are_bound(self):
        missing_id = copy.deepcopy(self.result)
        del missing_id["recommendation"]["recommendation_id"]
        self.blocked(check(self.data, missing_id, self.load), "recommendation_mismatch")
        mutations = [
            (key, "different") for key in ("company_code", "rule_code", "rule_name", "rule_config_version", "issue")
        ]
        for key, value in mutations:
            changed = copy.deepcopy(self.result)
            changed["recommendation"][key] = value
            with self.subTest(key=key):
                self.blocked(check(self.data, changed, self.load), "recommendation_mismatch")
        for key, value in (
            ("alert_count", 1001),
            ("window_start", "2025-12-31T23:00:00+00:00"),
            ("sample_alert_ids", ["999"]),
        ):
            changed = copy.deepcopy(self.result)
            changed["recommendation"]["evidence"][key] = value
            self.blocked(check(self.data, changed, self.load), "recommendation_mismatch")
        self.load.assert_not_called()

    def test_individually_supported_conditions_must_match_the_same_log(self):
        self.data["samples"][0]["raw_logs"] = [
            {"host": "synthetic.invalid", "path": "/other"},
            {"host": "other.invalid", "path": "/health"},
        ]
        answer = fixtures.draft()
        answer["filter"].update(expression="A && B", B={"name": "path", "value": "/health", "operator": "equal"})
        result = analyze(self.data, answer)
        self.assertEqual(result["status"], "proposal")
        outcome = check(self.data, result, self.load)
        self.blocked(outcome, "cited_samples_not_supported")
        self.assertEqual(outcome["unsupported_sample_alert_ids"], ["101"])
        self.load.assert_not_called()

    def test_every_cited_sample_needs_a_definite_match(self):
        data = fixtures.materials([fixtures.fixtures.row(), fixtures.fixtures.row("102", "task-b")])
        data["samples"][1]["raw_logs"] = [{"host": "different.invalid"}]
        answer = {**fixtures.draft(), "sample_alert_ids": ["101", "102"]}
        outcome = check(data, analyze(data, answer), loader(data))
        self.blocked(outcome, "cited_samples_not_supported")
        self.assertEqual(outcome["unsupported_sample_alert_ids"], ["102"])

    def test_unknown_cited_sample_is_not_a_match(self):
        data = fixtures.materials([fixtures.fixtures.row(), fixtures.fixtures.row("102", "task-b")])
        data["samples"][0]["raw_logs"] = [{"other": "present"}]
        answer = {**fixtures.draft(), "sample_alert_ids": ["101", "102"]}
        outcome = check(data, analyze(data, answer), loader(data))
        self.blocked(outcome, "cited_samples_not_supported")
        self.assertEqual(outcome["evaluation"]["counts"]["unknown"], 1)

    def test_coverage_keeps_fully_omitted_and_unavailable_samples(self):
        data = fixtures.materials([fixtures.fixtures.row(), fixtures.fixtures.row("102", "task-b")])
        data["samples"][0]["raw_logs"] *= 4
        data["samples"][1]["raw_logs"] = [{"host": "synthetic.invalid", "large": "x" * 9000}]
        data.update(
            status="partial", requested_sample_count=3, issues=[{"code": "sample_unavailable", "alert_id": "103"}]
        )
        outcome = check(data, analyze(data), loader(data))
        self.assertEqual(outcome["status"], "reviewable")
        coverage = outcome["coverage"]
        self.assertEqual(
            [
                coverage[k]
                for k in (
                    "requested_alert_count",
                    "input_alert_count",
                    "projected_alert_count",
                    "unavailable_alert_count",
                )
            ],
            [3, 2, 1, 1],
        )
        self.assertEqual(
            [coverage[k] for k in ("input_log_count", "projected_log_count", "omitted_log_count")], [5, 3, 2]
        )
        self.assertEqual(
            coverage["per_alert"][1], {"alert_id": "102", "input_logs": 1, "projected_logs": 0, "omitted_logs": 1}
        )
        self.assertEqual(outcome["material_issues"], data["issues"])

    def test_configuration_version_or_content_change_blocks_even_if_version_reused(self):
        for key, value in (
            ("rule_config_version", "new-v2"),
            ("rule_name", "new-name"),
            ("configuration", {"terms": []}),
        ):
            fresh = loader(self.data)
            fresh.return_value.get_rule.return_value[key] = value
            with self.subTest(key=key):
                self.blocked(check(self.data, self.result, fresh), "configuration_changed")

    def test_configuration_source_and_identity_cannot_be_switched(self):
        for key, value in (
            ("company_code", "different"),
            ("topic", "different"),
            ("rule_code", "different"),
            ("source", {"kind": "other"}),
        ):
            fresh = loader(self.data)
            fresh.return_value.get_rule.return_value[key] = value
            with self.subTest(key=key):
                outcome = check(self.data, self.result, fresh)
                self.assertEqual(outcome["status"], "blocked")
                self.assertIsNone(outcome["recommendation"])

    def test_unrelated_csv_snapshot_metadata_change_is_allowed(self):
        self.data["rule"]["source"] = {"kind": "csv_snapshot", "file_sha256": "old"}
        fresh = loader(self.data)
        fresh.return_value.get_rule.return_value["source"]["file_sha256"] = "new"
        self.assertEqual(check(self.data, analyze(self.data), fresh)["status"], "reviewable")

    def test_provider_failures_are_visible_without_exception_contents(self):
        common = fixtures.fixtures.common
        for error in (RuntimeError, common.RuleNotFoundError, common.AmbiguousRuleError):
            fresh = Mock(side_effect=error("SECRET provider body"))
            outcome = check(self.data, self.result, fresh)
            self.blocked(outcome, "configuration_unavailable")
            self.assertNotIn("SECRET", json.dumps(outcome))
            fresh = loader(self.data)
            fresh.return_value.get_rule.side_effect = error("SECRET lookup body")
            outcome = check(self.data, self.result, fresh)
            self.blocked(outcome, "configuration_unavailable")
            self.assertNotIn("SECRET", json.dumps(outcome))

    def test_nonproposal_and_empty_materials_do_not_lookup_configuration(self):
        for status in ("no_change", "insufficient_evidence", "needs_existing_condition_change"):
            answer = {**fixtures.draft(), "status": status, "filter": None, "sample_alert_ids": []}
            result = analyze(self.data, answer)
            self.assertEqual(check(self.data, result, self.load)["status"], "not_applicable")
            self.blocked(check(self.data, {**result, "prompt_sha256": "wrong"}, self.load), "prompt_mismatch")
        empty = fixtures.materials([])
        self.assertEqual(check(empty, analyze(empty), self.load)["status"], "not_applicable")
        self.blocked(check(self.data, {**self.result, "status": "analysis_failed"}, self.load), "analysis_failed")
        self.load.assert_not_called()

    def test_malformed_results_or_materials_fail_before_provider_lookup(self):
        for value in (None, {}, {**self.result, "schema_version": "other"}, {**self.result, "status": "other"}):
            self.blocked(check(self.data, value, self.load), "invalid_analysis")
        for value in (None, {}, {**self.data, "samples": "bad"}):
            self.blocked(check(value, self.result, self.load), "invalid_materials")
        self.load.assert_not_called()


if __name__ == "__main__":
    if sys.argv[1:] == ["--demo"]:
        data = fixtures.materials()
        analysis = analyze(data)
        result = check(data, analysis, loader(data))
        print(
            json.dumps(
                {
                    "mode": "demo",
                    "status": result["status"],
                    "counts": result["evaluation"]["counts"],
                    "recommendation_id_preserved": result["recommendation"]["recommendation_id"]
                    == analysis["recommendation"]["recommendation_id"],
                    "automatic_apply": result["automatic_apply"],
                    "engine_compatibility": result["engine_compatibility"],
                    "model_calls": 0,
                    "database_calls": 0,
                    "redis_calls": 0,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        unittest.main(verbosity=2)
''',
}
expected = {'my_workflows/zeus/rule_refine/proposal_check.py': 'd6ca2c833edceab255c91798cef93d9687c93a641df4913f41c06db13dbb7338', 'test/rule_refine/test_proposal_check.py': '97ad1061ffe1e77d8e6b1d90527529cf82062c305078ff1f72a371adc2da47b4'}
for name, content in files.items():
    raw = content.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected[name]:
        raise SystemExit("停止：粘贴内容校验失败，未修改：" + name)
    compile(content, name, "exec")
    target = root / name
    if target.exists() and target.read_bytes() != raw:
        raise SystemExit("停止：已有不同版本，未覆盖：" + name)
for name, content in files.items():
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        with target.open("x", encoding="utf-8") as stream:
            stream.write(content)
        print("新增：" + name)
    else:
        print("已存在且一致：" + name)
PYWRITE
.venv/bin/python test/rule_refine/test_proposal_check.py
.venv/bin/python test/rule_refine/test_proposal_check.py --demo
SOC_STEP05B
