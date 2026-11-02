bash <<'SOC_SAVE_MONITOR'
set -euo pipefail
cd "$HOME/sec_know_model"
.venv/bin/python - <<'PYINSTALL'
import hashlib
import json
import os
import tempfile
from pathlib import Path

runtime = Path("my_workflows/zeus/rule_refine/runtime_monitor.py")
if not runtime.is_file() or hashlib.sha256(runtime.read_bytes()).hexdigest() != "d559868c3227f85e03f0a2e3ae978716ab7a84de89ccd945283fc8c4a845e948":
    raise SystemExit("停止：监测模块版本不匹配，未修改。")

files = [
    ('my_workflows/zeus/rule_refine/local_settings.py', '8712f940df8f16195f706a4eb5a639b37487ab1aac26b6b7f8e93c58a67d515c', r'''"""启动时读取本机规则监测配置；环境变量优先，故障只关闭监测。"""

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
        if len(overrides) != len(KEYS):
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
'''),
    ('test/rule_refine/test_local_settings.py', '4edb99dc7a5cf6c7226e9dd15ec07315e31fea1c01d57a091155ea8036d61597', r'''"""本机监测配置测试；只操作临时文件，不加载应用或连接外部服务。"""

import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[2] / "my_workflows/zeus/rule_refine"
package = types.ModuleType("_rule_refine_settings_test")
package.__path__ = [str(source)]
sys.modules[package.__name__] = package
module = importlib.import_module(package.__name__ + ".local_settings")


class SettingsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.path = self.root / "config/rule_refine.local.json"
        self.path.parent.mkdir()
        self.env = {"env_profile": "STG", "OTHER_SETTING": "keep"}
        self.valid = {"enabled": True, "csv_path": "data/rules.csv", "namespace": "existing-namespace"}

    def write(self, value):
        self.path.write_text(json.dumps(value), encoding="utf-8")

    def configure(self):
        return module.configure_rule_monitor(self.root, self.env)

    def test_missing_file_keeps_default_off_and_other_environment(self):
        result = self.configure()
        self.assertEqual((result["status"], result["enabled"], result["source"]), ("configured", False, "defaults"))
        self.assertEqual((self.env["env_profile"], self.env["OTHER_SETTING"]), ("STG", "keep"))

    def test_file_settings_relative_to_project_and_repeat_namespace_stable(self):
        self.write(self.valid)
        previous = Path.cwd()
        try:
            os.chdir(self.path.parent)
            result = self.configure()
        finally:
            os.chdir(previous)
        self.assertEqual((result["enabled"], result["source"]), (True, "local_file"))
        self.assertEqual(self.env["RULE_REFINE_CSV_PATH"], str(self.root / "data/rules.csv"))
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "existing-namespace")
        self.assertTrue(self.configure()["enabled"])
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "existing-namespace")

    def test_explicit_off_bypasses_broken_file(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.env["RULE_REFINE_MONITOR_ENABLED"] = "0"
        result = self.configure()
        self.assertEqual((result["status"], result["enabled"], result["source"]), ("configured", False, "environment"))

    def test_complete_environment_overrides_broken_file_and_expands_home(self):
        self.path.write_text("{broken", encoding="utf-8")
        self.env.update(
            RULE_REFINE_MONITOR_ENABLED="1",
            RULE_REFINE_CSV_PATH="~/no-read-needed.csv",
            RULE_REFINE_NAMESPACE="env-namespace",
        )
        result = self.configure()
        self.assertEqual((result["enabled"], result["source"]), (True, "environment"))
        self.assertEqual(self.env["RULE_REFINE_CSV_PATH"], str(Path.home() / "no-read-needed.csv"))
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "env-namespace")

    def test_partial_environment_overrides_corresponding_file_field(self):
        self.write(self.valid)
        self.env["RULE_REFINE_NAMESPACE"] = "override-namespace"
        result = self.configure()
        self.assertEqual((result["enabled"], result["source"]), (True, "local_file_and_environment"))
        self.assertEqual(self.env["RULE_REFINE_NAMESPACE"], "override-namespace")
        self.assertEqual(self.env["env_profile"], "STG")

    def test_invalid_json_shape_unknown_and_duplicate_fields_disable_monitor(self):
        for text in ("{broken", "[]", '{"unexpected":1}', '{"enabled":false,"enabled":true}'):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                self.env = {"env_profile": "LOCAL"}
                result = self.configure()
                self.assertEqual((result["status"], result["enabled"]), ("configuration_error", False))
                self.assertEqual(self.env, {"env_profile": "LOCAL", "RULE_REFINE_MONITOR_ENABLED": "0"})

    def test_invalid_enabled_values_or_required_fields_are_not_partially_applied(self):
        changes = [{"enabled": value} for value in (1, "true", None)]
        changes += [{"csv_path": ""}, {"csv_path": []}, {"namespace": " "}, {"namespace": "n" * 513}]
        for change in changes:
            with self.subTest(change=change):
                self.env = {"OTHER_SETTING": "keep"}
                self.write({**self.valid, **change})
                self.assertEqual(self.configure()["status"], "configuration_error")
                self.assertEqual(self.env, {"OTHER_SETTING": "keep", "RULE_REFINE_MONITOR_ENABLED": "0"})

    def test_invalid_environment_switch_or_partial_override_does_not_write_file_values(self):
        self.write(self.valid)
        for changes in ({"RULE_REFINE_MONITOR_ENABLED": "true"}, {"RULE_REFINE_NAMESPACE": ""}):
            self.env = dict(changes)
            self.assertEqual(self.configure()["status"], "configuration_error")
            self.assertEqual(self.env, {**changes, "RULE_REFINE_MONITOR_ENABLED": "0"})

    def test_disabled_file_needs_no_paths_and_loads_no_service_dependencies(self):
        self.write({"enabled": False})
        blocked = {"redis": None, "util.root_config": None, package.__name__ + ".counter": None}
        with patch.dict(sys.modules, blocked):
            self.assertFalse(self.configure()["enabled"])
            self.write(self.valid)
            self.env.clear()
            self.assertTrue(self.configure()["enabled"])

    def test_default_project_root_uses_module_location_not_working_directory(self):
        self.write(self.valid)
        with patch.object(module, "PROJECT_ROOT", self.root):
            result = module.configure_rule_monitor(environ=self.env)
        self.assertEqual((result["status"], result["provider"]), ("configured", "csv_snapshot"))
        self.assertEqual(self.env["RULE_REFINE_CSV_PATH"], str(self.root / "data/rules.csv"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
'''),
 ]
