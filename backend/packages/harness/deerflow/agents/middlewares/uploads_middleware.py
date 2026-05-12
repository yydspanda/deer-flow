"""yyds: 文件上传中间件 — 处理用户上传的文件，将文件列表和结构大纲注入到对话上下文中。

【做什么】在 Agent 执行前，检测用户消息中附带的文件上传信息，扫描上传目录中的历史文件，
   生成格式化的文件列表（包含文件名、大小、路径、文档大纲），并注入到最后一条用户消息中。
【为什么存在】模型需要知道有哪些文件可用才能正确回答问题。此中间件将文件信息以结构化方式呈现，
   包括文档大纲（标题+行号），让模型能精准定位到需要阅读的章节。
【在链中的位置】before_agent 阶段执行，在 Agent 开始处理前注入文件上下文。
【关键设计】
   - 区分"新上传文件"（来自当前消息的 additional_kwargs.files）和"历史文件"（扫描 uploads 目录）。
   - 对 Markdown 转换后的文件提取文档大纲（heading → line number），帮助模型用 read_file 精确定位。
   - 无大纲时退化为读取文件前5行作为预览（outline_preview）。
   - 支持多模态消息格式：纯文本内容直接拼接，列表内容（含图片等）在头部插入文本块。
   - 文件路径使用沙箱虚拟路径 /mnt/user-data/uploads/，与实际物理路径隔离。
   - 保留原始 additional_kwargs（含 files 元数据），前端可从流式消息中读取结构化文件信息。
"""

import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langchain_core.runnables import run_in_executor
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.file_conversion import extract_outline
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY, message_content_to_text

logger = logging.getLogger(__name__)


_OUTLINE_PREVIEW_LINES = 5  # yyds: 无大纲时，读取文件前 5 行作为内容预览


