"""yyds: 技能验证 — SKILL.md 的 frontmatter 必须遵守什么规则？

parser.py 负责"解析"（提取字段），
validation.py 负责"验证"（检查字段是否合法）。

验证规则：
  1. 必须有 YAML frontmatter（--- 包裹）
  2. 只允许白名单里的字段（name/description/license/allowed-tools/metadata/compatibility/version/author）
  3. name 和 description 必填
  4. name 必须是 kebab-case（小写字母+数字+连字符），不超过 64 字符
  5. description 不允许尖括号（防 XSS），不超过 1024 字符
  6. allowed-tools 必须是字符串列表

返回值：(is_valid, message, skill_name)
  is_valid   — 是否通过
  message    — 通过时 "Skill is valid!"，失败时是具体错误信息
  skill_name — 通过时返回技能名，失败时 None
"""

import re
from pathlib import Path

import yaml

from deerflow.skills.parser import parse_allowed_tools
from deerflow.skills.types import SKILL_MD_FILE

ALLOWED_FRONTMATTER_PROPERTIES = {"name", "description", "license", "allowed-tools", "metadata", "compatibility", "version", "author"}


def _validate_skill_frontmatter(skill_dir: Path) -> tuple[bool, str, str | None]:
    """验证技能目录下的 SKILL.md frontmatter 是否合法。

    为什么 name 要 kebab-case？
      技能名会变成目录名、URL 路径、Docker 标签，
      kebab-case 是这些场景的通用规范，避免空格/大写/特殊字符问题。

    为什么 description 禁止尖括号？
      防止注入 HTML/XML 标签。技能描述可能被渲染到 Web UI，
      尖括号可能被浏览器解析为 HTML 标签导致 XSS。
    """
    skill_md = skill_dir / SKILL_MD_FILE
    if not skill_md.exists():
        return False, f"{SKILL_MD_FILE} not found", None

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False, "No YAML frontmatter found", None

    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format", None

    frontmatter_text = match.group(1)

    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary", None
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}", None

    unexpected_keys = set(frontmatter.keys()) - ALLOWED_FRONTMATTER_PROPERTIES
    if unexpected_keys:
        return False, f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}", None

    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter", None
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter", None

    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}", None
    name = name.strip()
    if not name:
        return False, "Name cannot be empty", None

    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"Name '{name}' should be hyphen-case (lowercase letters, digits, and hyphens only)", None
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens", None
    if len(name) > 64:
        return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters.", None

    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}", None
    description = description.strip()
    if description:
        if "<" in description or ">" in description:
            return False, "Description cannot contain angle brackets (< or >)", None
        if len(description) > 1024:
            return False, f"Description is too long ({len(description)} characters). Maximum is 1024 characters.", None

    try:
        parse_allowed_tools(frontmatter.get("allowed-tools"), skill_md)
    except ValueError as e:
        return False, str(e).replace(str(skill_md), SKILL_MD_FILE), None

    return True, "Skill is valid!", name
