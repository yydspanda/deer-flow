"""yyds: Memory Prompt 模板 + 格式化函数。

【大白话讲清楚】
  这个文件是 memory 系统的"翻译层"。它解决两个翻译问题：

  ① memory → system prompt：怎么把 memory.json 的数据"翻译"成 Agent 能看懂的上下文？
     format_memory_for_injection() 按 confidence 排序取 top facts，控制在 max_tokens 内，
     输出 "User Context / History / Facts" 三段文本，注入到 system prompt 的 <memory> 标签里。

  ② conversation → LLM prompt：怎么把对话"翻译"成给 LLM 的更新指令？
     format_conversation_for_update() 过滤掉工具调用、文件上传，只保留用户输入和 AI 回复，
     截断长消息（>1000 字符），输出 "User: ... Assistant: ..." 格式文本。

  两个 prompt 模板：
    MEMORY_UPDATE_PROMPT: 全量更新用的系统 prompt（对话 → LLM → JSON 更新指令）
    FACT_EXTRACTION_PROMPT: 单条消息提取 fact（当前代码未使用，预留给未来功能）

【具体例子】
  format_memory_for_injection 例子：
    memory.json 里有 30 条 facts（confidence 从 0.99 到 0.3）
    max_tokens = 2000

    ① User Context 段：
      - Work: AI Agent 开发工程师，专注企业级 Agent 框架
      - Personal: 中英双语，偏好中文交流
      - Current Focus: 正在学习 DeerFlow 源码，计划构建通用 Agent 框架

    ② History 段：
      - Recent: 最近在学习 LangGraph 多 Agent 架构...
      - Earlier: 之前有 AI 安全背景...
      - Background: 长期关注 AI 应用层...

    ③ Facts 段（按 confidence 排序，top N 塞满 2000 tokens）：
      - [preference | 0.95] 偏好中文回复
      - [knowledge | 0.90] 技术栈：Python + LangGraph
      - [context | 0.85] 项目名：deer-flow
      ... （一直加到 token 预算用完）

  format_conversation_for_update 例子：
    原始消息 [HumanMsg, AIMsg(tool_calls=[...]), ToolMsg, AIMsg, HumanMsg, AIMsg]
    过滤后只保留 [HumanMsg, AIMsg, HumanMsg, AIMsg]
    输出：
      "User: 我在做一个 AI Agent 框架

       Assistant: 好的，你打算用什么技术栈？

       User: LangGraph

       Assistant: LangGraph 是个好选择..."

---
Prompt templates and formatting helpers for memory update and injection.
"""

import math
import re
from typing import Any

try:
    import tiktoken

    TIKTOKEN_AVAILABLE = True
except ImportError:
    TIKTOKEN_AVAILABLE = False

