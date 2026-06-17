# 中间件 Debug 实操指南

> 看一百遍源码不如跑一遍测试。每个中间件按以下步骤操作：
>
> 1. 先跑测试，确认全绿
> 2. 挑一个核心测试，在源码里加 print 调试
> 3. 再跑一次，看 print 输出理解数据流
> 4. 理解后删 print

---

## 环境准备

所有命令都在 repo 根目录执行：

```bash
cd /home/yydspei/projects/deer-flow
```

跑测试的通用命令模板：

```bash
PYTHONPATH=. uv run pytest backend/tests/test_xxx.py::TestClassName::test_name -v -s
```

- `-v` 显示每个测试的结果
- `-s` 显示 print 输出（不加这个 print 会被吃掉）
- `::TestClassName::test_name` 可以精确跑单个测试

---

## Day 1 — 最简单的 3 个（热身）

### 1. SubagentLimitMiddleware

**测试文件**: `backend/tests/test_subagent_limit_middleware.py` (165 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/subagent_limit_middleware.py`

#### 先跑全量测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_subagent_limit_middleware.py -v -s
```

#### 核心测试：理解截断逻辑

```bash
PYTHONPATH=. uv run pytest backend/tests/test_subagent_limit_middleware.py::TestTruncateTaskCalls::test_task_calls_exceeding_limit_truncated -v -s
```

**调试步骤**：在源码的 `_truncate_task_calls` 方法里加 print：

```python
# 在 task_indices = [...] 后面加：
print(f"yyds: task调用数={len(task_indices)}, 上限={self.max_concurrent}")
print(f"yyds: 要丢弃的索引={indices_to_drop}")
print(f"yyds: 截断前tool_calls={[tc['name'] for tc in tool_calls]}")
print(f"yyds: 截断后tool_calls={[tc['name'] for tc in truncated_tool_calls]}")
```

再跑一次测试，观察输出。

**你将理解**：
- 只截断 `name=="task"` 的调用，其他工具（bash/read）不受影响
- 保留前 N 个 task，丢弃后面的
- 用 `model_copy` 替换消息（保持相同 id）

#### 清理 print 后跑下一个测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_subagent_limit_middleware.py::TestTruncateTaskCalls::test_non_task_calls_preserved -v -s
```

这个测试验证：4 个 task 被截断到 2 个，但 bash 和 read 调用完好无损。

---

### 2. DanglingToolCallMiddleware

**测试文件**: `backend/tests/test_dangling_tool_call_middleware.py` (215 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/dangling_tool_call_middleware.py`

#### 先跑全量测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_dangling_tool_call_middleware.py -v -s
```

#### 核心测试：理解补丁逻辑

```bash
PYTHONPATH=. uv run pytest backend/tests/test_dangling_tool_call_middleware.py::TestBuildPatchedMessagesPatching::test_single_dangling_call -v -s
```

**调试步骤**：在源码的 `_build_patched_messages` 里加 print：

```python
# 在 existing_tool_msg_ids 收集完后加：
print(f"yyds: 已有ToolMessage的ID={existing_tool_msg_ids}")

# 在 needs_patch 检查后加：
print(f"yyds: 需要补丁={needs_patch}")

# 在 for tc in self._message_tool_calls(msg): 循环里加：
print(f"yyds: 检查tool_call_id={tc_id}, 已存在={tc_id in existing_tool_msg_ids}, 已补过={tc_id in patched_ids}")
```

**你将理解**：
- 第一遍扫描：收集所有已有 ToolMessage 的 ID
- 第二遍扫描：找到有 tool_calls 但没有对应 ToolMessage 的 AIMessage → 在它后面插入合成的错误 ToolMessage
- `patched_ids` 防止同一个 tool_call 被重复补丁

#### 进阶测试：混合情况

```bash
PYTHONPATH=. uv run pytest backend/tests/test_dangling_tool_call_middleware.py::TestBuildPatchedMessagesPatching::test_mixed_responded_and_dangling -v -s
```

一个 AIMessage 有 3 个 tool_call，其中 2 个有 ToolMessage，1 个没有 → 只补那 1 个。

---

### 3. LoopDetectionMiddleware

**测试文件**: `backend/tests/test_loop_detection_middleware.py` (746 行，最大的)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/loop_detection_middleware.py`

#### 先跑全量测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_loop_detection_middleware.py -v -s
```

#### 哈希测试组：理解"相同调用"的定义

```bash
PYTHONPATH=. uv run pytest backend/tests/test_loop_detection_middleware.py::TestHashToolCalls -v -s
```

**调试步骤**：在源码的 `_hash_tool_calls` 里加 print：

```python
# 在 normalized.sort() 后加：
print(f"yyds: 标准化后的调用列表={normalized}")
print(f"yyds: 哈希={hashlib.md5(blob.encode()).hexdigest()[:12]}")
```

跑 `test_order_independent`，看两个不同顺序的调用列表如何产生相同哈希。

#### 核心测试：理解警告→强制停止

```bash
PYTHONPATH=. uv run pytest backend/tests/test_loop_detection_middleware.py::TestLoopDetection::test_hard_stop_at_limit -v -s
```

**调试步骤**：在源码的 `_track_and_check` 里加 print：

```python
# 在 history.append(call_hash) 后加：
print(f"yyds: 线程={thread_id}, 哈希={call_hash}, 窗口历史={history}")
print(f"yyds: 该哈希出现次数={count}, 警告阈值={self.warn_threshold}, 强制阈值={self.hard_limit}")
```

连续跑 5 次相同调用，观察 count 从 1 涨到 5，触发 hard_stop。

#### 频率检测测试：理解第二层

```bash
PYTHONPATH=. uv run pytest backend/tests/test_loop_detection_middleware.py::TestToolFrequencyDetection::test_freq_hard_stop_at_limit -v -s
```

**调试步骤**：在 `_track_and_check` 的 Layer 2 部分加 print：

```python
# 在 freq[name] += 1 后加：
print(f"yyds: 工具={name}, 累计调用={tc_count}, 频率警告阈值={eff_warn}, 频率强制阈值={eff_hard}")
```

观察同一个工具（参数不同）调用 50 次后触发强制停止。

---

## Day 2 — 安全相关 3 个

### 4. SandboxAuditMiddleware

**测试文件**: `backend/tests/test_sandbox_audit_middleware.py` (716 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/sandbox_audit_middleware.py`

#### 先跑全量测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_sandbox_audit_middleware.py -v -s
```

#### 命令分类测试：理解 28 条高危规则

```bash
PYTHONPATH=. uv run pytest backend/tests/test_sandbox_audit_middleware.py::TestClassifyCommand::test_high_risk_classified_as_block -v -s
```

这个测试有 28 个参数化用例，每个都是一条高危命令。逐条看测试参数就能理解规则。

**调试步骤**：在源码的 `_classify_single_command` 里加 print：

```python
# 在 for pattern in _HIGH_RISK_PATTERNS: 循环里加：
if pattern.search(normalized):
    print(f"yyds: 高危匹配! 命令='{normalized[:50]}', 匹配规则='{pattern.pattern}'")
```

#### 复合命令拆分测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_sandbox_audit_middleware.py::TestSplitCompoundCommand -v -s
```

观察 `"safe;rm -rf /"` 被拆分成 `["safe", "rm -rf /"]`，第二个子命令被 block。

#### 实际拦截测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_sandbox_audit_middleware.py::TestSandboxAuditMiddlewareWrapToolCall::test_high_risk_blocks_handler -v -s
```

观察 handler 根本没被调用（返回了错误 ToolMessage），中危命令才调 handler。

---

### 5. ToolErrorHandlingMiddleware

**测试文件**: `backend/tests/test_tool_error_handling_middleware.py` (247 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py`

#### 核心测试：理解 try/except 降级

```bash
PYTHONPATH=. uv run pytest backend/tests/test_tool_error_handling_middleware.py::test_wrap_tool_call_returns_error_tool_message_on_exception -v -s
```

**调试步骤**：在源码的 `_build_error_message` 里加 print：

```python
# 在 content = ... 后加：
print(f"yyds: 工具={tool_name} 执行失败, 错误={detail[:100]}")
print(f"yyds: 生成错误ToolMessage, tool_call_id={tool_call_id}")
```

**你将理解**：
- handler 抛异常 → 被捕获 → 生成 ToolMessage(status="error")
- LLM 看到错误 ToolMessage → 知道这个工具挂了，可以换一个
- GraphBubbleUp 不被捕获（LangGraph 控制流信号）

---

### 6. DynamicContextMiddleware

**测试文件**: `backend/tests/test_dynamic_context_middleware.py` (336 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py`

#### 核心测试：理解 ID 交换技巧

```bash
PYTHONPATH=. uv run pytest backend/tests/test_dynamic_context_middleware.py::test_injects_system_reminder_into_first_human_message -v -s
```

**调试步骤**：在源码的 `_make_reminder_and_user_messages` 里加 print：

```python
# 在 return 前加：
print(f"yyds: reminder_msg.id={reminder_msg.id}, hide_from_ui={reminder_msg.additional_kwargs.get('hide_from_ui')}")
print(f"yyds: user_msg.id={user_msg.id}")
print(f"yyds: reminder内容={reminder_content[:100]}...")
```

**你将理解**：
- reminder_msg 继承原始消息 ID → add_messages 原地替换
- user_msg 用 `{id}__user` 派生 ID → 追加到 reminder 后面
- hide_from_ui=True → 前端不展示

#### 跨午夜测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_dynamic_context_middleware.py::test_midnight_crossing_injects_date_update_as_separate_message -v -s
```

看日期变化时如何注入轻量更新。

---

## Day 3 — 对话增强 3 个

### 7. TitleMiddleware

**测试文件**: `backend/tests/test_title_middleware_core_logic.py` (299 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/title_middleware.py`

#### 核心测试：理解触发条件

```bash
PYTHONPATH=. uv run pytest backend/tests/test_title_middleware_core_logic.py::TestTitleMiddlewareCoreLogic::test_should_generate_title_for_first_complete_exchange -v -s
```

**调试步骤**：在源码的 `_should_generate_title` 里加 print：

```python
# 在 return 前加：
print(f"yyds: enabled={config.enabled}, 已有标题={bool(state.get('title'))}, 用户消息数={len(user_messages)}, 助手消息数={len(assistant_messages)}")
```

#### 同步 vs 异步策略对比

```bash
# 同步版：直接截取前50字符
PYTHONPATH=. uv run pytest backend/tests/test_title_middleware_core_logic.py::TestTitleMiddlewareCoreLogic::test_sync_generate_title_uses_fallback_without_model -v -s

# 异步版：调LLM生成高质量标题
PYTHONPATH=. uv run pytest backend/tests/test_title_middleware_core_logic.py::TestTitleMiddlewareCoreLogic::test_generate_title_uses_async_model_and_respects_max_chars -v -s
```

---

### 8. ClarificationMiddleware

**测试文件**: `backend/tests/test_clarification_middleware.py` (179 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/clarification_middleware.py`

#### 核心测试：理解中断机制

```bash
PYTHONPATH=. uv run pytest backend/tests/test_clarification_middleware.py::TestClarificationCommandIdempotency::test_repeated_tool_call_uses_stable_message_id -v -s
```

**调试步骤**：在源码的 `_handle_clarification` 里加 print：

```python
# 在 return Command(...) 前加：
print(f"yyds: 拦截ask_clarification! 问题={question}")
print(f"yyds: 格式化消息={formatted_message[:100]}")
print(f"yyds: 返回Command(goto=END), 消息ID={self._stable_message_id(tool_call_id, formatted_message)}")
```

**你将理解**：
- `Command(goto=END)` 中断整个 StateGraph
- 确定性 ID 确保重试时替换而不是追加
- 5 种类型有不同的图标（❓🤔🔀⚠️💡）

---

### 9. ViewImageMiddleware

**测试文件**: `backend/tests/test_view_image_middleware.py` (398 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/view_image_middleware.py`

#### 核心测试：理解多模态消息构造

```bash
PYTHONPATH=. uv run pytest backend/tests/test_view_image_middleware.py::TestInjectImageMessage::test_returns_state_update_with_human_message -v -s
```

**调试步骤**：在源码的 `_create_image_details_message` 里加 print：

```python
# 在 for 循环里加：
print(f"yyds: 图片={image_path}, MIME={mime_type}, base64长度={len(base64_data)}")
```

**你将理解**：
- 从 `state["viewed_images"]` 取 base64 数据
- 构造 `[{"type": "text", ...}, {"type": "image_url", ...}]` 多模态 content blocks
- 幂等性检查：已有 "Here are the images you've viewed" 就不再注入

---

## Day 4 — 高级功能 3 个

### 10. TokenUsageMiddleware

**测试文件**: `backend/tests/test_token_usage_middleware.py` (234 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/token_usage_middleware.py`

#### 核心测试：理解步骤归因

```bash
PYTHONPATH=. uv run pytest backend/tests/test_token_usage_middleware.py::TestTokenUsageMiddleware::test_annotates_todo_updates_with_structured_actions -v -s
```

**调试步骤**：在源码的 `_build_attribution` 返回前加 print：

```python
print(f"yyds: 归因={attribution}")
```

**你将理解**：
- 步骤类型推断：tool_batch / subagent_dispatch / todo_update / final_answer / thinking
- write_todos 的前后差异对比 → 精确识别新建/开始/完成/删除
- 归因信息存入 `AIMessage.additional_kwargs["token_usage_attribution"]`

---

### 11. UploadsMiddleware

**测试文件**: `backend/tests/test_uploads_middleware_core_logic.py` (474 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/uploads_middleware.py`

#### 核心测试：理解文件注入

```bash
PYTHONPATH=. uv run pytest backend/tests/test_uploads_middleware_core_logic.py::TestBeforeAgent::test_injects_uploaded_files_tag_into_string_content -v -s
```

**调试步骤**：在源码的 `before_agent` 里加 print：

```python
# 在 files_message = self._create_files_message(...) 后加：
print(f"yyds: 新文件={[f['filename'] for f in new_files]}")
print(f"yyds: 历史文件={[f['filename'] for f in historical_files]}")
print(f"yyds: 注入的<uploaded_files>块={files_message[:200]}...")
```

**你将理解**：
- `additional_kwargs.files` 里取新文件，`uploads_dir` 里扫描历史文件
- 文档大纲（heading → line number）让模型能 `read_file(start_line=N)` 精准定位
- 路径一律转成虚拟路径 `/mnt/user-data/uploads/`

---

### 12. TodoMiddleware

**测试文件**: `backend/tests/test_todo_middleware.py` (302 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/todo_middleware.py`

#### 核心测试 1：上下文丢失检测

```bash
PYTHONPATH=. uv run pytest backend/tests/test_todo_middleware.py::TestBeforeModel::test_injects_reminder_when_todos_exist_but_truncated -v -s
```

**调试步骤**：在源码的 `before_model` 里加 print：

```python
# 在 if not todos: return None 前加：
print(f"yyds: 待办列表={[t.get('content') for t in todos]}")
print(f"yyds: 消息里有write_todos吗={_todos_in_messages(messages)}")
print(f"yyds: 已有提醒吗={_reminder_in_messages(messages)}")
```

**你将理解**：
- state 里有 todos 但消息里找不到 write_todos 调用 → 被摘要截断了
- 注入一条 HumanMessage(name="todo_reminder") 提醒模型

#### 核心测试 2：过早退出预防

```bash
PYTHONPATH=. uv run pytest backend/tests/test_todo_middleware.py::TestAfterModel::test_injects_reminder_and_jumps_to_model_when_incomplete -v -s
```

观察 `{"jump_to": "model", "messages": [reminder]}` 如何强制跳回模型节点。

---

## Day 5 — 最复杂的 3 个

### 13. LLMErrorHandlingMiddleware

**测试文件**: `backend/tests/test_llm_error_handling_middleware.py` (439 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/llm_error_handling_middleware.py`

#### 核心测试：理解熔断器状态机

```bash
PYTHONPATH=. uv run pytest backend/tests/test_llm_error_handling_middleware.py::test_circuit_breaker_trips_and_recovers -v -s
```

**调试步骤**：在源码的 `_check_circuit`、`_record_success`、`_record_failure` 里加 print：

```python
# 在每个状态转换处加：
print(f"yyds: 熔断器状态={self._circuit_state}, 失败次数={self._circuit_failure_count}")
```

**你将理解**：
- closed → 连续失败达阈值 → open（跳闸）
- open + 超时 → half_open（放一个探测）
- half_open + 成功 → closed，失败 → open
- 三态循环的状态机

#### 错误分类测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_llm_error_handling_middleware.py::test_classify_error_read_error_is_retriable -v -s
```

在 `_classify_error` 里加 print 看每种异常怎么被分类为 transient/quota/auth/generic。

---

### 14. SummarizationMiddleware

**测试文件**: `backend/tests/test_summarization_middleware.py` (636 行)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py`

#### 核心测试：理解 Skill Rescue

```bash
PYTHONPATH=. uv run pytest backend/tests/test_summarization_middleware.py::test_skill_rescue_keeps_recent_skill_reads_out_of_summary -v -s
```

**调试步骤**：在源码的 `_partition_with_skill_rescue` 里加 print：

```python
# 在 bundles = self._find_skill_bundles(...) 后加：
print(f"yyds: 找到{len(bundles)}个skill bundle")
for b in bundles:
    print(f"  yyds: bundle在消息{b.ai_index}, 工具调用ID={b.skill_tool_call_ids}, token={b.skill_tool_tokens}")

# 在 rescue_bundles = self._select_bundles_to_rescue(...) 后加：
print(f"yyds: 选择保护{len(rescue_bundles)}个bundle")
```

**你将理解**：
- Skill bundle = AIMessage(调了read_file读skill) + 对应的ToolMessage(含skill内容)
- 按预算选择保护的 bundle（数量上限 + token上限 + 单skill上限）
- AIMessage 拆分：skill 的 tool_calls 保留，非 skill 的压缩掉
- **这就是你踩的 bug 来源**：拆分后 ToolMessage 和 AIMessage 可能不匹配

#### 钩子测试

```bash
PYTHONPATH=. uv run pytest backend/tests/test_summarization_middleware.py::test_before_summarization_hook_receives_messages_before_compression -v -s
```

看 memory_flush_hook 如何在压缩前把消息持久化到记忆系统。

---

### 15. MemoryMiddleware

**测试文件**: 无独立文件，部分覆盖在 `backend/tests/test_lead_agent_model_resolution.py` 里
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/memory_middleware.py`

这个中间件没有独立测试，但源码只有 122 行，逻辑最简单：

```python
# 手动理解：在源码的 after_agent 里加 print
print(f"yyds: memory启用={config.enabled}")
print(f"yyds: thread_id={thread_id}")
print(f"yyds: 过滤后消息数={len(filtered_messages)}, 用户消息={len(user_messages)}, 助手消息={len(assistant_messages)}")
print(f"yyds: 检测到纠正={correction_detected}, 检测到强化={reinforcement_detected}")
print(f"yyds: user_id={user_id}")
```

**你将理解**：
- 只保留 human + ai 消息，过滤工具调用
- 检测纠正/强化语义
- 放入 MemoryQueue（防抖 + 批量 LLM 提取）
- 在入队时捕获 user_id（Timer 线程中 ContextVar 不传播）

---

## 额外：ThreadDataMiddleware

**测试文件**: `backend/tests/test_thread_data_middleware.py` (58 行，最短)
**源码文件**: `backend/packages/harness/deerflow/agents/middlewares/thread_data_middleware.py`

```bash
PYTHONPATH=. uv run pytest backend/tests/test_thread_data_middleware.py -v -s
```

4 个测试覆盖：
- thread_id 从 runtime.context 取
- context 为 None → fallback 到 get_config().configurable
- context 没有thread_id → 同样 fallback
- 哪都没有 → 抛 ValueError

---

## 常用调试技巧

### 1. 只跑一个测试方法

```bash
PYTHONPATH=. uv run pytest backend/tests/test_loop_detection_middleware.py::TestLoopDetection::test_hard_stop_at_limit -v -s
```

### 2. 用 -k 模糊匹配测试名

```bash
PYTHONPATH=. uv run pytest backend/tests/test_loop_detection_middleware.py -k "hard_stop" -v -s
```

### 3. 测试失败时看完整 traceback

```bash
PYTHONPATH=. uv run pytest backend/tests/test_xxx.py -v -s --tb=long
```

### 4. 在测试文件里打断点（需要 pdb）

在源码任意位置加：
```python
import pdb; pdb.set_trace()
```
跑测试时会停在那里，可以交互式查看变量。输入 `c` 继续执行，`q` 退出。

### 5. 加完 print 记得删

```bash
# 跑完测试后检查有没有残留 print
rg "yyds:" backend/packages/harness/deerflow/agents/middlewares/ --no-heading
```

---

## 测试文件速查表

| 中间件 | 测试文件 | 行数 | 测试数 |
|--------|---------|------|--------|
| SubagentLimit | `test_subagent_limit_middleware.py` | 165 | 14 |
| DanglingToolCall | `test_dangling_tool_call_middleware.py` | 215 | 16 |
| LoopDetection | `test_loop_detection_middleware.py` | 746 | 47 |
| SandboxAudit | `test_sandbox_audit_middleware.py` | 716 | 50+ |
| ToolErrorHandling | `test_tool_error_handling_middleware.py` | 247 | 10 |
| DynamicContext | `test_dynamic_context_middleware.py` | 336 | 14 |
| Title | `test_title_middleware_core_logic.py` | 299 | 16 |
| Clarification | `test_clarification_middleware.py` | 179 | 11 |
| ViewImage | `test_view_image_middleware.py` | 398 | 30+ |
| TokenUsage | `test_token_usage_middleware.py` | 234 | 7 |
| Uploads | `test_uploads_middleware_core_logic.py` | 474 | 30+ |
| Todo | `test_todo_middleware.py` | 302 | 25 |
| LLMErrorHandling | `test_llm_error_handling_middleware.py` | 439 | 14 |
| Summarization | `test_summarization_middleware.py` | 636 | 20+ |
| Memory | 无独立测试 | - | - |
| ThreadData | `test_thread_data_middleware.py` | 58 | 4 |
| Guardrail | `test_guardrail_middleware.py` | 344 | 24 |
