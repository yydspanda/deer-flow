"""yyds: 技能文件解析器 — 从 SKILL.md 的 YAML frontmatter 提取技能元数据。

SKILL.md 长什么样？
  ---
  name: my-skill
  description: 一个数据分析技能
  allowed-tools:
    - search
    - read_file
  ---
  # 技能正文（Markdown）
  你是一个数据分析助手...

这个文件做的事：
  1. 正则匹配 --- 之间的 YAML 块
  2. yaml.safe_load 解析成 dict
  3. 提取 name、description、license、allowed-tools
  4. 构造 Skill 对象返回

解析失败时返回 None（不抛异常），调用方自己决定怎么处理。
"""

import logging
import re
from pathlib import Path

import yaml

from .types import SKILL_MD_FILE, Skill, SkillCategory

logger = logging.getLogger(__name__)


def parse_allowed_tools(raw: object, skill_file: Path) -> list[str] | None:
    """解析 allowed-tools 字段。

    返回值三态：
      None → 字段不存在，不限制
      []   → 字段存在但为空列表，不给用任何工具
      ["search", "read"] → 允许用这些工具

    为什么要单独一个函数？
      因为 installer.py 的安全扫描也需要验证 allowed-tools 格式，
      提取出来避免重复代码。
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError(f"allowed-tools in {skill_file} must be a list of strings")

    allowed_tools: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"allowed-tools in {skill_file} must contain only strings")
        tool_name = item.strip()
        if not tool_name:
            raise ValueError(f"allowed-tools in {skill_file} cannot contain empty tool names")
        allowed_tools.append(tool_name)
    return allowed_tools


def parse_skill_file(skill_file: Path, category: SkillCategory, relative_path: Path | None = None) -> Skill | None:
    """解析 SKILL.md 文件，提取元数据并构造 Skill 对象。

    解析流程：
      文件存在检查 → 正则提取 YAML → safe_load 解析 →
      校验 name/description 必填 → 解析 allowed-tools → 构造 Skill

    为什么用正则 `^---\s*\n(.*?)\n---\s*\n` 而不是专门的 frontmatter 库？
      因为 SKILL.md 的格式非常固定（YAML 在两个 --- 之间），
      一个正则就够了，不引入额外依赖。

    参数 relative_path：
      技能目录相对于 skills/{category}/ 的路径。
      比如技能在 skills/custom/my-org/my-skill/，
      relative_path 就是 my-org/my-skill。
      省略时取 skill_file.parent.name（最后一级目录名）。
    """
    if not skill_file.exists() or skill_file.name != SKILL_MD_FILE:
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")

        front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not front_matter_match:
            return None

        front_matter_text = front_matter_match.group(1)

        try:
            metadata = yaml.safe_load(front_matter_text)
        except yaml.YAMLError as exc:
            logger.error("Invalid YAML front-matter in %s: %s", skill_file, exc)
            return None

        if not isinstance(metadata, dict):
            logger.error("Front-matter in %s is not a YAML mapping", skill_file)
            return None

        name = metadata.get("name")
        description = metadata.get("description")

        if not name or not isinstance(name, str):
            return None
        if not description or not isinstance(description, str):
            return None

        name = name.strip()
        description = description.strip()

        if not name or not description:
            return None

        license_text = metadata.get("license")
        if license_text is not None:
            license_text = str(license_text).strip() or None

        try:
            allowed_tools = parse_allowed_tools(metadata.get("allowed-tools"), skill_file)
        except ValueError as exc:
            logger.error("Invalid allowed-tools in %s: %s", skill_file, exc)
            return None

        return Skill(
            name=name,
            description=description,
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            allowed_tools=allowed_tools,
            enabled=True,  # 实际启用状态从 extensions config 文件读，这里先默认 True
        )

    except Exception:
        logger.exception("Unexpected error parsing skill file %s", skill_file)
        return None
