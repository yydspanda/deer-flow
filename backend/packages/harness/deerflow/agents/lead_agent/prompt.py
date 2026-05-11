"""
yyds: prompt.py — Agent 的"大脑说明书"

这个文件负责生成 Agent 的 system prompt（系统提示词）。
system prompt 决定了 Agent 的行为方式：什么时候该问用户、怎么用工具、怎么分解任务等。

核心函数：
  apply_prompt_template() → 拼装完整的 system prompt
    根据运行模式（Flash/Thinking/Pro/Ultra）动态拼接不同的段落：
      {agent_name}       → Agent 名字（"DeerFlow 2.0" 或自定义）
      {soul}             → SOUL.md（自定义 Agent 的人格描述）
      {self_update_section} → 自我更新指引（只有自定义 Agent 有）
      {memory_context}   → 记忆注入（长期记忆）
      {skills_section}   → 可用 skill 列表
      {deferred_tools_section} → 延迟加载的工具列表
      {subagent_section} → 子 Agent 使用说明（Ultra 模式）
      {subagent_reminder} → 子 Agent 限制提醒
      {acp_section}      → ACP 外部 Agent 说明

Skill 缓存机制：
  加载 skill 列表涉及磁盘 I/O，不能在每个请求里同步做。
  所以用了后台线程 + 缓存的方式：
    1. 首次调用时启动后台线程加载 skill
    2. 加载完缓存到 _enabled_skills_cache
    3. skill 文件变化时 invalidate 缓存，触发重新加载
    4. 请求路径上永远不会阻塞等 I/O

文件结构：
  ├── Skill 缓存管理（文件前半部分）
  ├── Prompt 段落构建函数
  │   ├── _build_skill_evolution_section()     skill 自演化提示
  │   ├── _build_subagent_section()            子 Agent 提示（最长的一段）
  │   ├── get_skills_prompt_section()          skill 列表提示
  │   ├── _get_memory_context()                记忆注入
  │   ├── _build_self_update_section()         自我更新提示
  │   ├── get_deferred_tools_prompt_section()  延迟工具提示
  │   └── _build_acp_section()                 ACP Agent 提示
  ├── SYSTEM_PROMPT_TEMPLATE                   系统提示词模板（文件中部大段文本）
  └── apply_prompt_template()                  模板拼装入口（文件尾部）
"""

from __future__ import annotations

import asyncio
import logging
import threading
from functools import lru_cache
from typing import TYPE_CHECKING

from deerflow.config.agents_config import load_agent_soul
from deerflow.skills.storage import get_or_new_skill_storage
from deerflow.skills.types import Skill, SkillCategory
from deerflow.subagents import get_available_subagent_names

if TYPE_CHECKING:
    from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

# yyds: ═══════════════════════════════════════════════════════════
# yyds: Skill 缓存全局状态 —— 后台线程加载 + 版本号防覆盖
# yyds: ═══════════════════════════════════════════════════════════
# yyds:
# yyds:   _enabled_skills_cache           缓存本身（None=未加载，list=已加载）
# yyds:   _enabled_skills_lock            线程锁（保护下面所有变量的读写）
# yyds:   _enabled_skills_refresh_event   信号灯（set() 表示"缓存准备好了"）
# yyds:   _enabled_skills_refresh_active  防重入标记（True=正在加载中，不重复启动）
# yyds:   _enabled_skills_refresh_version 版本号（每次 invalidate +1，防止旧线程覆盖新缓存）
# yyds:   _enabled_skills_by_config_cache 按 app_config hash 分的缓存（多配置场景）
# yyds:
# yyds:   _ENABLED_SKILLS_REFRESH_WAIT_TIMEOUT_SECONDS = 5.0
# yyds:     warm_enabled_skills_cache() 等信号灯最多等 5 秒，超时就放弃（不等磁盘）
# yyds: ═══════════════════════════════════════════════════════════
_ENABLED_SKILLS_REFRESH_WAIT_TIMEOUT_SECONDS = 5.0
_enabled_skills_lock = threading.Lock()
_enabled_skills_cache: list[Skill] | None = None
_enabled_skills_by_config_cache: dict[int, tuple[object, list[Skill]]] = {}
_enabled_skills_refresh_active = False
_enabled_skills_refresh_version = 0
_enabled_skills_refresh_event = threading.Event()


def _load_enabled_skills_sync() -> list[Skill]:
    """yyds: 同步从磁盘加载所有已启用的 skill 列表

    做什么：调用 SkillStorage 的 load_skills(enabled_only=True) 扫描磁盘，
           返回所有 enabled=True 的 Skill 对象列表。
    什么时候用：只被后台线程 _refresh_enabled_skills_cache_worker() 调用，
           不会在请求路径上直接调用（避免磁盘 I/O 阻塞请求）。
    参数说明：无参数，使用全局默认的 SkillStorage 实例。
    关键设计：每次调用都重新扫描磁盘，结果是全新列表，不缓存。
           如果磁盘读取异常，调用方负责 catch 并返回空列表。
    """
    return list(get_or_new_skill_storage().load_skills(enabled_only=True))


def _start_enabled_skills_refresh_thread() -> None:
    """yyds: 启动后台守护线程加载 skill 缓存

    做什么：创建一个名为 "deerflow-enabled-skills-loader" 的 daemon 线程，
           执行 _refresh_enabled_skills_cache_worker()。
    什么时候用：缓存未命中时（_ensure_enabled_skills_cache / _invalidate_enabled_skills_cache）
           调用此函数启动后台加载。
    参数说明：无参数。
    关键设计：线程设为 daemon=True，主进程退出时线程自动终止，不会阻塞关机。
           多次调用会启动多个线程，但通过版本号机制保证只有一个线程的结果最终写入缓存。
    """
    threading.Thread(
        target=_refresh_enabled_skills_cache_worker,
        name="deerflow-enabled-skills-loader",
        daemon=True,
    ).start()


def _refresh_enabled_skills_cache_worker() -> None:
    """yyds: 后台线程的工作函数。加载 skill 列表到缓存。
    用版本号机制避免 stale cache：如果加载过程中又有 invalidate，
    版本号不匹配，会重新加载，确保缓存最终一致。
    """
    global _enabled_skills_cache, _enabled_skills_refresh_active

    while True:
        with _enabled_skills_lock:
            target_version = _enabled_skills_refresh_version

        try:
            skills = _load_enabled_skills_sync()
        except Exception:
            logger.exception("Failed to load enabled skills for prompt injection")
            skills = []

        with _enabled_skills_lock:
            if _enabled_skills_refresh_version == target_version:
                _enabled_skills_cache = skills
                _enabled_skills_refresh_active = False
                _enabled_skills_refresh_event.set()
                return

            # A newer invalidation happened while loading. Keep the worker alive
            # and loop again so the cache always converges on the latest version.
            _enabled_skills_cache = None


