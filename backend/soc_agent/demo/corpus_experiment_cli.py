"""Thin HTTP client for internal Mac DEV batch control; no local Runtime loop."""

import argparse
import json
import os
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx

from soc_agent.contracts.memory_drafts import MemoryDraftGenerateCommand
from soc_agent.demo.corpus_experiment_reports import compare_reports, export_draft_report, export_report
from soc_agent.demo.corpus_retest_selection import build_retest_plan, read_retest_plan
from soc_agent.utils.hashing import stable_hash

PREFIX = "/api/soc/dev/corpus-workbench"


def _limit(value):
    if value == "all":
        return 2_147_483_647
    try:
        result = int(value)
        if result > 0:
            return result
    except ValueError:
        pass
    raise argparse.ArgumentTypeError("limit must be a positive count or 'all'")


def parser():
    root = argparse.ArgumentParser(description="内网 DEV 两批经验验证；任务在服务器后台执行，退出终端不会取消任务。")
    root.add_argument("--url", default=os.environ.get("SOC_DEV_API_URL", "http://127.0.0.1:2026"))
    commands = root.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="固定当前样本名单，不调用模型")
    prepare.add_argument("--experiment", required=True)
    prepare.add_argument("--name", default="两批经验验证")
    listing = commands.add_parser("list", help="列出实验或某实验的轮次")
    listing.add_argument("--experiment")
    listing.add_argument("--offset", type=int, default=0)
    candidates = commands.add_parser("candidates", help="分页查看本实验第一批产生的待审/已确认经验")
    candidates.add_argument("--experiment", required=True)
    candidates.add_argument("--stage", choices=("pending", "confirmed", "closed", "all"), default="pending")
    candidates.add_argument("--offset", type=int, default=0)
    draft_plan = commands.add_parser("draft-plan", help="从已保存的运营判断生成起草名单，不调用模型")
    draft_plan.add_argument("--experiment", required=True)
    draft_plan.add_argument("--output", type=Path, required=True)
    draft_plan.add_argument("--limit", type=_limit, default=5)
    draft_plan.add_argument("--candidate", action="append", default=[])
    draft_plan.add_argument("--regenerate", action="store_true", help="明确允许重新生成已有文字，生成期间的新编辑仍不会被覆盖")
    drafting = commands.add_parser("draft-candidates", help="提交固定起草名单，服务器后台执行，不自动审核")
    drafting.add_argument("--plan", type=Path, required=True)
    drafting.add_argument("--request-key")
    draft_status = commands.add_parser("draft-status", help="查看起草任务、模型计量与失败原因")
    draft_status.add_argument("--experiment", required=True)
    draft_status.add_argument("--offset", type=int, default=0)
    draft_status.add_argument("--job")
    draft_retry = commands.add_parser("draft-retry", help="明确重试一次失败起草，可能再次调用模型")
    draft_retry.add_argument("--experiment", required=True)
    draft_retry.add_argument("--job", required=True)
    draft_retry.add_argument("--version", type=int, required=True)
    draft_export = commands.add_parser("draft-export", help="单独导出已结束的后台起草任务与用量，不混入告警研判成本")
    draft_export.add_argument("--experiment", required=True)
    draft_export.add_argument("--output-dir", type=Path, required=True)
    retest = commands.add_parser("retest-plan", help="从已导出的固定报告选择失败项或实际经验使用项；不调用模型")
    retest.add_argument("--report", type=Path, required=True)
    retest.add_argument("--output", type=Path, required=True)
    retest.add_argument("--failed", action="store_true")
    retest.add_argument("--used-memory", action="append", default=[])
    retest.add_argument("--group", action="append", default=[])
    retest.add_argument("--rule", action="append", default=[])
    retest.add_argument("--alert", action="append", default=[])
    retest.add_argument("--all", action="store_true", help="明确选择旧报告全部成员；否则不同筛选条件之间取交集")
    for name, description in (("learn", "第一批后台沉淀，经验仍需运营审核"), ("validate", "第二批只验证，固定已审核经验，不自动学习")):
        command = commands.add_parser(name, help=description)
        command.add_argument("--experiment", required=True)
        command.add_argument("--group", action="append", default=[])
        command.add_argument("--rule", action="append", default=[])
        command.add_argument("--alert", action="append", default=[])
        command.add_argument("--scope", choices=("reuse", "explore", "all"), help="默认 reuse；reuse=验证经验复用；explore=其他告警测试；all=两类均跑、分开统计")
        command.add_argument("--selection-file", type=Path, help="使用 retest-plan 固定名单；自动关联旧轮次，不与范围筛选叠加")
        command.add_argument("--limit", type=_limit, default=5, help="本轮累计上限；5 -> 50 -> all，续跑不重跑已完成项")
        command.add_argument("--concurrency", type=int)
        command.add_argument("--purpose", choices=("memory", "full_flow"), default="memory", help="memory=本轮关闭企业策略；full_flow=按部署默认企业策略跑完整流程")
        command.add_argument("--memory", choices=("snapshot", "none"), default="snapshot", help="none 显式建立无经验对照；第一批始终不使用历史经验")
        command.add_argument("--normalization", choices=("off", "shadow", "apply"))
        command.add_argument("--refresh-normalization", action="store_true")
        command.add_argument("--parent-round")
        command.add_argument("--request-key", help="网络中断后用原值重交，不重复创建轮次")
        command.add_argument("--dry-run", action="store_true", help="只看选择范围，不创建/启动任务")
        command.add_argument("--wait", action="store_true", help="观察至暂停、失败阻断或本次预算完成；退出不取消后台任务")
    for name in ("status", "pause", "resume", "retry-failed", "export"):
        command = commands.add_parser(name)
        command.add_argument("--round", required=True)
        if name == "status":
            command.add_argument("--watch", action="store_true")
        if name == "resume":
            command.add_argument("--limit", type=_limit)
            command.add_argument("--concurrency", type=int)
        if name == "export":
            command.add_argument("--output-dir", type=Path, required=True)
    compare = commands.add_parser("compare", help="比较同一实验的两个固定轮次报告")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    compare.add_argument("--output", type=Path, required=True)
    return root


