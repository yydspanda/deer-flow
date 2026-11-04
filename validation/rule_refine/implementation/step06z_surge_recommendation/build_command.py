"""Build a standalone copy/paste command; include code, never private examples."""

import base64
import hashlib
import json
import textwrap
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ORDER = (
    "context_budget",
    "historical_adapter",
    "surge_materials",
    "ai_review",
    "sampling",
    "surge_runner",
)
REPLAY_ORDER = ("ai_review", "saved_generation")
RESUME_ORDER = ORDER + ("saved_generation", "saved_review", "resume_review")
INDEPENDENT_ORDER = RESUME_ORDER + ("independent_assessment",)
FINAL_REPLAY_ORDER = ("ai_review", "saved_generation", "saved_review")


def contents():
    previous = HERE.parent / "step06y_historical_batches"
    paths = {
        "context_budget": HERE / "context_budget.py",
        "historical_adapter": previous / "adapter.py",
        "surge_materials": HERE / "materials.py",
        "ai_review": HERE / "ai_review.py",
        "sampling": HERE / "sampling.py",
        "surge_runner": HERE / "runner.py",
    }
    return {
        "files": {
            name: path.read_text(encoding="utf-8") for name, path in paths.items()
        }
    }


def replay_contents():
    return {
        "files": {
            name: (HERE / (name + ".py")).read_text(encoding="utf-8")
            for name in REPLAY_ORDER
        }
    }


def resume_contents():
    packet = contents()
    packet["files"].update(
        {
            name: (HERE / (name + ".py")).read_text(encoding="utf-8")
            for name in ("saved_generation", "saved_review", "resume_review")
        }
    )
    return packet


def final_replay_contents():
    return {
        "files": {
            name: (HERE / (name + ".py")).read_text(encoding="utf-8")
            for name in FINAL_REPLAY_ORDER
        }
    }


def independent_contents():
    packet = resume_contents()
    packet["files"]["independent_assessment"] = (
        HERE / "independent_assessment.py"
    ).read_text(encoding="utf-8")
    return packet


def runtime_source(
    packet,
    *,
    offline=False,
    resume=False,
    final_replay=False,
    rereview=False,
    independent=False,
):
    if sum((offline, resume, final_replay, rereview, independent)) > 1:
        raise ValueError("conflicting_tool_modes")
    resume = resume or rereview
    order = (
        INDEPENDENT_ORDER
        if independent
        else FINAL_REPLAY_ORDER
        if final_replay
        else REPLAY_ORDER
        if offline
        else RESUME_ORDER
        if resume
        else ORDER
    )
    entry = (
        "independent_assessment"
        if independent
        else "saved_review"
        if final_replay
        else "saved_generation"
        if offline
        else "resume_review"
        if resume
        else "surge_runner"
    )
    if set(packet["files"]) != set(order):
        raise ValueError("unexpected_tool_modules")
    for name, source in packet["files"].items():
        compile(source, name, "exec")
    raw = json.dumps(packet, ensure_ascii=False, sort_keys=True).encode("utf-8")
    payload = "\n".join(
        textwrap.wrap(base64.b64encode(zlib.compress(raw, 9)).decode("ascii"), 100)
    )
    return (
        '''import base64, hashlib, json, sys, types, zlib
from pathlib import Path
payload = """__PAYLOAD__"""
decoder = zlib.decompressobj()
raw = decoder.decompress(base64.b64decode(payload), __SIZE__ + 1)
if (len(raw) != __SIZE__ or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
        or hashlib.sha256(raw).hexdigest() != "__DIGEST__"):
    raise SystemExit("工具校验失败，未调用模型。")
packet = json.loads(raw)
order = __ORDER__
if set(packet["files"]) != set(order):
    raise SystemExit("工具目录不匹配，未调用模型。")
compiled = {name: compile(source, name, "exec") for name, source in packet["files"].items()}
for name in order:
    module = types.ModuleType(name)
    module.__file__ = str(Path.cwd() / (name + ".py"))
    sys.modules[name] = module
    exec(compiled[name], module.__dict__)
tool_hashes = {name: hashlib.sha256(source.encode("utf-8")).hexdigest() for name, source in packet["files"].items()}
raise SystemExit(sys.modules["__ENTRY__"].main(tool_hashes=tool_hashes__EXTRA_ARGS__))
'''.replace("__PAYLOAD__", payload)
        .replace("__SIZE__", str(len(raw)))
        .replace("__DIGEST__", hashlib.sha256(raw).hexdigest())
        .replace("__ORDER__", repr(order))
        .replace("__ENTRY__", entry)
        .replace("__EXTRA_ARGS__", ", reuse_review_samples=True" if rereview else "")
    )


