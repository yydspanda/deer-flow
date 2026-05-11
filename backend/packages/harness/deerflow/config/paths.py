
import hashlib
import logging
# yyds: paths.py — DeerFlow 集中式路径管理（所有文件系统路径的唯一入口）
# 一、为什么需要这个文件？
#   DeerFlow 的 Agent 要读写文件（代码执行、上传下载、记忆存储）。
#   如果路径散落在各个中间件和工具函数里，改一个目录结构就要改 20 个文件。
#   所以所有路径计算都集中在这里——改一处，全局生效。
# 二、目录树（宿主机上的实际布局）
#   {base_dir}/  （默认 .deer-flow/）
#   ├── memory.json                          ← 全局记忆
#   ├── USER.md                              ← 全局用户画像（注入给所有 Agent）
#   ├── agents/                              ← 遗留路径，2.0 用 users/{uid}/agents/
#   │       ├── SOUL.md                      ← Agent 人格
#   │       └── memory.json                  ← Agent 专属记忆
#       └── {user_id}/                       ← 用户隔离桶
#           ├── memory.json                  ← 用户级记忆
#           │   └── {agent_name}/            ← 用户自定义 Agent
#                   └── user-data/           ← 沙箱内映射为 /mnt/user-data/
#                       ├── workspace/       ← Agent 工作目录（bash 在这里执行）
#                       ├── uploads/         ← 用户上传的文件
#                       ├── outputs/         ← Agent 生成的产出物
#                       └── acp-workspace/   ← ACP Agent 工作区（跨 Agent 协作）
# 三、沙箱路径映射（虚拟 ↔ 宿主机）
#   Agent 在沙箱里看到的路径          宿主机实际路径
#   映射关系：Docker volume bind mount（容器启动时由 LocalSandbox 设置）
# 四、base_dir 解析优先级（三级 fallback）
#   1. 构造参数 Paths(base_dir="xxx")         ← 测试用，精确控制
#   2. 环境变量 DEER_FLOW_HOME                ← Docker/自定义部署
#   3. runtime_home() → {project_root}/.deer-flow/  ← 默认
# 五、DooD 场景（Docker outside of Docker）
#   Gateway 跑在容器里，沙箱又是另一个容器。Docker daemon 在宿主机上。
#   宿主机不知道容器内的路径 /app/.deer-flow/，只知道 /home/user/...。
#   所以需要 DEER_FLOW_HOST_BASE_DIR 告诉 Docker 宿主机侧的对应路径。
#   host_* 方法返回的就是这个宿主机侧路径（用于 Docker bind mount）。

import os
import re
import shutil
from pathlib import Path, PureWindowsPath

from deerflow.config.runtime_paths import runtime_home

# yyds: 虚拟路径前缀 — Agent 在沙箱里看到的根目录
# 所有工具函数对 Agent 暴露的路径都以 /mnt/user-data/ 开头
# 通过 Docker volume bind mount 映射到宿主机的 user-data/ 目录
VIRTUAL_PATH_PREFIX = "/mnt/user-data"

# yyds: 安全正则 — 只允许字母、数字、下划线、连字符
# 拒绝 ../ 和 / 等路径遍历字符，防止恶意 thread_id/user_id 逃逸出隔离目录
_SAFE_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_SAFE_USER_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_UNSAFE_USER_ID_CHAR_RE = re.compile(r"[^A-Za-z0-9_\-]")
_SAFE_USER_ID_DIGEST_HEX_LEN = 16

logger = logging.getLogger(__name__)


def _default_local_base_dir() -> Path:
    """yyds: 默认 base_dir — 委托给 runtime_home()（通常是 {project_root}/.deer-flow/）"""
    return runtime_home()


