"""yyds: Memory 存储层 — memory.json 怎么读写、存在哪、怎么防崩溃丢数据。

【大白话讲清楚】
  Memory 系统需要一个地方存数据。这个文件就是"存取仓库"。

  解决三个问题：

  问题 A — 存哪？
    多用户多 Agent 场景下，每个人每条 Agent 的记忆要分开存。
    → 按 user_id + agent_name 组合定位不同文件路径（优先级见下）

  问题 B — 每次都读磁盘太慢？
    → 带 mtime 检测的内存缓存：文件没改过就返回缓存，改过才重新读

  问题 C — 写到一半断电怎么办？
    → 原子写入：先写到 .tmp 文件 → rename 覆盖原文件。
    rename 是原子操作，要么成功要么没发生，不会出现半截文件。

【具体例子】
  路径解析（user_id="alice", agent_name="coding-helper"）：
    → user_dir/alice/memory/coding-helper.json

  路径解析（user_id="alice", agent_name=None）：
    → user_dir/alice/memory.json

  路径解析（user_id=None, agent_name="coding-helper"）：
    → agent_memory/coding-helper.json

  路径解析（都没给）：
    → 全局 memory.json

  原子写入：
    写 memory.json → 先写 memory.a1b2c3.tmp → 写完 rename 覆盖 memory.json
    如果写到一半断电 → memory.json 还是旧的，memory.xxx.tmp 是垃圾（下次清理）
    如果 rename 完成断电 → memory.json 是新的，没问题

【加载条件】
  全局单例，通过 get_memory_storage() 懒加载。
  config.yaml 的 memory.storage_class 可以指定自定义实现（比如数据库存储），
  加载失败则回退到默认的 FileMemoryStorage。

---
Memory storage providers.
"""

import abc
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from deerflow.config.agents_config import AGENT_NAME_PATTERN
from deerflow.config.memory_config import get_memory_config
from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)


def utc_now_iso_z() -> str:
    """yyds: 生成 "2026-05-18T12:34:56.789Z" 格式的时间戳。

    为什么不用 isoformat() 的默认输出？
    默认输出是 "2026-05-18T12:34:56.789+00:00"，带时区偏移。
    memory.json 里统一用 Z 后缀（更短、更通用），所以手动替换。
    """
    return datetime.now(UTC).isoformat().removesuffix("+00:00") + "Z"