MEMORY_UPDATE_PROMPT = """You are a memory management system. Your task is to analyze a conversation and update the user's memory profile.

Current Memory State:
<current_memory>
{current_memory}
</current_memory>

New Conversation to Process:
<conversation>
{conversation}
</conversation>

Instructions:
1. Analyze the conversation for important information about the user
2. Extract relevant facts, preferences, and context with specific details (numbers, names, technologies)
3. Update the memory sections as needed following the detailed length guidelines below

Before extracting facts, perform a structured reflection on the conversation:
1. Error/Retry Detection: Did the agent encounter errors, require retries, or produce incorrect results?
   If yes, record the root cause and correct approach as a high-confidence fact with category "correction".
2. User Correction Detection: Did the user correct the agent's direction, understanding, or output?
   If yes, record the correct interpretation or approach as a high-confidence fact with category "correction".
   Include what went wrong in "sourceError" only when category is "correction" and the mistake is explicit in the conversation.
3. Project Constraint Discovery: Were any project-specific constraints discovered during the conversation?
   If yes, record them as facts with the most appropriate category and confidence.

{correction_hint}

Memory Section Guidelines:

**User Context** (Current state - concise summaries):
- workContext: Professional role, company, key projects, main technologies (2-3 sentences)
  Example: Core contributor, project names with metrics (16k+ stars), technical stack
- personalContext: Languages, communication preferences, key interests (1-2 sentences)
  Example: Bilingual capabilities, specific interest areas, expertise domains
- topOfMind: Multiple ongoing focus areas and priorities (3-5 sentences, detailed paragraph)
  Example: Primary project work, parallel technical investigations, ongoing learning/tracking
  Include: Active implementation work, troubleshooting issues, market/research interests
  Note: This captures SEVERAL concurrent focus areas, not just one task

**History** (Temporal context - rich paragraphs):
- recentMonths: Detailed summary of recent activities (4-6 sentences or 1-2 paragraphs)
  Timeline: Last 1-3 months of interactions
  Include: Technologies explored, projects worked on, problems solved, interests demonstrated
- earlierContext: Important historical patterns (3-5 sentences or 1 paragraph)
  Timeline: 3-12 months ago
  Include: Past projects, learning journeys, established patterns
- longTermBackground: Persistent background and foundational context (2-4 sentences)
  Timeline: Overall/foundational information
  Include: Core expertise, longstanding interests, fundamental working style

**Facts Extraction**:
- Extract specific, quantifiable details (e.g., "16k+ GitHub stars", "200+ datasets")
- Include proper nouns (company names, project names, technology names)
- Preserve technical terminology and version numbers
- Categories:
  * preference: Tools, styles, approaches user prefers/dislikes
  * knowledge: Specific expertise, technologies mastered, domain knowledge
  * context: Background facts (job title, projects, locations, languages)
  * behavior: Working patterns, communication habits, problem-solving approaches
  * goal: Stated objectives, learning targets, project ambitions
  * correction: Explicit agent mistakes or user corrections, including the correct approach
- Confidence levels:
  * 0.9-1.0: Explicitly stated facts ("I work on X", "My role is Y")
  * 0.7-0.8: Strongly implied from actions/discussions
  * 0.5-0.6: Inferred patterns (use sparingly, only for clear patterns)

**What Goes Where**:
- workContext: Current job, active projects, primary tech stack
- personalContext: Languages, personality, interests outside direct work tasks
- topOfMind: Multiple ongoing priorities and focus areas user cares about recently (gets updated most frequently)
  Should capture 3-5 concurrent themes: main work, side explorations, learning/tracking interests
- recentMonths: Detailed account of recent technical explorations and work
- earlierContext: Patterns from slightly older interactions still relevant
- longTermBackground: Unchanging foundational facts about the user

**Multilingual Content**:
- Preserve original language for proper nouns and company names
- Keep technical terms in their original form (DeepSeek, LangGraph, etc.)
- Note language capabilities in personalContext

Output Format (JSON):
{{
  "user": {{
    "workContext": {{ "summary": "...", "shouldUpdate": true/false }},
    "personalContext": {{ "summary": "...", "shouldUpdate": true/false }},
    "topOfMind": {{ "summary": "...", "shouldUpdate": true/false }}
  }},
  "history": {{
    "recentMonths": {{ "summary": "...", "shouldUpdate": true/false }},
    "earlierContext": {{ "summary": "...", "shouldUpdate": true/false }},
    "longTermBackground": {{ "summary": "...", "shouldUpdate": true/false }}
  }},
  "newFacts": [
    {{ "content": "...", "category": "preference|knowledge|context|behavior|goal|correction", "confidence": 0.0-1.0 }}
  ],
  "factsToRemove": ["fact_id_1", "fact_id_2"]
}}

Important Rules:
- Only set shouldUpdate=true if there's meaningful new information
- Follow length guidelines: workContext/personalContext are concise (1-3 sentences), topOfMind and history sections are detailed (paragraphs)
- Include specific metrics, version numbers, and proper nouns in facts
- Only add facts that are clearly stated (0.9+) or strongly implied (0.7+)
- Use category "correction" for explicit agent mistakes or user corrections; assign confidence >= 0.95 when the correction is explicit
- Include "sourceError" only for explicit correction facts when the prior mistake or wrong approach is clearly stated; omit it otherwise
- Remove facts that are contradicted by new information
- When updating topOfMind, integrate new focus areas while removing completed/abandoned ones
  Keep 3-5 concurrent focus themes that are still active and relevant
- For history sections, integrate new information chronologically into appropriate time period
- Preserve technical accuracy - keep exact names of technologies, companies, projects
- Focus on information useful for future interactions and personalization
- IMPORTANT: Do NOT record file upload events in memory. Uploaded files are
  session-specific and ephemeral — they will not be accessible in future sessions.
  Recording upload events causes confusion in subsequent conversations.

Return ONLY valid JSON, no explanation or markdown."""