def _validate_thread_id(thread_id: str) -> str:
    """yyds: 路径安全守卫 — 校验 thread_id 只含安全字符，防止路径遍历攻击。

    例如 thread_id="../../etc/passwd" 会被拒绝。
    允许的字符：A-Z a-z 0-9 _ - （跟 UUID、时间戳、随机字符串兼容）
    """
    if not _SAFE_THREAD_ID_RE.match(thread_id):
        raise ValueError(f"Invalid thread_id {thread_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
    return thread_id


def _validate_user_id(user_id: str) -> str:
    """yyds: 路径安全守卫 — 同上，校验 user_id。规则和 thread_id 一样。"""
    if not _SAFE_USER_ID_RE.match(user_id):
        raise ValueError(f"Invalid user_id {user_id!r}: only alphanumeric characters, hyphens, and underscores are allowed.")
    return user_id


def make_safe_user_id(raw: str) -> str:
    """Normalize an external identity into the user-id charset (``[A-Za-z0-9_-]``).

    IM channel ids (Feishu/Slack/Telegram) may contain characters that
    :func:`_validate_user_id` rejects. Already-safe ids pass through unchanged;
    lossy ones get a short digest suffix so two distinct inputs never share a
    storage bucket.
    """
    if not raw:
        raise ValueError("user_id must be a non-empty string.")
    sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw)
    if sanitized == raw:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:_SAFE_USER_ID_DIGEST_HEX_LEN]
    return f"{sanitized}-{digest}"


def _legacy_safe_user_id(raw: str, sanitized: str) -> str:
    """Bucket name produced by the previous (SHA-1) digest revision for ``raw``."""
    digest = hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:_SAFE_USER_ID_DIGEST_HEX_LEN]
    return f"{sanitized}-{digest}"


def _join_host_path(base: str, *parts: str) -> str:
    """yyds: 跨平台路径拼接 — 自动检测 base 是 Windows 还是 POSIX 路径。

    为什么不用 Path(base) / parts？
      Docker Desktop on Windows 的 bind mount 要求源路径用 Windows 格式
      （C:\\repo\\...），如果用 POSIX 的 Path 拼接会产生混合分隔符，
      Docker daemon 解析不了，容器启动失败。

    所以这个函数会：
      - 检测到 Windows 路径（C:\\ 或 UNC \\\\server）→ 用 PureWindowsPath
      - 否则 → 用 POSIX Path
    """
    if not parts:
        return base

    if re.match(r"^[A-Za-z]:[\\/]", base) or base.startswith("\\\\") or "\\" in base:
        result = PureWindowsPath(base)
        for part in parts:
            result /= part
        return str(result)

    result = Path(base)
    for part in parts:
        result /= part
    return str(result)


def join_host_path(base: str, *parts: str) -> str:
    """yyds: 公开版 _join_host_path — 供外部模块调用。"""
    return _join_host_path(base, *parts)