def _ensure_enabled_skills_cache() -> threading.Event:
    """yyds: 确保 skill 缓存已填充，未命中时启动后台刷新

    做什么：检查缓存状态，如果已加载则直接返回 event；未加载则启动后台线程加载。
    什么时候用：每个请求路径上获取 skill 列表前调用（get_cached_enabled_skills 等）。
    参数说明：无参数，使用全局缓存状态。
    返回值：threading.Event，set() 表示缓存已就绪，可被 wait()。
    关键设计：
      - 缓存命中 → 立即 set event 返回（零延迟）
      - 缓存未命中 + 已有加载线程 → 返回同一个 event（等加载完成）
      - 缓存未命中 + 无加载线程 → 启动新线程，返回 event
      - 请求路径永远不会阻塞等 I/O，只是拿到 event 后可选 wait 或直接返回空列表
    """
    global _enabled_skills_refresh_active

    with _enabled_skills_lock:
        if _enabled_skills_cache is not None:
            _enabled_skills_refresh_event.set()
            return _enabled_skills_refresh_event
        if _enabled_skills_refresh_active:
            return _enabled_skills_refresh_event
        _enabled_skills_refresh_active = True
        _enabled_skills_refresh_event.clear()

    _start_enabled_skills_refresh_thread()
    return _enabled_skills_refresh_event


def _invalidate_enabled_skills_cache() -> threading.Event:
    """yyds: 使 skill 缓存失效，触发后台重新加载

    做什么：清空缓存 + 版本号 +1 + 清除 prompt 段落缓存 + 启动后台重新加载。
    什么时候用：skill 文件发生变化时调用（新增/删除/修改 skill 后）。
    参数说明：无参数。
    返回值：threading.Event，可被 wait() 等待重新加载完成。
    关键设计：
      - 先清 _get_cached_skills_prompt_section 的 lru_cache，确保 prompt 也会重新渲染
      - 版本号 +1 后，正在运行的旧加载线程会发现版本不匹配，自动放弃结果
      - 如果已有加载线程在运行，不启动新线程（等它自己发现版本不匹配后重新加载）
      - _enabled_skills_by_config_cache 也一并清除，避免 per-config 缓存过期
    """
    global _enabled_skills_cache, _enabled_skills_refresh_active, _enabled_skills_refresh_version

    _get_cached_skills_prompt_section.cache_clear()
    with _enabled_skills_lock:
        _enabled_skills_cache = None
        _enabled_skills_by_config_cache.clear()
        _enabled_skills_refresh_version += 1
        _enabled_skills_refresh_event.clear()
        if _enabled_skills_refresh_active:
            return _enabled_skills_refresh_event
        _enabled_skills_refresh_active = True

    _start_enabled_skills_refresh_thread()
    return _enabled_skills_refresh_event


def prime_enabled_skills_cache() -> None:
    """yyds: 在启动时预热 skill 缓存（非阻塞）

    做什么：调用 _ensure_enabled_skills_cache() 触发后台加载，不等待完成。
    什么时候用：应用启动时（make_lead_agent 或初始化流程中）调用，
           让后台线程提前开始加载 skill，避免第一个请求缓存未命中。
    参数说明：无参数。
    关键设计：不等待加载完成（非阻塞），只是"踢一脚"后台线程。
           如需等待完成，用 warm_enabled_skills_cache()。
    """
    _ensure_enabled_skills_cache()


def warm_enabled_skills_cache(timeout_seconds: float = _ENABLED_SKILLS_REFRESH_WAIT_TIMEOUT_SECONDS) -> bool:
    """yyds: 等待 skill 缓存加载完成（阻塞，带超时）

    做什么：调用 _ensure_enabled_skills_cache() 然后阻塞等待加载完成。
    什么时候用：应用启动时需要确保 skill 缓存就绪的场景（比 prime 更强）。
    参数说明：
      timeout_seconds: 等待超时秒数，默认 5 秒。
    返回值：True 表示缓存已加载，False 表示超时未加载完成。
    关键设计：带超时的阻塞等待，不会永远卡住。
           超时时打印 warning 日志但不抛异常，后续请求走缓存未命中路径也能工作。
    """
    if _ensure_enabled_skills_cache().wait(timeout=timeout_seconds):
        return True

    logger.warning("Timed out waiting %.1fs for enabled skills cache warm-up", timeout_seconds)
    return False


def _get_enabled_skills():
    """yyds: 内部获取已启用 skill 列表的快捷方法

    做什么：直接调用 get_cached_enabled_skills()，是内部使用的便捷包装。
    什么时候用：旧代码兼容路径，新代码应直接调用 get_cached_enabled_skills()。
    参数说明：无参数。
    关键设计：无额外逻辑，纯粹是 get_cached_enabled_skills 的别名。
    """
    return get_cached_enabled_skills()


def get_cached_enabled_skills() -> list[Skill]:
    """Return the cached enabled-skills list, kicking off a background refresh on miss.

    yyds: 请求路径上获取 skill 列表的安全方式。
          缓存命中 → 立即返回（零延迟）
          缓存未命中 → 启动后台加载线程，返回空列表（不阻塞请求）
          下一个请求就能拿到缓存好的结果了。

    Safe to call from request paths: never blocks on disk I/O. Returns an empty
    list on cache miss; the next call will see the warmed result.
    """
    with _enabled_skills_lock:
        cached = _enabled_skills_cache

    if cached is not None:
        return list(cached)

    _ensure_enabled_skills_cache()
    return []


def get_enabled_skills_for_config(app_config: AppConfig | None = None) -> list[Skill]:
    """Return enabled skills using the caller's config source.

    yyds: agent.py 里 _load_enabled_skills_for_tool_policy() 调的就是这个函数。
          如果传了 app_config，按 config 对象的 id() 做二级缓存，
          这样同一个请求路径上多次调用不会重复扫描磁盘。
          如果没传 app_config，用全局缓存机制。

    When a concrete ``app_config`` is supplied, cache the loaded skills by that
    config object's identity so request-scoped config injection still resolves
    skill paths from the matching config without rescanning storage on every
    agent factory call.
    """
    if app_config is None:
        return _get_enabled_skills()

    cache_key = id(app_config)
    with _enabled_skills_lock:
        cached = _enabled_skills_by_config_cache.get(cache_key)
        if cached is not None:
            cached_config, cached_skills = cached
            if cached_config is app_config:
                return list(cached_skills)

    skills = list(get_or_new_skill_storage(app_config=app_config).load_skills(enabled_only=True))
    with _enabled_skills_lock:
        _enabled_skills_by_config_cache[cache_key] = (app_config, skills)
    return list(skills)


