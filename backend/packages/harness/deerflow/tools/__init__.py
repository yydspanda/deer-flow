# yyds: 工具模块公共接口，导出工具注册/发现功能，skill_manage_tool通过懒加载避免循环依赖
"""工具系统代码结构：
tools/
├── tools.py                  ★ 核心：get_available_tools() 四源合并 + 去重
├── types.py                  数据类型定义（Runtime 等）
├── sync.py                   异步→同步 wrapper（make_sync_tool_wrapper）
├── skill_manage_tool.py      技能管理工具（条件加载）
├── builtins/                 内置工具
│   ├── present_file_tool.py   展示文件内容给用户
│   ├── clarification_tool.py  追问用户（不确定意图时）
│   ├── view_image_tool.py     查看图片（条件加载，需 vision 模型）
│   ├── task_tool.py           Sub-Agent 委派（条件加载，需 Ultra 模式）
│   ├── tool_search.py         延迟工具搜索（条件加载，需 tool_search 配置）
│   ├── setup_agent_tool.py    Agent 初始化设置
│   ├── update_agent_tool.py   Agent 更新
│   └── invoke_acp_agent_tool.py  ACP 外部 Agent 调用
└── __init__.py
建议阅读顺序
先看骨架（理解全局）：
顺序	文件	理由
1	types.py	最小文件，定义工具用的数据类型，先搞清楚 Runtime 是什么
2	tools.py	核心入口，四源合并逻辑、去重、条件加载全在这里
3	sync.py	异步→同步桥接，理解为什么需要它
再看内置工具（按重要性）：
顺序	文件	理由
4	builtins/present_file_tool.py	最简单的内置工具，先看懂工具长什么样
5	builtins/clarification_tool.py	第二简单，和 ClarificationMiddleware 配合
6	builtins/task_tool.py	最复杂，Sub-Agent 委派的核心，200+ 行
7	builtins/view_image_tool.py	简单，但涉及条件加载逻辑
8	builtins/tool_search.py	延迟加载机制，和 MCP 有交集
最后看可选的（扫一眼即可）：
顺序	文件	理由
9	skill_manage_tool.py	技能 CRUD，独立模块
10	builtins/setup_agent_tool.py + update_agent_tool.py	Agent 管理，简单
11	builtins/invoke_acp_agent_tool.py	ACP 协议，除非你关心外部 Agent 集成
关键点：tools.py 是必须吃透的，它把配置、反射、MCP、沙箱安全全串起来了。看完它你就理解了工具系统的 80%。"""

from .tools import get_available_tools

__all__ = ["get_available_tools", "skill_manage_tool"]


def __getattr__(name: str):
    if name == "skill_manage_tool":
        from .skill_manage_tool import skill_manage_tool

        return skill_manage_tool
    raise AttributeError(name)
