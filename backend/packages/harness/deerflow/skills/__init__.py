"""yyds: 技能系统 — 让 Agent 学会新能力的插件机制。

代码结构：
skills/
├── __init__.py                模块入口，导出核心类型
├── types.py                   Skill 数据类 + SkillCategory 枚举
├── parser.py                  ★ 从 SKILL.md 解析技能元数据
├── validation.py              SKILL.md frontmatter 验证（命名规范、字段白名单）
├── tool_policy.py             技能 allowed-tools → 工具过滤
├── installer.py               ★★ ZIP 技能包安全安装（解压 + 扫描 + 原子移动）
├── security_scanner.py        ★★ LLM 安全审查（检测提示注入、权限提升）
└── storage/
    ├── skill_storage.py       SkillStorage 抽象基类 + CRUD
    └── local_skill_storage.py 本地文件系统实现

建议阅读顺序：
  1. types.py             — 最小文件，搞清楚 Skill 是什么
  2. parser.py            — SKILL.md 怎么变成 Skill 对象
  3. tool_policy.py       — 技能怎么限制工具
  4. validation.py        — 验证规则（跟 parser 配合）
  5. installer.py         — 安装流程（安全解压 + 扫描 + 原子安装）
  6. security_scanner.py  — LLM 安全审查（最有趣的部分）
  7. storage/             — 按需看

什么是 Skill？
  一个 SKILL.md 文件 + 可选的脚本/模板。
  Agent 加载技能后，system prompt 里会注入技能内容，
  相当于给 Agent "读了本说明书"，它就会新技能了。
"""

from __future__ import annotations

from .installer import SkillAlreadyExistsError, SkillSecurityScanError
from .storage import LocalSkillStorage, SkillStorage, get_or_new_skill_storage
from .types import Skill
from .validation import ALLOWED_FRONTMATTER_PROPERTIES, _validate_skill_frontmatter

__all__ = [
    "Skill",
    "ALLOWED_FRONTMATTER_PROPERTIES",
    "_validate_skill_frontmatter",
    "SkillAlreadyExistsError",
    "SkillSecurityScanError",
    "SkillStorage",
    "LocalSkillStorage",
    "get_or_new_skill_storage",
]