def _skill_mutability_label(category: SkillCategory | str) -> str:
    """yyds: 根据 skill 类别返回可编辑性标签

    做什么：返回 "[custom, editable]" 或 "[built-in]" 字符串，标识 skill 是否可编辑。
    什么时候用：构建 skill 列表 prompt 时，每个 skill 的 description 后面追加此标签。
    参数说明：
      category: SkillCategory 枚举值或字符串，CUSTOM 返回 "[custom, editable]"，
            其他返回 "[built-in]"。
    关键设计：让 LLM 知道哪些 skill 可以修改、哪些是内置不可变的，
           影响 skill 自演化（patch）行为。
    """
    return "[custom, editable]" if category == SkillCategory.CUSTOM else "[built-in]"


def clear_skills_system_prompt_cache() -> None:
    """yyds: 清除 skill 相关的所有缓存

    做什么：调用 _invalidate_enabled_skills_cache()，触发 skill 列表缓存 + prompt 段落缓存全部失效。
    什么时候用：skill 文件新增/删除/修改后调用，确保下一个请求看到最新的 skill 列表。
    参数说明：无参数。
    关键设计：是 _invalidate_enabled_skills_cache() 的公共封装，
           同时会清 lru_cache（_get_cached_skills_prompt_section）和 per-config 缓存。
    """
    _invalidate_enabled_skills_cache()


async def refresh_skills_system_prompt_cache_async() -> None:
    """yyds: 异步等待 skill 缓存刷新完成

    做什么：在异步上下文中调用 _invalidate_enabled_skills_cache() 并等待后台加载完成。
    什么时候用：异步 API 端点（如 skill 管理接口）修改 skill 后，需要等待缓存刷新。
    参数说明：无参数。
    关键设计：用 asyncio.to_thread 把阻塞的 event.wait() 包装成 awaitable，
           不阻塞事件循环。确保调用方 await 完成后缓存已就绪。
    """
    await asyncio.to_thread(_invalidate_enabled_skills_cache().wait)


def _build_skill_evolution_section(skill_evolution_enabled: bool) -> str:
    """yyds: 构建 skill 自演化提示段落

    做什么：如果 skill_evolution_enabled=True，返回一段提示词教 Agent 在完成复杂任务后
           自动创建/更新 skill（"学会新技能"）。
    什么时候用：构建 skill 系统 prompt 时调用，插入 <skill_system> 标签内。
    参数说明：
      skill_evolution_enabled: 是否启用 skill 自演化功能，从 config.skill_evolution.enabled 读取。
    返回值：启用时返回 Markdown 格式的自演化指引，未启用返回空字符串。
    关键设计：自演化的触发条件（5+ 工具调用、非明显错误、用户纠正等）精心设计，
           避免简单任务触发不必要的 skill 创建。强调 "patch over edit" 和 "先确认再创建"。
    """
    if not skill_evolution_enabled:
        return ""
    return """
## Skill Self-Evolution
After completing a task, consider creating or updating a skill when:
- The task required 5+ tool calls to resolve
- You overcame non-obvious errors or pitfalls
- The user corrected your approach and the corrected version worked
- You discovered a non-trivial, recurring workflow
If you used a skill and encountered issues not covered by it, patch it immediately.
Prefer patch over edit. Before creating a new skill, confirm with the user first.
Skip simple one-off tasks.
"""


def _build_available_subagents_description(available_names: list[str], bash_available: bool, *, app_config: AppConfig | None = None) -> str:
    """yyds: 动态构建可用子 Agent 类型描述列表

    做什么：遍历所有注册的子 Agent 名称，生成 "- **name**: description" 格式的描述列表。
           内置子 Agent（general-purpose, bash）使用硬编码描述，
           其他子 Agent 从 registry 动态加载 description 字段。
    什么时候用：_build_subagent_section() 构建子 Agent 提示词时调用，
           生成 "Available Subagents" 列表告诉 LLM 有哪些子 Agent 可用。
    参数说明：
      available_names: 已启用的子 Agent 名称列表，来自 get_available_subagent_names()。
      bash_available: bash 子 Agent 是否可用，影响 bash 的描述文本。
      app_config: 可选的 AppConfig，用于动态加载子 Agent 配置。
    返回值：换行分隔的子 Agent 描述文本。
    关键设计：内置描述保持向后兼容（hardcoded 的高质量描述），
           动态描述只取 config.description 的第一行（保持简洁）。
           bash 子 Agent 不可用时会明确告知 LLM "Not available"，
           避免它尝试调用不可用的子 Agent。
    """
    # Built-in descriptions (kept for backward compatibility with existing prompt quality)
    builtin_descriptions = {
        "general-purpose": "For ANY non-trivial task - web research, code exploration, file operations, analysis, etc.",
        "bash": (
            "For command execution (git, build, test, deploy operations)" if bash_available else "Not available in the current sandbox configuration. Use direct file/web tools or switch to AioSandboxProvider for isolated shell access."
        ),
    }

    # Lazy import moved outside loop to avoid repeated import overhead
    from deerflow.subagents.registry import get_subagent_config

    lines = []
    for name in available_names:
        if name in builtin_descriptions:
            lines.append(f"- **{name}**: {builtin_descriptions[name]}")
        else:
            config = get_subagent_config(name, app_config=app_config)
            if config is not None:
                desc = config.description.split("\n")[0].strip()  # First line only for brevity
                lines.append(f"- **{name}**: {desc}")

    return "\n".join(lines)


