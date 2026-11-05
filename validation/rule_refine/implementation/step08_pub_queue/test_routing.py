"""Offline execution of transformed legacy routing with DB/Redis/Celery substitutes."""

from __future__ import annotations

import ast
import copy
import datetime
import json
import os
from pathlib import Path
import sys
import types
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
import zipfile

from routing_transforms import SUPPORTED_PATHS, transforms


PUB = "Pub_DeepSeek_V4_Flash"
STG_MODEL = "DeepSeek_V32"
STG_QUEUE = "DeepSeek_V32_queue"
RETIRED = ("DeepSeek_V32", "DeepSeek_V4_Flash_My")
ROOT = (
    None
    if os.environ.get("SEC_KNOW_SOURCE_ROOT")
    else Path(__file__).resolve().parents[4]
)


def original(path):
    source_root = os.environ.get("SEC_KNOW_SOURCE_ROOT")
    if source_root:
        return (Path(source_root) / path).read_text(encoding="utf-8")
    extras = {
        "my_workflows/zeus/rule_refine/background_service.py": "step07_surge_runtime",
        "test/rule_refine/test_background_service.py": "step06w_unified_limits",
    }
    if path in extras:
        return (
            ROOT
            / "validation/rule_refine/implementation"
            / extras[path]
            / "payload"
            / path
        ).read_text(encoding="utf-8")
    with zipfile.ZipFile(ROOT / "validation/sec_know_model.zip") as archive:
        return archive.read(path).decode("utf-8")


def source(path):
    return transforms(path, original(path))


