# DeerFlow 项目规范化实践总结

> 目的：以 DeerFlow 为范本，提炼出"如何从零搭建一个规范化的全栈项目"。
> 适用场景：你自己开发 Agent/SaaS 项目时，对照这份清单逐项检查。

---

## 一、项目根目录该有哪些文件

一个规范的项目，根目录不是随便堆代码，而是像一张"入职手册"：

```
项目根目录/
├── README.md                    ← 项目介绍（第一眼看到的文件）
├── CONTRIBUTING.md              ← 贡献指南（怎么参与开发）
├── LICENSE                      ← 开源协议
├── .gitignore                   ← Git 忽略规则
├── .gitattributes               ← 统一行尾符（LF vs CRLF）
├── .editorconfig                ← 统一编辑器缩进风格（可选）
├── .pre-commit-config.yaml      ← Git 提交前自动检查
├── .env.example                 ← 环境变量模板（不含真实密钥）
├── config.example.yaml          ← 配置文件模板
├── Makefile                     ← 统一命令入口
├── AGENTS.md                    ← AI 编程助手指令（新时代标配）
└── scripts/                     ← 所有脚本集中管理
    ├── check.py                 ← 依赖检查
    ├── serve.sh                 ← 服务启动
    └── doctor.py                ← 环境诊断
```

### 每个文件的作用和为什么要它

| 文件 | 作用 | 不加会怎样 |
|------|------|-----------|
| `README.md` | 新人看到项目的第一印象 | 别人不知道你这项目干嘛的 |
| `CONTRIBUTING.md` | 告诉协作者怎么开发、怎么提 PR | 每个人风格不统一 |
| `.gitignore` | 防止提交敏感信息和垃圾文件 | `.env`、`__pycache__` 泄露到仓库 |
| `.gitattributes` | 强制所有文件用 LF 换行 | Windows 同事提交后满屏 diff |
| `.pre-commit-config.yaml` | 提交代码前自动格式化+检查 | 格式不统一的代码混进仓库 |
| `.env.example` | 列出所有需要的环境变量 | 新人不知道要配哪些 key |
| `config.example.yaml` | 完整配置模板 | 新人不知道配置有哪些字段 |
| `Makefile` | 统一命令入口 | 每个人用自己的方式启动项目 |
| `AGENTS.md` | 告诉 AI 助手项目规则 | AI 生成的代码不符合项目规范 |

---

## 二、七层标准化体系

DeerFlow 的规范化不是随便加的，它有清晰的层次：

```
第 1 层：开发环境标准化
    → 确保每个人电脑上的工具版本一致
第 2 层：代码风格标准化
    → 确保所有人写的代码长得一样
第 3 层：项目结构标准化
    → 确保文件放在该放的位置
第 4 层：命令接口标准化
    → 确保所有人用同样的方式操作项目
第 5 层：质量门禁标准化
    → 确保不合格的代码进不了主分支
第 6 层：文档标准化
    → 确保知识和决策不被遗忘
第 7 层：配置管理标准化
    → 确保配置可追踪、可升级、不泄密
```

下面逐层展开。

---

## 第 1 层：开发环境标准化

### 核心原则：版本锁定 + 提前检查

DeerFlow 的做法：

```python
# scripts/check.py —— 在项目启动前检查工具版本
# Node.js 22+、pnpm 10+、uv、nginx，少一个都不让启动
```

**你该学什么**：

1. **锁定语言版本**
   - Python：根目录放 `.python-version` 文件，内容 `3.12`（DeerFlow 做了）
   - Node.js：`package.json` 里 `"packageManager": "pnpm@10.26.2"` 锁定 pnpm 版本
   - pyproject.toml 里 `requires-python = ">=3.12"` 锁定 Python 版本

2. **写一个依赖检查脚本**
   - 新人 clone 项目后，敲一个命令就知道缺什么
   - DeerFlow 用 Python 写的（`check.py`），你也可以用 shell
   - 关键：**告诉用户怎么装**，不只是说"你缺 xxx"

3. **统一包管理器**
   - Python：用 `uv`（新一代，比 poetry 快 10 倍）
   - 前端：用 `pnpm`（比 npm 快，比 yarn 更省磁盘）
   - 不要让团队里有人用 pip、有人用 poetry、有人用 conda