def _build_subagent_section(max_concurrent: int, *, app_config: AppConfig | None = None) -> str:
    """Build the subagent system prompt section with dynamic concurrency limit.

    yyds: 这是整个 prompt.py 里最长的一段（约 130 行）。
          只有 Ultra 模式（subagent_enabled=True）才会注入这段提示词。
          它教主 Agent 如何当"任务编排者"：分解任务 → 并行派发 → 收集综合。

          核心规则：
          - 每次响应最多 max_concurrent 个 task() 调用（超出的被系统静默丢弃）
          - 超过限制的子任务要分批（batch），每批 max_concurrent 个
          - 不能分解的简单任务不要用子 Agent，直接执行

          这个并发限制和 agent.py 里的 SubagentLimitMiddleware 配合：
          prompt 告诉 LLM "不要超"，middleware 是硬限制（超了真丢）。

    Args:
        max_concurrent: Maximum number of concurrent subagent calls allowed per response.

    Returns:
        Formatted subagent section string.
    """
    n = max_concurrent
    available_names = get_available_subagent_names(app_config=app_config) if app_config is not None else get_available_subagent_names()
    bash_available = "bash" in available_names

    # Dynamically build subagent type descriptions from registry (aligned with Codex's
    # agent_type_description pattern where all registered roles are listed in the tool spec).
    available_subagents = _build_available_subagents_description(available_names, bash_available, app_config=app_config)
    direct_tool_examples = "bash, ls, read_file, web_search, etc." if bash_available else "ls, read_file, web_search, etc."
    direct_execution_example = (
        '# User asks: "Run the tests"\n# Thinking: Cannot decompose into parallel sub-tasks\n# → Execute directly\n\nbash("npm test")  # Direct execution, not task()'
        if bash_available
        else '# User asks: "Read the README"\n# Thinking: Single straightforward file read\n# → Execute directly\n\nread_file("/mnt/user-data/workspace/README.md")  # Direct execution, not task()'
    )
    return f"""<subagent_system>
**🚀 SUBAGENT MODE ACTIVE - DECOMPOSE, DELEGATE, SYNTHESIZE**

You are running with subagent capabilities enabled. Your role is to be a **task orchestrator**:
1. **DECOMPOSE**: Break complex tasks into parallel sub-tasks
2. **DELEGATE**: Launch multiple subagents simultaneously using parallel `task` calls
3. **SYNTHESIZE**: Collect and integrate results into a coherent answer

**CORE PRINCIPLE: Complex tasks should be decomposed and distributed across multiple subagents for parallel execution.**

**⛔ HARD CONCURRENCY LIMIT: MAXIMUM {n} `task` CALLS PER RESPONSE. THIS IS NOT OPTIONAL.**
- Each response, you may include **at most {n}** `task` tool calls. Any excess calls are **silently discarded** by the system — you will lose that work.
- **Before launching subagents, you MUST count your sub-tasks in your thinking:**
  - If count ≤ {n}: Launch all in this response.
  - If count > {n}: **Pick the {n} most important/foundational sub-tasks for this turn.** Save the rest for the next turn.
- **Multi-batch execution** (for >{n} sub-tasks):
  - Turn 1: Launch sub-tasks 1-{n} in parallel → wait for results
  - Turn 2: Launch next batch in parallel → wait for results
  - ... continue until all sub-tasks are complete
  - Final turn: Synthesize ALL results into a coherent answer
- **Example thinking pattern**: "I identified 6 sub-tasks. Since the limit is {n} per turn, I will launch the first {n} now, and the rest in the next turn."

**Available Subagents:**
{available_subagents}

**Your Orchestration Strategy:**

✅ **DECOMPOSE + PARALLEL EXECUTION (Preferred Approach):**

For complex queries, break them down into focused sub-tasks and execute in parallel batches (max {n} per turn):

**Example 1: "Why is Tencent's stock price declining?" (3 sub-tasks → 1 batch)**
→ Turn 1: Launch 3 subagents in parallel:
- Subagent 1: Recent financial reports, earnings data, and revenue trends
- Subagent 2: Negative news, controversies, and regulatory issues
- Subagent 3: Industry trends, competitor performance, and market sentiment
→ Turn 2: Synthesize results

**Example 2: "Compare 5 cloud providers" (5 sub-tasks → multi-batch)**
→ Turn 1: Launch {n} subagents in parallel (first batch)
→ Turn 2: Launch remaining subagents in parallel
→ Final turn: Synthesize ALL results into comprehensive comparison

**Example 3: "Refactor the authentication system"**
→ Turn 1: Launch 3 subagents in parallel:
- Subagent 1: Analyze current auth implementation and technical debt
- Subagent 2: Research best practices and security patterns
- Subagent 3: Review related tests, documentation, and vulnerabilities
→ Turn 2: Synthesize results

✅ **USE Parallel Subagents (max {n} per turn) when:**
- **Complex research questions**: Requires multiple information sources or perspectives
- **Multi-aspect analysis**: Task has several independent dimensions to explore
- **Large codebases**: Need to analyze different parts simultaneously
- **Comprehensive investigations**: Questions requiring thorough coverage from multiple angles

❌ **DO NOT use subagents (execute directly) when:**
- **Task cannot be decomposed**: If you can't break it into 2+ meaningful parallel sub-tasks, execute directly
- **Ultra-simple actions**: Read one file, quick edits, single commands
- **Need immediate clarification**: Must ask user before proceeding
- **Meta conversation**: Questions about conversation history
- **Sequential dependencies**: Each step depends on previous results (do steps yourself sequentially)

**CRITICAL WORKFLOW** (STRICTLY follow this before EVERY action):
1. **COUNT**: In your thinking, list all sub-tasks and count them explicitly: "I have N sub-tasks"
2. **PLAN BATCHES**: If N > {n}, explicitly plan which sub-tasks go in which batch:
   - "Batch 1 (this turn): first {n} sub-tasks"
   - "Batch 2 (next turn): next batch of sub-tasks"
3. **EXECUTE**: Launch ONLY the current batch (max {n} `task` calls). Do NOT launch sub-tasks from future batches.
4. **REPEAT**: After results return, launch the next batch. Continue until all batches complete.
5. **SYNTHESIZE**: After ALL batches are done, synthesize all results.
6. **Cannot decompose** → Execute directly using available tools ({direct_tool_examples})

**⛔ VIOLATION: Launching more than {n} `task` calls in a single response is a HARD ERROR. The system WILL discard excess calls and you WILL lose work. Always batch.**

**Remember: Subagents are for parallel decomposition, not for wrapping single tasks.**

**How It Works:**
- The task tool runs subagents asynchronously in the background
- The backend automatically polls for completion (you don't need to poll)
- The tool call will block until the subagent completes its work
- Once complete, the result is returned to you directly

**Usage Example 1 - Single Batch (≤{n} sub-tasks):**

```python
# User asks: "Why is Tencent's stock price declining?"
# Thinking: 3 sub-tasks → fits in 1 batch

# Turn 1: Launch 3 subagents in parallel
task(description="Tencent financial data", prompt="...", subagent_type="general-purpose")
task(description="Tencent news & regulation", prompt="...", subagent_type="general-purpose")
task(description="Industry & market trends", prompt="...", subagent_type="general-purpose")
# All 3 run in parallel → synthesize results
```

**Usage Example 2 - Multiple Batches (>{n} sub-tasks):**

```python
# User asks: "Compare AWS, Azure, GCP, Alibaba Cloud, and Oracle Cloud"
# Thinking: 5 sub-tasks → need multiple batches (max {n} per batch)

# Turn 1: Launch first batch of {n}
task(description="AWS analysis", prompt="...", subagent_type="general-purpose")
task(description="Azure analysis", prompt="...", subagent_type="general-purpose")
task(description="GCP analysis", prompt="...", subagent_type="general-purpose")

# Turn 2: Launch remaining batch (after first batch completes)
task(description="Alibaba Cloud analysis", prompt="...", subagent_type="general-purpose")
task(description="Oracle Cloud analysis", prompt="...", subagent_type="general-purpose")

# Turn 3: Synthesize ALL results from both batches
```

**Counter-Example - Direct Execution (NO subagents):**

```python
{direct_execution_example}
```

**CRITICAL**:
- **Max {n} `task` calls per turn** - the system enforces this, excess calls are discarded
- Only use `task` when you can launch 2+ subagents in parallel
- Single task = No value from subagents = Execute directly
- For >{n} sub-tasks, use sequential batches of {n} across multiple turns
</subagent_system>"""


