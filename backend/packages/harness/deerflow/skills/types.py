"""yyds: 技能数据类型 — Skill 是什么？

一个技能就是一个 SKILL.md 文件 + 可选的脚本/模板。
这个文件定义了 Skill 的数据结构，让 parser 解析出来的结果有个地方放。

关键概念：
  SkillCategory — 技能来源：PUBLIC(内置只读) vs CUSTOM(用户可编辑)
  Skill         — 技能数据类：名称、描述、路径、允许的工具
  SKILL_MD_FILE — 技能文件名常量，固定 "SKILL.md"

注意 Skill.get_container_path() —— 技能会被挂载到沙箱容器里，
路径是 /mnt/skills/{category}/{relative_path}，这样 Agent 在沙箱里也能读到技能。
"""

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

SKILL_MD_FILE = "SKILL.md"


class SkillCategory(StrEnum):
    """技能来源类别。

    为什么用 StrEnum 而不是普通 Enum？
      因为值就是字符串 "public"/"custom"，直接当字符串用，
      不用 .value 取值。序列化 JSON 时也省事。
    """

    PUBLIC = "public"  # 内置技能，随平台发布，只读不可删除
    CUSTOM = "custom"  # 用户上传/创建的技能，可编辑可删除


@dataclass
class Skill:
    """技能数据类。

    一个技能包含什么？
      - name/description：元数据，从 SKILL.md 的 YAML frontmatter 解析
      - skill_dir/skill_file：文件系统路径（宿主机上的路径）
      - relative_path：相对于 skills/{category}/ 的路径
      - category：PUBLIC 或 CUSTOM
      - allowed_tools：声明技能需要用哪些工具，None=不限，[]=不给用
      - enabled：是否启用（实际状态从 extensions config 文件读）

    为什么不用 Pydantic BaseModel？
      因为 Skill 不需要 JSON 序列化/反序列化/验证，
      它只被 parser 构造、被 storage 存储、被 runtime 读取。
      用 dataclass 更轻量。
    """

    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path
    category: SkillCategory
    allowed_tools: list[str] | None = None
    enabled: bool = False

    @property
    def skill_path(self) -> str:
        """技能在类别根目录下的相对路径字符串。

        例如 skills/custom/my-skill → "my-skill"
        如果技能就在根目录下 → ""
        """
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(self, container_base_path: str = "/mnt/skills") -> str:
        """技能在沙箱容器中的完整目录路径。

        为什么要这个？
          Agent 在沙箱容器中运行时，需要读取 SKILL.md 来加载技能。
          宿主机的 skills/ 目录被挂载到容器的 /mnt/skills/，
          所以要把宿主机的 Path 转换成容器内的路径字符串。
        """
        category_base = f"{container_base_path}/{self.category}"
        skill_path = self.skill_path
        if skill_path:
            return f"{category_base}/{skill_path}"
        return category_base

    def get_container_file_path(self, container_base_path: str = "/mnt/skills") -> str:
        """技能 SKILL.md 在沙箱容器中的完整文件路径。"""
        return f"{self.get_container_path(container_base_path)}/SKILL.md"

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, description={self.description!r}, category={self.category!r})"