def _request(client, method, path, **kwargs):
    response = client.request(method, PREFIX + path, **kwargs)
    if response.is_error:
        try:
            message = response.json().get("detail") or response.reason_phrase
        except (ValueError, AttributeError):
            message = response.reason_phrase
        raise ValueError(f"HTTP {response.status_code}: {message}")
    return response.json()


def _print(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str), flush=True)


def _status(client, round_id, *, watch=False):
    previous = None
    while True:
        progress = _request(client, "GET", f"/rounds/{round_id}")
        display = {key: value for key, value in progress.items() if key != "round"}
        display.update({key: progress["round"].get(key) for key in ("round_id", "experiment_id", "state", "state_reason", "execution_limit", "concurrency")})
        if display != previous:
            _print(display)
            previous = display
        if not watch or (progress["round"]["state"] != "running" and not progress["active_count"]):
            return progress
        time.sleep(2)


def _export(client, round_id, output):
    first = _request(client, "GET", f"/rounds/{round_id}")
    if first["round"]["state"] == "running" or first["active_count"]:
        raise ValueError("请先 pause 并等待进行中的任务完成，或等本轮预算完成后导出固定报告")
    offset, rows, source = 0, [], None
    while True:
        page = _request(client, "GET", f"/rounds/{round_id}/results", params={"offset": offset, "limit": 100})
        rows.extend(page["items"])
        source = page["source_identity"]
        offset += len(page["items"])
        if offset >= page["total"]:
            break
        if not page["items"]:
            raise ValueError("report pagination stopped unexpectedly")
    final = _request(client, "GET", f"/rounds/{round_id}")
    # Read-time clocks change while a paused round is idle; only persisted state
    # and job counts determine whether the exported membership stayed fixed.
    if {key: value for key, value in first.items() if key != "timing"} != {key: value for key, value in final.items() if key != "timing"}:
        raise ValueError("导出期间轮次有变化，请暂停后重试；未写入不一致的报告")
    report = {
        "schema_version": "soc.corpus_round_report.v1",
        "round": first["round"],
        "progress": {key: value for key, value in first.items() if key != "round"},
        "source_identity": source,
        "created_at": datetime.now(UTC).isoformat(),
        "rows": rows,
    }
    export_report(report, output.expanduser().resolve())
    _print({"round_id": round_id, "rows": len(rows), "output_dir": str(output.resolve())})


