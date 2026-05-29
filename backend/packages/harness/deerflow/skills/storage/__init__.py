"""yyds: 技能存储入口 — 单例工厂，按需创建 SkillStorage 实例。

三种创建模式：
  1. 显式路径（skills_path=...）→ 每次创建新实例，不缓存
  2. 请求级配置（app_config=...）→ 每次创建新实例，不缓存
  3. 无参数 → 进程级单例，首次创建后复用

为什么需要三种模式？
  - 测试/CLI：显式指定路径，不依赖全局配置
  - Gateway 请求：每个请求可能用不同的 app_config（多租户）
  - Client/普通使用：单例就够，避免重复读配置

单例失效条件：
  app_config 对象变了（is not 比较）→ 重新创建
  这实现了"热重载"：配置文件修改后，下次调用自动用新配置。
"""

from __future__ import annotations

from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.storage.skill_storage import SkillStorage

_default_skill_storage: SkillStorage | None = None
_default_skill_storage_config: object | None = None


def get_or_new_skill_storage(**kwargs) -> SkillStorage:
    """获取或创建技能存储实例。

    反射机制：
      config.skills.use 指定存储类名（如 "LocalSkillStorage"），
      resolve_class() 动态加载，不需要硬编码 if/else。
      以后加新的存储后端（S3、数据库等），只要实现 SkillStorage ABC，
      改配置就行，不用改这里的代码。
    """
    global _default_skill_storage, _default_skill_storage_config

    from deerflow.config import get_app_config
    from deerflow.config.skills_config import SkillsConfig

    def _make_storage(skills_config: SkillsConfig, *, host_path: str | None = None, **kwargs) -> SkillStorage:
        from deerflow.reflection import resolve_class

        cls = resolve_class(skills_config.use, SkillStorage)
        return cls(
            host_path=host_path if host_path is not None else str(skills_config.get_skills_path()),
            container_path=skills_config.container_path,
            **kwargs,
        )

    skills_path = kwargs.pop("skills_path", None)
    app_config = kwargs.pop("app_config", None)

    if skills_path is not None:
        if app_config is not None:
            return _make_storage(app_config.skills, host_path=str(skills_path), **kwargs)
        from deerflow.config.skills_config import SkillsConfig

        return _make_storage(SkillsConfig(), host_path=str(skills_path), **kwargs)

    if app_config is not None:
        return _make_storage(app_config.skills, **kwargs)

    if _default_skill_storage is not None and _default_skill_storage_config is None:
        return _default_skill_storage

    app_config_now = get_app_config()
    if _default_skill_storage is None or _default_skill_storage_config is not app_config_now:
        _default_skill_storage = _make_storage(app_config_now.skills, **kwargs)
        _default_skill_storage_config = app_config_now
    return _default_skill_storage


def reset_skill_storage() -> None:
    """重置单例缓存，测试用。"""
    global _default_skill_storage, _default_skill_storage_config
    _default_skill_storage = None
    _default_skill_storage_config = None


__all__ = [
    "LocalSkillStorage",
    "SkillStorage",
    "get_or_new_skill_storage",
    "reset_skill_storage",
]