def definitions(path, names, ns=None, baseline=False):
    tree = ast.parse(original(path) if baseline else source(path))
    selected = []
    for node in tree.body:
        if (
            isinstance(node, ast.If)
            and node.body
            and all(
                isinstance(child, (ast.Assign, ast.AnnAssign))
                and isinstance(
                    child.target
                    if isinstance(child, ast.AnnAssign)
                    else child.targets[0],
                    ast.Name,
                )
                and (
                    child.target
                    if isinstance(child, ast.AnnAssign)
                    else child.targets[0]
                ).id
                in names
                for child in node.body
            )
        ):
            selected.append(node)
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names:
            node.decorator_list = (
                [] if isinstance(node, ast.FunctionDef) else node.decorator_list
            )
            selected.append(node)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            target = node.target if isinstance(node, ast.AnnAssign) else node.targets[0]
            if isinstance(target, ast.Name) and target.id in names:
                selected.append(node)
    module = ast.Module(
        body=[
            ast.ImportFrom(
                module="__future__", names=[ast.alias(name="annotations")], level=0
            )
        ]
        + selected,
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {} if ns is None else ns
    exec(compile(module, path, "exec"), namespace)
    return namespace


def model_namespace(env="PRD", baseline=False):
    # This module has no project imports, services, or environment initialization.
    name = "offline_queue_policy_under_test"
    module = types.ModuleType(name)
    with (
        patch.dict(sys.modules, {name: module}),
        patch.dict(os.environ, {"env_profile": env}),
    ):
        exec(
            compile(
                (original if baseline else source)("config/model_limits.py"),
                "config/model_limits.py",
                "exec",
            ),
            module.__dict__,
        )
    return module.__dict__


def vars_like(policy):
    return (
        policy.queue,
        policy.semaphore_key,
        policy.concurrency,
        policy.lease_ms,
        policy.heartbeat_ms,
    )


class FakeRedis:
    def __init__(self, fail_hset=False):
        self.pending = {}
        self.meta = {}
        self.fail_hset = fail_hset

    def time(self):
        return 100, 0

    def zadd(self, key, mapping):
        self.pending.setdefault(key, {}).update(mapping)

    def hset(self, key, mapping):
        if self.fail_hset:
            raise RuntimeError("metadata write failed")
        self.meta[key] = mapping

    def expire(self, *_):
        pass

    def zrem(self, key, task_id):
        self.pending.get(key, {}).pop(task_id, None)

    def delete(self, key):
        self.meta.pop(key, None)


def routing_namespace(env="PRD", redis=None, circuit_open=False, baseline=False):
    redis = redis or FakeRedis()
    ns = model_namespace(env, baseline=baseline)
    loads = []

    class Tracker:
        minimum_samples = 3

        def __init__(self, redis):
            pass

        def snapshot(self, names):
            loads.append(list(names))
            return [
                types.SimpleNamespace(
                    model_name=name, sample_count=1, predicted_load_ms=10, pending=0
                )
                for name in names
            ]

    ns.update(
        ENV=env,
        PRIORITY_ZERO_MODEL_NAME=PUB,
        RedisModelLoadTracker=Tracker,
        observed_load_snapshots=loads,
        datetime=datetime,
        uuid=uuid,
        get_redis=lambda: redis,
        RedisCircuitBreaker=lambda _: types.SimpleNamespace(
            retry_after=lambda _: 10 if circuit_open else 0
        ),
        _routing_lock=lambda _: "owned-token",
        _release_owned_lock=Mock(),
        ROUTING_LOCK_KEY="th:dispatch:routing-lock",
        PENDING_TTL_SECONDS=60,
        DONE_TTL_SECONDS=120,
        pending_key=lambda name: "pending:" + name,
        task_meta_key=lambda name: "meta:" + name,
        logger=Mock(),
        WorkflowTaskModel=lambda **kwargs: types.SimpleNamespace(**kwargs),
    )
    definitions(
        "model/db/crud/workflow_task.py",
        {
            "NoHealthyModel",
            "_select_model",
            "_get_pub_selected_model",
            "get_selected_model",
            "get_priority",
            "create_work_flow_task",
            "create_comm_work_flow_task",
        },
        ns,
        baseline=baseline,
    )
    return ns, redis


class RoutingTests(unittest.TestCase):
    def test_all_supported_sources_compile_and_are_idempotent(self):
        for path in SUPPORTED_PATHS:
            with self.subTest(path=path):
                result = source(path)
                compile(result, path, "exec")
                self.assertTrue(
                    result == transforms(path, result),
                    "transform not idempotent; source suppressed",
                )

    def test_limits_and_environment_defaults(self):
        baseline = model_namespace("STG", baseline=True)["MODEL_POLICY_BY_MODEL_NAME"]
        for env, expected in [("PRD", PUB), ("LOCAL", PUB), ("STG", STG_MODEL)]:
            ns = model_namespace(env)
            policies = ns["MODEL_POLICY_BY_MODEL_NAME"]
            if env == "STG":
                self.assertEqual(
                    {k: vars_like(v) for k, v in policies.items()},
                    {k: vars_like(v) for k, v in baseline.items()},
                )
            else:
                self.assertEqual(set(policies), {PUB, "Pingangpt_pro", "PingAnGPT_R"})
                self.assertEqual(policies[PUB].concurrency, 27)
            self.assertEqual(ns["default_alert_model"](env), expected)
            self.assertEqual(ns["resolve_alert_model"](None, env), expected)
            self.assertEqual(
                ns["resolve_alert_model"]("PingAnGPT_R", env), "PingAnGPT_R"
            )
            for retired in RETIRED:
                if env == "STG":
                    self.assertEqual(ns["resolve_alert_model"](retired, env), retired)
                else:
                    with self.assertRaisesRegex(ValueError, "retired"):
                        ns["resolve_alert_model"](retired, env)

    def test_queue_catalog_and_6f_appendices_preserved(self):
        path = "config/celery_config.py"
        for env in ("PRD", "LOCAL", "STG"):

            def catalog(baseline=False):
                return definitions(
                    path,
                    {"MODEL_QUEUE_CONFIG", "task_queues"},
                    {
                        "Queue": lambda name, *a, **k: name,
                        "default_exchange": object(),
                        "rc": types.SimpleNamespace(ENV=env),
                    },
                    baseline=baseline,
                )

            ns = catalog()
            config = ns["MODEL_QUEUE_CONFIG"]
            if env == "STG":
                before = catalog(True)
                self.assertEqual(config, before["MODEL_QUEUE_CONFIG"])
                self.assertEqual(ns["task_queues"], before["task_queues"])
            else:
                self.assertEqual(
                    config[PUB], {"queue": PUB + "_queue", "concurrency": 27}
                )
                for retired in RETIRED:
                    self.assertNotIn(retired, config)
                    self.assertNotIn(retired + "_queue", ns["task_queues"])
        for path in ["config/celery_config.py", "tasks/task.py"]:
            marker = "\n# Existing registration must remain byte-identical.\nEXISTING_REGISTRATION = True\n"
            self.assertTrue(transforms(path, original(path) + marker).endswith(marker))

    def test_every_priority_dispatches_one_environment_model_and_preserves_payload(
        self,
    ):
        for env, model in [("PRD", PUB), ("LOCAL", PUB), ("STG", STG_MODEL)]:
            for priority in range(10):
                with self.subTest(env=env, priority=priority):
                    ns, redis = routing_namespace(env)
                    ns["get_priority"] = lambda _: (priority, 3)
                    payload = {"alert": {"id": 11}, "logs": [{"a": [1, 2]}]}
                    before = copy.deepcopy(payload)
                    info = types.SimpleNamespace(
                        session_id="s",
                        app_code="zeus",
                        alert_id="11",
                        flow_id="alert_agent",
                        alert_data=payload,
                        dict=lambda: {"alert_data": payload},
                    )
                    task = Mock()
                    task.apply_async.side_effect = lambda **kw: types.SimpleNamespace(
                        id=kw["task_id"]
                    )
                    with patch.dict(
                        sys.modules,
                        {"tasks.task": types.SimpleNamespace(agent_task=task)},
                    ):
                        ns["create_work_flow_task"](Mock(), info)
                    sent = task.apply_async.call_args.kwargs
                    self.assertEqual(sent["priority"], priority)
                    selected = PUB if env == "STG" and priority == 0 else model
                    expected_queue = selected + "_queue"
                    self.assertEqual(sent["queue"], expected_queue)
                    self.assertEqual(sent["routing_key"], expected_queue)
                    self.assertEqual(sent["kwargs"]["model_name"], selected)
                    self.assertIs(sent["kwargs"]["alert_data"], payload)
                    self.assertEqual(payload, before)
                    self.assertEqual(
                        redis.meta["meta:" + sent["task_id"]],
                        {"model": selected, "queue": expected_queue},
                    )

    def test_breaker_blocks_all_priorities_without_fallback(self):
        for priority in [0, 3, 9]:
            ns, redis = routing_namespace(circuit_open=True)
            with self.assertRaises(ns["NoHealthyModel"]):
                ns["get_selected_model"](redis, priority, [PUB], "task")
            self.assertFalse(redis.meta)
            self.assertFalse(any(redis.pending.values()))

    def test_dispatch_and_partial_metadata_failures_release_accounting(self):
        ns, redis = routing_namespace(redis=FakeRedis(fail_hset=True))
        with self.assertRaisesRegex(RuntimeError, "metadata"):
            ns["get_selected_model"](redis, 5, [PUB], "task")
        self.assertFalse(any(redis.pending.values()))
        self.assertFalse(redis.meta)
        ns["_release_owned_lock"].assert_called_once()
        ns, redis = routing_namespace()
        ns["get_priority"] = lambda _: (9, 3)
        task = Mock()
        task.apply_async.side_effect = RuntimeError("broker rejected")
        info = types.SimpleNamespace(
            session_id="s",
            app_code="zeus",
            alert_id="1",
            flow_id="alert_agent",
            alert_data={},
        )
        with patch.dict(
            sys.modules, {"tasks.task": types.SimpleNamespace(agent_task=task)}
        ):
            with self.assertRaisesRegex(RuntimeError, "broker"):
                ns["create_work_flow_task"](Mock(), info)
        self.assertFalse(any(redis.pending.values()))
        self.assertFalse(redis.meta)

    def test_comm_queue_rejects_retired_and_preserves_other_defaults(self):
        ns, _ = routing_namespace()
        queue_config = definitions(
            "config/celery_config.py",
            {"MODEL_QUEUE_CONFIG"},
            {"rc": types.SimpleNamespace(ENV="PRD")},
        )["MODEL_QUEUE_CONFIG"]
        task = Mock()
        modules = {
            "tasks.task": types.SimpleNamespace(comm_agent_task=task),
            "config.celery_config": types.SimpleNamespace(
                MODEL_QUEUE_CONFIG=queue_config
            ),
        }
        with patch.dict(sys.modules, modules):
            for retired in RETIRED:
                info = types.SimpleNamespace(model_queue=retired, params={})
                with self.assertRaisesRegex(ValueError, "retired"):
                    ns["create_comm_work_flow_task"](Mock(), info)
                # The execution model is separate from the queue selector.
                info = types.SimpleNamespace(
                    model_queue=PUB, params={"model_name": retired}
                )
                with self.assertRaisesRegex(ValueError, "retired"):
                    ns["create_comm_work_flow_task"](Mock(), info)
            task.apply_async.assert_not_called()
            info = types.SimpleNamespace(
                model_queue=PUB,
                query="q",
                params={"value": 1},
                app_code="zeus",
                flow_id="alert_agent",
                session_id="s",
            )
            ns["create_comm_work_flow_task"](Mock(), info)
            self.assertEqual(task.apply_async.call_args.kwargs["queue"], PUB + "_queue")
            self.assertIs(
                task.apply_async.call_args.kwargs["kwargs"]["params"], info.params
            )
            info.model_queue = "Pingangpt_pro"
            ns["create_comm_work_flow_task"](Mock(), info)
            self.assertEqual(
                task.apply_async.call_args.kwargs["queue"], "Pingangpt_pro_queue"
            )
            for model in ("PingAnGPT_R",):
                info.model_queue = model
                ns["create_comm_work_flow_task"](Mock(), info)
                self.assertEqual(
                    task.apply_async.call_args.kwargs["queue"], "PingAnGPT_R_queue"
                )

    def test_actual_task_uses_resolved_shared_semaphore_and_breaker(self):
        for env, expected, concurrency in [
            ("PRD", PUB, 27),
            ("LOCAL", PUB, 27),
            ("STG", STG_MODEL, 6),
        ]:
            ns = model_namespace(env)
            semaphore = Mock()
            lease = Mock(spec=["assert_valid"])
            semaphore.acquire.return_value.__enter__ = Mock(return_value=lease)
            semaphore.acquire.return_value.__exit__ = Mock(return_value=False)
            breaker, tracker, redis = Mock(), Mock(), Mock()
            ns.update(
                rc=types.SimpleNamespace(ENV=env),
                get_redis=lambda: redis,
                RedisModelSemaphore=lambda *a, **k: semaphore,
                RedisCircuitBreaker=lambda _: breaker,
                RedisModelLoadTracker=lambda _: tracker,
                pending_key=lambda m: "pending:" + m,
                LOCAL_MODEL_ATTEMPTS=1,
                CIRCUIT_MAX_WAIT_SECONDS=1,
                SEMAPHORE_WAIT_SECONDS=1,
                time=types.SimpleNamespace(monotonic=lambda: 0),
                do_agent_task=Mock(return_value={"done": True}),
                logger=Mock(),
                RedisError=RuntimeError,
                RetryableModelError=RuntimeError,
                ModelCircuitOpen=RuntimeError,
                ModelLeaseLost=RuntimeError,
            )
            definitions("tasks/task.py", {"agent_task"}, ns)
            task = types.SimpleNamespace(request=types.SimpleNamespace(id="task"))
            outcome = ns["agent_task"](task, "zeus", "s", "alert_agent", "1", {})
            self.assertEqual(outcome, {"done": True})
            semaphore.acquire.assert_called_once_with(
                expected, concurrency, wait_timeout=1
            )
            breaker.wait_until_closed.assert_called_once_with(expected, 1)
            breaker.record_success.assert_called_once_with(expected)
            tracker.record_duration.assert_called_once_with(expected, 0)
            self.assertEqual(
                ns["do_agent_task"].call_args.kwargs["model_name"], expected
            )
            redis.zrem.assert_called_once_with("pending:" + expected, "task")

    def test_rule_refinement_default_is_pub_and_still_local_only(self):
        ns = {"Path": Path, "json": json, "__package__": "offline_background"}
        definitions(
            "my_workflows/zeus/rule_refine/background_service.py", {"_settings"}, ns
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config").mkdir()
            (root / "config/rule_refine.analysis.local.json").write_text(
                '{"enabled": true}', encoding="utf-8"
            )
            with patch.dict(
                sys.modules,
                {
                    "offline_background.local_settings": types.SimpleNamespace(
                        _object=dict
                    )
                },
            ):
                settings, reason = ns["_settings"](root, {"env_profile": "LOCAL"})
            self.assertIsNone(reason)
            self.assertEqual(settings["model"], PUB)
            for key in ("env_profile", "ENV_PROFILE"):
                for environment in ("PRD", "STG"):
                    self.assertEqual(
                        ns["_settings"](root, {key: environment}),
                        (None, "local_only"),
                    )

    def test_task_defaults_resolve_before_redis_or_workflow(self):
        for function in ["agent_task", "do_agent_task"]:
            for env, expected in [("PRD", PUB), ("LOCAL", PUB), ("STG", STG_MODEL)]:
                ns = model_namespace(env)
                caught = []

                def resolve(model, environment):
                    caught.append(ns["resolve_original"](model, environment))
                    raise RuntimeError("stop before external work")

                ns["resolve_original"] = ns["resolve_alert_model"]
                ns.update(
                    resolve_alert_model=resolve, rc=types.SimpleNamespace(ENV=env)
                )
                definitions("tasks/task.py", {function}, ns)
                args = dict(
                    app_code="zeus",
                    session_id="s",
                    flow_id="alert_agent",
                    alert_id="1",
                    alert_data={},
                    task_created_at=None,
                )
                if function == "agent_task":
                    args["self"] = object()
                with self.assertRaisesRegex(RuntimeError, "external work"):
                    ns[function](**args)
                self.assertEqual(caught, [expected])
                for retired in RETIRED:
                    error = "external work" if env == "STG" else "retired"
                    with self.assertRaisesRegex((ValueError, RuntimeError), error):
                        ns[function](model_name=retired, **args)

    def test_stg_routing_matches_original_for_every_priority(self):
        for priority in range(10):
            for circuit_open in (False, True):
                outcomes = []
                for baseline in (True, False):
                    ns, redis = routing_namespace(
                        "STG", baseline=baseline, circuit_open=circuit_open
                    )
                    try:
                        outcome = ns["get_selected_model"](
                            redis, priority, [STG_MODEL], "same-task"
                        )
                    except Exception as exc:
                        outcome = (type(exc).__name__, str(exc))
                    outcomes.append(
                        (
                            outcome,
                            redis.pending,
                            redis.meta,
                            ns["observed_load_snapshots"],
                        )
                    )
                self.assertEqual(outcomes[0], outcomes[1])

    def test_io_model_keeps_stg_catalog_and_consolidates_other_environments(self):
        from typing import Literal, get_args

        for env in ("STG", "PRD", "LOCAL"):
            namespace = {"Literal": Literal, "rc": types.SimpleNamespace(ENV=env)}
            ns = definitions("service/io_models.py", {"model_list"}, namespace)
            names = get_args(ns["model_list"])
            if env == "STG":
                before = definitions(
                    "service/io_models.py",
                    {"model_list"},
                    {"Literal": Literal},
                    baseline=True,
                )
                self.assertEqual(names, get_args(before["model_list"]))
            else:
                self.assertIn(PUB, names)
                self.assertNotIn(RETIRED[0], names)
                self.assertNotIn(RETIRED[1], names)
            self.assertNotIn("Qwen38_27B", names)
            self.assertIn("DeepSeek_V4_Flash", names)

    def test_unrelated_task_bodies_and_priorities_are_not_rewritten(self):
        for path, protected in [
            ("tasks/task.py", {"comm_agent_task", "doc_task", "gen_content"}),
            (
                "model/db/crud/workflow_task.py",
                {"get_priority", "update_result_by_task_id", "_release_owned_lock"},
            ),
        ]:
            before = {
                n.name: ast.dump(n)
                for n in ast.parse(original(path)).body
                if isinstance(n, ast.FunctionDef)
            }
            after = {
                n.name: ast.dump(n)
                for n in ast.parse(source(path)).body
                if isinstance(n, ast.FunctionDef)
            }
            for name in protected & before.keys():
                self.assertEqual(before[name], after[name])

    def test_load_comparison_is_bypassed_only_for_prd_and_local(self):
        for env in ("PRD", "LOCAL"):
            ns, _ = routing_namespace(env)
            for priority in range(10):
                self.assertEqual(
                    ns["get_selected_model"](FakeRedis(), priority, [PUB], "task"),
                    (PUB, PUB + "_queue"),
                )
            self.assertEqual(ns["observed_load_snapshots"], [])
        path = "model/db/crud/workflow_task.py"
        before = next(
            node
            for node in ast.parse(original(path)).body
            if isinstance(node, ast.FunctionDef) and node.name == "_select_model"
        )
        after = next(
            node
            for node in ast.parse(source(path)).body
            if isinstance(node, ast.FunctionDef) and node.name == "_select_model"
        )
        self.assertEqual(ast.dump(before), ast.dump(after))


if __name__ == "__main__":
    unittest.main()
