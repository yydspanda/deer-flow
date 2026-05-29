"""yyds: 技能存储抽象基类 — 模板方法模式。

设计思路：
  基类定义"做什么"（模板方法），子类定义"怎么做"（原子操作）。
  这样新增存储后端（S3、数据库等）只需实现 ~10 个抽象方法，
  就能复用基类的加载、验证、路径计算等逻辑。

目录布局（协议的一部分）：
  <root>/public/<name>/SKILL.md     内置技能
  <root>/custom/<name>/SKILL.md     用户技能
  <root>/custom/.history/<name>.jsonl  历史记录

三大类方法：
  1. 静态协议辅助 — validate_skill_name、validate_relative_path 等
  2. 抽象原子操作 — 子类必须实现的存储介质相关方法
  3. 具体模板方法 — load_skills（发现+解析+合并启用状态）
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable
from pathlib import Path

from deerflow.skills.types import SKILL_MD_FILE, Skill, SkillCategory  # noqa: F401

logger = logging.getLogger(__name__)

_SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillStorage(ABC):
    """技能存储抽象基类。

    为什么用模板方法而不是直接继承？
      因为 load_skills 的流程是固定的（遍历→解析→合并启用状态→排序），
      只有"遍历文件"和"读写文件"跟存储介质相关。
      把流程固化在基类，子类只需关注存储细节。
    """

    def __init__(self, container_path: str = "/mnt/skills") -> None:
        self._container_root = container_path

    # ------------------------------------------------------------------
    # 静态协议辅助（不依赖存储介质）
    # ------------------------------------------------------------------

    @staticmethod
    def validate_skill_name(name: str) -> str:
        """验证并规范化技能名称。

        规则：小写字母+数字，用连字符分隔，最长 64 字符。
        比如 my-skill-2 合法，My_Skill 不合法。
        """
        normalized = name.strip()
        if not _SKILL_NAME_PATTERN.fullmatch(normalized):
            raise ValueError("Skill name must be hyphen-case using lowercase letters, digits, and hyphens only.")
        if len(normalized) > 64:
            raise ValueError("Skill name must be 64 characters or fewer.")
        return normalized

    @staticmethod
    def validate_relative_path(relative_path: str, base_dir: Path) -> Path:
        """验证相对路径不会逃逸出 base_dir。

        为什么不直接 base_dir / relative_path？
          如果 relative_path 包含 ../，拼接后可能跑出 base_dir。
          resolve() 展开所有 .. 和符号链接，relative_to() 检查是否在范围内。
        """
        if not relative_path:
            raise ValueError("relative_path must not be empty.")
        resolved_base = base_dir.resolve()
        target = (resolved_base / relative_path).resolve()
        try:
            target.relative_to(resolved_base)
        except ValueError as exc:
            raise ValueError("relative_path must resolve within the skill directory.") from exc
        return target

    @staticmethod
    def validate_skill_markdown_content(name: str, content: str) -> None:
        """验证 SKILL.md 内容，写到一个临时目录再跑验证。

        为什么不直接解析 content 字符串？
          因为 _validate_skill_frontmatter 接收的是目录路径，
          它会构造 skill_md = skill_dir / SKILL.md 的路径。
          所以需要临时目录 + 临时文件。这是验证逻辑复用的代价。
        """
        import tempfile

        from deerflow.skills.validation import _validate_skill_frontmatter

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_skill_dir = Path(tmp_dir) / SkillStorage.validate_skill_name(name)
            temp_skill_dir.mkdir(parents=True, exist_ok=True)
            (temp_skill_dir / SKILL_MD_FILE).write_text(content, encoding="utf-8")
            is_valid, message, parsed_name = _validate_skill_frontmatter(temp_skill_dir)
            if not is_valid:
                raise ValueError(message)
            if parsed_name != name:
                raise ValueError(f"Frontmatter name '{parsed_name}' must match requested skill name '{name}'.")

    def ensure_safe_support_path(self, name: str, relative_path: str) -> Path:
        """验证辅助文件路径，只允许在 references/templates/scripts/assets 下。

        为什么限制子目录？
          技能目录下有 SKILL.md（主文件）和辅助文件。
          辅助文件必须放在指定子目录，不能随意创建目录结构，
          防止把文件写到意外位置。
        """
        _ALLOWED_SUPPORT_SUBDIRS = {"references", "templates", "scripts", "assets"}
        skill_dir = self.get_custom_skill_dir(self.validate_skill_name(name)).resolve()
        if not relative_path or relative_path.endswith("/"):
            raise ValueError("Supporting file path must include a filename.")
        relative = Path(relative_path)
        if relative.is_absolute():
            raise ValueError("Supporting file path must be relative.")
        if any(part in {"..", ""} for part in relative.parts):
            raise ValueError("Supporting file path must not contain parent-directory traversal.")
        top_level = relative.parts[0] if relative.parts else ""
        if top_level not in _ALLOWED_SUPPORT_SUBDIRS:
            raise ValueError(f"Supporting files must live under one of: {', '.join(sorted(_ALLOWED_SUPPORT_SUBDIRS))}.")
        target = (skill_dir / relative).resolve()
        allowed_root = (skill_dir / top_level).resolve()
        try:
            target.relative_to(allowed_root)
        except ValueError as exc:
            raise ValueError("Supporting file path must stay within the selected support directory.") from exc
        return target

    # ------------------------------------------------------------------
    # 抽象原子操作（存储介质相关，子类必须实现）
    # ------------------------------------------------------------------

    @abstractmethod
    def get_skills_root_path(self) -> Path:
        """技能根目录的宿主机绝对路径，用于沙箱挂载。"""

    @abstractmethod
    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        """遍历所有 SKILL.md 文件，返回 (类别, 类别根目录, 文件路径) 元组。"""

    @abstractmethod
    def read_custom_skill(self, name: str) -> str:
        """读取自定义技能的 SKILL.md 内容。"""

    @abstractmethod
    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        """原子化写入文件到 custom/<name>/<relative_path>。"""

    @abstractmethod
    async def ainstall_skill_from_archive(self, archive_path: str | Path) -> dict:
        """异步安装 .skill ZIP 包。"""

    def install_skill_from_archive(self, archive_path: str | Path) -> dict:
        """同步包装器，在同步上下文中调用异步安装。"""
        from deerflow.skills.installer import _run_async_install

        return _run_async_install(self.ainstall_skill_from_archive(archive_path))

    @abstractmethod
    def delete_custom_skill(self, name: str, *, history_meta: dict | None = None) -> None:
        """删除自定义技能，可选记录操作历史。"""

    @abstractmethod
    def custom_skill_exists(self, name: str) -> bool:
        """自定义技能是否存在。"""

    @abstractmethod
    def public_skill_exists(self, name: str) -> bool:
        """内置技能是否存在。"""

    @abstractmethod
    def append_history(self, name: str, record: dict) -> None:
        """追加 JSONL 历史记录。"""

    @abstractmethod
    def read_history(self, name: str) -> list[dict]:
        """读取所有历史记录，按时间正序。"""

    # ------------------------------------------------------------------
    # 具体路径辅助方法（目录布局是协议的一部分）
    # ------------------------------------------------------------------

    def get_container_root(self) -> str:
        """容器中的技能根挂载路径。"""
        return self._container_root

    def get_custom_skill_dir(self, name: str) -> Path:
        """自定义技能目录路径 custom/<name>，不创建目录。"""
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / normalized_name

    def get_custom_skill_file(self, name: str) -> Path:
        """自定义技能 SKILL.md 文件路径。"""
        normalized_name = self.validate_skill_name(name)
        return self.get_custom_skill_dir(normalized_name) / SKILL_MD_FILE

    def get_skill_history_file(self, name: str) -> Path:
        """技能历史 JSONL 文件路径 custom/.history/<name>.jsonl。"""
        normalized_name = self.validate_skill_name(name)
        return self.get_skills_root_path() / SkillCategory.CUSTOM.value / ".history" / f"{normalized_name}.jsonl"

    # ------------------------------------------------------------------
    # 具体模板方法流程
    # ------------------------------------------------------------------

    def load_skills(self, *, enabled_only: bool = False) -> list[Skill]:
        """发现所有技能，合并启用状态，排序后返回。

        流程：
          1. _iter_skill_files() 遍历所有 SKILL.md
          2. parse_skill_file() 解析每个文件
          3. 同名技能去重（后出现的覆盖前面的）
          4. 从 extensions config 读取启用状态
          5. 可选过滤 enabled_only
          6. 按名称排序

        为什么每次都重新读取 extensions config？
          因为配置可能被其他进程修改了（热重载），
          不重新读就会用过期状态。
        """
        from deerflow.skills.parser import parse_skill_file

        skills_by_name: dict[str, Skill] = {}
        for category, category_root, md_path in self._iter_skill_files():
            skill = parse_skill_file(
                md_path,
                category=category,
                relative_path=md_path.parent.relative_to(category_root),
            )
            if skill:
                skills_by_name[skill.name] = skill

        skills = list(skills_by_name.values())

        try:
            from deerflow.config.extensions_config import ExtensionsConfig

            extensions_config = ExtensionsConfig.from_file()
            for skill in skills:
                skill.enabled = extensions_config.is_skill_enabled(skill.name, skill.category)
        except Exception as e:
            logger.warning("Failed to load extensions config: %s", e)

        if enabled_only:
            skills = [s for s in skills if s.enabled]

        skills.sort(key=lambda s: s.name)
        return skills

    def ensure_custom_skill_is_editable(self, name: str) -> None:
        """确认技能可编辑：自定义技能可以，内置技能不行。

        如果同名技能同时存在于 public 和 custom，custom 版本覆盖 public。
        这允许用户"覆盖"内置技能来自定义行为。
        """
        if self.custom_skill_exists(name):
            return
        if self.public_skill_exists(name):
            raise ValueError(f"'{name}' is a built-in skill. To customise it, create a new skill with the same name under skills/custom/.")
        raise FileNotFoundError(f"Custom skill '{name}' not found.")
