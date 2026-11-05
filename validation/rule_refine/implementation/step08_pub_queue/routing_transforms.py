"""Small reviewed edits to legacy sources; never imports or starts the target app."""

from __future__ import annotations

import ast
import textwrap

PUB = "Pub_DeepSeek_V4_Flash"
RETIRED = frozenset({"DeepSeek_V32", "DeepSeek_V4_Flash_My"})
SUPPORTED_PATHS = frozenset(
    {
        "config/celery_config.py",
        "config/model_limits.py",
        "model/db/crud/workflow_task.py",
        "tasks/task.py",
        "service/io_models.py",
        "my_workflows/zeus/rule_refine/background_service.py",
        "test/rule_refine/test_background_service.py",
    }
)


def _offset(source, line, column):
    lines = source.splitlines(keepends=True)
    return sum(map(len, lines[: line - 1])) + len(
        lines[line - 1].encode("utf-8")[:column].decode("utf-8")
    )


def _span(source, node):
    return _offset(source, node.lineno, node.col_offset), _offset(
        source, node.end_lineno, node.end_col_offset
    )


def _edit(source, changes):
    for start, end, replacement in sorted(changes, reverse=True):
        source = source[:start] + replacement + source[end:]
    return source


def _node_edit(source, node, replacement):
    return (*_span(source, node), replacement)


def _line_edit(source, first, last, replacement=""):
    lines = source.splitlines(keepends=True)
    return sum(map(len, lines[: first - 1])), sum(map(len, lines[:last])), replacement


def _assignment(source, name):
    for node in ast.parse(source).body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(
                isinstance(target, ast.Name) and target.id == name for target in targets
            ):
                return node
    raise ValueError(f"expected assignment missing: {name}")


def _function(source, name):
    nodes = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    if len(nodes) != 1:
        raise ValueError(f"expected one function: {name}")
    return nodes[0]


def _once(source, before, after):
    if before not in source and after in source:
        return source
    if source.count(before) != 1:
        raise ValueError("reviewed source fragment is missing or ambiguous")
    return source.replace(before, after, 1)


def _consolidated_catalog(source, name, policies=False):
    mapping = _assignment(source, name).value
    if not isinstance(mapping, ast.Dict):
        raise ValueError("model configuration must remain a literal mapping")
    changes = []
    for key, value in zip(mapping.keys, mapping.values):
        if not isinstance(key, ast.Constant):
            raise ValueError("model configuration has a nonliteral key")
        if key.value in RETIRED:
            changes.append(_line_edit(source, key.lineno, value.end_lineno))
        elif key.value == PUB:
            if policies:
                field = next(k.value for k in value.keywords if k.arg == "concurrency")
            else:
                field = next(
                    v
                    for k, v in zip(value.keys, value.values)
                    if isinstance(k, ast.Constant) and k.value == "concurrency"
                )
            changes.append(_node_edit(source, field, "27"))
    return _edit(source, changes)


def _environment_override(source, name, expression, updated_assignment):
    marker = f"# PRD/LOCAL alert consolidation: {name}; STG retains its original value."
    if marker in source:
        return source
    node = _assignment(source, name)
    position = _span(source, node)[1]
    added = f'\n\n{marker}\nif {expression} in {{"PRD", "LOCAL"}}:\n' + textwrap.indent(
        updated_assignment, "    "
    )
    return _edit(source, [(position, position, added)])


def _catalog(source, name, expression, policies=False):
    if f"# PRD/LOCAL alert consolidation: {name};" in source:
        return source
    updated = _consolidated_catalog(source, name, policies=policies)
    assignment = ast.get_source_segment(updated, _assignment(updated, name))
    return _environment_override(source, name, expression, assignment)