def _draft_export(client, args):
    def read_all():
        jobs = []
        while True:
            page = _request(client, "GET", f"/experiments/{args.experiment}/draft-jobs", params={"limit": 100, "offset": len(jobs)})
            items = page["items"]
            jobs.extend(items)
            if len(items) < 100:
                return jobs

    jobs = read_all()
    if len({job["job_id"] for job in jobs}) != len(jobs) or jobs != read_all():
        raise ValueError("导出期间起草任务变化，请等待任务结束后重试；未写入不一致报告")
    export_draft_report(args.experiment, jobs, args.output_dir.expanduser().resolve())
    _print({"experiment_id": args.experiment, "job_count": len(jobs), "output_dir": str(args.output_dir)})


def _draft_plan(client, args):
    selected, excluded, offset = [], {}, 0
    requested = set(args.candidate)
    seen = set()
    while True:
        page = _request(client, "GET", f"/experiments/{args.experiment}/draft-inputs", params={"limit": 50, "offset": offset})
        for view in page["items"]:
            candidate_id = view["candidate_id"]
            if requested and candidate_id not in requested:
                continue
            seen.add(candidate_id)
            draft = view.get("draft")
            content = (draft or {}).get("content", {})
            has_text = any(content.get(field) for field in ("detection_scenario", "observed_event", "conclusion", "business_rationale", "generalization_boundaries", "invalidation_conditions", "handling_guidance"))
            reason = "未保存运营最终判断" if not content.get("reviewer_verdict") else "候选来源已变化" if view.get("stale") or not view.get("editable") else "已有文字，需明确重新生成" if has_text and not args.regenerate else None
            if reason:
                excluded[candidate_id] = reason
                continue
            if len(selected) < args.limit:
                selected.append(MemoryDraftGenerateCommand(candidate_id=candidate_id, expected_version=draft["version"], candidate_revision=view["candidate_revision"], regenerate=args.regenerate).model_dump(mode="json"))
        offset += len(page["items"])
        if offset >= page["total"]:
            break
        if not page["items"]:
            raise ValueError("候选分页提前结束，未写入起草名单")
    if requested - seen:
        raise ValueError("指定候选不在本实验待审队列：" + ", ".join(sorted(requested - seen)))
    if not selected:
        raise ValueError("没有可起草候选。请在经验审核页选择最终判断并保存草稿；不会使用模型初判或历史标签代填。")
    plan = {"schema_version": "soc.corpus_draft_plan.v1", "experiment_id": args.experiment, "commands": selected}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        args.output.chmod(0o600)
        json.dump(plan, stream, ensure_ascii=False, indent=2)
    _print({"output": str(args.output), "selected_count": len(selected), "excluded": excluded, "model_calls": 0})