FACT_EXTRACTION_PROMPT = """Extract factual information about the user from this message.

Message:
{message}

Extract facts in this JSON format:
{{
  "facts": [
    {{ "content": "...", "category": "preference|knowledge|context|behavior|goal|correction", "confidence": 0.0-1.0 }}
  ]
}}

Categories:
- preference: User preferences (likes/dislikes, styles, tools)
- knowledge: User's expertise or knowledge areas
- context: Background context (location, job, projects)
- behavior: Behavioral patterns
- goal: User's goals or objectives
- correction: Explicit corrections or mistakes to avoid repeating

Rules:
- Only extract clear, specific facts
- Confidence should reflect certainty (explicit statement = 0.9+, implied = 0.6-0.8)
- Skip vague or temporary information

Return ONLY valid JSON."""


def _count_tokens(text: str, encoding_name: str = "cl100k_base") -> int:
    """yyds: token 计数 — tiktoken 精确计算，不可用时 len/4 估算。

    cl100k_base 是 GPT-4/Claude 通用的 tokenizer。
    fallback 用 len//4 是经验值（1 token ≈ 4 字符）。

    为什么需要精确计数？
      format_memory_for_injection 要把 memory 塞进 system prompt，
      system prompt 有 token 上限（比如 4000），超出会被截断。
      精确计数确保 facts 列表尽可能多塞，但不超限。
    """
    if not TIKTOKEN_AVAILABLE:
        return len(text) // 4

    try:
        encoding = tiktoken.get_encoding(encoding_name)
        return len(encoding.encode(text))
    except Exception:
        return len(text) // 4


def _coerce_confidence(value: Any, default: float = 0.0) -> float:
    """yyds: 安全地转 confidence — 处理 NaN/inf/字符串/None 等异常值。

    LLM 返回的 JSON 里 confidence 可能不是合法 float（比如 "high"、null）。
    这个函数兜底：转失败 → 用 default，NaN/inf → 用 default，然后 clamp 到 [0,1]。

    为什么不直接 float()？
      float("high") → ValueError
      float(None) → TypeError
      float("nan") → nan（nan > 0.5 是 False，排序会出问题）
    """
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return max(0.0, min(1.0, default))
    if not math.isfinite(confidence):
        return max(0.0, min(1.0, default))
    return max(0.0, min(1.0, confidence))