class Paths:
    """yyds: 路径管理核心类 — 所有路径计算的入口。

    设计模式：延迟计算 + 三级 fallback
      - 构造时不做任何 I/O
      - 每次 .base_dir 访问时按优先级解析（构造参数 > 环境变量 > 默认）
      - 所有路径方法都是纯计算，不碰磁盘

    两组方法的区别：
      - xxx_dir()      → 返回 Path 对象，用于 Python 内部文件操作
      - host_xxx_dir() → 返回 str，保留 Windows 路径格式，用于 Docker bind mount
    """

    def __init__(self, base_dir: str | Path | None = None) -> None:
        # yyds: base_dir 可以为 None，表示"还没定"，用到时再按优先级解析
        self._base_dir = Path(base_dir).resolve() if base_dir is not None else None

    @property
    def host_base_dir(self) -> Path:
        """yyds: Docker bind mount 用的宿主机侧根目录。

        三个场景：
          1. 本地开发（没有 DEER_FLOW_HOST_BASE_DIR）→ 跟 base_dir 一样
          2. Gateway 在容器里（DooD）→ 环境变量指向宿主机上的对应路径
          3. 测试 → 构造参数指定

        为什么需要？
          Docker daemon 在宿主机上运行，它只认宿主机路径。
          容器内的 /app/.deer-flow/ 在宿主机上可能是 /home/user/project/.deer-flow/
          不转换的话，bind mount 会指向不存在的路径。
        """
        if env := os.getenv("DEER_FLOW_HOST_BASE_DIR"):
            return Path(env)
        return self.base_dir

    def _host_base_dir_str(self) -> str:
        """yyds: host_base_dir 的原始字符串版 — 保留 Windows 反斜杠，不转成 POSIX。"""
        if env := os.getenv("DEER_FLOW_HOST_BASE_DIR"):
            return env
        return str(self.base_dir)

    @property
    def base_dir(self) -> Path:
        """yyds: 根目录 — 三级 fallback 解析。

        优先级：构造参数 > DEER_FLOW_HOME 环境变量 > runtime_home() 默认值
        每次访问都重新解析（不是缓存），所以运行时改环境变量会立即生效。
        """
        if self._base_dir is not None:
            return self._base_dir

        if env_home := os.getenv("DEER_FLOW_HOME"):
            return Path(env_home).resolve()

        return _default_local_base_dir()

    @property
    def memory_file(self) -> Path:
        """yyds: 全局记忆文件 → {base_dir}/memory.json"""
        return self.base_dir / "memory.json"

    @property
    def user_md_file(self) -> Path:
        """yyds: 全局用户画像 → {base_dir}/USER.md
        这个文件的内容会被注入到所有 Agent 的 system prompt 里，
        让 Agent 了解用户的偏好、背景等信息。
        """
        return self.base_dir / "USER.md"

    @property
    def agents_dir(self) -> Path:
        """yyds: 遗留路径 — 2.0 之前 agents 没有用户隔离，都放在这里。
        新代码应该用 user_agents_dir(user_id)。这里只是读旧数据的 fallback。
        """
        return self.base_dir / "agents"

    def agent_dir(self, name: str) -> Path:
        """yyds: 遗留路径 — 单个 Agent 的目录（无用户隔离）。"""
        return self.agents_dir / name.lower()

    def agent_memory_file(self, name: str) -> Path:
        """yyds: 遗留路径 — Agent 专属记忆文件。"""
        return self.agent_dir(name) / "memory.json"

    def user_dir(self, user_id: str) -> Path:
        """yyds: 用户隔离桶 → {base_dir}/users/{user_id}/

        这是所有用户级数据的根。下面挂 memory.json、agents/、threads/。
        user_id 会被 _validate_user_id 校验，防止路径遍历。
        """
        return self.base_dir / "users" / _validate_user_id(user_id)

    def prepare_user_dir_for_raw_id(self, raw_user_id: str) -> str:
        """Return the safe user ID and migrate this ID's legacy unsafe-id bucket.

        A previous branch revision used SHA-1 for unsafe external user IDs.
        New IDs use SHA-256; the legacy bucket name is recomputed from the same
        raw ID, so only this user's own old bucket can ever be moved — a
        different raw ID sharing the sanitized prefix produces a different
        legacy digest and is never touched.
        """
        safe_user_id = make_safe_user_id(raw_user_id)
        sanitized = _UNSAFE_USER_ID_CHAR_RE.sub("-", raw_user_id)
        if safe_user_id == raw_user_id:
            return safe_user_id

        users_dir = self.base_dir / "users"
        target_dir = users_dir / safe_user_id
        legacy_dir = users_dir / _legacy_safe_user_id(raw_user_id, sanitized)
        try:
            if target_dir.exists() or not legacy_dir.is_dir():
                return safe_user_id
            legacy_dir.rename(target_dir)
            logger.info("Migrated legacy unsafe-id user directory to the current digest format")
        except OSError:
            logger.exception("Failed to migrate legacy unsafe-id user directory")
        return safe_user_id

    def user_memory_file(self, user_id: str) -> Path:
        """yyds: 用户级记忆 → {base_dir}/users/{user_id}/memory.json"""
        return self.user_dir(user_id) / "memory.json"

    def user_agents_dir(self, user_id: str) -> Path:
        """yyds: 用户自定义 Agent 目录 → {base_dir}/users/{user_id}/agents/"""
        return self.user_dir(user_id) / "agents"

    def user_agent_dir(self, user_id: str, agent_name: str) -> Path:
        """yyds: 用户 + Agent 维度的目录 → {base_dir}/users/{user_id}/agents/{name}/"""
        return self.user_agents_dir(user_id) / agent_name.lower()

    def user_agent_memory_file(self, user_id: str, agent_name: str) -> Path:
        """yyds: 用户 Agent 的记忆 → {base_dir}/users/{user_id}/agents/{name}/memory.json"""
        return self.user_agent_dir(user_id, agent_name) / "memory.json"

    def thread_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """yyds: Thread 的根目录 — 路径分支的关键点。

        user_id 不为 None（正常情况，包括 "default"）：
            → {base_dir}/users/{user_id}/threads/{thread_id}/
        user_id 为 None（遗留布局，2.0 基本不用）：
            → {base_dir}/threads/{thread_id}/

        get_effective_user_id() 永远不返回 None，所以实际永远走 users/ 路径。
        thread_id 和 user_id 都会被正则校验，拒绝路径遍历字符。
        """
        if user_id is not None:
            return self.user_dir(user_id) / "threads" / _validate_thread_id(thread_id)
        return self.base_dir / "threads" / _validate_thread_id(thread_id)

    def sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """yyds: Agent 工作目录（宿主机路径）
        宿主机: .../threads/{tid}/user-data/workspace/
        沙箱内: /mnt/user-data/workspace/  （bash、代码执行都在这里）
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "workspace"

    def sandbox_uploads_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """yyds: 用户上传目录（宿主机路径）
        宿主机: .../threads/{tid}/user-data/uploads/
        沙箱内: /mnt/user-data/uploads/
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "uploads"

    def sandbox_outputs_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """yyds: Agent 产出物目录（宿主机路径）
        宿主机: .../threads/{tid}/user-data/outputs/
        沙箱内: /mnt/user-data/outputs/
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data" / "outputs"

    def acp_workspace_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """yyds: ACP（Agent Communication Protocol）工作区
        宿主机: .../threads/{tid}/acp-workspace/
        沙箱内: /mnt/acp-workspace/

        每个 thread 有独立的 ACP 工作区，防止并发会话互相读到对方的中间结果。
        """
        return self.thread_dir(thread_id, user_id=user_id) / "acp-workspace"

    def sandbox_user_data_dir(self, thread_id: str, *, user_id: str | None = None) -> Path:
        """yyds: user-data 根目录 — workspace/uploads/outputs 的父目录
        宿主机: .../threads/{tid}/user-data/
        沙箱内: /mnt/user-data/
        """
        return self.thread_dir(thread_id, user_id=user_id) / "user-data"

    def host_thread_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """yyds: Thread 根目录的宿主机字符串版 — 保留 Windows 路径格式。
        用于 Docker bind mount 的 source 参数（必须是宿主机原生格式）。
        """
        if user_id is not None:
            return _join_host_path(self._host_base_dir_str(), "users", _validate_user_id(user_id), "threads", _validate_thread_id(thread_id))
        return _join_host_path(self._host_base_dir_str(), "threads", _validate_thread_id(thread_id))

    def host_sandbox_user_data_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """yyds: user-data 根目录的宿主机字符串版。"""
        return _join_host_path(self.host_thread_dir(thread_id, user_id=user_id), "user-data")

    def host_sandbox_work_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """yyds: workspace 目录的宿主机字符串版。"""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "workspace")

    def host_sandbox_uploads_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """yyds: uploads 目录的宿主机字符串版。"""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "uploads")

    def host_sandbox_outputs_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """yyds: outputs 目录的宿主机字符串版。"""
        return _join_host_path(self.host_sandbox_user_data_dir(thread_id, user_id=user_id), "outputs")

    def host_acp_workspace_dir(self, thread_id: str, *, user_id: str | None = None) -> str:
        """yyds: ACP 工作区的宿主机字符串版。"""
        return _join_host_path(self.host_thread_dir(thread_id, user_id=user_id), "acp-workspace")

    def ensure_thread_dirs(self, thread_id: str, *, user_id: str | None = None) -> None:
        """yyds: 创建 thread 的所有标准目录（workspace/uploads/outputs/acp-workspace）。

        为什么用 chmod 0o777 而不是 mkdir(mode=0o777)？
          mkdir 的 mode 参数受进程 umask 影响，可能得不到 777。
          显式 chmod() 绕过 umask，确保沙箱容器（可能用不同 UID 运行）能写入。

        为什么 acp-workspace 也要提前创建？
          即使还没调用 ACP Agent，Docker 容器启动时就需要 bind mount 这个目录，
          如果不存在会报错。所以提前创建，即使暂时是空目录。
        """
        for d in [
            self.sandbox_work_dir(thread_id, user_id=user_id),
            self.sandbox_uploads_dir(thread_id, user_id=user_id),
            self.sandbox_outputs_dir(thread_id, user_id=user_id),
            self.acp_workspace_dir(thread_id, user_id=user_id),
        ]:
            d.mkdir(parents=True, exist_ok=True)
            d.chmod(0o777)

    def delete_thread_dir(self, thread_id: str, *, user_id: str | None = None) -> None:
        """yyds: 删除 thread 的所有数据（幂等操作，目录不存在也不报错）。"""
        thread_dir = self.thread_dir(thread_id, user_id=user_id)
        if thread_dir.exists():
            shutil.rmtree(thread_dir)

    def resolve_virtual_path(self, thread_id: str, virtual_path: str, *, user_id: str | None = None) -> Path:
        """yyds: 虚拟路径 → 宿主机路径（沙箱外 → 宿主机）

        场景：Agent 在沙箱里生成文件后，工具函数需要知道文件在宿主机上的真实位置。
        例如：/mnt/user-data/outputs/report.pdf → .deer-flow/users/{uid}/threads/{tid}/user-data/outputs/report.pdf

        安全防护（双重检查）：
          1. 前缀校验：必须以 /mnt/user-data/ 开头，拒绝其他路径
             精确匹配段边界，防止 /mnt/user-dataX/ 绕过
          2. 路径遍历检查：resolve() 后检查是否还在 user-data 目录内
             防止 /mnt/user-data/../../etc/passwd 这种攻击
        """
        stripped = virtual_path.lstrip("/")
        prefix = VIRTUAL_PATH_PREFIX.lstrip("/")

        # Require an exact segment-boundary match to avoid prefix confusion
        # (e.g. reject paths like "mnt/user-dataX/...").
        if stripped != prefix and not stripped.startswith(prefix + "/"):
            raise ValueError(f"Path must start with /{prefix}")

        relative = stripped[len(prefix) :].lstrip("/")
        base = self.sandbox_user_data_dir(thread_id, user_id=user_id).resolve()
        actual = (base / relative).resolve()

        try:
            actual.relative_to(base)
        except ValueError:
            raise ValueError("Access denied: path traversal detected")

        return actual


# yyds: 全局单例 — 所有模块通过 get_paths() 拿到同一个 Paths 实例
# 好处：改一处 base_dir，所有路径跟着变
_paths: Paths | None = None


def get_paths() -> Paths:
    """yyds: 获取全局 Paths 单例（懒初始化，第一次调用时创建）。"""
    global _paths
    if _paths is None:
        _paths = Paths()
    return _paths


def resolve_path(path: str) -> Path:
    """yyds: 把相对路径解析为绝对路径（相对于 base_dir）。
    绝对路径直接返回（只做 normalize）。
    用于 config.yaml 里的相对路径解析。
    """
    p = Path(path)
    if not p.is_absolute():
        p = get_paths().base_dir / path
    return p.resolve()
