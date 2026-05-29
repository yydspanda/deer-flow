"""yyds: 全局认证中间件 — 每个请求的第一道门，确认"你是谁"并记下来。

【大白话讲清楚】
  这个中间件做一件事：拦截所有非公开请求，验明身份，然后告诉后续代码"这个人叫 Alice"。

  问题 — 后续几十层代码（Agent、工具、Memory、persistence）都需要知道"当前用户是谁"，
          但不可能每个函数都传一个 user_id 参数下去（参数爆炸）。

  方案 — 两步：
    1. 验证身份：检查 cookie 里的 JWT token（就像酒店前台检查身份证）
    2. 记住身份：set_current_user(user) 写入 ContextVar（就像前台给你发房卡）
       后续任何代码调 get_current_user() 就能知道"当前请求是谁发的"
       不需要一层层传参，不需要知道请求对象长什么样

  安全原则 — fail-closed（默认拒绝）：
    不在白名单里的路径一律要求登录。
    新加的 API 端点自动受保护，不需要开发者记得加锁。

【具体例子】
  Alice 发消息给 Agent：
    请求 → AuthMiddleware.dispatch()
    → 检查 cookie 里的 access_token（JWT）
    → 解析出 User(id="9e79df8b...", email="alice@example.com")
    → set_current_user(user)  ← 记住"这是 Alice"
    → call_next(request)      ← 放行，后续代码能读到 Alice
    → finally: reset_current_user(token)  ← 请求结束，清掉

  Bob 没登录就访问 /api/threads：
    请求 → AuthMiddleware.dispatch()
    → 没有 cookie → 401 {"code": "not_authenticated"}

  内部服务调用（SSE 推送等）：
    请求带 X-DeerFlow-Internal-Token → 跳过 JWT，直接用 internal user
    因为是同一进程内的可信调用，不需要走完整的登录流程

【白名单规则】
  公开路径（不需要登录）：
    - /health, /docs, /redoc, /openapi.json → 健康检查和文档
    - /api/v1/auth/login, /register, /logout → 登录注册本身
  其他所有 /api/* 路径都需要登录。
  注意：/api/v1/auth/me 和 /api/v1/auth/change-password 是受保护的（你已经登录了才能改密码）。

【在请求链中的位置】
  这是第一个中间件，最先执行：
    请求 → AuthMiddleware（本文件）→ 路由处理 → Agent → 工具 → Memory
    写入 ContextVar 在这里，读取在任何后续层。

---
Global authentication middleware — fail-closed safety net.

Rejects unauthenticated requests to non-public paths with 401. When a
request passes the cookie check, resolves the JWT payload to a real
``User`` object and stamps it into both ``request.state.user`` and the
``deerflow.runtime.user_context`` contextvar so that repository-layer
owner filtering works automatically via the sentinel pattern.

Fine-grained permission checks remain in authz.py decorators.
"""

from collections.abc import Callable

from fastapi import HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp

from app.gateway.auth.errors import AuthErrorCode, AuthErrorResponse
from app.gateway.authz import _ALL_PERMISSIONS, AuthContext
from app.gateway.internal_auth import INTERNAL_AUTH_HEADER_NAME, get_internal_user, is_valid_internal_auth_token
from deerflow.runtime.user_context import reset_current_user, set_current_user

_PUBLIC_PATH_PREFIXES: tuple[str, ...] = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)

_PUBLIC_EXACT_PATHS: frozenset[str] = frozenset(
    {
        "/api/v1/auth/login/local",
        "/api/v1/auth/register",
        "/api/v1/auth/logout",
        "/api/v1/auth/setup-status",
        "/api/v1/auth/initialize",
    }
)


def _is_public(path: str) -> bool:
    """yyds: 判断路径是否公开（不需要登录）。

    两种公开路径：
      - 精确匹配：/api/v1/auth/login 必须一字不差（否则 /api/v1/auth/login/../../../etc 这种路径就绕过了）
      - 前缀匹配：/health 匹配 /health/、/health/check 等
    不在白名单里的 → 一律需要登录（fail-closed）
    """
    stripped = path.rstrip("/")
    if stripped in _PUBLIC_EXACT_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in _PUBLIC_PATH_PREFIXES)