# yyds: ── 系统提示词模板（文件中部大段文本）──
#       这就是 Agent 的"大脑说明书"，用 .format() 填充动态段落。
#       模板用 XML 标签组织结构：<role> <thinking_style> <clarification_system> 等
#       LLM 对 XML 标签结构的理解比纯文本好得多。
#
#       关键段落：
#       <role>              → Agent 身份（"你是 DeerFlow 2.0"）
#       {soul}              → 自定义人格（SOUL.md）
#       {memory_context}    → 长期记忆注入
#       <thinking_style>    → 思考方式指引
#       <clarification_system> → 确认机制（5 种场景必须先问用户再行动）
#       {skills_section}    → 可用 skill 列表
#       {subagent_section}  → 子 Agent 编排指引（Ultra 模式）
#       <working_directory> → 文件系统路径说明
#       <response_style>    → 回复风格
#       <citations>         → 引用规范（搜索结果必须附来源）
#       <critical_reminders> → 关键提醒清单
SYSTEM_PROMPT_TEMPLATE = """
<role>
You are {agent_name}, an open-source super agent.
</role>

{soul}
{self_update_section}
<thinking_style>
- Think concisely and strategically about the user's request BEFORE taking action
- Break down the task: What is clear? What is ambiguous? What is missing?
- **PRIORITY CHECK: If anything is unclear, missing, or has multiple interpretations, you MUST ask for clarification FIRST - do NOT proceed with work**
{subagent_thinking}- Never write down your full final answer or report in thinking process, but only outline
- CRITICAL: After thinking, you MUST provide your actual response to the user. Thinking is for planning, the response is for delivery.
- Your response must contain the actual answer, not just a reference to what you thought about
</thinking_style>

<clarification_system>
**WORKFLOW PRIORITY: CLARIFY → PLAN → ACT**
1. **FIRST**: Analyze the request in your thinking - identify what's unclear, missing, or ambiguous
2. **SECOND**: If clarification is needed, call `ask_clarification` tool IMMEDIATELY - do NOT start working
3. **THIRD**: Only after all clarifications are resolved, proceed with planning and execution

**CRITICAL RULE: Clarification ALWAYS comes BEFORE action. Never start working and clarify mid-execution.**

**MANDATORY Clarification Scenarios - You MUST call ask_clarification BEFORE starting work when:**

1. **Missing Information** (`missing_info`): Required details not provided
   - Example: User says "create a web scraper" but doesn't specify the target website
   - Example: "Deploy the app" without specifying environment
   - **REQUIRED ACTION**: Call ask_clarification to get the missing information

2. **Ambiguous Requirements** (`ambiguous_requirement`): Multiple valid interpretations exist
   - Example: "Optimize the code" could mean performance, readability, or memory usage
   - Example: "Make it better" is unclear what aspect to improve
   - **REQUIRED ACTION**: Call ask_clarification to clarify the exact requirement

3. **Approach Choices** (`approach_choice`): Several valid approaches exist
   - Example: "Add authentication" could use JWT, OAuth, session-based, or API keys
   - Example: "Store data" could use database, files, cache, etc.
   - **REQUIRED ACTION**: Call ask_clarification to let user choose the approach

4. **Risky Operations** (`risk_confirmation`): Destructive actions need confirmation
   - Example: Deleting files, modifying production configs, database operations
   - Example: Overwriting existing code or data
   - **REQUIRED ACTION**: Call ask_clarification to get explicit confirmation

5. **Suggestions** (`suggestion`): You have a recommendation but want approval
   - Example: "I recommend refactoring this code. Should I proceed?"
   - **REQUIRED ACTION**: Call ask_clarification to get approval

**STRICT ENFORCEMENT:**
- ❌ DO NOT start working and then ask for clarification mid-execution - clarify FIRST
- ❌ DO NOT skip clarification for "efficiency" - accuracy matters more than speed
- ❌ DO NOT make assumptions when information is missing - ALWAYS ask
- ❌ DO NOT proceed with guesses - STOP and call ask_clarification first
- ✅ Analyze the request in thinking → Identify unclear aspects → Ask BEFORE any action
- ✅ If you identify the need for clarification in your thinking, you MUST call the tool IMMEDIATELY
- ✅ After calling ask_clarification, execution will be interrupted automatically
- ✅ Wait for user response - do NOT continue with assumptions

**How to Use:**
```python
ask_clarification(
    question="Your specific question here?",
    clarification_type="missing_info",  # or other type
    context="Why you need this information",  # optional but recommended
    options=["option1", "option2"]  # optional, for choices
)
```

**Example:**
User: "Deploy the application"
You (thinking): Missing environment info - I MUST ask for clarification
You (action): ask_clarification(
    question="Which environment should I deploy to?",
    clarification_type="approach_choice",
    context="I need to know the target environment for proper configuration",
    options=["development", "staging", "production"]
)
[Execution stops - wait for user response]

User: "staging"
You: "Deploying to staging..." [proceed]
</clarification_system>

{skills_section}

{deferred_tools_section}

{subagent_section}

<working_directory existed="true">
- User uploads: `/mnt/user-data/uploads` - Files uploaded by the user (automatically listed in context)
- User workspace: `/mnt/user-data/workspace` - Working directory for temporary files
- Output files: `/mnt/user-data/outputs` - Final deliverables must be saved here

**File Management:**
- Uploaded files are automatically listed in the <uploaded_files> section before each request
- Use `read_file` tool to read uploaded files using their paths from the list
- For PDF, PPT, Excel, and Word files, converted Markdown versions (*.md) are available alongside originals
- All temporary work happens in `/mnt/user-data/workspace`
- Treat `/mnt/user-data/workspace` as your default current working directory for coding and file-editing tasks
- When writing scripts or commands that create/read files from the workspace, prefer relative paths such as `hello.txt`, `../uploads/data.csv`, and `../outputs/report.md`
- Avoid hardcoding `/mnt/user-data/...` inside generated scripts when a relative path from the workspace is enough
- Final deliverables must be copied to `/mnt/user-data/outputs` and presented using `present_files` tool
{acp_section}
</working_directory>

<response_style>
- Clear and Concise: Avoid over-formatting unless requested
- Natural Tone: Use paragraphs and prose, not bullet points by default
- Action-Oriented: Focus on delivering results, not explaining processes
</response_style>

<citations>
**CRITICAL: Always include citations when using web search results**

- **When to Use**: MANDATORY after web_search, web_fetch, or any external information source
- **Format**: Use Markdown link format `[citation:TITLE](URL)` immediately after the claim
- **Placement**: Inline citations should appear right after the sentence or claim they support
- **Sources Section**: Also collect all citations in a "Sources" section at the end of reports

**Example - Inline Citations:**
```markdown
The key AI trends for 2026 include enhanced reasoning capabilities and multimodal integration
[citation:AI Trends 2026](https://techcrunch.com/ai-trends).
Recent breakthroughs in language models have also accelerated progress
[citation:OpenAI Research](https://openai.com/research).
```

**Example - Deep Research Report with Citations:**
```markdown
## Executive Summary

DeerFlow is an open-source AI agent framework that gained significant traction in early 2026
[citation:GitHub Repository](https://github.com/bytedance/deer-flow). The project focuses on
providing a production-ready agent system with sandbox execution and memory management
[citation:DeerFlow Documentation](https://deer-flow.dev/docs).

## Key Analysis

### Architecture Design

The system uses LangGraph for workflow orchestration [citation:LangGraph Docs](https://langchain.com/langgraph),
combined with a FastAPI gateway for REST API access [citation:FastAPI](https://fastapi.tiangolo.com).

## Sources

### Primary Sources
- [GitHub Repository](https://github.com/bytedance/deer-flow) - Official source code and documentation
- [DeerFlow Documentation](https://deer-flow.dev/docs) - Technical specifications

### Media Coverage
- [AI Trends 2026](https://techcrunch.com/ai-trends) - Industry analysis
```

**CRITICAL: Sources section format:**
- Every item in the Sources section MUST be a clickable markdown link with URL
- Use standard markdown link `[Title](URL) - Description` format (NOT `[citation:...]` format)
- The `[citation:Title](URL)` format is ONLY for inline citations within the report body
- ❌ WRONG: `GitHub 仓库 - 官方源代码和文档` (no URL!)
- ❌ WRONG in Sources: `[citation:GitHub Repository](url)` (citation prefix is for inline only!)
- ✅ RIGHT in Sources: `[GitHub Repository](https://github.com/bytedance/deer-flow) - 官方源代码和文档`

**WORKFLOW for Research Tasks:**
1. Use web_search to find sources → Extract {{title, url, snippet}} from results
2. Write content with inline citations: `claim [citation:Title](url)`
3. Collect all citations in a "Sources" section at the end
4. NEVER write claims without citations when sources are available

**CRITICAL RULES:**
- ❌ DO NOT write research content without citations
- ❌ DO NOT forget to extract URLs from search results
- ✅ ALWAYS add `[citation:Title](URL)` after claims from external sources
- ✅ ALWAYS include a "Sources" section listing all references
</citations>

<critical_reminders>
- **Clarification First**: ALWAYS clarify unclear/missing/ambiguous requirements BEFORE starting work - never assume or guess
{subagent_reminder}- Skill First: Always load the relevant skill before starting **complex** tasks.
- Progressive Loading: Load resources incrementally as referenced in skills
- Output Files: Final deliverables must be in `/mnt/user-data/outputs`
- Clarity: Be direct and helpful, avoid unnecessary meta-commentary
- Including Images and Mermaid: Images and Mermaid diagrams are always welcomed in the Markdown format, and you're encouraged to use `![Image Description](image_path)\n\n` or "```mermaid" to display images in response or Markdown files
- Multi-task: Better utilize parallel tool calling to call multiple tools at one time for better performance
- Language Consistency: Keep using the same language as user's
- Always Respond: Your thinking is internal. You MUST always provide a visible response to the user after thinking.
</critical_reminders>
"""