def _extract_outline_for_file(file_path: Path) -> tuple[list[dict], list[str]]:
    """yyds: 为单个文件提取文档大纲（标题+行号）或前几行预览。

    上传管道会把 PDF/Word 等文件转换成 Markdown（同名 .md 文件）。
    这个函数读取那个 .md 文件，提取 heading 结构作为大纲。

    返回值: (outline, preview)
      - 有大纲时: (outline 列表, []) — outline 是 [{"title": "...", "line": 42}] 格式
      - 无大纲时: ([], preview 列表) — preview 是文件前 5 行非空内容
      - 没有 .md 文件: ([], []) — 该文件不是文档类型（如图片）
    """
    md_path = file_path.with_suffix(".md")
    if not md_path.is_file():
        return [], []

    outline = extract_outline(md_path)
    if outline:
        logger.debug("Extracted %d outline entries from %s", len(outline), file_path.name)
        return outline, []

    # outline is empty — read the first few non-empty lines as a content preview
    preview: list[str] = []
    try:
        with md_path.open(encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    preview.append(stripped)
                if len(preview) >= _OUTLINE_PREVIEW_LINES:
                    break
    except Exception:
        logger.debug("Failed to read preview lines from %s", md_path, exc_info=True)
    return [], preview


class UploadsMiddlewareState(AgentState):
    """yyds: Uploads 中间件的状态扩展 — 在 AgentState 基础上加了 uploaded_files 字段。
    uploaded_files 存储本次消息上传的文件列表，供其他中间件（如 Memory）读取。
    """

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """yyds: 文件上传中间件 — 把用户上传的文件信息注入到对话上下文中。

    执行时机：before_agent（Agent 开始处理前）
    做两件事：
      1. 从当前消息的 additional_kwargs.files 取"新上传文件"
      2. 扫描 uploads 目录取"历史文件"（之前上传的还在的）
    然后生成格式化的文件列表（含文件名、大小、路径、文档大纲），
    插入到最后一条 HumanMessage.content 的前面，让模型知道有哪些文件可用。

    为什么插到 content 里而不是注入新消息？
      因为 AIMessage(tool_calls) 和 ToolMessage 有严格配对关系，
      额外插入消息会破坏配对。修改现有消息的 content 最安全。
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """yyds: 初始化 — 获取路径管理器。base_dir 参数主要用于测试隔离。"""
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _format_file_entry(self, file: dict, lines: list[str]) -> None:
        """yyds: 格式化单个文件条目 — 生成 "- filename (size) / Path: ... / outline..." 格式。
        大纲格式：L{行号}: {标题}，模型看到后可以用 read_file(start_line=N) 精准定位。
        无大纲时退化：显示前 5 行内容预览 + 提示用 grep 搜索。
        """
        size_kb = file["size"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        lines.append(f"- {file['filename']} ({size_str})")
        lines.append(f"  Path: {file['path']}")
        outline = file.get("outline") or []
        if outline:
            truncated = outline[-1].get("truncated", False)
            visible = [e for e in outline if not e.get("truncated")]
            lines.append("  Document outline (use `read_file` with line ranges to read sections):")
            for entry in visible:
                lines.append(f"    L{entry['line']}: {entry['title']}")
            if truncated:
                lines.append(f"    ... (showing first {len(visible)} headings; use `read_file` to explore further)")
        else:
            preview = file.get("outline_preview") or []
            if preview:
                lines.append("  No structural headings detected. Document begins with:")
                for text in preview:
                    lines.append(f"    > {text}")
            lines.append("  Use `grep` to search for keywords (e.g. `grep(pattern='keyword', path='/mnt/user-data/uploads/')`).")
        lines.append("")

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """yyds: 拼装完整的 <uploaded_files> XML 块 — 分两段：本次上传 + 历史文件。
        末尾附带使用指引：先用 read_file 按大纲读，不确定用 grep 搜，最后才考虑 web search。
        """
        lines = ["<uploaded_files>"]

        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                self._format_file_entry(file, lines)
        else:
            lines.append("(empty)")
            lines.append("")

        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                self._format_file_entry(file, lines)

        lines.append("To work with these files:")
        lines.append("- Read from the file first — use the outline line numbers and `read_file` to locate relevant sections.")
        lines.append("- Use `grep` to search for keywords when you are not sure which section to look at")
        lines.append("  (e.g. `grep(pattern='revenue', path='/mnt/user-data/uploads/')`).")
        lines.append("- Use `glob` to find files by name pattern")
        lines.append("  (e.g. `glob(pattern='**/*.md', path='/mnt/user-data/uploads/')`).")
        lines.append("- Only fall back to web search if the file content is clearly insufficient to answer the question.")
        lines.append("</uploaded_files>")

        return "\n".join(lines)

    def _files_from_kwargs(self, message: HumanMessage, uploads_dir: Path | None = None) -> list[dict] | None:
        """yyds: 从 HumanMessage.additional_kwargs.files 提取文件信息。

        前端上传文件后，把元数据放在 additional_kwargs.files 里：
          [{"filename": "report.pdf", "size": 12345, "path": "...", "status": "ok"}, ...]

        这个函数做安全过滤：
          - 只取 filename 是纯文件名的（拒绝路径遍历，如 "../../etc/passwd"）
          - 如果传了 uploads_dir，还会检查文件是否真的存在于磁盘
          - size 强制转 int（防止注入）
          - 路径一律转换为虚拟路径 /mnt/user-data/uploads/{filename}
        """
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            if not filename or Path(filename).name != filename:
                continue
            if uploads_dir is not None and not (uploads_dir / filename).is_file():
                continue
            files.append(
                {
                    "filename": filename,
                    "size": int(f.get("size") or 0),
                    "path": f"/mnt/user-data/uploads/{filename}",
                    "extension": Path(filename).suffix,
                }
            )
        return files if files else None

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 主入口 — 在 Agent 执行前注入文件上下文。

        执行流程：
          1. 取最后一条消息，只处理 HumanMessage
          2. 从 runtime.context 拿 thread_id → 定位 uploads 目录
          3. 从 additional_kwargs.files 取"新上传文件" → 做安全过滤 + 文件存在性检查
          4. 扫描 uploads 目录取"历史文件" → 提取大纲/预览
          5. 给新文件也附加大纲信息
          6. 拼装 <uploaded_files> 消息 → 插入到 HumanMessage.content 前面
          7. 返回更新后的 messages + uploaded_files

        关键：保留原始 additional_kwargs（含 files 元数据），
        前端可以从流式消息中读取结构化文件信息来渲染 UI。
        """
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # Resolve uploads directory for existence checks
        thread_id = (runtime.context or {}).get("thread_id")
        if thread_id is None:
            try:
                from langgraph.config import get_config

                thread_id = get_config().get("configurable", {}).get("thread_id")
            except RuntimeError:
                pass  # get_config() raises outside a runnable context (e.g. unit tests)
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id, user_id=get_effective_user_id()) if thread_id else None

        # Get newly uploaded files from the current message's additional_kwargs.files
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # Collect historical files from the uploads directory (all except the new ones)
        new_filenames = {f["filename"] for f in new_files}
        historical_files: list[dict] = []
        if uploads_dir and uploads_dir.exists():
            for file_path in sorted(uploads_dir.iterdir()):
                if file_path.is_file() and file_path.name not in new_filenames:
                    stat = file_path.stat()
                    outline, preview = _extract_outline_for_file(file_path)
                    historical_files.append(
                        {
                            "filename": file_path.name,
                            "size": stat.st_size,
                            "path": f"/mnt/user-data/uploads/{file_path.name}",
                            "extension": file_path.suffix,
                            "outline": outline,
                            "outline_preview": preview,
                        }
                    )

        # Attach outlines to new files as well
        if uploads_dir:
            for file in new_files:
                phys_path = uploads_dir / file["filename"]
                outline, preview = _extract_outline_for_file(phys_path)
                file["outline"] = outline
                file["outline_preview"] = preview

        if not new_files and not historical_files:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # Create files message and prepend to the last human message content
        files_message = self._create_files_message(new_files, historical_files)

        # Extract original content - handle both string and list formats
        original_content = last_message.content
        additional_kwargs = dict(last_message.additional_kwargs or {})
        additional_kwargs.setdefault(ORIGINAL_USER_CONTENT_KEY, message_content_to_text(original_content))
        if isinstance(original_content, str):
            # Simple case: string content, just prepend files message
            updated_content = f"{files_message}\n\n{original_content}"
        elif isinstance(original_content, list):
            # Complex case: list content (multimodal), preserve all blocks
            # Prepend files message as the first text block
            files_block = {"type": "text", "text": f"{files_message}\n\n"}
            # Keep all original blocks (including images)
            updated_content = [files_block, *original_content]
        else:
            # Other types, preserve as-is
            updated_content = original_content

        # Create new message with combined content.
        # Preserve additional_kwargs (including files metadata) so the frontend
        # can read structured file info from the streamed message.
        updated_message = HumanMessage(
            content=updated_content,
            id=last_message.id,
            name=last_message.name,
            additional_kwargs=additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }

    @override
    async def abefore_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """Async hook that offloads the synchronous uploads scan off the event loop.

        ``before_agent`` performs blocking filesystem IO (directory enumeration,
        ``stat``, reading sibling ``.md`` outlines). When the graph runs async,
        langgraph would otherwise execute the sync hook directly on the event
        loop, so it is dispatched to a worker thread via ``run_in_executor``.
        ``run_in_executor`` copies the current context, so the ``user_id``
        contextvar read by ``get_effective_user_id()`` is preserved.
        """
        return await run_in_executor(None, self.before_agent, state, runtime)