def create_empty_memory() -> dict[str, Any]:
    """yyds: 创建一份空白 memory.json 结构。

    结构对应 __init__.py 里画的三块内容：
      user:   3 个 summary 段（工作/个人/当前关注）
      history: 3 个 summary 段（近期/早期/长期）
      facts:  空列表（后续由 LLM 提取后逐条追加）

    什么时候用？
      - 新用户第一次对话，memory.json 不存在 → load() 返回空结构
      - 文件损坏（JSON 解析失败）→ 返回空结构，不崩溃
      - clear_memory_data() 重置 → 用空结构覆盖
    """
    return {
        "version": "1.0",
        "lastUpdated": utc_now_iso_z(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


class MemoryStorage(abc.ABC):
    """yyds: 存储抽象接口 — 只定义三个操作。

    谁在读？谁在写？
      load()   ← updater.py 读当前 memory（准备 prompt 用）
                 ← prompt.py 的 format_memory_for_injection() 读 memory 注入 prompt
      reload() ← 外部 API 调用强制刷新（绕过缓存）
      save()   ← updater.py 的 _finalize_update() 写入 LLM 提取后的新 memory

    为什么要抽象？
      默认用文件（FileMemoryStorage），但用户可以在 config.yaml 配置
      memory.storage_class 换成数据库存储等。接口不变，实现可替换。
    """

    @abc.abstractmethod
    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Load memory data for the given agent."""
        pass

    @abc.abstractmethod
    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Force reload memory data for the given agent."""
        pass

    @abc.abstractmethod
    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        """Save memory data for the given agent."""
        pass


class FileMemoryStorage(MemoryStorage):
    """yyds: 文件存储实现 — memory.json 读写，带缓存和原子写入。

    完整生命周期：

    load() 被调用（updater 要读 memory）
      │
      file 存在吗？
      ├─ 不存在 → 返回空结构
      ├─ 存在 → mtime 和缓存里一样吗？
      │   ├─ 一样 → 返回缓存（省一次磁盘读取）
      │   └─ 不一样 → 重新读文件 + 更新缓存
      └─ 缓存里没有 → 读文件 + 存缓存

    save() 被调用（updater 提取完新 memory 要写入）
      │
      浅拷贝 + 加 lastUpdated 时间戳
      → 写到 .tmp 文件
      → rename 覆盖原文件（原子操作）
      → 更新缓存
    """

    def __init__(self):
        """Initialize the file memory storage."""
        self._memory_cache: dict[tuple[str | None, str | None], tuple[dict[str, Any], float | None]] = {}
        self._cache_lock = threading.Lock()  # yyds: 缓存锁，queue 的后台线程和主线程可能同时读写缓存

    def _validate_agent_name(self, agent_name: str) -> None:
        """Validate that the agent name is safe to use in filesystem paths.

        Uses the repository's established AGENT_NAME_PATTERN to ensure consistency
        across the codebase and prevent path traversal or other problematic characters.
        """
        if not agent_name:
            raise ValueError("Agent name must be a non-empty string.")
        if not AGENT_NAME_PATTERN.match(agent_name):
            raise ValueError(f"Invalid agent name {agent_name!r}: names must match {AGENT_NAME_PATTERN.pattern}")

    def _get_memory_file_path(self, agent_name: str | None = None, *, user_id: str | None = None) -> Path:
        """yyds: 路径解析 — 根据 user_id 和 agent_name 的组合返回不同文件。

        优先级（从高到低）：
          user_id + agent_name → user_dir/{user_id}/memory/{agent_name}.json
          user_id only        → user_dir/{user_id}/memory.json
          agent_name only     → agent_memory/{agent_name}.json
          都没有              → 全局 memory.json（或 config 里配的 storage_path）

        为什么这样分？
          多用户场景：每个用户的记忆隔离（user_id 分目录）
          多 Agent 场景：同一用户不同 Agent 的记忆隔离（再按 agent_name 分文件）
        """
        if user_id is not None:
            if agent_name is not None:
                self._validate_agent_name(agent_name)
                return get_paths().user_agent_memory_file(user_id, agent_name)
            config = get_memory_config()
            if config.storage_path and Path(config.storage_path).is_absolute():
                return Path(config.storage_path)
            return get_paths().user_memory_file(user_id)
        if agent_name is not None:
            self._validate_agent_name(agent_name)
            return get_paths().agent_memory_file(agent_name)
        config = get_memory_config()
        if config.storage_path:
            p = Path(config.storage_path)
            return p if p.is_absolute() else get_paths().base_dir / p
        return get_paths().memory_file

    def _load_memory_from_file(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """Load memory data from file."""
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)

        if not file_path.exists():
            return create_empty_memory()

        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file: %s", e)
            return create_empty_memory()  # yyds: 文件损坏不崩溃，返回空结构从头开始

    @staticmethod
    def _cache_key(agent_name: str | None = None, *, user_id: str | None = None) -> tuple[str | None, str | None]:
        return (user_id, agent_name)

    def load(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """yyds: 带缓存的加载 — 文件没改过就返回缓存，改过才重新读。

        缓存机制：
          key = (user_id, agent_name)
          value = (memory_data, file_mtime)

          每次先拿文件的 mtime（修改时间），和缓存里的比对：
            一样 → 文件没被外部改过，返回缓存
            不一样 → 文件被改了（可能是另一个进程写的），重新读

        为什么不用 "内存里改了就更新缓存" 的方式？
          因为可能有多个进程/线程操作同一个 memory.json 文件。
          用 mtime 做最终一致性检测，比维护状态更简单。
        """
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            current_mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            current_mtime = None

        with self._cache_lock:
            cached = self._memory_cache.get(cache_key)
            if cached is not None and cached[1] == current_mtime:
                return cached[0]

        memory_data = self._load_memory_from_file(agent_name, user_id=user_id)

        with self._cache_lock:
            self._memory_cache[cache_key] = (memory_data, current_mtime)

        return memory_data

    def reload(self, agent_name: str | None = None, *, user_id: str | None = None) -> dict[str, Any]:
        """yyds: 强制重载 — 绕过缓存，直接从文件读取。

        什么时候用？
          外部 API 修改了 memory（比如 import_memory_data），需要立即看到最新数据。
        """
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        memory_data = self._load_memory_from_file(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            mtime = file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            mtime = None

        with self._cache_lock:
            self._memory_cache[cache_key] = (memory_data, mtime)
        return memory_data

    def save(self, memory_data: dict[str, Any], agent_name: str | None = None, *, user_id: str | None = None) -> bool:
        """yyds: 原子写入 — 防止写到一半断电导致数据丢失。

        步骤：
          ① 浅拷贝 + 加 lastUpdated（不改调用方的 dict）
          ② 写到 .tmp 临时文件
          ③ rename 覆盖原文件（操作系统保证 rename 是原子的）
          ④ 更新内存缓存
        """
        file_path = self._get_memory_file_path(agent_name, user_id=user_id)
        cache_key = self._cache_key(agent_name, user_id=user_id)

        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            memory_data = {**memory_data, "lastUpdated": utc_now_iso_z()}  # yyds: 浅拷贝，避免改调用方的 dict

            temp_path = file_path.with_suffix(f".{uuid.uuid4().hex}.tmp")  # yyds: 随机文件名，避免并发写冲突
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(memory_data, f, indent=2, ensure_ascii=False)

            temp_path.replace(file_path)  # yyds: rename 是原子操作，要么成功要么没发生

            try:
                mtime = file_path.stat().st_mtime
            except OSError:
                mtime = None

            with self._cache_lock:
                self._memory_cache[cache_key] = (memory_data, mtime)
            logger.info("Memory saved to %s", file_path)
            return True
        except OSError as e:
            logger.error("Failed to save memory file: %s", e)
            return False


_storage_instance: MemoryStorage | None = None
_storage_lock = threading.Lock()


def get_memory_storage() -> MemoryStorage:
    """yyds: 全局存储单例 — 懒加载，可配置，失败回退。

    加载链：
      config.yaml 里配了 memory.storage_class？
      ├─ 没配 → 直接用 FileMemoryStorage
      └─ 配了（如 "myapp.RedisMemoryStorage"）
          ├─ 动态 import 成功 + 是 MemoryStorage 子类 → 用它
          └─ 失败（类不存在、不是子类等）→ 回退到 FileMemoryStorage，记 error 日志
    """
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    with _storage_lock:
        if _storage_instance is not None:
            return _storage_instance

        config = get_memory_config()
        storage_class_path = config.storage_class

        try:
            module_path, class_name = storage_class_path.rsplit(".", 1)
            import importlib

            module = importlib.import_module(module_path)
            storage_class = getattr(module, class_name)

            if not isinstance(storage_class, type):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a class: {storage_class!r}")
            if not issubclass(storage_class, MemoryStorage):
                raise TypeError(f"Configured memory storage '{storage_class_path}' is not a subclass of MemoryStorage")

            _storage_instance = storage_class()
        except Exception as e:
            logger.error(
                "Failed to load memory storage %s, falling back to FileMemoryStorage: %s",
                storage_class_path,
                e,
            )
            _storage_instance = FileMemoryStorage()

    return _storage_instance
