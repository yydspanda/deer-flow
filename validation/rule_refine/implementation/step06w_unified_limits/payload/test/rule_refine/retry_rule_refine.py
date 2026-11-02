"""List or explicitly requeue saved failed rule analyses; no direct model calls."""

import argparse
import hashlib
import importlib
import json
import os
import sys
import types
from pathlib import Path


def failure_result(error):
    controlled = {
        "monitor_configuration_required",
        "maintenance_is_local_only",
        "loaded_profile_not_local",
        "select_rule_or_all_explicitly",
        "ambiguous_rule_use_topic_and_company",
        "maintenance_counter_scope_mismatch",
        "analysis_unconfirmed_requires_explicit_authorization",
        "only_failed_or_blocked_analysis_can_be_retried",
        "counter_required_for_explicit_action",
    }
    reason = (
        str(error)
        if str(error) in controlled
        else {
            "FileNotFoundError": "configuration_or_saved_file_missing",
            "PermissionError": "local_file_permission_denied",
            "ConnectionError": "redis_connection_failed",
            "TimeoutError": "redis_operation_timed_out",
            "ResponseError": "redis_command_rejected",
            "JSONDecodeError": "local_json_file_invalid",
            "SavedAnalysisError": "saved_analysis_record_invalid",
            "ValueError": "configuration_or_saved_record_invalid",
        }.get(type(error).__name__, "maintenance_failed")
    )
    return {
        "status": "failed",
        "reason": reason,
        "error_type": type(error).__name__,
        "model_calls": 0,
    }


def main():
    parser = argparse.ArgumentParser(description="规则分析失败恢复；默认只列出")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--retry", action="store_true", help="重新排期，已启用后台会调用模型"
    )
    actions.add_argument(
        "--release", action="store_true", help="保留失败记录并释放待办，不调用模型"
    )
    parser.add_argument("--rule")
    parser.add_argument("--topic")
    parser.add_argument("--company")
    parser.add_argument("--all", action="store_true", dest="all_rules")
    parser.add_argument(
        "--allow-unconfirmed", action="store_true", help="明确允许操作结果未知的旧调用"
    )
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    package = types.ModuleType("_rule_refine_maintenance")
    package.__path__ = [str(root / "my_workflows/zeus/rule_refine")]
    sys.modules[package.__name__] = package
    control = importlib.import_module(package.__name__ + ".retry_control")
    settings_module = importlib.import_module(package.__name__ + ".local_settings")
    environment = dict(os.environ)
    monitor = settings_module.configure_rule_monitor(root, environment)
    if monitor.get("status") != "configured" or not monitor.get("enabled"):
        raise ValueError("monitor_configuration_required")
    config_path = root / "config/rule_refine.analysis.local.json"
    data = (
        json.loads(
            config_path.read_text(encoding="utf-8"),
            object_pairs_hook=settings_module._object,
        )
        if config_path.exists()
        else {}
    )
    state = Path(
        data.get("state_dir", str(Path.home() / "sec-rule-refine-data/background"))
    ).expanduser()
    if not state.is_absolute():
        state = root / state
    namespace = environment["RULE_REFINE_NAMESPACE"]
    state = state / hashlib.sha256(namespace.encode("utf-8")).hexdigest()[:16]
    options = dict(
        action="retry" if args.retry else "release" if args.release else "list",
        rule=args.rule,
        topic=args.topic,
        company=args.company,
        all_rules=args.all_rules,
        allow_unconfirmed=args.allow_unconfirmed,
    )
    if options["action"] == "list":
        result = control.maintain(state / "processing", state / "scheduler", **options)
    else:
        if any(
            environment.get(name, "LOCAL").upper() != "LOCAL"
            for name in ("env_profile", "ENV_PROFILE")
        ):
            raise ValueError("maintenance_is_local_only")
        sys.path.insert(0, str(root))
        import util.root_config as rc
        from redis import Redis

        counter_module = importlib.import_module(package.__name__ + ".counter")
        if rc.ENV != "LOCAL":
            raise ValueError("loaded_profile_not_local")
        client = Redis.from_url(
            rc.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            counter = counter_module.RedisRuleCounter(
                client, mode="live", namespace=namespace, transaction_retries=3
            )
            result = control.maintain(
                state / "processing", state / "scheduler", counter=counter, **options
            )
        finally:
            client.close()
    result["automatic_analysis_enabled"] = data.get("enabled") is True
    result["next_background_tick_may_call_model"] = data.get("enabled") is True and any(
        row.get("next_background_tick_may_call_model")
        for row in result.get("items", [])
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(json.dumps(failure_result(error), ensure_ascii=False))
        raise SystemExit(1)