def context_route_command():
    return (
        "bash <<'SOC_CONTEXT_ROUTE_CHECK'\nset -euo pipefail\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_CONTEXT_ROUTE_CHECK'\n"
        + (HERE / "context_route_check.py").read_text(encoding="utf-8")
        + "\nPY_CONTEXT_ROUTE_CHECK\nSOC_CONTEXT_ROUTE_CHECK\n"
    )


def upstream_metadata_command():
    return (
        "bash <<'SOC_UPSTREAM_CONTEXT_CHECK'\nset -euo pipefail\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_UPSTREAM_CONTEXT_CHECK'\n"
        + (HERE / "upstream_metadata_check.py").read_text(encoding="utf-8")
        + "\nPY_UPSTREAM_CONTEXT_CHECK\nSOC_UPSTREAM_CONTEXT_CHECK\n"
    )


def build():
    command = (
        "bash <<'SOC_SURGE_AI_REVIEW'\nset -euo pipefail\numask 077\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_SURGE_AI_REVIEW'\n"
        + runtime_source(contents())
        + "PY_SURGE_AI_REVIEW\nSOC_SURGE_AI_REVIEW\n"
    )
    (HERE / "REVIEW-SURGE-WITH-AI.sh").write_text(command, encoding="utf-8")
    diagnostic = (
        "bash <<'SOC_PROXY_ERROR_CHECK'\nset -euo pipefail\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_PROXY_ERROR_CHECK'\n"
        + (HERE / "proxy_error_check.py").read_text(encoding="utf-8")
        + "\nPY_PROXY_ERROR_CHECK\nSOC_PROXY_ERROR_CHECK\n"
    )
    (HERE / "CHECK-PROXY-ERROR.sh").write_text(diagnostic, encoding="utf-8")
    (HERE / "CHECK-CONTEXT-ROUTE.sh").write_text(
        context_route_command(), encoding="utf-8"
    )
    (HERE / "CHECK-UPSTREAM-CONTEXT.sh").write_text(
        upstream_metadata_command(), encoding="utf-8"
    )
    replay = (
        "bash <<'SOC_RECHECK_SAVED_GENERATION'\nset -euo pipefail\numask 077\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_SAVED_GENERATION'\n"
        + runtime_source(replay_contents(), offline=True)
        + "PY_SAVED_GENERATION\nSOC_RECHECK_SAVED_GENERATION\n"
    )
    (HERE / "RECHECK-SAVED-GENERATION.sh").write_text(replay, encoding="utf-8")
    resume = (
        "bash <<'SOC_CONTINUE_AI_REVIEW'\nset -euo pipefail\numask 077\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_CONTINUE_AI_REVIEW'\n"
        + runtime_source(resume_contents(), resume=True)
        + "PY_CONTINUE_AI_REVIEW\nSOC_CONTINUE_AI_REVIEW\n"
    )
    (HERE / "CONTINUE-AI-REVIEW.sh").write_text(resume, encoding="utf-8")
    rereview = (
        "bash <<'SOC_REVIEW_WITH_NEW_PROMPT'\nset -euo pipefail\numask 077\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_REVIEW_WITH_NEW_PROMPT'\n"
        + runtime_source(resume_contents(), rereview=True)
        + "PY_REVIEW_WITH_NEW_PROMPT\nSOC_REVIEW_WITH_NEW_PROMPT\n"
    )
    (HERE / "REVIEW-WITH-NEW-PROMPT.sh").write_text(rereview, encoding="utf-8")
    final_replay = (
        "bash <<'SOC_RECHECK_SAVED_REVIEW'\nset -euo pipefail\numask 077\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_RECHECK_SAVED_REVIEW'\n"
        + runtime_source(final_replay_contents(), final_replay=True)
        + "PY_RECHECK_SAVED_REVIEW\nSOC_RECHECK_SAVED_REVIEW\n"
    )
    (HERE / "RECHECK-SAVED-REVIEW.sh").write_text(final_replay, encoding="utf-8")
    independent = (
        "bash <<'SOC_INDEPENDENT_AI_ASSESSMENT'\nset -euo pipefail\numask 077\n"
        "cd \"$HOME/sec_know_model\"\n.venv/bin/python -B - <<'PY_INDEPENDENT_AI_ASSESSMENT'\n"
        + runtime_source(independent_contents(), independent=True)
        + "PY_INDEPENDENT_AI_ASSESSMENT\nSOC_INDEPENDENT_AI_ASSESSMENT\n"
    )
    (HERE / "INDEPENDENT-AI-ASSESSMENT.sh").write_text(independent, encoding="utf-8")
    return command


if __name__ == "__main__":
    build()
