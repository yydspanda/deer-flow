"""yyds: 技能安装器 — ZIP 技能包的安全解压、内容扫描和原子安装。

用户上传一个 ZIP 包，里面是技能文件（SKILL.md + 可选脚本/模板）。
安装流程：

  1. 安全解压（safe_extract_skill_archive）
     - 拒绝绝对路径和 .. 路径遍历
     - 跳过符号链接
     - 限制总解压大小（防 ZIP 炸弹，默认 512MB）

  2. 定位技能根目录（resolve_skill_dir_from_archive）
     - 过滤 macOS 元数据（__MACOSX、.DS_Store）
     - 如果只有一级目录 → 用那个目录作为根

  3. 安全扫描（_scan_skill_archive_contents_or_raise）
     - 遍历所有 scripts/ 和 references/templates/ 下的文本文件
     - 每个文件发给 LLM 判断是 allow/warn/block
     - 脚本文件要求必须 allow，普通文件 allow/warn 都行

  4. 原子安装（_move_staged_skill_into_reserved_target）
     - 先 mkdir 占位（reserved）
     - 然后逐个 move 文件
     - 任何步骤失败 → rmtree 回滚

安全设计亮点：
  - ZIP 解压三重防护：路径遍历 + 符号链接 + ZIP 炸弹
  - 嵌套 SKILL.md 禁止（防目录逃逸）
  - 脚本文件扫描标准更严（必须 allow，warn 都不行）
  - 安装失败自动回滚（原子性）

这个文件是纯业务逻辑，没有 FastAPI/HTTP 依赖。
Gateway 和 Client 都调用这些函数。
"""

import asyncio
import concurrent.futures
import logging
import posixpath
import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath

from deerflow.skills.permissions import make_skill_tree_sandbox_readable
from deerflow.skills.security_scanner import scan_skill_content

logger = logging.getLogger(__name__)

_PROMPT_INPUT_DIRS = {"references", "templates"}
_PROMPT_INPUT_SUFFIXES = frozenset({".json", ".markdown", ".md", ".rst", ".txt", ".yaml", ".yml"})


class SkillAlreadyExistsError(ValueError):
    """同名技能已存在。安装时先占位目录，如果目录已存在说明重名。"""


class SkillSecurityScanError(ValueError):
    """安全扫描失败。可能是 LLM 判定为 block，也可能是 LLM 调用失败。"""


def is_unsafe_zip_member(info: zipfile.ZipInfo) -> bool:
    """检查 ZIP 成员路径是否危险。

    三种危险路径：
      1. 绝对路径：/etc/passwd — Unix
      2. 绝对路径：C:\Windows — Windows（也要防）
      3. 目录遍历：../../etc/passwd — ..

    为什么用 PureWindowsPath 也要检查？
      因为攻击者可以在 Linux 上构造包含 Windows 绝对路径的 ZIP，
      如果服务器运行在 Windows 上就会被攻击。
    """
    name = info.filename
    if not name:
        return False
    normalized = name.replace("\\", "/")
    if normalized.startswith("/"):
        return True
    path = PurePosixPath(normalized)
    if path.is_absolute():
        return True
    if PureWindowsPath(name).is_absolute():
        return True
    if ".." in path.parts:
        return True
    return False


def is_symlink_member(info: zipfile.ZipInfo) -> bool:
    """检测 ZIP 条目是否为符号链接。

    符号链接可以指向系统任意文件，解压后可能暴露敏感信息。
    检测方法：读取 external_attr 的高 16 位（Unix 文件模式），
    用 stat.S_ISLNK 判断是否为链接。
    """
    mode = info.external_attr >> 16
    return stat.S_ISLNK(mode)


def should_ignore_archive_entry(path: Path) -> bool:
    """忽略 macOS 元数据和隐藏文件。"""
    return path.name.startswith(".") or path.name == "__MACOSX"


def resolve_skill_dir_from_archive(temp_path: Path) -> Path:
    """从解压内容中定位技能根目录。

    典型 ZIP 结构：
      情况 1：ZIP 里只有一个目录 my-skill/（用户压缩的是文件夹）
        → 返回 my-skill/
      情况 2：ZIP 里直接是 SKILL.md + 其他文件（用户压缩的是文件夹内容）
        → 返回 temp_path 本身
    """
    items = [p for p in temp_path.iterdir() if not should_ignore_archive_entry(p)]
    if not items:
        raise ValueError("Skill archive is empty")
    if len(items) == 1 and items[0].is_dir():
        return items[0]
    return temp_path


def safe_extract_skill_archive(
    zip_ref: zipfile.ZipFile,
    dest_path: Path,
    max_total_size: int = 512 * 1024 * 1024,
) -> None:
    """安全解压技能 ZIP 包。

    三重防护：
      1. 路径遍历（is_unsafe_zip_member）— 拒绝解压
      2. 符号链接（is_symlink_member）— 跳过不解压
      3. ZIP 炸弹（max_total_size）— 累计写入大小超限则中断

    为什么用 resolve() + is_relative_to() 二次检查？
      正则和 PurePosixPath 检查可能遗漏边界情况，
      resolve() 会展开所有 .. 和符号链接，is_relative_to() 确保结果路径不会跑出目标目录。
    """
    dest_root = dest_path.resolve()
    total_written = 0

    for info in zip_ref.infolist():
        if is_unsafe_zip_member(info):
            raise ValueError(f"Archive contains unsafe member path: {info.filename!r}")

        if is_symlink_member(info):
            logger.warning("Skipping symlink entry in skill archive: %s", info.filename)
            continue

        normalized_name = posixpath.normpath(info.filename.replace("\\", "/"))
        member_path = dest_root.joinpath(*PurePosixPath(normalized_name).parts)
        if not member_path.resolve().is_relative_to(dest_root):
            raise ValueError(f"Zip entry escapes destination: {info.filename!r}")
        member_path.parent.mkdir(parents=True, exist_ok=True)

        if info.is_dir():
            member_path.mkdir(parents=True, exist_ok=True)
            continue

        with zip_ref.open(info) as src, member_path.open("wb") as dst:
            while chunk := src.read(65536):
                total_written += len(chunk)
                if total_written > max_total_size:
                    raise ValueError("Skill archive is too large or appears highly compressed.")
                dst.write(chunk)


