"""yyds: 本地文件系统技能存储实现。

目录布局：
  <root>/public/<name>/SKILL.md        内置技能
  <root>/custom/<name>/SKILL.md        用户技能
  <root>/custom/.history/<name>.jsonl  操作历史

实现 SkillStorage 的 ~10 个抽象方法，全部基于本地文件系统操作。
亮点：
  - write_custom_skill 用临时文件+rename 实现原子写入
  - ainstall_skill_from_archive 完整安装流程（解压→验证→扫描→原子部署）
  - delete_custom_skill 删除前可选保存历史（容忍权限错误继续删除）
"""

from __future__ import annotations

import errno
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from deerflow.config.runtime_paths import resolve_path
from deerflow.skills.permissions import make_skill_written_path_sandbox_readable
from deerflow.skills.storage.skill_storage import SKILL_MD_FILE, SkillStorage
from deerflow.skills.types import SkillCategory

logger = logging.getLogger(__name__)

DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"


class LocalSkillStorage(SkillStorage):
    """基于本地文件系统的技能存储。

    目录结构的约定：
      public/ 下的技能是只读的（随平台发布）
      custom/ 下的技能可增删改（用户创建）
      custom/.history/ 存操作历史（JSONL 格式）
    """

    def __init__(
        self,
        host_path: str | None = None,
        container_path: str = DEFAULT_SKILLS_CONTAINER_PATH,
        app_config=None,
    ) -> None:
        super().__init__(container_path=container_path)
        if host_path is None:
            from deerflow.config import get_app_config

            config = app_config or get_app_config()
            self._host_root: Path = config.skills.get_skills_path()
        else:
            self._host_root = resolve_path(host_path)

    # ------------------------------------------------------------------
    # 抽象方法实现
    # ------------------------------------------------------------------

    def get_skills_root_path(self) -> Path:
        return self._host_root

    def custom_skill_exists(self, name: str) -> bool:
        return self.get_custom_skill_file(name).exists()

    def public_skill_exists(self, name: str) -> bool:
        normalized_name = self.validate_skill_name(name)
        return (self._host_root / SkillCategory.PUBLIC.value / normalized_name / SKILL_MD_FILE).exists()

    def _iter_skill_files(self) -> Iterable[tuple[SkillCategory, Path, Path]]:
        """递归遍历 public/custom 目录，找出所有 SKILL.md。

        os.walk + followlinks=True：
          跟随符号链接，这样可以用 symlink 指向共享技能目录。
        dir_names 过滤掉 . 开头的目录：
          隐藏目录（.history 等）不参与技能发现。
        """
        if not self._host_root.exists():
            return
        for category in SkillCategory:
            category_path = self._host_root / category.value
            if not category_path.exists() or not category_path.is_dir():
                continue
            for current_root, dir_names, file_names in os.walk(category_path, followlinks=True):
                dir_names[:] = sorted(name for name in dir_names if not name.startswith("."))
                if SKILL_MD_FILE not in file_names:
                    continue
                yield category, category_path, Path(current_root) / SKILL_MD_FILE

    def read_custom_skill(self, name: str) -> str:
        if not self.custom_skill_exists(name):
            raise FileNotFoundError(f"Custom skill '{name}' not found.")
        return (self.get_custom_skill_dir(name) / SKILL_MD_FILE).read_text(encoding="utf-8")

    def write_custom_skill(self, name: str, relative_path: str, content: str) -> None:
        """原子写入：先写临时文件，再 rename。

        为什么用 tempfile + replace 而不是直接 open("w")？
          如果写入到一半进程崩溃（OOM、kill -9），
          直接写会留下半截文件。
          临时文件写入完成后 rename 是原子操作（同一文件系统），
          保证文件要么是旧的要么是新的，不会出现半截。
        """
        target = self.validate_relative_path(relative_path, self.get_custom_skill_dir(name))
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=str(target.parent),
        ) as tmp_file:
            tmp_file.write(content)
            tmp_path = Path(tmp_file.name)
        tmp_path.replace(target)
        make_skill_written_path_sandbox_readable(self.get_custom_skill_dir(name), target)

    async def ainstall_skill_from_archive(self, archive_path: str | Path) -> dict:
        """完整安装流程。

        步骤：
          1. 校验文件存在且扩展名为 .skill
          2. 安全解压到临时目录
          3. 定位技能根目录
          4. 验证 frontmatter 格式
          5. 检查重名
          6. LLM 安全扫描所有文本和脚本文件
          7. 原子部署（staging → target）

        为什么用 staging 目录？
          直接往目标目录写文件，如果中途失败需要手动清理。
          用 staging 目录 + _move_staged_skill_into_reserved_target，
          失败时 staging 目录随 TemporaryDirectory 自动清理，
          目标目录要么完全创建成功，要么完全不存在。
        """
        import zipfile

        from deerflow.skills.installer import (
            SkillAlreadyExistsError,
            _move_staged_skill_into_reserved_target,
            _scan_skill_archive_contents_or_raise,
            resolve_skill_dir_from_archive,
            safe_extract_skill_archive,
        )
        from deerflow.skills.validation import _validate_skill_frontmatter

        logger.info("Installing skill from %s", archive_path)
        path = Path(archive_path)
        if not path.is_file():
            if not path.exists():
                raise FileNotFoundError(f"Skill file not found: {archive_path}")
            raise ValueError(f"Path is not a file: {archive_path}")
        if path.suffix != ".skill":
            raise ValueError("File must have .skill extension")

        custom_dir = self._host_root / "custom"
        custom_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)

            try:
                zf = zipfile.ZipFile(path, "r")
            except FileNotFoundError:
                raise FileNotFoundError(f"Skill file not found: {archive_path}") from None
            except (zipfile.BadZipFile, IsADirectoryError):
                raise ValueError("File is not a valid ZIP archive") from None

            with zf:
                safe_extract_skill_archive(zf, tmp_path)

            skill_dir = resolve_skill_dir_from_archive(tmp_path)

            is_valid, message, skill_name = _validate_skill_frontmatter(skill_dir)
            if not is_valid:
                raise ValueError(f"Invalid skill: {message}")
            if not skill_name or "/" in skill_name or "\\" in skill_name or ".." in skill_name:
                raise ValueError(f"Invalid skill name: {skill_name}")

            target = custom_dir / skill_name
            if target.exists():
                raise SkillAlreadyExistsError(f"Skill '{skill_name}' already exists")

            await _scan_skill_archive_contents_or_raise(skill_dir, skill_name)

            with tempfile.TemporaryDirectory(prefix=f".installing-{skill_name}-", dir=custom_dir) as staging_root:
                staging_target = Path(staging_root) / skill_name
                shutil.copytree(skill_dir, staging_target)
                _move_staged_skill_into_reserved_target(staging_target, target)
            logger.info("Skill %r installed to %s", skill_name, target)

        return {
            "success": True,
            "skill_name": skill_name,
            "message": f"Skill '{skill_name}' installed successfully",
        }

    def delete_custom_skill(self, name: str, *, history_meta: dict | None = None) -> None:
        """删除自定义技能目录。

        如果提供 history_meta，删除前会保存当前内容到历史。
        历史写入失败不会阻止删除（权限错误时只 warning）。
        这个"宽容"设计是因为删除比保存历史更重要——
        用户要求删除就应该删掉，不能因为历史文件只读而卡住。
        """
        self.validate_skill_name(name)
        self.ensure_custom_skill_is_editable(name)
        target = self.get_custom_skill_dir(name)
        if history_meta is not None:
            prev_content = self.read_custom_skill(name)
            try:
                self.append_history(name, {**history_meta, "prev_content": prev_content})
            except OSError as e:
                if not isinstance(e, PermissionError) and e.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
                    raise
                logger.warning(
                    "Skipping delete history write for custom skill %s due to readonly/permission failure; continuing with skill directory removal: %s",
                    name,
                    e,
                )
        if target.exists():
            shutil.rmtree(target)

    def append_history(self, name: str, record: dict) -> None:
        """追加 JSONL 历史记录，自动加时间戳。"""
        self.validate_skill_name(name)
        payload = {"ts": datetime.now(UTC).isoformat(), **record}
        history_path = self.get_skill_history_file(name)
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with history_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
            f.write("\n")

    def read_history(self, name: str) -> list[dict]:
        """读取所有 JSONL 历史记录。"""
        self.validate_skill_name(name)
        history_path = self.get_skill_history_file(name)
        if not history_path.exists():
            return []
        records: list[dict] = []
        for line in history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            records.append(json.loads(line))
        return records
