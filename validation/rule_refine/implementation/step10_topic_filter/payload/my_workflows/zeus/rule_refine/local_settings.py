"""启动时读取本机规则监测配置；环境变量优先，故障只关闭监测。"""

import json
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
KEYS = {
    "enabled": "RULE_REFINE_MONITOR_ENABLED",
    "csv_path": "RULE_REFINE_CSV_PATH",
    "namespace": "RULE_REFINE_NAMESPACE",
}


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("重复配置字段")
        result[key] = value
    return result


def configure_rule_monitor(project_root=None, environ=None):
    """只读配置并应用三个监测变量；不连接外部服务，不修改其他环境变量。"""
    env = os.environ if environ is None else environ
    summary = {"status": "configured", "enabled": False, "source": "defaults", "provider": "csv_snapshot"}
    try:
        root = PROJECT_ROOT if project_root is None else Path(project_root).resolve()
        overrides = {field: env[key] for field, key in KEYS.items() if key in env}
        data = {}
        if overrides:
            summary["source"] = "environment"
        # An explicit off switch remains usable even when the optional file is broken.
        if overrides.get("enabled") == "0":
            return summary
        from .topic_settings import load_settings as load_topic_settings

        topic_settings = load_topic_settings(root, env)
        if topic_settings is not None:
            data = {key: topic_settings[key] for key in KEYS}
            summary["source"] = "topic_file_and_environment" if overrides else "topic_file"
        elif len(overrides) != len(KEYS):
            path = root / "config/rule_refine.local.json"
            try:
                raw = path.read_text(encoding="utf-8")
            except FileNotFoundError:
                raw = None
            if raw is not None:
                summary["source"] = "local_file_and_environment" if overrides else "local_file"
                data = json.loads(raw, object_pairs_hook=_object)
                if not isinstance(data, dict) or set(data) - set(KEYS):
                    raise ValueError("规则监测配置字段无效")
        if "enabled" in overrides:
            if overrides["enabled"] not in ("0", "1"):
                raise ValueError("监测开关环境变量必须为 0 或 1")
            enabled = overrides["enabled"] == "1"
        else:
            enabled = data.get("enabled", False)
            if type(enabled) is not bool:
                raise ValueError("配置 enabled 必须是布尔值")
        updates = {KEYS["enabled"]: "1" if enabled else "0"}
        if enabled:
            values = {field: overrides.get(field, data.get(field)) for field in ("csv_path", "namespace")}
            for field, value in values.items():
                if not isinstance(value, str) or not value.strip():
                    raise ValueError("开启监测需要 CSV 路径和稳定命名空间")
            namespace = values["namespace"].strip()
            if len(namespace) > 512:
                raise ValueError("监测命名空间过长")
            csv_path = Path(values["csv_path"].strip()).expanduser()
            if not csv_path.is_absolute():
                csv_path = root / csv_path
            updates.update({KEYS["csv_path"]: str(csv_path), KEYS["namespace"]: namespace})
        # Validate everything before writing: a failure never installs half a configuration.
        env.update(updates)
        summary["enabled"] = enabled
    except Exception as error:
        env[KEYS["enabled"]] = "0"
        summary.update(status="configuration_error", enabled=False, error_type=type(error).__name__)
    return summary