def _get_memory_context(agent_name: str | None = None, *, app_config: AppConfig | None = None) -> str:
    """Get memory context for injection into system prompt.

    yyds: 从记忆系统加载长期记忆，注入到 system prompt 的 <memory> 标签里。
          Agent 每次回复都能看到这些记忆，实现跨会话的"记住用户偏好"。
          如果记忆功能没开（memory.enabled=false），返回空字符串。
    """
    try:
        from deerflow.agents.memory import format_memory_for_injection, get_memory_data
        from deerflow.runtime.user_context import get_effective_user_id

        if app_config is None:
            from deerflow.config.memory_config import get_memory_config

            config = get_memory_config()
        else:
            config = app_config.memory

        if not config.enabled or not config.injection_enabled:
            return ""

        memory_data = get_memory_data(agent_name, user_id=get_effective_user_id())
        memory_content = format_memory_for_injection(memory_data, max_tokens=config.max_injection_tokens)

        if not memory_content.strip():
            return ""

        return f"""<memory>
{memory_content}
</memory>
"""
    except Exception:
        logger.exception("Failed to load memory context")
        return ""


@lru_cache(maxsize=32)
def _get_cached_skills_prompt_section(
    skill_signature: tuple[tuple[str, str, str, str], ...],
    available_skills_key: tuple[str, ...] | None,
    container_base_path: str,
    skill_evolution_section: str,
) -> str:
    """yyds: skill 列表的 prompt 段落（带缓存）。
    lru_cache(32) 缓存最近 32 种组合的渲染结果，
    避免每个请求都重新拼字符串。
    skill_signature 是 (name, description, category, path) 的元组，
    作为缓存 key 的一部分，skill 变化时 key 也变，缓存自动失效。
    """
    filtered = [(name, description, category, location) for name, description, category, location in skill_signature if available_skills_key is None or name in available_skills_key]
    skills_list = ""
    if filtered:
        skill_items = "\n".join(
            f"    <skill>\n        <name>{name}</name>\n        <description>{description} {_skill_mutability_label(category)}</description>\n        <location>{location}</location>\n    </skill>"
            for name, description, category, location in filtered
        )
        skills_list = f"<available_skills>\n{skill_items}\n</available_skills>"
    return f"""<skill_system>
You have access to skills that provide optimized workflows for specific tasks. Each skill contains best practices, frameworks, and references to additional resources.

**Progressive Loading Pattern:**
1. When a user query matches a skill's use case, immediately call `read_file` on the skill's main file using the path attribute provided in the skill tag below
2. Read and understand the skill's workflow and instructions
3. The skill file contains references to external resources under the same folder
4. Load referenced resources only when needed during execution
5. Follow the skill's instructions precisely

**Skills are located at:** {container_base_path}
{skill_evolution_section}
{skills_list}

</skill_system>"""