_HELPERS = '''

# Services retired in PRD/LOCAL remain available to the unchanged STG path.
RETIRED_ALERT_MODELS = frozenset({"DeepSeek_V32", "DeepSeek_V4_Flash_My"})


def default_alert_model(environment: str) -> str:
    """Keep STG's original task model; its controller substitution is unchanged."""
    environment = environment.upper()
    if environment == "STG":
        return "DeepSeek_V32"
    if environment in {"PRD", "LOCAL"}:
        return "Pub_DeepSeek_V4_Flash"
    raise ValueError(f"unsupported alert environment: {environment!r}")


def resolve_alert_model(model_name: str | None, environment: str) -> str:
    if model_name is None:
        return default_alert_model(environment)
    if environment.upper() in {"PRD", "LOCAL"} and model_name in RETIRED_ALERT_MODELS:
        raise ValueError(f"retired alert model: {model_name!r}")
    return model_name
'''

_SELECTED = """def _get_pub_selected_model(redis, priority, allowed_models, task_id):
    # Only PRD/LOCAL use fixed Pub; STG continues through its original selector.
    selected_model = default_alert_model(ENV)
    if allowed_models is not None:
        names = [resolve_alert_model(name, ENV) for name in allowed_models]
        if selected_model not in names:
            raise ValueError("the environment alert model is not allowed")
    selected_queue = get_model_policy(selected_model).queue
    lock_token: str | None = None
    try:
        lock_token = _routing_lock(redis)
        if RedisCircuitBreaker(redis).retry_after(selected_model):
            raise NoHealthyModel("the environment alert model circuit is open")
        seconds, microseconds = redis.time()
        expires_ms = seconds * 1000 + microseconds // 1000 + PENDING_TTL_SECONDS * 1000
        redis.zadd(pending_key(selected_model), {task_id: expires_ms})
        redis.hset(
            task_meta_key(task_id),
            mapping={"model": selected_model, "queue": selected_queue},
        )
        redis.expire(task_meta_key(task_id), DONE_TTL_SECONDS)
    except Exception:
        # A failed metadata write must not leave dispatch pressure behind.
        try:
            redis.zrem(pending_key(selected_model), task_id)
        finally:
            redis.delete(task_meta_key(task_id))
        raise
    finally:
        if lock_token is not None:
            _release_owned_lock(redis, ROUTING_LOCK_KEY, lock_token)
    return selected_model, selected_queue"""


def _workflow(source):
    imported = "from config.model_limits import MODEL_POLICY_BY_MODEL_NAME, get_model_policy, default_alert_model, resolve_alert_model"
    if imported not in source:
        source = _once(
            source,
            "from config.model_limits import MODEL_POLICY_BY_MODEL_NAME, get_model_policy",
            imported,
        )
    if "def _get_pub_selected_model(" not in source:
        node = _function(source, "get_selected_model")
        original = ast.get_source_segment(source, node)
        signature, body = original.split("\n", 1)
        guarded = (
            signature + '\n    if ENV in {"PRD", "LOCAL"}:\n'
            "        return _get_pub_selected_model(redis, priority, allowed_models, task_id)\n"
            + body
        )
        source = _edit(
            source, [_node_edit(source, node, _SELECTED + "\n\n\n" + guarded)]
        )
    before = """    # 依据当前的任务积压情况获取合适的模型，可用模型如下
    allowed_model = [
        "DeepSeek_V32",
        "DeepSeek_V4_Flash_My",
        "Pub_DeepSeek_V4_Flash",
    ]
    if ENV == "STG":
        allowed_model = ["DeepSeek_V32"]"""
    after = (
        before
        + """
    if ENV in {"PRD", "LOCAL"}:
        allowed_model = [default_alert_model(ENV)]"""
    )
    if after not in source:
        source = _once(source, before, after)
    before = '    queue_config = MODEL_QUEUE_CONFIG.get(workflow_task_info.model_queue, MODEL_QUEUE_CONFIG["Pingangpt_pro"])'
    after = """    # Retired PRD/LOCAL services must not silently fall back to another queue.
    if ENV in {"PRD", "LOCAL"}:
        resolve_alert_model(workflow_task_info.model_queue, ENV)
        if isinstance(workflow_task_info.params, dict):
            resolve_alert_model(workflow_task_info.params.get("model_name"), ENV)
    queue_config = MODEL_QUEUE_CONFIG.get(workflow_task_info.model_queue, MODEL_QUEUE_CONFIG["Pingangpt_pro"])"""
    if "resolve_alert_model(workflow_task_info.model_queue, ENV)" not in source:
        source = _once(source, before, after)
    return source