def format_memory_for_injection(memory_data: dict[str, Any], max_tokens: int = 2000) -> str:
    """yyds: memory → system prompt 文本 — 按 confidence 排序取 top facts，控制在 max_tokens 内。

    谁调的？MemoryMiddleware.attach() 里，把格式化后的 memory 注入到 system prompt 的 <memory> 标签。

    三段输出：
      User Context: Work / Personal / Current Focus
      History: Recent / Earlier / Background
      Facts: 按 confidence 降序排列，token 预算用完为止

    token 预算分配策略：
      先把 User Context + History 的文本全部算进去（这是基础），
      然后剩余预算塞 facts，按 confidence 从高到低逐条加，加不下就停。

    例子（max_tokens=2000）：
      User Context + History 占了 500 tokens → 剩 1500 tokens 给 facts
      30 条 facts 按 confidence 排序，前 20 条总共 1400 tokens → 全塞
      第 21 条 120 tokens → 1400+120=1520 < 1500 → 塞
      第 22 条 130 tokens → 1520+130=1650 > 1500 → 停止
      最终注入 22 条 facts
    """
    if not memory_data:
        return ""

    sections = []

    user_data = memory_data.get("user", {})
    if user_data:
        user_sections = []

        work_ctx = user_data.get("workContext", {})
        if work_ctx.get("summary"):
            user_sections.append(f"Work: {work_ctx['summary']}")

        personal_ctx = user_data.get("personalContext", {})
        if personal_ctx.get("summary"):
            user_sections.append(f"Personal: {personal_ctx['summary']}")

        top_of_mind = user_data.get("topOfMind", {})
        if top_of_mind.get("summary"):
            user_sections.append(f"Current Focus: {top_of_mind['summary']}")

        if user_sections:
            sections.append("User Context:\n" + "\n".join(f"- {s}" for s in user_sections))

    history_data = memory_data.get("history", {})
    if history_data:
        history_sections = []

        recent = history_data.get("recentMonths", {})
        if recent.get("summary"):
            history_sections.append(f"Recent: {recent['summary']}")

        earlier = history_data.get("earlierContext", {})
        if earlier.get("summary"):
            history_sections.append(f"Earlier: {earlier['summary']}")

        background = history_data.get("longTermBackground", {})
        if background.get("summary"):
            history_sections.append(f"Background: {background['summary']}")

        if history_sections:
            sections.append("History:\n" + "\n".join(f"- {s}" for s in history_sections))

    facts_data = memory_data.get("facts", [])
    if isinstance(facts_data, list) and facts_data:
        ranked_facts = sorted(
            (f for f in facts_data if isinstance(f, dict) and isinstance(f.get("content"), str) and f.get("content").strip()),
            key=lambda fact: _coerce_confidence(fact.get("confidence"), default=0.0),
            reverse=True,
        )

        base_text = "\n\n".join(sections)
        base_tokens = _count_tokens(base_text) if base_text else 0
        facts_header = "Facts:\n"
        separator_tokens = _count_tokens("\n\n" + facts_header) if base_text else _count_tokens(facts_header)
        running_tokens = base_tokens + separator_tokens

        fact_lines: list[str] = []
        for fact in ranked_facts:
            content_value = fact.get("content")
            if not isinstance(content_value, str):
                continue
            content = content_value.strip()
            if not content:
                continue
            category = str(fact.get("category", "context")).strip() or "context"
            confidence = _coerce_confidence(fact.get("confidence"), default=0.0)
            source_error = fact.get("sourceError")
            if category == "correction" and isinstance(source_error, str) and source_error.strip():
                line = f"- [{category} | {confidence:.2f}] {content} (avoid: {source_error.strip()})"
            else:
                line = f"- [{category} | {confidence:.2f}] {content}"

            line_text = ("\n" + line) if fact_lines else line
            line_tokens = _count_tokens(line_text)

            if running_tokens + line_tokens <= max_tokens:
                fact_lines.append(line)
                running_tokens += line_tokens
            else:
                break

        if fact_lines:
            sections.append("Facts:\n" + "\n".join(fact_lines))

    if not sections:
        return ""

    result = "\n\n".join(sections)

    token_count = _count_tokens(result)
    if token_count > max_tokens:
        char_per_token = len(result) / token_count
        target_chars = int(max_tokens * char_per_token * 0.95)
        result = result[:target_chars] + "\n..."

    return result


def format_conversation_for_update(messages: list[Any]) -> str:
    """yyds: 对话消息 → 给 LLM 的文本 — 只保留 human/ai，过滤上传，截断长消息。

    谁调的？updater._prepare_update_prompt() 构造 MEMORY_UPDATE_PROMPT 时。

    过滤规则：
      - human 消息：保留，但去掉 <uploaded_files> 标签（文件路径是临时的，不进 memory）
      - ai 消息：保留，但如果有 tool_calls 就跳过（工具调用的中间过程不进 memory）
      - 其他（tool/system）：全部跳过

    截断规则：
      单条消息超过 1000 字符 → 截断到 1000 + "..."

    例子：
      输入 [HumanMsg("你好"), AIMsg(tool_calls=[...]), ToolMsg("result"), AIMsg("你好！"), HumanMsg("帮我写代码")]
      输出 "User: 你好\n\nAssistant: 你好！\n\nUser: 帮我写代码"
      （tool_calls 的 AIMsg 和 ToolMsg 被过滤掉了）
    """
    lines = []
    for msg in messages:
        role = getattr(msg, "type", "unknown")
        content = getattr(msg, "content", str(msg))

        if isinstance(content, list):
            text_parts = []
            for p in content:
                if isinstance(p, str):
                    text_parts.append(p)
                elif isinstance(p, dict):
                    text_val = p.get("text")
                    if isinstance(text_val, str):
                        text_parts.append(text_val)
            content = " ".join(text_parts) if text_parts else str(content)

        if role == "human":
            content = re.sub(r"<uploaded_files>[\s\S]*?</uploaded_files>\n*", "", str(content)).strip()
            if not content:
                continue

        if len(str(content)) > 1000:
            content = str(content)[:1000] + "..."

        if role == "human":
            lines.append(f"User: {content}")
        elif role == "ai":
            lines.append(f"Assistant: {content}")

    return "\n\n".join(lines)
