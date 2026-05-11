"""yyds: 线程数据中间件 — 整个中间件链的第 1 个中间件，也是最重要一个。

═══════════════════════════════════════════════════════════════════════════════
一、它在做什么？（一句话）
═══════════════════════════════════════════════════════════════════════════════

   为当前 thread 计算三个隔离目录的路径，写入 state["thread_data"]。

═══════════════════════════════════════════════════════════════════════════════
二、为什么要这样做？（问题 → 方案）
═══════════════════════════════════════════════════════════════════════════════

   问题：Agent 需要读写文件（执行代码、保存结果、接收用户上传）。
         如果多个 thread 共用同一个目录，文件会互相覆盖。
         如果多个用户共用同一个目录，数据会泄露。

   方案：每个 thread 有独立目录，每个用户也有独立目录。
         路径 = 基于thread_id和user_id计算的"隔离空间"。

   最终的目录树（宿主机上）：

    本地开发（user_id="default"）：
      .deer-flow/users/default/threads/{thread_id}/user-data/
        ├── workspace/    ← Agent 工作目录，bash 在这里执行
        ├── uploads/      ← 用户上传的文件被放这里
        └── outputs/      ← Agent 生成的产出物

    企业部署（user_id=真实 UUID）：
      .deer-flow/users/{user_id}/threads/{thread_id}/user-data/
        ├── workspace/
        ├── uploads/
        └── outputs/

    注意：路径永远是 users/{uid}/threads/{tid}/ 格式。
    .deer-flow/threads/{tid}/ 是遗留路径（user_id=None 时），2.0 基本不用。

   Agent 在沙箱里看到的路径是虚拟的（/mnt/user-data/workspace/），
   通过 Docker volume mount 映射到宿主机的真实路径。
   这个映射关系由 Paths 类管理，不在本中间件里。

═══════════════════════════════════════════════════════════════════════════════
三、它怎么区分用户的？（user_id 的来源）
═══════════════════════════════════════════════════════════════════════════════

   user_id 来自 user_context.py 中的 ContextVar（异步任务级变量）：

   ┌──────────────────────────────────────────────────────────┐
   │  用户请求 → Gateway auth 中间件 → set_current_user(user) │
   │                                    写入 ContextVar        │
   │                                                          │
   │  本中间件 → get_effective_user_id()                      │
   │              从 ContextVar 读取 user_id                  │
   │              没有登录用户 → 返回 "default"               │
   └──────────────────────────────────────────────────────────┘

    所以：
    - 本地开发 → user_id = "default" → .deer-flow/users/default/threads/{tid}/
    - 企业部署 → user_id = "abc123"  → .deer-flow/users/abc123/threads/{tid}/
    get_effective_user_id() 永远不返回 None，所以永远走 users/ 路径。

   路径安全：thread_id 和 user_id 都用正则 ``^[A-Za-z0-9_-]+$`` 校验，
   拒绝 ../ 等路径遍历攻击（见 paths.py 的 _validate_thread_id / _validate_user_id）。

═══════════════════════════════════════════════════════════════════════════════
四、为什么这里定义了 ThreadDataMiddlewareState，而不是直接用 ThreadState？
═══════════════════════════════════════════════════════════════════════════════

   这是个"最小声明"原则：

   ThreadState 是整个 Agent 的完整状态（messages + sandbox + thread_data
   + title + artifacts + todos + uploaded_files + viewed_images，8 个字段）。

   但这个中间件只关心 2 个字段：messages 和 thread_data。
   其余 6 个字段它根本不碰。

   所以它定义了 ThreadDataMiddlewareState，只声明自己会读写的字段：
     - messages: NotRequired[...]   ← 来自 AgentState（父类已有）
     - thread_data: NotRequired[ThreadDataState | None]  ← 本中间件写入的

   这样做的好处：
   1. 类型安全：中间件的 before_agent() 签名明确声明它只操作哪些字段
   2. 解耦：中间件不需要 import 完整的 ThreadState，不依赖其他中间件的字段
   3. 可测试：测试时只需要构造 {"messages": [...]} 而不需要构造完整的 ThreadState

   LangGraph 的 state 机制是"duck typing"——只要返回的 dict 里有 thread_data 字段，
   LangGraph 就会自动把它合并到全局 ThreadState 里。所以各中间件可以各自声明自己的
   "视角"，最终 LangGraph 会把它们全部合并。

═══════════════════════════════════════════════════════════════════════════════
五、执行流程（before_agent 阶段，链中最先执行）
═══════════════════════════════════════════════════════════════════════════════

   输入：state（包含 messages）+ runtime（包含 thread_id 等上下文）

   Step 1: 拿 thread_id
     ├── 先从 runtime.context["thread_id"] 取（Gateway 传入的）
     └── 取不到就从 LangGraph get_config()["configurable"]["thread_id"] 取
     └── 都没有 → 抛 ValueError，Agent 无法运行

   Step 2: 拿 user_id
     └── get_effective_user_id() → ContextVar → "default" 或真实 user_id

   Step 3: 计算路径
     ├── lazy_init=True（默认）→ _get_thread_paths() → 只算路径字符串，不创建目录
     └── lazy_init=False        → _create_thread_directories() → 算路径 + mkdir -p + chmod 777

   Step 4: 给最后一条 HumanMessage 注入元数据
     └── 在 additional_kwargs 里加 run_id 和 timestamp
     └── 用于追踪"这条消息是哪次运行、什么时间发出的"

   输出：{"thread_data": {workspace_path, uploads_path, outputs_path}, "messages": [...]}

═══════════════════════════════════════════════════════════════════════════════
六、谁依赖它的输出？
═══════════════════════════════════════════════════════════════════════════════

   state["thread_data"] 被以下中间件/模块消费：

   1. UploadsMiddleware  → 读 uploads_path，把用户上传的文件放到正确位置
   2. SandboxMiddleware  → 读全部三个路径，作为 Docker volume mount 的源目录
   3. bash_tool / write_file 等沙箱工具 → 通过 thread_data 知道"工作目录在哪"

   所以它必须在最前面执行。这也是它排在中间件链第 1 位的原因。

═══════════════════════════════════════════════════════════════════════════════
七、lazy_init 两种模式对比
═══════════════════════════════════════════════════════════════════════════════

   lazy_init=True（默认，生产用）：
     - before_agent() 只计算路径字符串（纯内存操作，零 I/O）
     - 目录在 Sandbox 中间件 acquire() 时才真正 mkdir
     - 好处：如果这次 Agent 调用不需要文件操作（比如只是问答），就省了磁盘 I/O

   lazy_init=False（测试或特殊场景用）：
     - before_agent() 立即 mkdir -p + chmod 777
     - 确保目录在本中间件返回后就可用
     - chmod 777 是因为沙箱容器可能以不同 UID 运行，需要写权限
"""

