"""后台适配测试；依赖全部注入或替换，不连接实际服务。"""

import builtins
import importlib
import importlib.util
import json
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

spec = importlib.util.spec_from_file_location(
    "_background_service_fixtures",
    Path(__file__).with_name("test_signal_processing.py"),
)
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)
package = fixtures.base.package.__name__
module = importlib.import_module(package + ".background_service")


class WaitingError(Exception):
    pass


class App:
    def __init__(self):
        self.tasks, self.options = {}, []

    def task(self, **options):
        self.options.append(options)

        def register(function):
            self.tasks[options["name"]] = function
            return function

        return register


@contextmanager
def dependencies(root):
    fake = {}

    def add(name, **attributes):
        value = ModuleType(name)
        value.__path__ = []
        value.__dict__.update(attributes)
        fake[name] = value
        if "." in name:
            parent, child = name.rsplit(".", 1)
            if parent in fake:
                setattr(fake[parent], child, value)
        return value

    database = root / "nas/data/sec_know_model.sqlite"
    database.parent.mkdir(parents=True)
    database.touch()
    redis_client = Mock(
        connection_pool=SimpleNamespace(connection_kwargs={}, max_connections=100)
    )
    http, client, breaker = MagicMock(), Mock(), Mock(assert_closed=Mock())
    http.__enter__.return_value = http
    lease = MagicMock(assert_valid=Mock())
    lease.__enter__.return_value = lease
    semaphore = Mock(acquire=Mock(return_value=lease))
    policy = SimpleNamespace(
        semaphore_key="shared-model", concurrency=6, lease_ms=300000, heartbeat_ms=30000
    )
    add("httpx", Client=Mock(return_value=http))
    add("openai", OpenAI=Mock(return_value=client))
    add("redis", Redis=SimpleNamespace(from_url=Mock(return_value=redis_client)))
    add("redis.backoff", NoBackoff=Mock())
    add("redis.retry", Retry=Mock())
    add("util")
    rc = add(
        "util.root_config",
        ENV="LOCAL",
        DB_PATH=database,
        LITELLM_OPENAI_SERVER="http://localhost:4001/v1/",
        REDIS_URL="private",
    )
    add(
        "util.model_semaphore",
        ModelSemaphoreTimeout=WaitingError,
        RedisModelSemaphore=Mock(return_value=semaphore),
    )
    add("config")
    add(
        "config.circuit_breaker",
        ModelCircuitOpen=WaitingError,
        RedisCircuitBreaker=Mock(return_value=breaker),
    )
    add("config.model_limits", get_model_policy=Mock(return_value=policy))
    add("nas")
    add("nas.config", APP_KEY_DICT={"litellm_key": "synthetic-key"})
    counter = Mock(mode="live", namespace="scope")
    add(package + ".counter", RedisRuleCounter=Mock(return_value=counter))
    rules = Mock()
    add(package + ".csv_config_source", CsvRuleConfigSource=Mock(return_value=rules))
    add(package + ".local_sink", LocalRecommendationSink=Mock())
    adapter = add(
        package + ".model_adapter",
        analyze_with_model=Mock(
            return_value={
                "status": "proposal",
                "model_transport": {"sdk_request_calls": 1, "error_code": None},
            }
        ),
    )
    add(package + ".task_source", LocalWorkflowTaskSource=Mock())
    captured = {}

    def processor_factory(directory, **options):
        captured.update(directory=directory, **options)
        return Mock(
            process=Mock(
                side_effect=lambda signal: options["analyze"]({"synthetic": True})
            )
        )

    add(
        package + ".signal_processing",
        SignalProcessor=Mock(side_effect=processor_factory),
    )

    def tick(directory, **options):
        captured.update(tick_directory=directory, guard=options["processor"])
        return options["processor"].process({"signal_id": "synthetic"})

    tick_module = add(package + ".background_tick", run_tick=Mock(side_effect=tick))
    settings = {"model": "DeepSeek_V32", "state_dir": str(root / "state")}
    env = {
        "RULE_REFINE_NAMESPACE": "scope",
        "RULE_REFINE_CSV_PATH": str(root / "rules.csv"),
    }
    with patch.dict(sys.modules, fake):
        yield SimpleNamespace(**locals())


class BackgroundServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "config").mkdir()

    def configure(self, analysis=None, monitor=None):
        if analysis is not None:
            (self.root / "config/rule_refine.analysis.local.json").write_text(
                json.dumps(analysis), encoding="utf-8"
            )
        if monitor is not None:
            (self.root / "config/rule_refine.local.json").write_text(
                json.dumps(monitor), encoding="utf-8"
            )

    def test_default_off_and_nonlocal_do_not_import_service_dependencies(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {
                "httpx",
                "openai",
                "redis",
                "util",
                "config",
                "nas",
            }:
                raise AssertionError("service dependency imported")
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=guarded):
            self.assertEqual(
                module.run_background(self.root, {})["reason"], "analysis_disabled"
            )
            self.configure({"enabled": True})
            for name in ("env_profile", "ENV_PROFILE"):
                self.assertEqual(
                    module.run_background(self.root, {name: "PRD"})["reason"],
                    "local_only",
                )

    def test_disabled_configuration_does_not_require_other_fields(self):
        self.configure({"enabled": False, "model": None, "state_dir": None})
        execute = Mock()
        self.assertEqual(
            module.run_background(self.root, {}, execute=execute)["status"], "disabled"
        )
        execute.assert_not_called()

    def test_invalid_configuration_fails_without_execution(self):
        path = self.root / "config/rule_refine.analysis.local.json"
        for raw in (
            "[]",
            '{"enabled":true,"enabled":false}',
            '{"enabled":"yes"}',
            '{"unknown":1}',
            "{",
        ):
            with self.subTest(raw=raw):
                path.write_text(raw, encoding="utf-8")
                execute = Mock()
                result = module.run_background(self.root, {}, execute=execute)
                self.assertEqual(result["status"], "failed")
                execute.assert_not_called()

    def test_enabled_requires_valid_monitor_and_respects_environment_off(self):
        self.configure({"enabled": True})
        execute = Mock()
        for env in ({}, {"RULE_REFINE_MONITOR_ENABLED": "0"}):
            self.assertEqual(
                module.run_background(self.root, env, execute=execute)["reason"],
                "monitor_not_enabled_or_invalid",
            )
        execute.assert_not_called()

    def test_local_settings_are_loaded_into_copy_and_paths_resolve(self):
        self.configure(
            {"enabled": True, "state_dir": "results"},
            {"enabled": True, "csv_path": "rules.csv", "namespace": "scope"},
        )
        env = {"env_profile": "LOCAL"}
        execute = Mock(return_value={"status": "idle", "automatic_apply": True})
        result = module.run_background(self.root, env, execute=execute)
        _, settings, configured = execute.call_args.args
        self.assertEqual(env, {"env_profile": "LOCAL"})
        self.assertEqual(settings["state_dir"], str(self.root / "results"))
        self.assertEqual(
            configured["RULE_REFINE_CSV_PATH"], str(self.root / "rules.csv")
        )
        self.assertEqual(configured["RULE_REFINE_MONITOR_ENABLED"], "1")
        self.assertIs(result["automatic_apply"], False)

    def test_execution_error_reports_only_type(self):
        self.configure(
            {"enabled": True},
            {"enabled": True, "csv_path": "rules.csv", "namespace": "scope"},
        )
        result = module.run_background(
            self.root, {}, execute=Mock(side_effect=RuntimeError("secret-value"))
        )
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("secret-value", json.dumps(result))

    def test_capacity_wait_occurs_before_processor_start(self):
        processor, semaphore, breaker = Mock(), Mock(), Mock(assert_closed=Mock())
        policy = SimpleNamespace(semaphore_key="shared", concurrency=6)
        guard = module._ModelGuard(
            processor, semaphore, breaker, policy, "model", (WaitingError,)
        )
        semaphore.acquire.side_effect = WaitingError()
        self.assertEqual(guard.process({})["status"], "capacity_wait")
        processor.process.assert_not_called()
        semaphore.acquire.assert_called_once_with("shared", 6, wait_timeout=0)
        breaker.assert_closed.side_effect = WaitingError()
        semaphore.reset_mock()
        self.assertEqual(guard.process({})["status"], "capacity_wait")
        semaphore.acquire.assert_not_called()

    def test_lease_exits_and_reference_clears_on_processing_error(self):
        lease = MagicMock(assert_valid=Mock())
        lease.__enter__.return_value = lease
        processor = Mock(process=Mock(side_effect=OSError("disk")))
        guard = module._ModelGuard(
            processor,
            Mock(acquire=Mock(return_value=lease)),
            Mock(assert_closed=Mock()),
            SimpleNamespace(semaphore_key="s", concurrency=1),
            "m",
            (),
        )
        with self.assertRaises(OSError):
            guard.process({})
        lease.__exit__.assert_called_once()
        self.assertIsNone(guard.lease)

    def test_execute_wires_shared_limits_local_storage_and_closes_clients(self):
        with dependencies(self.root) as d:
            self.assertEqual(
                module._execute(self.root, d.settings, d.env)["status"], "proposal"
            )
            d.semaphore.acquire.assert_called_once_with(
                "shared-model", 6, wait_timeout=0
            )
            d.fake["httpx"].Client.assert_called_once_with(trust_env=False, timeout=240)
            options = d.fake["openai"].OpenAI.call_args.kwargs
            self.assertEqual((options["max_retries"], options["timeout"]), (0, 240))
            self.assertEqual(
                d.redis_client.connection_pool.connection_kwargs["socket_timeout"], 2
            )
            self.assertEqual(d.redis_client.connection_pool.max_connections, 8)
            d.fake[
                package + ".task_source"
            ].LocalWorkflowTaskSource.assert_called_once_with(d.database)
            self.assertEqual(d.captured["source_kind"], "workflow_task_store")
            d.adapter.analyze_with_model.assert_called_once_with(
                {"synthetic": True},
                d.client,
                model="DeepSeek_V32",
                transport_mode="live",
                timeout_seconds=240,
            )
            d.breaker.record_success.assert_called_once_with("DeepSeek_V32")
            self.assertEqual(d.lease.assert_valid.call_count, 4)
            d.client.close.assert_called_once()
            d.redis_client.close.assert_called_once()
            d.http.__exit__.assert_called_once()

    def test_execute_failure_closes_resources_and_wrong_database_is_rejected(self):
        with dependencies(self.root) as d:
            d.tick_module.run_tick.side_effect = OSError("storage")
            with self.assertRaises(OSError):
                module._execute(self.root, d.settings, d.env)
            d.client.close.assert_called_once()
            d.redis_client.close.assert_called_once()
            d.http.__exit__.assert_called_once()
            d.rc.DB_PATH = self.root / "different.sqlite"
            d.fake["redis"].Redis.from_url.reset_mock()
            with self.assertRaises(ValueError):
                module._execute(self.root, d.settings, d.env)
            d.fake["redis"].Redis.from_url.assert_not_called()

    def test_task_registration_is_idempotent_and_ignores_results(self):
        app = App()
        task = module.register_rule_refine_task(app)
        self.assertIs(module.register_rule_refine_task(app), task)
        self.assertEqual(
            app.options,
            [{"name": module.TASK_NAME, "ignore_result": True, "max_retries": 0}],
        )
        with patch.object(
            module, "run_background", return_value={"status": "disabled"}
        ) as run:
            self.assertEqual(task()["status"], "disabled")
            run.assert_called_once_with()

    def test_cached_nonlocal_application_configuration_is_rejected_before_connections(
        self,
    ):
        with dependencies(self.root) as d:
            for profile in ("PRD", "STG", None):
                d.rc.ENV = profile
                with self.subTest(profile=profile), self.assertRaises(ValueError):
                    module._execute(self.root, d.settings, d.env)
            d.fake["config.model_limits"].get_model_policy.assert_not_called()
            d.fake["redis"].Redis.from_url.assert_not_called()
            d.fake["httpx"].Client.assert_not_called()


