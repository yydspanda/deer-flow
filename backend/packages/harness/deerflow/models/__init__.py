"""yyds: 模型工厂模块 — 根据配置名称和思维模式，创建对应的 ChatModel 实例。

代码结构：
models/
├── __init__.py                模块入口，导出 create_chat_model
├── factory.py                 ★★★ 核心工厂：配置解析 + 思维模式切换 + 实例化
├── credential_loader.py       ★★ 凭证加载：API Key 从环境变量/文件/OAuth 获取
├── patched_openai.py          OpenAI 兼容模型补丁（Doubao/豆包等）
├── patched_deepseek.py        DeepSeek 模型补丁
├── patched_minimax.py         MiniMax 模型补丁
├── claude_provider.py         Claude 模型适配（Extended Thinking）
├── openai_codex_provider.py   OpenAI Codex Responses API 适配
├── vllm_provider.py           vLLM 本地部署模型适配
└── mindie_provider.py         华为昇腾 MindIE 适配

建议阅读顺序：
  1. factory.py           — 核心，搞清楚"给个名字，怎么变成一个模型实例"
  2. credential_loader.py — API Key 怎么来，生产环境必看
  3. patched_openai.py    — 最常见的适配器模式
  4. 其余 provider        — 按需看，都是类似的适配器模式
"""

from .factory import create_chat_model

__all__ = ["create_chat_model"]
