# yyds: 技能系统存储和路径解析配置。
# yyds: 路径解析优先级：显式 path > 环境变量 DEER_FLOW_SKILLS_PATH > 项目根目录/skills > 旧版仓库根目录。
# yyds: container_path 定义沙箱容器内的技能挂载路径，支持 public/custom 两种分类。
# yyds: use 字段指定 SkillStorage 实现类，默认使用本地文件系统存储。
import os
from pathlib import Path

from pydantic import BaseModel, Field

from deerflow.config.runtime_paths import project_root, resolve_path


def _legacy_skills_candidates() -> tuple[Path, ...]:
    """Return source-tree skills locations for monorepo compatibility."""
    backend_dir = Path(__file__).resolve().parents[4]
    repo_root = backend_dir.parent
    return (repo_root / "skills",)


class SkillsConfig(BaseModel):
    """Configuration for skills system"""

    use: str = Field(
        default="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        description="Class path of the SkillStorage implementation.",
    )
    path: str | None = Field(
        default=None,
        description=("Path to skills directory. If not specified, defaults to `skills` under the caller project root, falling back to the legacy repo-root location for monorepo compatibility."),
    )
    container_path: str = Field(
        default="/mnt/skills",
        description="Path where skills are mounted in the sandbox container",
    )

    def get_skills_path(self) -> Path:
        """
        Get the resolved skills directory path.

        Resolution order:
            1. Explicit ``path`` field
            2. ``DEER_FLOW_SKILLS_PATH`` environment variable
            3. ``skills`` under the caller project root (``project_root()``)
            4. Legacy repo-root candidates for monorepo compatibility (``_legacy_skills_candidates``)

        When none of (3) or (4) exist on disk, the project-root default is returned so callers
        can still surface a stable "no skills" location without raising.
        """
        if self.path:
            # Use configured path (can be absolute or relative to project root)
            return resolve_path(self.path)
        if env_path := os.getenv("DEER_FLOW_SKILLS_PATH"):
            return resolve_path(env_path)

        project_default = project_root() / "skills"
        if project_default.is_dir():
            return project_default

        for candidate in _legacy_skills_candidates():
            if candidate.is_dir():
                return candidate

        return project_default

    def get_skill_container_path(self, skill_name: str, category: str = "public") -> str:
        """
        Get the full container path for a specific skill.

        Args:
            skill_name: Name of the skill (directory name)
            category: Category of the skill (public or custom)

        Returns:
            Full path to the skill in the container
        """
        return f"{self.container_path}/{category}/{skill_name}"