if __name__ == "__main__":
    if sys.argv[1:] == ["--celery-check"]:
        import celery

        app = celery.Celery(
            "rule_refine_registration_check", broker="memory://", set_as_current=False
        )
        try:
            task = module.register_rule_refine_task(app)
            assert module.register_rule_refine_task(app) is task
            assert app.tasks[module.TASK_NAME] is task and task.app is app
            assert task.ignore_result is True and task.max_retries == 0
            with patch.object(
                module, "run_background", return_value={"status": "disabled"}
            ) as run:
                assert task.run()["status"] == "disabled"
                run.assert_called_once_with()
            print(
                json.dumps(
                    {
                        "check": "rule_refine_celery_registration",
                        "status": "passed",
                        "celery_version": celery.__version__,
                        "task_registered": True,
                        "ignore_result": True,
                        "model_calls": 0,
                        "database_calls": 0,
                        "redis_calls": 0,
                        "real_queue_connected": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        finally:
            app.close()
    elif sys.argv[1:] == ["--demo"]:
        with tempfile.TemporaryDirectory() as temporary:
            app = App()
            task = module.register_rule_refine_task(app)
            result = module.run_background(Path(temporary), {})
            print(
                json.dumps(
                    {
                        "mode": "demo",
                        "status": "passed",
                        "task_registered": task is app.tasks[module.TASK_NAME],
                        "analysis_enabled": result["status"] != "disabled",
                        "disabled_reason": result["reason"],
                        "model_calls": 0,
                        "database_calls": 0,
                        "redis_calls": 0,
                        "real_queue_connected": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
    else:
        unittest.main(verbosity=2)