---

## 第 2 层：代码风格标准化

### 核心原则：机器强制 + 自动修复

DeerFlow 的做法：

```
写代码 → git add → git commit
                      ↓
              pre-commit 钩子自动触发
              ├── ruff check --fix（Python 自动修复）
              ├── ruff format（Python 自动格式化）
              ├── eslint --fix（TypeScript 自动修复）
              └── prettier --write（自动格式化）
                      ↓
              全部通过才允许提交
```

**你该学什么**：

1. **后端：用 ruff 替代 flake8 + black + isort**

   DeerFlow 的 `backend/ruff.toml`：
   ```toml
   line-length = 240          # 允许长行（项目选了这个，你也可以用 120）
   target-version = "py312"   # 目标 Python 版本
   select = ["E", "F", "I", "UP"]  # E=风格错误 F=语法错误 I=import排序 UP=语法现代化
   format.quote-style = "double"    # 双引号
   format.indent-style = "space"    # 空格缩进
   ```

   为什么用 ruff：一个工具替代三个（flake8 + black + isort），速度比它们快 100 倍。

2. **前端：ESLint + Prettier**

   - ESLint 检查代码质量（未使用变量、类型错误等）
   - Prettier 检查代码格式（缩进、换行、引号等）
   - 两者配合：ESLint 管逻辑，Prettier 管外观

3. **安装 pre-commit 钩子**

   DeerFlow 的 `.pre-commit-config.yaml`：
   ```yaml
   repos:
     - repo: local
       hooks:
         - id: ruff
           name: ruff lint
           entry: cd backend && uv run ruff check --fix
           language: system
           types: [python]
   ```

   效果：**每次 `git commit` 时自动格式化和检查**，开发者不需要记住跑 lint 命令。

4. **CI 里再跑一遍**

   `.github/workflows/lint-check.yml`：即使开发者跳过了 pre-commit，CI 也会拦住。

   ```
   本地 pre-commit  →  拦住大部分问题
   CI lint check    →  拦住漏网的
   两道防线，确保主分支代码永远格式统一
   ```

---

## 第 3 层：项目结构标准化

### 核心原则：约定优于配置

DeerFlow 的结构：

```
backend/                          ← 后端
├── app/                          ← 业务代码（不发版，import as app.*）
│   ├── __init__.py
│   ├── gateway/                  ← API 网关
│   └── channels/                 ← IM 通道（飞书/Slack/微信等）
├── packages/harness/deerflow/    ← 框架代码（可发版，import as deerflow.*）
│   ├── agents/                   ← Agent 核心
│   ├── tools/                    ← 工具定义
│   └── middlewares/              ← 中间件
├── tests/                        ← 测试（镜像 src 结构）
│   ├── test_harness_boundary.py  ← 架构边界检查（CI 强制）
│   └── ...
├── pyproject.toml                ← 依赖 + uv workspace 声明
├── ruff.toml                     ← 代码风格配置
├── Makefile                      ← 常用命令
├── langgraph.json                ← LangGraph 入口
└── CLAUDE.md                     ← AI 助手指令

frontend/                         ← 前端
├── src/                          ← 源码
│   ├── core/                     ← 核心业务逻辑
│   ├── components/ui/            ← Shadcn 组件（不要手动编辑）
│   └── components/               ← 业务组件
├── tests/                        ← 测试
│   ├── unit/                     ← 单元测试（镜像 src 结构）
│   └── e2e/                      ← E2E 测试
├── package.json                  ← 依赖 + 脚本
├── tsconfig.json                 ← TypeScript 配置
├── eslint.config.js              ← ESLint 配置
├── vitest.config.ts              ← 单元测试配置
└── playwright.config.ts          ← E2E 测试配置

scripts/                          ← 运维脚本（独立于前后端）
config.example.yaml               ← 配置模板
Makefile                          ← 总入口
```

**你该学什么**：

1. **测试目录镜像源码目录**
   ```
   源码: backend/packages/harness/deerflow/agents/lead.py
   测试: backend/tests/test_lead_agent.py
   
   源码: frontend/src/core/api/thread.ts
   测试: frontend/tests/unit/core/api/thread.test.ts
   ```
   新人看到源码文件，立刻知道对应测试在哪。