def _is_script_support_file(rel_path: Path) -> bool:
    """是否在 scripts/ 目录下。脚本文件扫描标准更严。"""
    return bool(rel_path.parts) and rel_path.parts[0] == "scripts"


def _should_scan_support_file(rel_path: Path) -> bool:
    """哪些文件需要安全扫描？

    两类文件需要扫描：
      1. scripts/ 下的所有文件 — 可执行，风险高
      2. references/ 或 templates/ 下的文本文件 — 会被注入到 prompt
    其他文件（图片、二进制等）不扫描，因为不参与 Agent 执行。
    """
    if _is_script_support_file(rel_path):
        return True
    return bool(rel_path.parts) and rel_path.parts[0] in _PROMPT_INPUT_DIRS and rel_path.suffix.lower() in _PROMPT_INPUT_SUFFIXES


def _move_staged_skill_into_reserved_target(staging_target: Path, target: Path) -> None:
    """原子化安装：先占位再移动，失败自动回滚。

    为什么要原子化？
      如果移动到一半失败了（比如磁盘满），
      目标目录里只有部分文件，技能处于损坏状态。
      用 reserved 标记位 + finally rmtree 保证：要么全成功，要么全回滚。
    """
    installed = False
    reserved = False
    try:
        target.mkdir(mode=0o700)
        reserved = True
        for child in staging_target.iterdir():
            shutil.move(str(child), target / child.name)
        make_skill_tree_sandbox_readable(target)
        installed = True
    except FileExistsError as e:
        raise SkillAlreadyExistsError(f"Skill '{target.name}' already exists") from e
    finally:
        if reserved and not installed and target.exists():
            shutil.rmtree(target)


async def _scan_skill_file_or_raise(skill_dir: Path, path: Path, skill_name: str, *, executable: bool) -> None:
    """扫描单个文件，不通过就抛异常。

    executable=True 时标准更严：
      LLM 返回 allow → 通过
      LLM 返回 warn  → 不通过（脚本只接受 allow）
      LLM 返回 block → 不通过
    executable=False 时：
      allow 和 warn 都通过，只有 block 不通过。
    """
    rel_path = path.relative_to(skill_dir).as_posix()
    location = f"{skill_name}/{rel_path}"
    try:
        content = await asyncio.to_thread(path.read_text, encoding="utf-8")
    except UnicodeDecodeError as e:
        raise SkillSecurityScanError(f"Security scan failed for skill '{skill_name}': {location} must be valid UTF-8") from e

    try:
        result = await scan_skill_content(content, executable=executable, location=location)
    except Exception as e:
        raise SkillSecurityScanError(f"Security scan failed for {location}: {e}") from e

    decision = getattr(result, "decision", None)
    reason = str(getattr(result, "reason", "") or "No reason provided.")
    if decision == "block":
        if rel_path == "SKILL.md":
            raise SkillSecurityScanError(f"Security scan blocked skill '{skill_name}': {reason}")
        raise SkillSecurityScanError(f"Security scan blocked {location}: {reason}")
    if executable and decision != "allow":
        raise SkillSecurityScanError(f"Security scan rejected executable {location}: {reason}")
    if decision not in {"allow", "warn"}:
        raise SkillSecurityScanError(f"Security scan failed for {location}: invalid scanner decision {decision!r}")




def _collect_scannable_files(skill_dir: Path) -> list[Path]:
    """Enumerate archive files for scanning (blocking; run off the event loop)."""
    return [candidate for candidate in sorted(skill_dir.rglob("*")) if candidate.is_file()]
# yyds: 遍历技能目录中的所有文本和脚本文件，逐一执行安全扫描


async def _scan_skill_archive_contents_or_raise(skill_dir: Path, skill_name: str) -> None:
    """遍历技能目录，对需要扫描的文件逐一安全审查。

    扫描顺序：
      1. SKILL.md（必须先扫，如果它有问题直接拒绝整个技能）
      2. 其他 scripts/ 和 references/templates/ 下的文件
    嵌套 SKILL.md 直接拒绝（防目录逃逸攻击）。
    """
    skill_md = skill_dir / "SKILL.md"
    await _scan_skill_file_or_raise(skill_dir, skill_md, skill_name, executable=False)

    for path in await asyncio.to_thread(_collect_scannable_files, skill_dir):
        rel_path = path.relative_to(skill_dir)
        if rel_path == Path("SKILL.md"):
            continue
        if path.name == "SKILL.md":
            raise SkillSecurityScanError(f"Security scan failed for skill '{skill_name}': nested SKILL.md is not allowed at {skill_name}/{rel_path.as_posix()}")
        if not _should_scan_support_file(rel_path):
            continue

        await _scan_skill_file_or_raise(skill_dir, path, skill_name, executable=_is_script_support_file(rel_path))


def _run_async_install(coro):
    """在同步上下文中运行异步安装协程。

    什么时候需要这个？
      Gateway 的 HTTP handler 是同步的（FastAPI 普通路由），
      但安全扫描是异步的（要调 LLM）。
      如果当前已有事件循环（在 async 函数中被调用），
      就新开一个线程跑 asyncio.run；
      如果没有事件循环，直接 asyncio.run。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(asyncio.run, coro).result()
    return asyncio.run(coro)