def _resolve_task_entry(source, name):
    node = _function(source, name)
    args = node.args.args[-len(node.args.defaults) :]
    default = next(
        value for arg, value in zip(args, node.args.defaults) if arg.arg == "model_name"
    )
    changes = [_node_edit(source, default, "None")]
    statement = "    model_name = resolve_alert_model(model_name, rc.ENV)\n"
    if statement.strip() not in ast.get_source_segment(source, node):
        position = _offset(source, node.body[0].lineno, 0)
        changes.append((position, position, statement))
    return _edit(source, changes)


def transforms(path: str, source: str) -> str:
    """Transform one hash-reviewed input, preserving unrelated bytes and appendices."""
    if path not in SUPPORTED_PATHS:
        raise ValueError(f"unsupported routing source: {path}")
    ast.parse(source)
    if path == "config/model_limits.py":
        if "import os as _alert_environment_os" not in source:
            source = _once(
                source,
                "from dataclasses import dataclass",
                "from dataclasses import dataclass\nimport os as _alert_environment_os",
            )
        source = _catalog(
            source,
            "MODEL_POLICY_BY_MODEL_NAME",
            '_alert_environment_os.environ.get("env_profile", "LOCAL").upper()',
            policies=True,
        )
        if "def default_alert_model(" not in source:
            source += _HELPERS
    elif path == "config/celery_config.py":
        source = _catalog(source, "MODEL_QUEUE_CONFIG", "rc.ENV")
        if "# PRD/LOCAL alert consolidation: task_queues;" not in source:
            changes = []
            queues = _assignment(source, "task_queues").value
            for node in queues.elts:
                if (
                    isinstance(node, ast.Call)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value in {name + "_queue" for name in RETIRED}
                ):
                    changes.append(_line_edit(source, node.lineno, node.end_lineno))
            updated = _edit(source, changes)
            assignment = ast.get_source_segment(
                updated, _assignment(updated, "task_queues")
            )
            source = _environment_override(source, "task_queues", "rc.ENV", assignment)
    elif path == "model/db/crud/workflow_task.py":
        source = _workflow(source)
    elif path == "tasks/task.py":
        source = (
            _once(
                source,
                "from config.model_limits import get_model_policy",
                "from config.model_limits import get_model_policy, resolve_alert_model",
            )
            if "from config.model_limits import get_model_policy, resolve_alert_model"
            not in source
            else source
        )
        for name in ("do_agent_task", "agent_task"):
            source = _resolve_task_entry(source, name)
    elif path == "service/io_models.py":
        if "# PRD/LOCAL alert consolidation: model_list;" not in source:
            node = _assignment(source, "model_list")
            changes = [
                _node_edit(source, value, repr(PUB))
                for value in ast.walk(node.value)
                if isinstance(value, ast.Constant) and value.value == "DeepSeek_V32"
            ]
            updated = _edit(source, changes)
            assignment = ast.get_source_segment(
                updated, _assignment(updated, "model_list")
            )
            source = _environment_override(source, "model_list", "rc.ENV", assignment)
    elif path == "my_workflows/zeus/rule_refine/background_service.py":
        source = _once(
            source,
            'model = data.get("model", "DeepSeek_V32")',
            'model = data.get("model", "Pub_DeepSeek_V4_Flash")',
        )
    elif path == "test/rule_refine/test_background_service.py":
        changes = [
            _node_edit(source, value, repr(PUB))
            for value in ast.walk(ast.parse(source))
            if isinstance(value, ast.Constant) and value.value == "DeepSeek_V32"
        ]
        source = _edit(source, changes)
    ast.parse(source)
    return source