2. **分层架构 + 边界检查**
   - DeerFlow 把代码分成 `deerflow.*`（框架）和 `app.*`（业务）
   - 用 `test_harness_boundary.py` 在 CI 里强制：`deerflow.*` 不能 import `app.*`
   - 这样框架可以被其他项目复用，业务代码不会反向依赖

3. **不要手动编辑生成的代码**
   - `frontend/src/components/ui/` 是 Shadcn 自动生成的，不改
   - 改了下次重新生成就会被覆盖

---

## 第 4 层：命令接口标准化

### 核心原则：Makefile 作为唯一入口

DeerFlow 的做法：**所有操作都通过 Makefile**。

```bash
make check     # 检查环境
make install   # 安装依赖
make setup     # 配置项目
make dev       # 启动开发
make test      # 跑测试
make lint      # 代码检查
make stop      # 停服务
make clean     # 清理
```

**你该学什么**：

1. **Makefile 只做调度，复杂逻辑放 scripts/**
   ```
   Makefile（入口）  →  scripts/serve.sh（实现）
   Makefile（入口）  →  scripts/check.py（实现）
   ```
   好处：Makefile 保持简洁，新人一眼看懂有哪些命令。

2. **help 任务放第一个**
   ```makefile
   help:
       @echo "make dev    - 启动开发"
       @echo "make test   - 跑测试"
   ```
   新人敲 `make` 就知道能做什么。

3. **命令命名规律**
   ```
   make dev          开发模式
   make dev-pro      开发 + Gateway 模式
   make dev-daemon   开发后台模式
   make dev-daemon-pro  开发 + Gateway + 后台
   ```
   用后缀叠加，而不是 `make dev-gateway-background` 这种冗长名字。

---

## 第 5 层：质量门禁标准化

### 核心原则：CI 不通过的代码绝对不能合并

DeerFlow 的 CI 配置（`.github/workflows/`）：

```
每次 PR 自动触发：
├── lint-check.yml
│   ├── 后端 ruff check + ruff format --check
│   └── 前端 prettier + eslint + typecheck + build
├── backend-unit-tests.yml
│   └── pytest（117 个测试文件）
├── frontend-unit-tests.yml
│   └── vitest run
└── e2e-tests.yml（仅 frontend/ 变更时触发）
    └── playwright test
```

**你该学什么**：

1. **CI 配置四件套**

   | 检查项 | 后端 | 前端 |
   |--------|------|------|
   | 格式化 | `ruff format --check` | `prettier --check` |
   | 代码检查 | `ruff check` | `eslint` |
   | 类型检查 | （Python 可选） | `tsc --noEmit` |
   | 测试 | `pytest` | `vitest` + `playwright` |

2. **CI 里的防呆设计**
   - `frozen-lockfile`：前端用 `--frozen-lockfile` 安装依赖，防止偷偷加包
   - `timeout-minutes: 15`：超时自动取消，防止卡死
   - `concurrency: cancel-in-progress`：同一 PR 多次推送，只跑最新的
   - E2E 只在前端变更时触发：不浪费资源

3. **本地也要能跑**
   CI 里跑的每一条命令，都要能在本地手动跑：
   ```
   CI:  cd backend && uv sync --group dev && make lint
   本地: cd backend && make lint
   ```

---

## 第 6 层：文档标准化

### 核心原则：文档跟着代码走，不单独存在

DeerFlow 的文档层次：

```
根目录 README.md           → 项目介绍（给外部人看）
根目录 CONTRIBUTING.md     → 贡献指南（给协作者看）
根目录 AGENTS.md           → AI 助手指令（给 AI 看）
backend/CLAUDE.md          → 后端架构文档（563 行，给开发者看）
backend/docs/              → 功能设计文档（给深入研究者看）
frontend/CLAUDE.md         → 前端架构文档
```

**你该学什么**：

1. **每个子目录都要有 CLAUDE.md 或 AGENTS.md**

   这些文件不只是给人看的，也是给 AI 编程助手看的。你用 Cursor/Copilot/Claude Code 时，它们会自动读取这些文件来理解项目。

   DeerFlow 的 `backend/CLAUDE.md`（563 行）包含：
   - 架构概述
   - 关键目录和文件
   - 开发命令
   - 代码风格规则
   - 测试策略
   - 常见坑点

2. **设计决策要留痕**

   DeerFlow 的 `backend/docs/` 里有大量 RFC 和设计文档：
   ```
   docs/middleware-execution-flow.md  ← 中间件怎么执行的
   docs/HARNESS_APP_SPLIT.md         ← 为什么分成 harness 和 app
   docs/rfc-xxx.md                   ← 功能提案
   ```

   为什么重要：三个月后你忘了当时为什么这么设计，文档告诉你。

3. **README 要有快速开始**

   DeerFlow 的 README 结构：
   ```
   1. 一句话说项目是什么
   2. 功能亮点
   3. 三步快速开始（clone → install → dev）
   4. 详细配置
   5. 架构图
   6. 部署指南
   ```

---

## 第 7 层：配置管理标准化

### 核心原则：模板 + 版本 + 不提交密钥

DeerFlow 的做法：

```
config.example.yaml       ← 模板（提交到 Git，977 行，所有字段都有注释）
config.yaml               ← 用户配置（.gitignore 忽略，不提交）
.env.example              ← 环境变量模板（提交到 Git）
.env                      ← 用户环境变量（.gitignore 忽略）

config_version: 8         ← 配置版本号
make config-upgrade       ← 自动合并新字段到旧配置
```

**你该学什么**：

1. **永远不要把密钥提交到 Git**

   `.gitignore` 里必须有的：
   ```
   .env
   config.yaml
   *.key
   *.pem
   credentials.json
   ```

   配置模板里用占位符：
   ```yaml
   api_key: $OPENAI_API_KEY    # 从环境变量读取
   ```

2. **配置文件加版本号**

   ```yaml
   config_version: 8    # 每次改 schema 就加 1
   ```

   程序启动时检查版本号，过旧就提示用户跑 `make config-upgrade`。

3. **配置生成有两条路径**

   - `make setup`：交互式向导，帮你选（推荐新手）
   - `make config`：直接复制模板（适合老手）
   - 两者都**拒绝覆盖已有配置**，防止误操作

---

## 三、对照清单：你的新项目该加什么

创建新项目时，按这个清单逐项检查：

### 必须有（不做就是不负责任）

- [ ] `.gitignore` — 忽略 `.env`、缓存、编译产物
- [ ] `.gitattributes` — 强制 LF 换行
- [ ] `README.md` — 项目介绍 + 快速开始
- [ ] `.env.example` — 环境变量模板
- [ ] `Makefile` — 统一命令入口（至少有 `help`、`install`、`dev`、`test`、`lint`）
- [ ] Linter 配置（Python: ruff.toml, 前端: eslint + prettier）
- [ ] `.pre-commit-config.yaml` — 提交前自动格式化
- [ ] GitHub Actions CI — PR 自动跑 lint + test

### 强烈推荐（做了项目质量上一个台阶）

- [ ] `CONTRIBUTING.md` — 贡献指南
- [ ] `AGENTS.md` 或 `CLAUDE.md` — AI 助手指令
- [ ] `scripts/check.py` — 环境检查脚本
- [ ] `config.example.yaml` — 完整配置模板
- [ ] 测试目录镜像源码目录
- [ ] TypeScript strict 模式（前端项目）
- [ ] `backend/docs/` — 设计文档目录

### 锦上添花（做大项目时再加）

- [ ] `scripts/doctor.py` — 深度诊断工具
- [ ] `scripts/setup_wizard.py` — 交互式配置向导
- [ ] `config_version` — 配置版本追踪
- [ ] E2E 测试（Playwright）
- [ ] Docker 开发/生产环境
- [ ] 多语言 README

---

## 四、一句话总结

**规范化的本质是：用工具代替人去记住规则。**

```
不要靠人去记"提交前要跑 lint"   → 用 pre-commit 钩子
不要靠人去记"代码要双引号"      → 用 ruff 自动格式化
不要靠人去记"测试要全过"        → 用 CI 自动检查
不要靠人去记"怎么启动项目"      → 用 Makefile 统一入口
不要靠人去记"要配哪些环境变量"  → 用 .env.example 提示
```

你只需要记住一条：**如果一件事需要靠人去记，就把它自动化。**
