"""yyds: 文件上传中间件 — 用户上传了文件后，把文件列表和大纲塞进对话，让 Agent 知道有哪些文件可用。

【做什么】Agent 执行前，检测用户是否上传了文件。如果有，生成一份"文件清单"（文件名、大小、路径、
   文档大纲），插入到用户消息前面，让 Agent 知道有哪些文件可以读。
【为什么存在】用户上传了一个 PDF 问"第三章讲了什么"，Agent 需要知道：
   1. 有哪些文件（report.pdf，2.3 MB）
   2. 文件的结构大纲（第一章...第二章...第三章...）
   3. 怎么读文件（用 read_file 从第 42 行开始读）
   没有这些信息，Agent 不知道文件的存在，更不知道从哪读起。
【在链中的位置】before_agent 阶段（Agent 执行前），仅 lead agent 有此中间件。
【关键设计】
   - 两种文件来源：新上传（从消息的 additional_kwargs.files 取）+ 历史文件（扫描 uploads 目录）
   - 文档大纲：上传管道会把 PDF/Word 转成 Markdown，本中间件提取 heading 结构
     （如 "第三章：结果分析 L42"），Agent 可以用 read_file(start_line=42) 精准定位
   - 无大纲时退化：读文件前 5 行作为预览，提示用 grep 搜索
   - 文件清单以 <uploaded_files> XML 块插入用户消息 content 前面
   - 路径用虚拟路径 /mnt/user-data/uploads/（沙箱内路径，不暴露真实磁盘路径）
   - 安全过滤：拒绝路径遍历（如 "../../etc/passwd"）、检查文件真实存在

用户看到的效果：
  用户上传 report.pdf，输入"第三章讲了什么"
    ↓
  Agent 收到的消息：
    <uploaded_files>
    The following files were uploaded in this message:

    - report.pdf (2.3 MB)
      Path: /mnt/user-data/uploads/report.pdf
      Document outline (use `read_file` with line ranges to read sections):
        L1: 第一章：引言
        L15: 第二章：方法
        L42: 第三章：结果分析
        L78: 第四章：讨论
        L120: 参考文献

    To work with these files:
    - Read from the file first — use the outline line numbers and `read_file`...
    </uploaded_files>

    第三章讲了什么？

  Agent 看到 L42 是"第三章"，调 read_file(path, start_line=42) → 读到内容 → 回答用户
"""

import logging
from pathlib import Path
from typing import NotRequired, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.file_conversion import extract_outline

logger = logging.getLogger(__name__)

_OUTLINE_PREVIEW_LINES = 5  # yyds: 无大纲时，读文件前 5 行作为内容预览


def _extract_outline_for_file(file_path: Path) -> tuple[list[dict], list[str]]:
    """yyds: 为单个文件提取文档大纲（heading + 行号）或前几行预览。

    上传管道会把 PDF/Word 等文件转换成同名的 .md 文件。
    这个函数读那个 .md 文件，提取 heading 结构作为大纲。

    yyds 执行顺序：
      ① 找同名 .md 文件（如 report.pdf → report.md）
      ② .md 不存在 → 返回 ([], [])（非文档类型，如图片）
      ③ extract_outline() 提取 heading → 有结果则返回大纲
      ④ 大纲为空 → 读 .md 前 5 行非空内容作为预览

    返回值: (outline, preview)
      有大纲: ([{"title": "第三章", "line": 42}, ...], [])
      无大纲: ([], ["# Title", "Some content...", ...])
      非 .md: ([], [])
    """
    md_path = file_path.with_suffix(".md")
    if not md_path.is_file():
        return [], []

    # yyds: ③ 提取 heading 结构
    outline = extract_outline(md_path)
    if outline:
        logger.debug("Extracted %d outline entries from %s", len(outline), file_path.name)
        return outline, []

    # yyds: ④ 无大纲 → 读前 5 行作为预览
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
    """yyds: Uploads 中间件的状态扩展 — 加了 uploaded_files 字段。

    uploaded_files 存储本次上传的文件列表（含 filename/size/path/outline），
    供其他中间件（如 Memory）和前端读取。
    """

    uploaded_files: NotRequired[list[dict] | None]