import logging
from datetime import UTC, datetime
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.config import get_config
from langgraph.runtime import Runtime

from deerflow.agents.thread_state import ThreadDataState
from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


class ThreadDataMiddlewareState(AgentState):
    """yyds: 本中间件的"状态视角"——只声明自己会读写的字段。

    为什么不直接用 ThreadState？
    因为 ThreadState 有 8 个字段（messages/sandbox/thread_data/title/artifacts/...），
    但本中间件只碰 messages 和 thread_data。声明一个精简的 schema 表示：
    "我只关心这些字段，其余的我不知道也不碰"。

    LangGraph 的 state 合并机制是 duck typing：
    只要 before_agent() 返回的 dict 里有 thread_data 和 messages，
    LangGraph 就会自动把它们合并到全局 ThreadState 里。

    所以各中间件可以各自定义自己的"最小视角"，互不耦合。
    """

    thread_data: NotRequired[ThreadDataState | None]


class ThreadDataMiddleware(AgentMiddleware[ThreadDataMiddlewareState]):
    """yyds: 中间件链第 1 个中间件。为每个 thread 计算隔离的文件目录路径。

    它是整个文件系统的"地基"——没有它，后续的沙箱、上传、工具执行都不知道
    文件该放哪。它在 before_agent 阶段执行，输出 state["thread_data"]。

    目录结构（本地开发为例，user_id="default"）：
      .deer-flow/users/default/threads/{thread_id}/user-data/
        ├── workspace/    Agent 在这里读写文件、执行命令
        ├── uploads/      用户上传的文件被存放在这里
        └── outputs/      Agent 生成的产出物（报告、图片等）
    """

    state_schema = ThreadDataMiddlewareState

    def __init__(self, base_dir: str | None = None, lazy_init: bool = True):
        """yyds: 初始化中间件。

        Args:
            base_dir: 根目录。默认从 get_paths() 获取（→ .deer-flow/）。
                      测试时可以传 tmp_path 来隔离。
            lazy_init: True=只算路径不创建目录（默认，性能好）。
                       False=立即 mkdir -p + chmod 777（测试用）。
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()
        self._lazy_init = lazy_init

    def _get_thread_paths(self, thread_id: str, user_id: str | None = None) -> dict[str, str]:
        """yyds: 纯计算——根据 thread_id 和 user_id 算出三个目录的绝对路径。

        做且只做一件事：把 Paths 对象的三个方法调用打包成 dict。

        user_id 怎么影响路径？
          user_id=None  → .deer-flow/threads/{thread_id}/user-data/workspace
          user_id="u1"  → .deer-flow/users/u1/threads/{thread_id}/user-data/workspace

        不做任何 I/O 操作（不检查目录是否存在，不创建目录）。
        这就是"lazy"的含义——先算好路径，用到时再创建。

        Returns:
            {"workspace_path": "/abs/path/workspace", "uploads_path": "...", "outputs_path": "..."}
        """
        return {
            "workspace_path": str(self._paths.sandbox_work_dir(thread_id, user_id=user_id)),
            "uploads_path": str(self._paths.sandbox_uploads_dir(thread_id, user_id=user_id)),
            "outputs_path": str(self._paths.sandbox_outputs_dir(thread_id, user_id=user_id)),
        }

    def _create_thread_directories(self, thread_id: str, user_id: str | None = None) -> dict[str, str]:
        """yyds: 先算路径，再创建目录。= _get_thread_paths() + ensure_thread_dirs()。

        ensure_thread_dirs() 做了什么？（定义在 paths.py）
          1. mkdir -p 创建 4 个目录（workspace/uploads/outputs + acp-workspace）
          2. chmod 777 每个目录（因为沙箱容器可能以不同 UID 运行，需要写权限）
          3. mkdir(parents=True, exist_ok=True) 保证幂等（已存在不报错）

        为什么 chmod 777？
          宿主机进程以 user A 身份运行，创建的目录属于 user A。
          Docker 容器以 root 或其他用户运行，默认无法写 user A 的目录。
          777 让所有用户都能读写，避免 "Permission denied"。
        """
        self._paths.ensure_thread_dirs(thread_id, user_id=user_id)
        return self._get_thread_paths(thread_id, user_id=user_id)

    @override
    def before_agent(self, state: ThreadDataMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 中间件链的入口。每次 Agent 执行一轮对话时，这是第一个被调用的钩子。

        执行顺序（在 before_agent 阶段）：
          ThreadDataMiddleware.before_agent()  ← 你在这里，第一个
          → UploadsMiddleware.before_agent()
          → SandboxMiddleware.before_agent()   ← 会读 state["thread_data"] 来挂载目录

        参数说明：
          state:  当前 LangGraph 的状态快照。至少包含 messages（对话历史）。
                  本中间件就是往 state 里加一个 thread_data 字段。
          runtime: LangGraph 运行时上下文。包含 thread_id、run_id 等信息。
                   context 是一个 dict，由 Gateway 在创建 run 时注入。

        返回值：
          一个 dict，会被 LangGraph 自动合并到全局 state。
          本中间件返回 {"thread_data": {...}, "messages": [...]}。
          LangGraph 看到这个返回值，就会更新 state 的对应字段。
        """

        # ── Step 1: 获取 thread_id ──────────────────────────────────
        # thread_id 标识一次会话。同一个 thread_id 的所有消息共享同一个工作目录。
        #
        # 两个来源（优先级从高到低）：
        #   1. runtime.context["thread_id"] — Gateway 直接传入的
        #   2. get_config()["configurable"]["thread_id"] — LangGraph checkpoint 配置里的
        #
        # 为什么有两个来源？
        #   因为 Gateway（app 层）和 LangGraph 运行时都可以设置 thread_id。
        #   Gateway 优先，因为它更"新"（每次请求都有），configurable 是 checkpoint 级别的。
        context = runtime.context or {}
        thread_id = context.get("thread_id")
        if thread_id is None:
            config = get_config()
            thread_id = config.get("configurable", {}).get("thread_id")

        if thread_id is None:
            raise ValueError("Thread ID is required in runtime context or config.configurable")

        # ── Step 2: 获取 user_id ──────────────────────────────────
        # user_id 决定目录挂在 users/ 下的哪个用户桶里。
        #
        # get_effective_user_id() 永远不返回 None：
        #   ContextVar 有值（已登录）→ 返回真实 user_id（如 "9e79df8b-..."）
        #   ContextVar 无值（没登录）→ 返回 "default"
        #
        # 所以路径永远是 .deer-flow/users/{user_id}/threads/{tid}/...
        # 本地开发 → users/default/threads/{tid}/
        # 企业部署 → users/9e79df8b-.../threads/{tid}/
        #
        # .deer-flow/threads/{tid}/ 是遗留路径（user_id=None 时才走），2.0 基本不用。
        user_id = get_effective_user_id()

        # ── Step 3: 计算路径 ──────────────────────────────────────
        if self._lazy_init:
            # 只算路径字符串，不做任何 I/O。目录在后续 Sandbox 中间件里按需创建。
            paths = self._get_thread_paths(thread_id, user_id=user_id)
        else:
            # 算路径 + mkdir -p + chmod 777。用于测试或需要立即访问目录的场景。
            paths = self._create_thread_directories(thread_id, user_id=user_id)
            logger.debug("Created thread data directories for thread %s", thread_id)

        # ── Step 4: 给最后一条 HumanMessage 注入元数据 ────────────
        # 为什么要做这件事？
        #   run_id 和 timestamp 是追踪和调试用的：
        #   - run_id: 标识这是第几次执行（一个 thread 可以有多次 run）
        #   - timestamp: 这条消息是什么时间到达中间件的
        #
        #   这些信息不会影响 Agent 的推理，但会写入 LangGraph 的 checkpoint，
        #   后续调试时可以看到"这条消息是在哪次 run 的什么时间被处理的"。
        #
        # 为什么只处理 HumanMessage？
        #   因为只有用户发的消息才需要追踪来源。AIMessage 和 ToolMessage
        #   都是 Agent 自己产生的，来源是明确的。
        messages = list(state.get("messages", []))
        last_message = messages[-1] if messages else None

        if last_message and isinstance(last_message, HumanMessage):
            messages[-1] = HumanMessage(
                content=last_message.content,
                id=last_message.id,
                name=last_message.name or "user-input",
                additional_kwargs={**last_message.additional_kwargs, "run_id": runtime.context.get("run_id"), "timestamp": datetime.now(UTC).isoformat()},
            )

        # ── 返回 ──────────────────────────────────────────────────
        # LangGraph 会自动把这个 dict 合并到全局 ThreadState。
        # thread_data: 三个路径，后续中间件和工具都会读取
        # messages: 可能被修改过（注入了元数据的最后一条消息）
        return {
            "thread_data": {
                **paths,
            },
            "messages": messages,
        }