prepared = []
def prepare(name, raw, old_hash=None):
    path = Path(name)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise SystemExit("停止：不是普通文件：" + name)
    previous = path.read_bytes() if path.exists() else None
    if previous == raw:
        print("内容一致：" + name)
        return
    if previous is not None and hashlib.sha256(previous).hexdigest() != old_hash:
        raise SystemExit("停止：已有其他改动，未覆盖：" + name)
    if path.suffix == ".py":
        compile(raw, name, "exec")
    prepared.append((path, previous, raw))

for name, expected, text in files:
    raw = text.encode("utf-8")
    if hashlib.sha256(raw).hexdigest() != expected:
        raise SystemExit("停止：复制内容校验失败：" + name)
    prepare(name, raw)

run = Path("run.py")
if not run.is_file() or run.is_symlink():
    raise SystemExit("停止：未找到原 run.py。")
raw = run.read_bytes()
digest = hashlib.sha256(raw).hexdigest()
old_hash = 'd073fdf29514af84acef361d7e1a7b2fd0af70b654896a6150125f92ab710506'
new_hash = 'bd56a7e4afbae09551ca6d272f4e10bbf97cca375c1c58e6cdf6ec86d305fdc2'
if digest == old_hash:
    before = 'def run_api_server():\n'
    after = r'''def run_api_server():
    # Load optional host-local monitor settings before serving requests.
    try:
        import json
        from my_workflows.zeus.rule_refine.local_settings import configure_rule_monitor
        monitor_config = configure_rule_monitor()
    except Exception as error:
        import os
        os.environ["RULE_REFINE_MONITOR_ENABLED"] = "0"
        monitor_config = {"status": "configuration_error", "enabled": False, "error_type": type(error).__name__}
    try:
        emit = logger.warning if monitor_config["status"] == "configuration_error" else logger.info
        emit("rule_refine_monitor_config %s", json.dumps(monitor_config, ensure_ascii=False))
    except Exception:
        pass
'''
    text = raw.decode("utf-8")
    if text.count(before) != 1:
        raise SystemExit("停止：启动入口位置不匹配。")
    raw = text.replace(before, after, 1).encode("utf-8")
elif digest != new_hash:
    raise SystemExit("停止：run.py 已有其他改动，未覆盖。")
if hashlib.sha256(raw).hexdigest() != new_hash:
    raise SystemExit("停止：启动入口校验失败。")
prepare("run.py", raw, old_hash)
backup = Path("run.py.before-step03g")
if backup.is_symlink() or (backup.exists() and (not backup.is_file() or hashlib.sha256(backup.read_bytes()).hexdigest() != old_hash)):
    raise SystemExit("停止：run.py 旧版副本不匹配。")

config = Path("config/rule_refine.local.json")
if config.is_symlink() or (config.exists() and not config.is_file()):
    raise SystemExit("停止：本地配置不是普通文件。")
if config.exists():
    print("保留已有本地配置：" + str(config))
else:
    settings = {
        "enabled": True,
        "csv_path": "~/sec-rule-refine-data/rules.csv",
        "namespace": "sec-know-rule-monitor-mac-zhangjianming627-v1",
    }
    prepare(str(config), (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
ignore = Path(".gitignore")
if ignore.is_symlink() or (ignore.exists() and not ignore.is_file()):
    raise SystemExit("停止：.gitignore 不是普通文件。")
previous_ignore = ignore.read_bytes() if ignore.exists() else b""
text = previous_ignore.decode("utf-8")
for line in ("/config/rule_refine.local.json", "/run.py.before-step03g"):
    if line not in text.splitlines():
        if text and not text.endswith("\n"):
            text += "\n"
        text += line + "\n"
prepare(".gitignore", text.encode("utf-8"), hashlib.sha256(previous_ignore).hexdigest())

for path, previous, raw in prepared:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path == run and previous is not None and not backup.exists():
        with backup.open("xb") as stream:
            stream.write(previous)
    mode = path.stat().st_mode & 0o777 if path.exists() else (0o600 if path == config else 0o644)
    descriptor, temporary = tempfile.mkstemp(prefix=".rule-settings-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
    print("已保存：" + str(path))
PYINSTALL
.venv/bin/python test/rule_refine/test_local_settings.py
.venv/bin/python - <<'PYCHECK'
import json
from my_workflows.zeus.rule_refine.local_settings import configure_rule_monitor
settings = {}
result = configure_rule_monitor(environ=settings)
print(json.dumps({**result, "namespace": settings.get("RULE_REFINE_NAMESPACE")}, ensure_ascii=False, indent=2))
if result["status"] != "configured":
    raise SystemExit(1)
PYCHECK
SOC_SAVE_MONITOR