class AuthMiddleware(BaseHTTPMiddleware):
    """yyds: 全局认证门卫 — 每个请求的必经之路。

    完整决策树：

    请求进来
    │
    ├─ 是公开路径（/health, /login 等）？
    │   └─ 是 → 直接放行，不检查身份
    │
    ├─ 带内部认证头（X-DeerFlow-Internal-Token）？
    │   └─ 是 → 用 internal_user（同进程可信调用，跳过 JWT）
    │
    ├─ 有 cookie（access_token）？
    │   └─ 没有 → 401 "Authentication required"
    │
    ├─ JWT 验证通过？
    │   └─ 不通过 → 401 "token_invalid" 或 "token_expired"
    │
    └─ 全部通过 → 记住用户身份 + 放行
        ├─ request.state.user = user      ← 传统方式：后续路由通过 request 读
        ├─ request.state.auth = ...       ← 权限上下文
        ├─ set_current_user(user)         ← ContextVar 方式：后续任何层都能读
        ├─ call_next(request)             ← 放行
        └─ finally: reset_current_user()  ← 请求结束，清掉（不管成功还是异常）
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """yyds: 每个请求的入口 — 验身份、记身份、放行、清理。

        例子（正常登录用户）：
          Alice 的请求带着 cookie: {access_token: "eyJhbG..."}
          → _is_public("/api/threads") → False（不是公开路径）
          → cookie 存在 → 不拦截
          → get_current_user_from_request(request) → User(id="alice-uuid")
          → set_current_user(user) ← ContextVar 写入 Alice
          → call_next(request) → 后续代码读到 Alice
          → finally: reset_current_user(token) ← 清掉

        ① 公开路径检查
        ② 内部认证检查（同进程调用）
        ③ Cookie 存在性检查
        ④ JWT 严格验证
        ⑤ 记住用户身份 + 放行 + 清理
        """

        # ① 公开路径 → 直接放行，不做任何认证
        if _is_public(request.url.path):
            return await call_next(request)

        # ② 内部认证 — 同进程内的可信调用（如 SSE 推送线程回调 Gateway）
        # 不需要 JWT，因为请求来自自己进程内部，不是外部用户
        internal_user = None
        if is_valid_internal_auth_token(request.headers.get(INTERNAL_AUTH_HEADER_NAME)):
            internal_user = get_internal_user()

        # ③ 没有 cookie → 连登录都没尝试过 → 直接 401
        if internal_user is None and not request.cookies.get("access_token"):
            return JSONResponse(
                status_code=401,
                content={
                    "detail": AuthErrorResponse(
                        code=AuthErrorCode.NOT_AUTHENTICATED,
                        message="Authentication required",
                    ).model_dump()
                },
            )

        # ④ 严格验证 JWT — 不是"有 cookie 就行"，而是"cookie 必须是合法的 JWT"
        # 防止"随便填个字符串当 cookie"绕过认证（fail-closed）
        # get_current_user_from_request 会解析 JWT、查数据库、确认用户存在
        from app.gateway.deps import get_current_user_from_request

        if internal_user is not None:
            user = internal_user
        else:
            try:
                user = await get_current_user_from_request(request)
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

        # ⑤ 记住用户身份 + 放行 + 确保清理
        # 三种存储方式：
        #   request.state.user — 传统方式，路由函数通过 request 对象读
        #   request.state.auth — 权限上下文，authz.py 的装饰器用
        #   set_current_user(user) — ContextVar 方式，任何层都能读（不用传 request）
        request.state.user = user
        request.state.auth = AuthContext(user=user, permissions=_ALL_PERMISSIONS)
        token = set_current_user(user)
        try:
            return await call_next(request)
        finally:
            # yyds: finally 确保无论成功还是异常都会清理。
            # 如果不清理，下一个请求复用同一个协程时，
            # 会读到上一个请求的残留用户（用户串了）。
            # token 让 reset 精确恢复到 set 之前的状态（支持嵌套）。
            reset_current_user(token)