def _dispatch(args, client):
    def request(method, path, **kwargs):
        return _request(client, method, path, **kwargs)

    if args.command == "prepare":
        _print(request("POST", "/experiments", json={"experiment_id": args.experiment, "name": args.name}))
    elif args.command == "list":
        _print(request("GET", "/rounds" if args.experiment else "/experiments", params={"offset": args.offset, **({"experiment_id": args.experiment} if args.experiment else {})}))
    elif args.command == "candidates":
        _print(request("GET", f"/experiments/{args.experiment}/candidates", params={"review_stage": args.stage, "offset": args.offset, "limit": 20}))
    elif args.command == "draft-plan":
        _draft_plan(client, args)
    elif args.command == "draft-candidates":
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        if plan.get("schema_version") != "soc.corpus_draft_plan.v1" or not isinstance(plan.get("experiment_id"), str) or not plan.get("commands"):
            raise ValueError("起草名单格式错误或为空，请用 draft-plan 生成")
        commands = [MemoryDraftGenerateCommand.model_validate(value).model_dump(mode="json") for value in plan["commands"]]
        if len({value["candidate_id"] for value in commands}) != len(commands):
            raise ValueError("起草名单包含重复候选")
        key = args.request_key or "draft-cli-" + stable_hash(plan)
        print(f"request-key: {key}（相同名单重复提交会沿用已有任务）", file=sys.stderr, flush=True)
        for offset in range(0, len(commands), 50):
            _print(request("POST", f"/experiments/{plan['experiment_id']}/draft-jobs", json={"commands": commands[offset : offset + 50]}, headers={"Idempotency-Key": f"{key}:{offset}"}))
    elif args.command == "draft-status":
        _print(request("GET", f"/experiments/{args.experiment}/draft-jobs" + (f"/{args.job}" if args.job else ""), params={"offset": args.offset, "limit": 50} if not args.job else {}))
    elif args.command == "draft-retry":
        _print(request("POST", f"/experiments/{args.experiment}/draft-jobs/{args.job}/retry", params={"expected_version": args.version}))
    elif args.command == "draft-export":
        _draft_export(client, args)
    elif args.command in {"learn", "validate"}:
        batch = "learning" if args.command == "learn" else "validation"
        selection = {"batch": batch, "scope": args.scope or "reuse", "group_ids": args.group, "rule_codes": args.rule, "alert_ids": args.alert}
        retest = None
        if args.selection_file:
            if args.group or args.rule or args.alert or args.scope is not None or args.parent_round:
                raise ValueError("复测名单已固定范围和旧轮次，不能再同时指定 group/rule/alert/scope/parent-round")
            retest = read_retest_plan(args.selection_file)
            if retest.experiment_id != args.experiment or retest.selection.batch != batch:
                raise ValueError("复测名单的实验或批次与命令不一致")
            selection = retest.selection.model_dump(mode="json")
        defaults = request("GET", "/experiments/configuration")
        options = dict(defaults["defaults"] if args.purpose == "memory" else defaults["full_flow_defaults"])
        if args.normalization is not None:
            options["normalization_review_mode"] = args.normalization
        options["refresh_normalization"] = args.refresh_normalization
        if args.dry_run:
            _print(request("POST", f"/experiments/{args.experiment}/selection", json=selection))
            return
        key = args.request_key or f"corpus-cli-{uuid4().hex}"
        print(f"request-key: {key}（响应丢失时，可用 --request-key 原值重交）", file=sys.stderr, flush=True)
        body = {
            "experiment_id": args.experiment,
            "selection": selection,
            "execution_limit": args.limit,
            "concurrency": args.concurrency if args.concurrency is not None else min(3, defaults["max_concurrency"]),
            "purpose": args.purpose,
            "options": options,
            "memory_mode": args.memory,
            "parent_round_id": retest.provenance.parent_round_id if retest else args.parent_round,
            "retest_provenance": retest.provenance.model_dump(mode="json") if retest else None,
        }
        round_ = request("POST", "/rounds", json=body, headers={"Idempotency-Key": key})
        result = request("POST", f"/rounds/{round_['round_id']}/start", json={})
        _print({key: result.get(key) for key in ("round_id", "experiment_id", "state", "execution_limit", "concurrency")})
        if args.wait:
            _status(client, round_["round_id"], watch=True)
    elif args.command == "status":
        _status(client, args.round, watch=args.watch)
    elif args.command == "export":
        _export(client, args.round, args.output_dir)
    else:
        action = "start" if args.command == "resume" else args.command
        body = {key: value for key, value in {"execution_limit": args.limit, "concurrency": args.concurrency}.items() if value is not None} if args.command == "resume" else None
        _print(request("POST", f"/rounds/{args.round}/{action}", **({"json": body} if body is not None else {})))


def main(argv=None, *, client=None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "retest-plan":
            plan = build_retest_plan(args.report, failed=args.failed, used_memory_ids=args.used_memory, group_ids=args.group, rule_codes=args.rule, alert_ids=args.alert, all_rows=args.all)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                args.output.chmod(0o600)
                stream.write(plan.model_dump_json(indent=2) + "\n")
            _print(
                {
                    "output": str(args.output),
                    "experiment_id": plan.experiment_id,
                    "parent_round_id": plan.provenance.parent_round_id,
                    "selected_count": len(plan.selection.alert_ids),
                    "batch": plan.selection.batch,
                    "scope": plan.selection.scope,
                    "model_calls": 0,
                }
            )
            return 0
        if args.command == "compare":

            def load(path):
                return json.loads((path / "report.json" if path.is_dir() else path).read_text(encoding="utf-8"))

            result = compare_reports(load(args.before), load(args.after))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8") as stream:
                args.output.chmod(0o600)
                json.dump(result, stream, ensure_ascii=False, indent=2)
            _print({key: value for key, value in result.items() if key != "rows"})
            return 0
        headers = {}
        if token := os.environ.get("SOC_DEV_API_TOKEN"):
            headers["Authorization"] = f"Bearer {token}"
        with nullcontext(client) if client is not None else httpx.Client(base_url=args.url, headers=headers, timeout=120.0, trust_env=False) as http:
            _dispatch(args, http)
        return 0
    except (OSError, ValueError, httpx.HTTPError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("已退出命令。服务器已受理的任务仍继续，可用 status 查询或 pause 暂停。", file=sys.stderr)
        return 130
