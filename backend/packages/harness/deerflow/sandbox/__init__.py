"""yyds: 沙箱模块 — Agent 的"手脚"，负责所有文件操作和命令执行。

sandbox/
├── sandbox.py                 ★ 抽象基类：定义 7 个操作（execute/read/write/glob/grep/...）
├── sandbox_provider.py        工厂模式 + 单例：SandboxProvider（acquire/get/release）
├── middleware.py               沙箱生命周期中间件（before_agent acquire → after_agent release）
├── tools.py                   ★★★ 7 个 @tool：bash/ls/glob/grep/read_file/write_file/str_replace
│                              1600+ 行，含完整路径安全验证 + 虚拟路径翻译
├── security.py                安全开关：local 模式默认禁 bash，Docker 模式默认允许
├── exceptions.py              异常体系（SandboxError 子类树）
├── file_operation_lock.py     并发锁（str_replace 防多线程写同一文件）
├── search.py                  文件搜索引擎（glob + grep 底层实现）
└── local/                     本地沙箱实现（dev 模式，宿主机执行）
    ├── local_sandbox.py        LocalSandbox（subprocess 执行 + 路径映射）
    ├── local_sandbox_provider.py LocalSandboxProvider（单例复用 + 路径初始化）
    └── list_dir.py             目录列表格式化（树形输出）

另一种实现在 community/aio_sandbox/（Docker 容器隔离，生产模式）

建议阅读顺序：
  先看骨架（理解抽象层）：
  顺序  文件                       理由
  1     sandbox.py                 抽象基类，7 个方法定义了沙箱能做什么
  2     sandbox_provider.py        工厂 + 单例，理解 acquire/get/release 生命周期
  3     security.py                最小文件，理解 local vs Docker 的安全边界
  4     middleware.py               理解沙箱什么时候创建、什么时候释放

  再看核心（★ 这是本模块最重要的部分）：
  5     tools.py ★★★               1600 行，7 个工具 + 路径安全 + 虚拟路径翻译
                                  必须吃透！这是 Agent 能力的基础

  最后看实现（扫一眼即可）：
  6     local/local_sandbox.py      subprocess 执行 + 路径映射的具体实现
  7     search.py                   glob/grep 的文件遍历实现
  8     exceptions.py               异常类，扫一眼层次结构即可
  9     file_operation_lock.py      31 行，WeakValueDictionary 锁

关键点：tools.py 占了这个模块 70% 的复杂度。它要解决的核心问题是：
  local 模式下，Agent 看到的是虚拟路径（/mnt/user-data/workspace），
  实际执行的是真实路径（/home/user/.deer-flow/threads/abc/workspace）。
  所有安全验证、路径翻译、输出脱敏都围绕这个虚拟↔真实映射展开。
"""

from .sandbox import Sandbox
from .sandbox_provider import SandboxProvider, get_sandbox_provider

__all__ = [
    "Sandbox",
    "SandboxProvider",
    "get_sandbox_provider",
]