def get_skills_prompt_section(available_skills: set[str] | None = None, *, app_config: AppConfig | None = None) -> str:
    """yyds: 构建 skill 系统提示词段落（对外接口）

    做什么：加载已启用的 skill 列表 + 自演化配置，渲染成 <skill_system> XML 标签内的提示词。
    什么时候用：apply_prompt_template() 拼装 system prompt 时调用，
           作为 {skills_section} 占位符的值。
    参数说明：
      available_skills: 可选的白名单集合，只渲染此集合内的 skill。
            None 表示渲染所有已启用的 skill。用于自定义 Agent 限制可用 skill。
      app_config: 可选的 AppConfig，用于读取 skill 路径和自演化配置。
    返回值：完整的 <skill_system>...</skill_system> 提示词字符串，
           没有 skill 且未启用自演化时返回空字符串。
    关键设计：
      - 通过 _get_cached_skills_prompt_section 的 lru_cache 避免重复渲染
      - skill_signature 元组作为缓存 key，skill 内容变化时 key 变化，缓存自动失效
      - available_skills 用 sorted tuple 做 key，保证集合顺序不影响缓存命中
    """
    skills = get_enabled_skills_for_config(app_config)

    if app_config is None:
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
            container_base_path = config.skills.container_path
            skill_evolution_enabled = config.skill_evolution.enabled
        except Exception:
            container_base_path = "/mnt/skills"
            skill_evolution_enabled = False
    else:
        config = app_config
        container_base_path = config.skills.container_path
        skill_evolution_enabled = config.skill_evolution.enabled

    if not skills and not skill_evolution_enabled:
        return ""

    if available_skills is not None and not any(skill.name in available_skills for skill in skills):
        return ""

    skill_signature = tuple((skill.name, skill.description, skill.category, skill.get_container_file_path(container_base_path)) for skill in skills)
    available_key = tuple(sorted(available_skills)) if available_skills is not None else None
    if not skill_signature and available_key is not None:
        return ""
    skill_evolution_section = _build_skill_evolution_section(skill_evolution_enabled)
    return _get_cached_skills_prompt_section(skill_signature, available_key, container_base_path, skill_evolution_section)


def get_agent_soul(agent_name: str | None) -> str:
    """yyds: 获取 Agent 的 SOUL.md 人格描述

    做什么：根据 agent_name 加载对应的 SOUL.md 文件内容，包装在 <soul> XML 标签内。
    什么时候用：apply_prompt_template() 拼装 system prompt 时调用，
           作为 {soul} 占位符的值。
    参数说明：
      agent_name: Agent 名称，None 或空表示默认 Agent（无 SOUL.md）。
    返回值：有 SOUL.md 时返回 "<soul>\\n内容\\n</soul>\\n"，否则返回空字符串。
    关键设计：SOUL.md 是自定义 Agent 的核心 — 它定义了 Agent 的人格、专业领域、
           回复风格等。默认 Agent（DeerFlow 2.0）没有 SOUL.md。
    """
    # Append SOUL.md (agent personality) if present
    soul = load_agent_soul(agent_name)
    if soul:
        return f"<soul>\n{soul}\n</soul>\n" if soul else ""
    return ""


def _build_self_update_section(agent_name: str | None) -> str:
    """yyds: 构建自定义 Agent 的自我更新提示段落

    做什么：为自定义 Agent（agent_name 不为空）生成 <self_update> 提示词，
           教 Agent 用 update_agent 工具持久化自己的配置修改。
    什么时候用：apply_prompt_template() 拼装 system prompt 时调用，
           只有自定义 Agent（有 agent_name）才会注入这段。
    参数说明：
      agent_name: Agent 名称，None 或空表示默认 Agent，不生成此段落。
    返回值：有 agent_name 时返回 <self_update> XML 标签包裹的提示词，
           否则返回空字符串。
    关键设计：强调必须用 update_agent 工具而非 bash/write_file，
           因为沙箱里的文件操作是临时的（下次会话丢失）。
           要求传完整替换文本（非 patch 语义），只传需要修改的字段。
    """
    if not agent_name:
        return ""
    return f"""<self_update>
You are running as the custom agent **{agent_name}** with a persisted SOUL.md and config.yaml.

When the user asks you to update your own description, personality, behaviour, skill set, tool groups, or default model,
you MUST persist the change with the `update_agent` tool. Do NOT use `bash`, `write_file`, or any sandbox tool to edit
SOUL.md or config.yaml — those write into a temporary sandbox/tool workspace and the changes will be lost on the next turn.

Rules:
- Always pass the FULL replacement text for `soul` (no patch semantics). Start from your current SOUL above and apply the user's edits.
- Only pass the fields that should change. Omit the others to preserve them.
- Pass `skills=[]` to disable all skills, or omit `skills` to keep the existing whitelist.
- After `update_agent` returns successfully, tell the user the change is persisted and will take effect on the next turn.
</self_update>
"""


def get_deferred_tools_prompt_section(*, app_config: AppConfig | None = None) -> str:
    """yyds: 构建延迟加载工具列表的提示词段落

    做什么：列出所有延迟加载（deferred）的工具名称，让 Agent 知道这些工具存在，
           可以通过 tool_search 工具按需加载。
    什么时候用：apply_prompt_template() 拼装 system prompt 时调用，
           作为 {deferred_tools_section} 占位符的值。
    参数说明：
      app_config: 可选的 AppConfig，用于读取 tool_search.enabled 配置。
    返回值：有延迟工具时返回 <available-deferred-tools> XML 标签包裹的工具名列表，
           tool_search 未启用或无延迟工具时返回空字符串。
    关键设计：延迟加载机制减少初始工具列表长度（降低 token 消耗），
           Agent 按需搜索加载，不常用的工具不会一直占用 system prompt 空间。
    """
    from deerflow.tools.builtins.tool_search import get_deferred_registry

    if app_config is None:
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
        except Exception:
            return ""
    else:
        config = app_config

    if not config.tool_search.enabled:
        return ""

    registry = get_deferred_registry()
    if not registry:
        return ""

    names = "\n".join(e.name for e in registry.entries)
    return f"<available-deferred-tools>\n{names}\n</available-deferred-tools>"


