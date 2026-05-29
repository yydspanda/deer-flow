"""yyds: 用户上下文管理 — 让任何代码都能知道"当前请求是谁发的"

════════════════════════════════════════════════════════════════════════════════
一、这个模块解决什么问题？
════════════════════════════════════════════════════════════════════════════════

   一个 HTTP 请求进来：
     用户 Alice → 浏览器发消息 → Gateway → Agent 执行 → 写文件到磁盘

   问题：Agent 执行时，代码怎么知道"这是 Alice 的请求"？
         不可能把 user_id 一层层传参传下去（几十层调用，参数爆炸）。

   方案：用 Python 的 ContextVar（上下文变量）。
         ContextVar 类似"全局变量"，但有个关键区别：
         - 全局变量：所有协程共享同一个值（多用户会串）
         - ContextVar：每个协程有自己的副本（自动隔离）

   为什么全局变量会串？因为 asyncio 是单线程的：
     协程 A: global_var = "Alice"
     协程 A: await sleep(0.1)  ← 让出 CPU，事件循环切到协程 B
     协程 B: global_var = "Bob"    ← 全局变量被改了
     协程 B: print(global_var) → "Bob"  ✅
     协程 A: sleep 结束，恢复执行
     协程 A: print(global_var) → "Bob"  ❌ 期望 Alice，但被 B 改了

   ContextVar 解决方案：每个协程维护自己的副本，set() 只改当前协程的副本。

   所以这个模块的本质是：
     一个请求级的"隐形传参通道"——Gateway 写入，任何层都能读取，
     不需要显式传参。

════════════════════════════════════════════════════════════════════════════════
二、数据流（谁写、谁读）
════════════════════════════════════════════════════════════════════════════════

   写入方（生产者）——只有一个地方：app/gateway/auth_middleware.py

     class AuthMiddleware:
         async def dispatch(self, request, call_next):
             user = await get_current_user_from_request(request)   # 解析 JWT
             token = set_current_user(user)    # ← 写入 ContextVar
             try:
                 return await call_next(request)
             finally:
                 reset_current_user(token)     # ← 请求结束，必须清理

   读取方（消费者）——很多地方：
     1. ThreadDataMiddleware    → get_effective_user_id()  → 文件路径隔离
     2. persistence 层         → resolve_user_id(AUTO)    → SQL WHERE 过滤
     3. Memory 系统            → 不同用户的记忆文件隔离

════════════════════════════════════════════════════════════════════════════════
三、为什么要用 Protocol 而不是直接 import User 类？
════════════════════════════════════════════════════════════════════════════════

   User 类在 app.gateway.auth.models（应用层），
   这个模块在 deerflow.runtime（框架层）。

   分层原则：框架层不能 import 应用层（harness/app 单向依赖）。

   传统做法（继承）：底层定义接口，上层实现。但需要 import 具体类。
   Protocol 做法：底层只声明"我需要一个有 .id 属性的对象"，
                  不 import 任何具体类。上层传什么对象都行，只要有 .id。

   这叫"结构化类型"（structural typing），也叫"鸭子类型的有类型版本"：
     "如果它走起来像鸭子、叫起来像鸭子，那它就是鸭子"
     ——不需要验 DNA（继承），只要行为对就行。

════════════════════════════════════════════════════════════════════════════════
四、本模块用到的 Python 类型工具
════════════════════════════════════════════════════════════════════════════════

   ContextVar  — 上下文变量。类似全局变量，但每个协程有自己的副本。
   Token       — set() 返回的回滚凭证。reset(token) 精确恢复到 set 之前的状态。
   Final       — 声明"这个变量只赋值一次"（类型检查器层面，运行时不强制）。
                 Final 锁的是变量名不能再赋值，但变量指向的对象可以调方法。
                 所以 _current_user: Final 后仍然可以 .set()/.get()/.reset()。
   Protocol    — 结构化类型。只声明"需要哪些属性/方法"，不需要继承。
   runtime_checkable — 让 Protocol 支持 isinstance() 运行时检查。

════════════════════════════════════════════════════════════════════════════════
五、三态 user_id 解析（AUTO / str / None）
════════════════════════════════════════════════════════════════════════════════

   persistence 层的方法签名通常是：
     def get_threads(self, user_id: str | None | _AutoSentinel = AUTO)

   三种传值方式：

   ┌──────────┬─────────────────────────────────────────────────────┐
   │ AUTO     │ 默认值。从 ContextVar 读 user_id。                 │
   │ (哨兵)   │ 没有登录用户 → 抛 RuntimeError。                   │
   │          │ 用于正常的 API 请求（必须知道是谁）。               │
   ├──────────┼─────────────────────────────────────────────────────┤
   │ "alice"  │ 显式传值，覆盖 ContextVar。                        │
   │ (字符串) │ 用于测试、admin 覆盖。                              │
   ├──────────┼─────────────────────────────────────────────────────┤
   │ None     │ 不过滤 user_id（不加 WHERE 子句）。                │
   │          │ 用于迁移脚本、管理 CLI，故意跳过隔离。              │
   └──────────┴─────────────────────────────────────────────────────┘

   为什么用哨兵而不是直接用 None？
     因为 None 有歧义："没传参数"和"故意跳过隔离"需要区分。
     AUTO = "没传参数，请从上下文自动解析"
     None = "我知道我在做什么，不要隔离"
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Final, Protocol, runtime_checkable


@runtime_checkable
class CurrentUser(Protocol):
    """yyds: 当前用户的结构化协议（Protocol）。

    为什么用 Protocol 而不是直接用 User 类？
      因为 User 类定义在 app.gateway.auth.models（应用层），
      而这个模块在 deerflow.runtime（框架层）。
      框架层不能 import 应用层（违反 harness/app 分层原则）。

    Protocol 的含义："我不关心你是什么类，只要你有一个 .id: str 属性，
    我就认为你是一个 CurrentUser"。

    runtime_checkable 让 isinstance(obj, CurrentUser) 可以在运行时检查。
    """

    id: str


# yyds: 核心——全局唯一的 ContextVar。
# 每个 asyncio task 有自己的副本，互不干扰。
# default=None 表示"没有设置时返回 None"（即没有登录用户）。
_current_user: Final[ContextVar[CurrentUser | None]] = ContextVar("deerflow_current_user", default=None)


def set_current_user(user: CurrentUser) -> Token[CurrentUser | None]:
    """yyds: 写入当前用户。只在 auth_middleware.py 里调用。

    返回一个 Token，用于 reset_current_user() 恢复之前的值。
    用法必须是 set + try/finally + reset，确保请求结束后清理：

        token = set_current_user(user)
        try:
            # 处理请求...
        finally:
            reset_current_user(token)  # 清理，防止下一个请求读到残留值

    为什么不直接赋值然后赋回 None？
      因为可能有嵌套：外层请求已经设了一个用户，内层又设了一个。
      Token 机制可以正确恢复到"上一层"的值。
    """
    return _current_user.set(user)


def reset_current_user(token: Token[CurrentUser | None]) -> None:
    """yyds: 恢复用户上下文到 token 捕获时的状态。

    和 set_current_user 配对使用，放在 finally 块里。
    如果 reset 前的值是 None（没有用户），就恢复为 None。
    如果 reset 前的值是 Alice（外层请求设的），就恢复为 Alice。
    """
    _current_user.reset(token)


def get_current_user() -> CurrentUser | None:
    """yyds: 安全读取。未登录返回 None，不抛异常。

    用于"可选登录"的场景：有用户就用，没用户也能继续。
    """
    return _current_user.get()


def require_current_user() -> CurrentUser:
    """yyds: 强制读取。未登录抛 RuntimeError。

    用于"必须登录"的场景：persistence 层的 AUTO 模式会用这个。
    """
    user = _current_user.get()
    if user is None:
        raise RuntimeError("repository accessed without user context")
    return user


DEFAULT_USER_ID: Final[str] = "default"


def get_effective_user_id() -> str:
    """yyds: 获取有效的 user_id 字符串。永远不抛异常。

    这就是 ThreadDataMiddleware 调用的那个函数。
    - 已登录 → 返回 user.id（如 "9e79df8b-c31c-..."）
    - 未登录 → 返回 "default"

    所以你在 .deer-flow/ 目录下看到的：
      users/default/threads/...    ← 本地开发（没登录，user_id="default"）
      users/9e79df8b-.../threads/... ← 企业部署（已登录，user_id=UUID）
    就是这个函数决定的。
    """
    user = _current_user.get()
    if user is None:
        return DEFAULT_USER_ID
    return str(user.id)


def resolve_runtime_user_id(runtime: object | None) -> str:
    """yyds: 工具/中间件获取 user_id 的首选入口 — 三级兜底。

    为什么不直接用 get_effective_user_id()（只读 ContextVar）？
      因为有些场景 ContextVar 会丢：
        - 工具被调度到其他线程执行
        - 后台任务在请求结束后才跑
      这时 ContextVar 已经是 None 了。

    所以多了一个备份来源：runtime.context["user_id"]。
    runtime 是 Agent 运行时的上下文对象，user_id 在请求进来时就被塞进去了，
    它跟着 runtime 对象走，不依赖 ContextVar。

    三级查找（优先级从高到低）：
      ① runtime.context["user_id"] → 最可靠，不依赖 ContextVar，跨线程也能用
      ② _current_user ContextVar   → 正常请求内可用（auth_middleware 写的）
      ③ "default"                  → 本地开发兜底（没登录就没前两个）

    就像出差带两种证件：
      runtime.context = 口袋里的身份证（什么时候都能用）
      ContextVar      = 公司工牌（在公司里方便，出了公司就没了）

    谁在调？工具（setup_agent、写文件）、Memory 系统 —
    任何需要持久化用户数据的地方都用这个，不用 get_effective_user_id()。

    ---
    Single source of truth for a tool/middleware's effective user_id.
    """
    context = getattr(runtime, "context", None)
    if isinstance(context, dict):
        ctx_user_id = context.get("user_id")
        if ctx_user_id:
            return str(ctx_user_id)
    return get_effective_user_id()


# ---------------------------------------------------------------------------
# Sentinel-based user_id resolution
# ---------------------------------------------------------------------------
#
# Repository methods accept a ``user_id`` keyword-only argument that
# defaults to ``AUTO``. The three possible values drive distinct
# behaviours; see the docstring on :func:`resolve_user_id`.


class _AutoSentinel:
    """yyds: 哨兵单例 — 一个"什么都不代表、只代表自己"的特殊标记值。

    ══════════════════════════════════════════════════════════════════
    先理解"哨兵值"是什么
    ══════════════════════════════════════════════════════════════════

    问题：persistence 层的方法需要区分"没传 user_id"和"传了 None"：

      get_threads()                    # 没传 → 从 ContextVar 自动解析
      get_threads(user_id=None)        # 传了 None → 故意跳过隔离
      get_threads(user_id="alice")     # 传了字符串 → 用这个值

    三种情况需要三种不同的默认值。但 Python 的默认值只能是 None：

      def get_threads(self, user_id=None):  # None 不能同时表示两种含义
          if user_id is None:
              # 这是"没传"还是"故意传 None"？分不清！

    解决方案：用一个"全世界独一无二的东西"作为默认值，
    这个东西就是"哨兵值"（sentinel value）。

      SENTINEL = object()   # 一个独一无二的对象

      def get_threads(self, user_id=SENTINEL):
          if user_id is SENTINEL:
              # "没传" → 从 ContextVar 自动解析
          elif user_id is None:
              # "故意传 None" → 跳过隔离
          else:
              # "传了字符串" → 用这个值

    哨兵值的唯一要求：它不能和任何正常值相等。
    object() 创建的对象永远只和自己 is 相等，所以是完美的哨兵。

    Python 标准库里的哨兵例子：
      - Ellipsis（...）  — NumPy 切片里表示"全部"
      - NotImplemented   — 运算符重载里表示"我不处理这个类型"

    ══════════════════════════════════════════════════════════════════
    为什么不直接用 object() 做哨兵？
    ══════════════════════════════════════════════════════════════════

      AUTO = object()    # 能用，但打印时显示 <object at 0x7f3b...>
                         # 调试时不知道这是什么

    用自定义类的好处：
      1. 打印友好：repr(AUTO) → "<AUTO>"（调试时一眼看出）
      2. isinstance 可以用：isinstance(value, _AutoSentinel) → 精确判断
      3. 类型注解：user_id: str | None | _AutoSentinel（IDE 能提示）

    ══════════════════════════════════════════════════════════════════
    为什么用单例？（__new__ 里检查 _instance）
    ══════════════════════════════════════════════════════════════════

    单例 = 全局只有一个实例，不管调用多少次 _AutoSentinel() 都返回同一个对象。

      a = _AutoSentinel()
      b = _AutoSentinel()
      a is b  → True   # 永远是同一个对象

    为什么必须是单例？
      如果每次创建新对象，isinstance 检查和 is 比较就会失效：
        a = _AutoSentinel()  # 对象 A
        b = _AutoSentinel()  # 对象 B（如果不是单例）
        a is b  → False  # 两个不同的哨兵，无法判断"这是不是那个 AUTO"

    实现原理：
      __new__ 是比 __init__ 更早调用的方法，负责"创建对象"。
      第一次调用：_instance 是 None → 创建新对象，存到 _instance
      之后调用：_instance 不是 None → 直接返回之前创建的那个

    ══════════════════════════════════════════════════════════════════
    完整流程
    ══════════════════════════════════════════════════════════════════

      1. 模块加载时：AUTO = _AutoSentinel() → 创建唯一的哨兵对象
      2. 方法定义：def get_threads(self, user_id=AUTO)  → 默认值是哨兵
      3. 方法调用：
         get_threads()              → user_id 是 AUTO → isinstance → True → 从 ContextVar 读
         get_threads(user_id=None)  → user_id 是 None → isinstance → False → 跳过隔离
         get_threads(user_id="alice") → user_id 是 str → isinstance → False → 用 "alice"
    """

    _instance: _AutoSentinel | None = None

    def __new__(cls) -> _AutoSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "<AUTO>"


AUTO: Final[_AutoSentinel] = _AutoSentinel()


def resolve_user_id(
    value: str | None | _AutoSentinel,
    *,
    method_name: str = "repository method",
) -> str | None:
    """yyds: 解析 user_id 参数的三态语义。

    被 persistence 层的每个方法调用：
      def get_threads(self, user_id: str | None | _AutoSentinel = AUTO):
          uid = resolve_user_id(user_id)   # ← 这里
          if uid is not None:
              query = query.where(user_id == uid)  # 加 WHERE 过滤

    三种输入：
      AUTO      → 从 ContextVar 读取，没有则抛异常（正常请求）
      "alice"   → 直接用这个字符串（测试、admin）
      None      → 返回 None，调用方不加 WHERE（迁移脚本、管理工具）
    """
    if isinstance(value, _AutoSentinel):
        user = _current_user.get()
        if user is None:
            raise RuntimeError(f"{method_name} called with user_id=AUTO but no user context is set; pass an explicit user_id, set the contextvar via auth middleware, or opt out with user_id=None for migration/CLI paths.")
        return str(user.id)
    return value