class UploadsMiddleware(AgentMiddleware[UploadsMiddlewareState]):
    """yyds: 文件上传中间件 — 把用户上传的文件信息注入到对话里。

    执行时机：before_agent（Agent 执行前），仅 lead agent 有此中间件。
    操作模式：修改最后一条 HumanMessage 的 content，在前面插入 <uploaded_files> 块。

    为什么修改现有消息而不是插入新消息？
      AIMessage(tool_calls) 和 ToolMessage 有严格的配对关系（靠 tool_call_id 关联）。
      额外插入消息可能破坏配对，导致 DanglingToolCallMiddleware 误判。
      修改现有消息的 content 最安全。

    数据流：
      before_agent(state)
        └─ 从最后一条 HumanMessage 取 additional_kwargs.files（新上传文件）
        └─ 从 uploads 目录取历史文件（之前上传的还在磁盘上的）
        └─ 为每个文件提取大纲/预览（_extract_outline_for_file）
        └─ _create_files_message() → <uploaded_files> XML 块
        └─ 插入到 HumanMessage.content 前面
        └─ 返回 {"messages": [...], "uploaded_files": [...]}
    """

    state_schema = UploadsMiddlewareState

    def __init__(self, base_dir: str | None = None):
        """yyds: 初始化 — 获取路径管理器。base_dir 参数主要用于测试隔离。"""
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _format_file_entry(self, file: dict, lines: list[str]) -> None:
        """yyds: 格式化单个文件条目 — 生成 "- filename (size) / Path: ... / outline..." 格式。

        yyds 执行顺序：
          ① 计算文件大小（KB 或 MB）
          ② 写文件名 + 大小行
          ③ 写虚拟路径行（/mnt/user-data/uploads/xxx）
          ④ 有大纲 → 列出 heading + 行号（L42: 第三章）
          ⑤ 无大纲 → 列出前 5 行预览 + 提示用 grep 搜索

        大纲的作用：
          Agent 看到 "L42: 第三章：结果分析" → 调 read_file(start_line=42) 精准定位
          不用大纲的话，Agent 要从第 1 行开始读完整个文件才知道第三章在哪。
        """
        # yyds: ①②③ 文件名 + 大小 + 路径
        size_kb = file["size"] / 1024
        size_str = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        lines.append(f"- {file['filename']} ({size_str})")
        lines.append(f"  Path: {file['path']}")

        # yyds: ④ 有大纲 → 列出 heading + 行号
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
            # yyds: ⑤ 无大纲 → 前 5 行预览 + grep 提示
            preview = file.get("outline_preview") or []
            if preview:
                lines.append("  No structural headings detected. Document begins with:")
                for text in preview:
                    lines.append(f"    > {text}")
            lines.append("  Use `grep` to search for keywords (e.g. `grep(pattern='keyword', path='/mnt/user-data/uploads/')`).")
        lines.append("")

    def _create_files_message(self, new_files: list[dict], historical_files: list[dict]) -> str:
        """yyds: 拼装完整的 <uploaded_files> XML 块 — 分两段：本次上传 + 历史文件。

        yyds 执行顺序：
          ① 开头标签 <uploaded_files>
          ② 列出本次上传的文件（new_files）
          ③ 列出历史文件（historical_files，之前上传的还在磁盘上的）
          ④ 使用指引（read_file → grep → glob → web search 的优先级）
          ⑤ 结束标签 </uploaded_files>

        为什么有使用指引？
          引导 Agent 按最高效的方式工作：
          先用 read_file 按大纲精确定位 → 不确定用 grep 搜索 →
          找文件用 glob → 实在不行才 web search。
          不加指引的话，Agent 可能每次都从头读整个文件，浪费 token。
        """
        lines = ["<uploaded_files>"]

        # yyds: ② 本次上传的文件
        lines.append("The following files were uploaded in this message:")
        lines.append("")
        if new_files:
            for file in new_files:
                self._format_file_entry(file, lines)
        else:
            lines.append("(empty)")
            lines.append("")

        # yyds: ③ 历史文件
        if historical_files:
            lines.append("The following files were uploaded in previous messages and are still available:")
            lines.append("")
            for file in historical_files:
                self._format_file_entry(file, lines)

        # yyds: ④ 使用指引
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
        """yyds: 从 HumanMessage.additional_kwargs.files 提取新上传的文件信息。

        前端上传文件后，把元数据放在 additional_kwargs.files 里：
          [{"filename": "report.pdf", "size": 12345, "path": "...", "status": "ok"}, ...]

        yyds 执行顺序：
          ① 从 additional_kwargs.files 取文件列表，不是 list → 返回 None
          ② 遍历每个文件，安全过滤：
             - 必须是 dict
             - filename 必须是纯文件名（拒绝 "../../etc/passwd" 等路径遍历）
             - 如果传了 uploads_dir，检查文件是否真的存在于磁盘
             - size 强制转 int（防止注入）
          ③ 路径一律转为虚拟路径 /mnt/user-data/uploads/{filename}
          ④ 返回过滤后的列表（空则返回 None）
        """
        # yyds: ① 取文件列表
        kwargs_files = (message.additional_kwargs or {}).get("files")
        if not isinstance(kwargs_files, list) or not kwargs_files:
            return None

        # yyds: ②③ 安全过滤 + 路径转换
        files = []
        for f in kwargs_files:
            if not isinstance(f, dict):
                continue
            filename = f.get("filename") or ""
            # yyds: 路径遍历防护 — filename 必须是纯文件名，不能含 /
            if not filename or Path(filename).name != filename:
                continue
            # yyds: 文件存在性检查（防止前端传了但磁盘上没有）
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
        # yyds: ④ 返回
        return files if files else None

    @override
    def before_agent(self, state: UploadsMiddlewareState, runtime: Runtime) -> dict | None:
        """yyds: 主入口 — 在 Agent 执行前，把文件清单插入用户消息。

        yyds 执行顺序：
          ① 取最后一条消息，不是 HumanMessage → 跳过
          ② 从 runtime.context 拿 thread_id → 定位 uploads 目录
          ③ _files_from_kwargs() 取"新上传文件" → 安全过滤 + 存在性检查
          ④ 扫描 uploads 目录取"历史文件" → 提取大纲/预览
          ⑤ 给新文件也附加大纲信息（_extract_outline_for_file）
          ⑥ 新文件和历史文件都为空 → 跳过（没有文件不需要注入）
          ⑦ _create_files_message() 拼装 <uploaded_files> 块
          ⑧ 插入到 HumanMessage.content 前面（str 直接拼接，list 插入 text block）
          ⑨ 返回更新后的 messages + uploaded_files（保留原始 additional_kwargs 给前端用）
        """
        # yyds: ① 取最后一条消息
        messages = list(state.get("messages", []))
        if not messages:
            return None

        last_message_index = len(messages) - 1
        last_message = messages[last_message_index]

        if not isinstance(last_message, HumanMessage):
            return None

        # yyds: ② 获取 uploads 目录路径
        thread_id = (runtime.context or {}).get("thread_id")
        if thread_id is None:
            try:
                from langgraph.config import get_config

                thread_id = get_config().get("configurable", {}).get("thread_id")
            except RuntimeError:
                pass
        uploads_dir = self._paths.sandbox_uploads_dir(thread_id, user_id=get_effective_user_id()) if thread_id else None

        # yyds: ③ 取新上传文件
        new_files = self._files_from_kwargs(last_message, uploads_dir) or []

        # yyds: ④ 扫描历史文件（uploads 目录中除了新文件以外的所有文件）
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

        # yyds: ⑤ 给新文件附加大纲
        if uploads_dir:
            for file in new_files:
                phys_path = uploads_dir / file["filename"]
                outline, preview = _extract_outline_for_file(phys_path)
                file["outline"] = outline
                file["outline_preview"] = preview

        # yyds: ⑥ 无文件 → 跳过
        if not new_files and not historical_files:
            return None

        logger.debug(f"New files: {[f['filename'] for f in new_files]}, historical: {[f['filename'] for f in historical_files]}")

        # yyds: ⑦ 拼装 <uploaded_files> 块
        files_message = self._create_files_message(new_files, historical_files)

        # yyds: ⑧ 插入到用户消息 content 前面
        original_content = last_message.content
        if isinstance(original_content, str):
            updated_content = f"{files_message}\n\n{original_content}"
        elif isinstance(original_content, list):
            # yyds: 多模态内容（文字 + 图片），在最前面插入 text block
            files_block = {"type": "text", "text": f"{files_message}\n\n"}
            updated_content = [files_block, *original_content]
        else:
            updated_content = original_content

        # yyds: ⑨ 创建新消息（保留原始 id + additional_kwargs）
        updated_message = HumanMessage(
            content=updated_content,
            id=last_message.id,
            name=last_message.name,
            additional_kwargs=last_message.additional_kwargs,
        )

        messages[last_message_index] = updated_message

        return {
            "uploaded_files": new_files,
            "messages": messages,
        }
