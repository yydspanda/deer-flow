"""Runner gates: no services before local/fixture/code checks or LIVE confirmation."""

import builtins
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch


HERE = Path(__file__).resolve().parent
FIXTURE = HERE.parent / "step06i_sample_source/synthetic-healthcase-v1.json"


class ServiceImportedTooEarly(BaseException):
    pass


class ReadOnlyTerminal(io.StringIO):
    def seek(self, *args, **kwargs):
        raise io.UnsupportedOperation("File or stream is not seekable")

    def write(self, *args, **kwargs):
        raise io.UnsupportedOperation("not writable")


class RunnerGateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HERE))
        spec = importlib.util.spec_from_file_location(
            "synthetic_runner_test", HERE / "runner.py"
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    @classmethod
    def tearDownClass(cls):
        sys.path.remove(str(HERE))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = self.root / "fixture.json"
        self.fixture.write_bytes(FIXTURE.read_bytes())

    @contextlib.contextmanager
    def no_application_imports(self):
        original = builtins.__import__

        def guarded(name, *args, **kwargs):
            if name.split(".")[0] in {
                "util",
                "config",
                "nas",
                "redis",
                "openai",
                "httpx",
            }:
                raise ServiceImportedTooEarly(name)
            return original(name, *args, **kwargs)

        with patch("builtins.__import__", guarded):
            yield

    def test_each_nonlocal_environment_switch_stops_before_application_import(self):
        for key in ("env_profile", "ENV_PROFILE"):
            with (
                self.subTest(key=key),
                patch.dict(
                    "os.environ",
                    {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL", key: "PRD"},
                ),
                self.no_application_imports(),
            ):
                with self.assertRaisesRegex(Exception, "(?i)local"):
                    self.module.preflight(self.root, self.fixture, {})

    def test_fixture_mismatch_stops_before_application_import(self):
        self.fixture.write_text("{}", encoding="utf-8")
        with (
            patch.dict("os.environ", {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}),
            self.no_application_imports(),
        ):
            with self.assertRaises(Exception):
                self.module.preflight(self.root, self.fixture, {})

    def test_missing_fixture_stops_before_application_import(self):
        self.fixture.unlink()
        with (
            patch.dict("os.environ", {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}),
            self.no_application_imports(),
        ):
            with self.assertRaises(Exception):
                self.module.preflight(self.root, self.fixture, {})

    def test_pinned_code_mismatch_stops_before_application_import(self):
        source = self.root / "my_workflows/zeus/rule_refine/counter.py"
        source.parent.mkdir(parents=True)
        source.write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
        pins = {"my_workflows/zeus/rule_refine/counter.py": "0" * 64}
        with (
            patch.dict("os.environ", {"env_profile": "LOCAL", "ENV_PROFILE": "LOCAL"}),
            self.no_application_imports(),
        ):
            with self.assertRaises(Exception):
                self.module.preflight(self.root, self.fixture, pins)

    def confirm(self, answer):
        def open_terminal(path, mode="r", **kwargs):
            self.assertEqual(str(path), "/dev/tty")
            self.assertEqual(mode, "r")
            self.assertEqual(kwargs.get("encoding"), "utf-8")
            return ReadOnlyTerminal(answer)

        with (
            patch("builtins.open", side_effect=open_terminal),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            return self.module.confirm_live()

    def test_live_confirmation_reads_nonseekable_terminal_read_only(self):
        self.assertTrue(self.confirm("LIVE\n"))

    def test_every_other_confirmation_cancels(self):
        for answer in ("", "\n", "YES\n", "ENABLE\n", "live\n"):
            with self.subTest(answer=answer):
                self.assertFalse(self.confirm(answer))


class ModelWaiting(Exception):
    pass


class RunnerExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        RunnerGateTests.setUpClass()
        cls.module = RunnerGateTests.module
        spec = importlib.util.spec_from_file_location(
            "synthetic_runner_original_guard",
            HERE.parent
            / "step06f_background/payload/my_workflows/zeus/rule_refine/background_service.py",
        )
        cls.background = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.background)

    @classmethod
    def tearDownClass(cls):
        RunnerGateTests.tearDownClass()

    def setUp(self):
        self.active = False
        self.redis = Mock()
        self.redis.connection_pool.connection_kwargs = {}
        self.http = MagicMock()
        self.http.__enter__.return_value = self.http
        self.http.__exit__.return_value = False
        self.client = Mock()
        self.breaker = Mock()
        self.breaker.assert_closed = Mock()
        self.lease = MagicMock()

        def enter():
            self.active = True
            return self.lease

        def leave(*args):
            self.active = False

        def assert_valid():
            self.assertTrue(self.active, "model lease must be held")

        self.lease.__enter__.side_effect = enter
        self.lease.__exit__.side_effect = leave
        self.lease.assert_valid = Mock(side_effect=assert_valid)
        self.semaphore = Mock()
        self.semaphore.acquire.return_value = self.lease
        self.result = {
            "status": "insufficient_evidence",
            "model_transport": {"sdk_request_calls": 1, "error_code": None},
        }

        def analyze(*args, **kwargs):
            self.assertTrue(self.active)
            self.assertGreater(self.lease.assert_valid.call_count, 0)
            return self.result

        self.adapter = SimpleNamespace(analyze_with_model=Mock(side_effect=analyze))
        self.prepared = SimpleNamespace(
            rc=SimpleNamespace(REDIS_URL="redis://synthetic.invalid/0"),
            endpoint="http://127.0.0.1:4001/v1/",
            key="synthetic-key",
            model="DeepSeek_V32",
            policy=SimpleNamespace(
                lease_ms=300000,
                heartbeat_ms=30000,
                semaphore_key="DeepSeek_V32",
                concurrency=6,
            ),
            modules=SimpleNamespace(
                model_adapter=self.adapter, background_service=self.background
            ),
            case={"source": "synthetic_fixture"},
        )
        self.imports = {}

        def add(name, **attrs):
            module = ModuleType(name)
            module.__dict__.update(attrs)
            self.imports[name] = module
            return module

        self.http_module = add("httpx", Client=Mock(return_value=self.http))
        self.openai_module = add("openai", OpenAI=Mock(return_value=self.client))
        self.redis_module = add("redis", Redis=Mock())
        self.redis_module.Redis.from_url.return_value = self.redis
        add("redis.backoff", NoBackoff=Mock())
        self.retry_module = add("redis.retry", Retry=Mock())
        add("config", __path__=[])
        add("util", __path__=[])
        add(
            "config.circuit_breaker",
            ModelCircuitOpen=ModelWaiting,
            RedisCircuitBreaker=Mock(return_value=self.breaker),
        )
        self.semaphore_module = add(
            "util.model_semaphore",
            ModelSemaphoreTimeout=ModelWaiting,
            RedisModelSemaphore=Mock(return_value=self.semaphore),
        )

    def run_execute(self):
        def run_check(output, *, analyze, wrap_processor, case, **kwargs):
            self.assertIs(case, self.prepared.case)
            processor = SimpleNamespace(
                process=lambda signal: analyze({"synthetic": True})
            )
            return wrap_processor(processor).process({"signal": "synthetic"})

        with (
            patch.dict(sys.modules, self.imports),
            patch.object(self.module.probe, "run_check", side_effect=run_check),
        ):
            return self.module.execute(self.prepared, Path("unused-output"))

    def test_one_request_under_shared_lease_and_all_clients_closed(self):
        result = self.run_execute()
        self.assertIs(result, self.result)
        self.adapter.analyze_with_model.assert_called_once_with(
            {"synthetic": True},
            self.client,
            model="DeepSeek_V32",
            transport_mode="live",
        )
        self.openai_module.OpenAI.assert_called_once_with(
            base_url=self.prepared.endpoint,
            api_key="synthetic-key",
            max_retries=0,
            timeout=240,
            http_client=self.http,
        )
        self.http_module.Client.assert_called_once_with(trust_env=False, timeout=240)
        self.semaphore_module.RedisModelSemaphore.assert_called_once_with(
            self.redis,
            lease_ms=300000,
            heartbeat_ms=30000,
        )
        self.semaphore.acquire.assert_called_once_with(
            "DeepSeek_V32", 6, wait_timeout=0
        )
        self.breaker.record_success.assert_called_once_with("DeepSeek_V32")
        self.breaker.record_failure.assert_not_called()
        self.assertFalse(self.active)
        self.redis.close.assert_called_once()
        self.client.close.assert_called_once()
        self.http.__exit__.assert_called_once()
        options = self.redis.connection_pool.connection_kwargs
        self.assertEqual(options["socket_connect_timeout"], 2)
        self.assertEqual(options["socket_timeout"], 2)
        self.assertIs(options["retry_on_timeout"], False)
        self.assertEqual(options["retry_on_error"], [])
        self.assertEqual(self.redis.connection_pool.max_connections, 8)
        self.assertEqual(self.retry_module.Retry.call_args.args[1], 0)

    def test_busy_shared_slot_does_not_start_analysis(self):
        self.semaphore.acquire.side_effect = ModelWaiting("busy")
        result = self.run_execute()
        self.assertEqual(result["status"], "capacity_wait")
        self.adapter.analyze_with_model.assert_not_called()
        self.lease.__enter__.assert_not_called()
        self.redis.close.assert_called_once()
        self.client.close.assert_called_once()

    def test_model_failure_updates_original_circuit_without_retry(self):
        self.result["model_transport"]["error_code"] = "request_failed"
        result = self.run_execute()
        self.assertIs(result, self.result)
        self.adapter.analyze_with_model.assert_called_once()
        self.breaker.record_failure.assert_called_once_with("DeepSeek_V32")
        self.breaker.record_success.assert_not_called()

    def test_model_client_setup_failure_closes_already_created_clients(self):
        self.openai_module.OpenAI.side_effect = ValueError("setup")
        with self.assertRaisesRegex(ValueError, "setup"):
            self.run_execute()
        self.redis.close.assert_called_once()
        self.http.__exit__.assert_called_once()
        self.adapter.analyze_with_model.assert_not_called()

    def test_circuit_record_failure_keeps_returned_model_result(self):
        self.breaker.record_success.side_effect = ConnectionError("private")
        result = self.run_execute()
        self.assertIs(result, self.result)
        self.assertEqual(
            result["model_transport"]["circuit_update_error_type"], "ConnectionError"
        )
        self.adapter.analyze_with_model.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
