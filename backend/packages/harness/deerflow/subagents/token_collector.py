"""yyds: Sub-Agent Token 用量收集器 — LangChain Callback，统计子 Agent 的 LLM token 消耗。

【大白话讲清楚】
  Sub-agent 在独立线程 + 独立 event loop 里跑，它的 LLM 调用产生的 token 用量
  不会被父 Agent 的 TokenUsageMiddleware 统计到（callback handler 不跨线程传播）。

  解决方案：给每个 sub-agent 创建独立的 SubagentTokenCollector，
  作为 callback 注入 agent.astream()。sub-agent 跑完后，
  调 snapshot_records() 取出记录，传给父 Agent 的 RunJournal。

  去重机制：_counted_run_ids 防止同一个 run_id 被重复统计。
  LangChain 有时会对同一个 LLM 调用触发多次 on_llm_end callback。

【具体例子】
  sub-agent "general-purpose" 跑了 5 轮 LLM 调用：
    on_llm_end 被触发 5 次 → _records 累积 5 条
    → snapshot_records() 返回：
      [
        {source_run_id: "abc", caller: "subagent:general-purpose", input_tokens: 500, output_tokens: 200, total_tokens: 700},
        {source_run_id: "def", caller: "subagent:general-purpose", input_tokens: 300, output_tokens: 150, total_tokens: 450},
        ...
      ]
    → 父 Agent 的 RunJournal 拿到后汇总展示

【在链中的位置】
  executor._aexecute() 里：
    collector = SubagentTokenCollector(caller="subagent:general-purpose")
    agent.astream(state, config={"callbacks": [collector]})
  → astream 过程中每次 LLM 调用结束都触发 on_llm_end
  → _aexecute 结束后 collector.snapshot_records() 取出所有记录

---
Callback handler that collects LLM token usage within a subagent.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import BaseCallbackHandler


class SubagentTokenCollector(BaseCallbackHandler):
    """yyds: Token 收集器 — 每次 LLM 调用结束，记录 token 用量。

    数据流：
      agent.astream() → LLM 调用 → on_llm_end() 触发
        → 这个 run_id 已经统计过了？
        ├─ 是 → 跳过（去重）
        └─ 否 → 提取 usage_metadata → 加入 _records
      → snapshot_records() 返回所有记录
    """

    def __init__(self, caller: str):
        super().__init__()
        self.caller = caller
        self._records: list[dict[str, int | str]] = []
        self._counted_run_ids: set[str] = set()  # yyds: 去重集合，防止同一个 run_id 被统计多次

    def on_llm_end(
        self,
        response: Any,
        *,
        run_id: Any,
        tags: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """yyds: LLM 调用结束时触发 — 提取 token 用量，去重后记录。

        LangChain 的 AIMessage.usage_metadata 格式：
          {input_tokens: 500, output_tokens: 200, total_tokens: 700}
        有些模型不返回 total_tokens，那就用 input + output 算。
        """
        rid = str(run_id)
        if rid in self._counted_run_ids:  # yyds: 去重，同一个 run_id 只统计一次
            return

        for generation in response.generations:
            for gen in generation:
                if not hasattr(gen, "message"):
                    continue
                usage = getattr(gen.message, "usage_metadata", None)
                usage_dict = dict(usage) if usage else {}
                input_tk = usage_dict.get("input_tokens", 0) or 0
                output_tk = usage_dict.get("output_tokens", 0) or 0
                total_tk = usage_dict.get("total_tokens", 0) or 0
                if total_tk <= 0:
                    total_tk = input_tk + output_tk  # yyds: 有些模型不返回 total，自己算
                if total_tk <= 0:  # yyds: 三个值都是 0 → 模型没返回 usage，跳过
                    continue
                self._counted_run_ids.add(rid)
                self._records.append(
                    {
                        "source_run_id": rid,
                        "caller": self.caller,
                        "input_tokens": input_tk,
                        "output_tokens": output_tk,
                        "total_tokens": total_tk,
                    }
                )
                return

    def snapshot_records(self) -> list[dict[str, int | str]]:
        """yyds: 取出所有记录 — _aexecute 结束后调这个，传给父 Agent 的 RunJournal。"""
        return list(self._records)