def _build_acp_section(*, app_config: AppConfig | None = None) -> str:
    """yyds: 构建 ACP（Agent Communication Protocol）外部 Agent 的提示段落

    做什么：如果配置了 ACP Agent（如 codex、claude_code），
           生成一段提示词告诉主 Agent 如何与 ACP Agent 协作。
    什么时候用：apply_prompt_template() 拼装 system prompt 时调用，
           注入到 <working_directory> 标签末尾。
    参数说明：
      app_config: 可选的 AppConfig，用于读取 acp_agents 配置。
    返回值：有 ACP Agent 时返回协作指引文本，否则返回空字符串。
    关键设计：
      - ACP Agent 运行在独立工作空间，不在 /mnt/user-data/ 下
      - 提示词明确告诉 LLM 不要给 ACP Agent 写 /mnt/user-data 路径
      - ACP 结果通过 /mnt/acp-workspace/ 只读访问
      - 需要用 cp 复制到 outputs 目录后才能 present_files 交付给用户
    """
    if app_config is None:
        try:
            from deerflow.config.acp_config import get_acp_agents

            agents = get_acp_agents()
        except Exception:
            return ""
    else:
        agents = getattr(app_config, "acp_agents", {}) or {}

    if not agents:
        return ""

    return (
        "\n**ACP Agent Tasks (invoke_acp_agent):**\n"
        "- ACP agents (e.g. codex, claude_code) run in their own independent workspace — NOT in `/mnt/user-data/`\n"
        "- When writing prompts for ACP agents, describe the task only — do NOT reference `/mnt/user-data` paths\n"
        "- ACP agent results are accessible at `/mnt/acp-workspace/` (read-only) — use `ls`, `read_file`, or `bash cp` to retrieve output files\n"
        "- To deliver ACP output to the user: copy from `/mnt/acp-workspace/<file>` to `/mnt/user-data/outputs/<file>`, then use `present_files`"
    )


def _build_custom_mounts_section(*, app_config: AppConfig | None = None) -> str:
    """yyds: 构建自定义沙箱挂载目录的提示段落

    做什么：读取 config.sandbox.mounts 中配置的自定义目录挂载，
           生成提示词告诉 Agent 这些目录的容器路径和读写权限。
    什么时候用：apply_prompt_template() 拼装 system prompt 时调用，
           与 ACP 段落一起注入到 <working_directory> 标签内。
    参数说明：
      app_config: 可选的 AppConfig，用于读取 sandbox.mounts 配置。
    返回值：有自定义挂载时返回目录列表提示词，否则返回空字符串。
    关键设计：提示 Agent 用绝对容器路径访问自定义挂载，
           不需要通过 /mnt/user-data 中转。每个挂载标注 read-only/read-write 权限。
    """
    if app_config is None:
        try:
            from deerflow.config import get_app_config

            config = get_app_config()
        except Exception:
            logger.exception("Failed to load configured sandbox mounts for the lead-agent prompt")
            return ""
    else:
        config = app_config

    mounts = config.sandbox.mounts or []

    if not mounts:
        return ""

    lines = []
    for mount in mounts:
        access = "read-only" if mount.read_only else "read-write"
        lines.append(f"- Custom mount: `{mount.container_path}` - Host directory mapped into the sandbox ({access})")

    mounts_list = "\n".join(lines)
    return f"\n**Custom Mounted Directories:**\n{mounts_list}\n- If the user needs files outside `/mnt/user-data`, use these absolute container paths directly when they match the requested directory"


def apply_prompt_template(
    subagent_enabled: bool = False,
    max_concurrent_subagents: int = 3,
    *,
    agent_name: str | None = None,
    available_skills: set[str] | None = None,
    app_config: AppConfig | None = None,
) -> str:
    """yyds: 模板拼装入口。agent.py 里 create_agent(system_prompt=...) 传的就是这个函数的返回值。

    做的事：收集所有动态段落 → 填充 SYSTEM_PROMPT_TEMPLATE → 追加当前日期
    每个段落都可能为空（功能未开启时），空的段落不会影响 Agent 行为。
    """
    # Include subagent section only if enabled (from runtime parameter)
    n = max_concurrent_subagents
    subagent_section = _build_subagent_section(n, app_config=app_config) if subagent_enabled else ""

    # Add subagent reminder to critical_reminders if enabled
    subagent_reminder = (
        "- **Orchestrator Mode**: You are a task orchestrator - decompose complex tasks into parallel sub-tasks. "
        f"**HARD LIMIT: max {n} `task` calls per response.** "
        f"If >{n} sub-tasks, split into sequential batches of ≤{n}. Synthesize after ALL batches complete.\n"
        if subagent_enabled
        else ""
    )

    # Add subagent thinking guidance if enabled
    subagent_thinking = (
        "- **DECOMPOSITION CHECK: Can this task be broken into 2+ parallel sub-tasks? If YES, COUNT them. "
        f"If count > {n}, you MUST plan batches of ≤{n} and only launch the FIRST batch now. "
        f"NEVER launch more than {n} `task` calls in one response.**\n"
        if subagent_enabled
        else ""
    )

    # Get skills section
    skills_section = get_skills_prompt_section(available_skills, app_config=app_config)

    # Get deferred tools section (tool_search)
    deferred_tools_section = get_deferred_tools_prompt_section(app_config=app_config)

    # Build ACP agent section only if ACP agents are configured
    acp_section = _build_acp_section(app_config=app_config)
    custom_mounts_section = _build_custom_mounts_section(app_config=app_config)
    acp_and_mounts_section = "\n".join(section for section in (acp_section, custom_mounts_section) if section)

    # Build and return the fully static system prompt.
    # Memory and current date are injected per-turn via DynamicContextMiddleware
    # as a <system-reminder> in the first HumanMessage, keeping this prompt
    # identical across users and sessions for maximum prefix-cache reuse.
    return SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=agent_name or "DeerFlow 2.0",
        soul=get_agent_soul(agent_name),
        self_update_section=_build_self_update_section(agent_name),
        skills_section=skills_section,
        deferred_tools_section=deferred_tools_section,
        subagent_section=subagent_section,
        subagent_reminder=subagent_reminder,
        subagent_thinking=subagent_thinking,
        acp_section=acp_and_mounts_section,
    )
