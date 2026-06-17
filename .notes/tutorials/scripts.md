# scripts/ 笔记

> 学习笔记，不修改 upstream 源码。按三问法（解决什么/放弃什么/什么条件崩）分析每个脚本。

---

## docker.sh — Docker 开发环境管理脚本

### 概览

命令分发器，5 个子命令，Makefile 里的 `make docker-*` 命令最终都调用这个脚本。

| 命令 | 做什么 | Makefile 对应 |
|------|--------|--------------|
| `init` | 检测 sandbox 模式，拉沙箱镜像 | `make docker-init` |
| `start` | 检测 sandbox 模式 → 确认配置文件存在 → docker compose up | `make docker-start` |
| `stop` | docker compose down + 清理沙箱容器 | `make docker-stop` |
| `restart` | docker compose restart | — |
| `logs` | docker compose logs -f，支持按服务过滤 | `make docker-logs` |

### 逐段详解

#### 1. 头部变量（1-16 行）

```bash
set -e                        # 任何命令失败就退出脚本，防止错误传播
SCRIPT_DIR=...                # 脚本所在目录（scripts/）
PROJECT_ROOT=...              # 项目根目录（scripts/ 的上一级）
DOCKER_DIR=...                # docker/ 目录
COMPOSE_CMD="docker compose -p deer-flow-dev -f docker-compose-dev.yaml"
                              # -p deer-flow-dev：项目名，容器名前缀
                              # -f docker-compose-dev.yaml：指定 compose 文件
```

#### 2. detect_sandbox_mode()（18-61 行）— 最精巧的部分

从 `config.yaml` 里解析 sandbox 配置，判断三种模式：

| 模式 | 含义 | 什么时候用 |
|------|------|-----------|
| `local` | 本地文件系统，不需要 Docker 沙箱 | 默认模式，开发用 |
| `aio` | All-in-One 沙箱，Docker 容器隔离 | 需要代码执行隔离 |
| `provisioner` | Kubernetes 模式，外部 provisioner 服务 | 生产/K8s 环境 |

**解析方式**：用 awk 手写状态机解析 YAML 缩进。原理：

```
第1步：遇到 "sandbox:" 这行 → in_sandbox=1（进入 sandbox 块）
第2步：在 sandbox 块内遇到 "use:" → 提取值
第3步：遇到非缩进行 → in_sandbox=0（退出 sandbox 块）
```

同样方式提取 `provisioner_url`。两个值组合判断模式：

```
sandbox.use 含 LocalSandboxProvider  → "local"
sandbox.use 含 AioSandboxProvider + 有 provisioner_url → "provisioner"
sandbox.use 含 AioSandboxProvider + 无 provisioner_url → "aio"
其他 / config.yaml 不存在           → "local"（兜底默认）
```

**潜在问题**：awk 解析依赖缩进，如果 YAML 格式变化（比如用 `{...}` 行内写法）会解析失败。

#### 3. init()（88-148 行）— 拉沙箱镜像

流程：
```
1. 调 detect_sandbox_mode() 获取模式
2. 如果是 local → 跳过，不需要镜像，检查 Docker 是否可用就返回
3. 如果是 aio/provisioner → 检查镜像是否已存在
4. 不存在 → docker pull
5. pull 失败 → 不中断，友好提示（可能是网络/代理/认证问题）
```

关键设计：
- **镜像地址是火山引擎（字节跳动）的 CR**：`enterprise-public-cn-beijing.cr.volces.com`
- **pull 失败不中断**：因为 local 模式不需要镜像，失败可能只是网络问题
- **幂等**：`docker images | grep` 检查已存在就跳过

#### 4. start()（151-238 行）— 启动 Docker 开发环境

流程：
```
1. 检测 sandbox 模式
2. 根据 mode 决定启动哪些服务：
   - 非 provisioner → frontend + gateway + nginx
   - provisioner    → frontend + gateway + provisioner + nginx
3. 设置 DEER_FLOW_ROOT 环境变量
4. 确保 config.yaml 存在（不存在则从 example 复制，并提示用户编辑）
5. 确保 extensions_config.json 存在（Docker bind-mount 的坑）
6. docker compose up --build -d --remove-orphans
```

**两个防坑设计**：

1. **config.yaml 自动从 example 复制**（190-209 行）：如果用户没跑 `make setup` 直接 `docker-start`，不会挂掉，而是复制 example 并提示编辑 API key

2. **extensions_config.json 必须作为文件存在**（211-221 行）：
   - Docker bind-mount 的行为：如果主机路径不存在，Docker 会**创建一个目录**而不是文件
   - 所以必须先 touch 出这个文件，否则容器里看到的是个目录
   - 这就是为什么注释写 "Docker creates a directory when bind-mounting a non-existent host path"

#### 5. stop()（275-286 行）— 停止服务

```
1. 设置 DEER_FLOW_ROOT（抑制 compose down 时的 "variable is not set" 警告）
2. docker compose down
3. 调 cleanup-containers.sh 清理沙箱容器（2>/dev/null 失败不报错）
```

#### 6. logs()（241-272 行）— 查看日志

用 case 匹配 `--frontend`/`--gateway`/`--nginx`/`--provisioner` 参数，传给 `docker compose logs -f`。

#### 7. main()（324-357 行）— 命令分发

标准 case 分发模式。注意 `start)` 有 `shift` — 把 `$1`（"start"）去掉，把剩余参数传给 `start()` 函数做校验（实际 start 函数拒绝任何额外参数）。

### Sandbox 三种模式

| 模式 | 沙箱容器谁创建 | 隔离程度 | 适用场景 |
|------|--------------|---------|---------|
| `local` | 无容器，直接本地目录 | 无隔离 | 开发调试，信任 Agent |
| `aio` | Gateway 进程自己创建 Docker 容器 | 进程+文件系统隔离 | 单机生产、开发、小规模 |
| `provisioner` | 独立 provisioner 服务统一管理沙箱容器/Pod | 完全隔离 | K8s 集群、多实例、大规模生产 |

**aio vs provisioner 的本质区别不是"能不能生产用"，而是"谁管理沙箱"**：
- `aio`：Gateway 自己创建 Docker 容器。单机够用，多实例时容器散落各处不好管
- `provisioner`：独立服务统一管理。适合 K8s（直接创建 Pod 而非 Docker-in-Docker）、多 Gateway 实例、需要全局资源控制

**类比**：
- `local` = 让客人住你家，随便用厨房
- `aio` = 给客人一个独立公寓，每个客人自己管钥匙
- `provisioner` = 酒店，前台统一管所有房间、管分配、管回收

**判断逻辑**（从 config.yaml 解析）：
```
use 含 LocalSandboxProvider                    → local
use 含 AioSandboxProvider + 有 provisioner_url → provisioner
use 含 AioSandboxProvider + 无 provisioner_url → aio
config.yaml 不存在 或 字段不认识              → local（安全兜底）
```

### 基础知识补充

#### awk 是什么

文本处理工具，逐行读文件，按规则匹配和处理。比 sed 强，比 Python 弱。

在 docker.sh 里用它解析 YAML，本质是：逐行扫描，遇到关键词"sandbox:"开始追踪，遇到"use:"提取值。

类比：你有一份清单，awk 就是一个"逐行扫清单、看到关键词就抄下来"的助手。

#### docker compose -p deer-flow-dev -f docker-compose-dev.yaml 做什么

| 参数 | 含义 |
|------|------|
| `-p deer-flow-dev` | 项目名。所有容器名、网络名都加这个前缀，比如 `deer-flow-dev-frontend-1`。同时跑多个 DeerFlow 实例时不会冲突 |
| `-f docker-compose-dev.yaml` | 指定 compose 文件。开发环境用 bind-mount 热加载，生产环境用构建好的镜像，配置不同 |
| `up --build -d` | 构建镜像 + 后台启动 |
| `--remove-orphans` | 清理不在当前 compose 文件里但属于同一项目的旧容器 |

#### 为什么用 awk 不用 Python yaml 库

因为这个脚本的运行环境可能没有 Python。设计目标：只有 bash 和 Docker 就能跑。

| 方案 | 优点 | 缺点 |
|------|------|------|
| awk | 零依赖，任何 Linux/macOS 都有 | 只能解析简单缩进 YAML，复杂写法会挂 |
| Python yaml | 解析完整 | 要装 Python + pip install pyyaml |
| yq（YAML 的 jq） | 专门干这个的 | 要额外安装 |

### 执行链路

从用户输入到最终执行：

```
用户输入 make docker-start
  → Makefile 里定义了：docker-start: ./scripts/docker.sh start
  → 执行 scripts/docker.sh start
  → main() 函数的 case "start") 分支
  → start() 函数执行：
     1. detect_sandbox_mode()    ← 读 config.yaml 判断模式
     2. 根据 mode 决定启动哪些服务
     3. 检查 config.yaml 存在（不存在则从 example 复制）
     4. 检查 extensions_config.json 存在（Docker bind-mount 坑）
     5. cd docker/ && docker compose up --build -d
  → Docker 容器启动
```

**为什么要套 shell 脚本，而不是 Makefile 直接调 docker compose？**

因为直接调 docker compose 缺少"启动前准备"：
1. 需要动态检测 sandbox 模式 → 决定启动哪些服务
2. 需要确保配置文件存在 → 不存在就从 example 复制
3. 需要设置环境变量 → DEER_FLOW_ROOT
4. 需要友好错误提示 → 纯 Makefile 做不好条件判断和彩色输出

好处：Makefile 只做薄转发（一行调用），复杂逻辑放 shell 脚本，用户只需记 `make docker-start`。

### 三问法分析

| 问题 | 答案 |
|------|------|
| **解决什么？** | 让用户用简单的 `make docker-start` 一条命令启动整个开发环境，不需要手动 docker compose、不需要手动准备配置文件 |
| **放弃了什么？** | 用 awk 解析 YAML 而不是调用 Python yaml 库 — 好处是不依赖 Python，坏处是脆弱（缩进敏感）。选择 shell 脚本而不是 Python — 好处是通用，坏处是解析能力弱 |
| **什么条件崩？** | ① config.yaml 的 sandbox 段缩进不对 → detect_sandbox_mode 误判为 local ② Docker daemon 没启动 → docker info 超时（没有 timeout） ③ bind-mount 路径有特殊字符 → 路径拼接可能出错 |

---

## scripts/ 全景图

### 两类 Docker，互不相关（易混淆点）

DeerFlow 里有两种完全独立的 Docker 用法：

1. **服务容器**：Gateway、Frontend、nginx 这些**服务**跑在哪？
   - `make dev` → 宿主机直接跑（需要本地装 Node/pnpm/uv/nginx）
   - `make docker-start` → Docker 容器里跑（只需要 Docker）

2. **沙箱容器**：Agent 执行 bash/写文件时的**隔离环境**是什么？
   - `local` → 无隔离，直接在文件系统上操作
   - `aio` → 单独的 Docker 容器
   - `provisioner` → K8s Pod

**类比**：开一家餐厅
- 服务容器 = 餐厅开在哪（路边摊 vs 商场店）→ 取决于你本地装没装那堆工具
- 沙箱容器 = 厨房有没有防火隔离（无 vs 有 vs 中央厨房）→ 取决于你需不需要隔离 Agent 的代码执行

所以 `make docker-start` + local sandbox = 商场店但厨房没隔离（容器跑服务 + Agent 直接操作文件系统）。两个选择互不影响。

### 为什么有了 make dev 还要 docker.sh

`make dev` 需要宿主机装好 Node 22+、pnpm、uv、nginx。`make docker-start` 只需要 Docker。
对不想折腾本地环境的用户，Docker 方式更简单。

### 所有脚本一览

**启动相关（4 个）**

| 脚本 | 干什么 |
|------|--------|
| `docker.sh` | Docker 开发环境：init/start/stop/restart/logs |
| `serve.sh` | 本地开发：起 LangGraph + Gateway + Frontend + nginx |
| `start-daemon.sh` | 后台启动单个服务（被 serve.sh 调用） |
| `deploy.sh` | 生产部署 |

**安装配置相关（4 个）**

| 脚本 | 干什么 |
|------|--------|
| `setup_wizard.py` | `make setup` 的交互式配置向导（生成 config.yaml） |
| `configure.py` | 低级配置工具 |
| `config-upgrade.sh` | `make config-upgrade`：合并新版 config.example.yaml 的字段到旧 config.yaml |
| `doctor.py` | `make doctor`：检查配置和系统是否 OK |

**检查相关（2 个）**

| 脚本 | 干什么 |
|------|--------|
| `check.sh` | `make check`：检查 Node/pnpm/uv/nginx 是否安装 |
| `check.py` | check 的 Python 版本（可能被其他脚本调用） |

**运维工具（4 个）**

| 脚本 | 干什么 |
|------|--------|
| `cleanup-containers.sh` | 清理沙箱容器（被 docker.sh stop 调用） |
| `wait-for-port.sh` | 等待某个端口可用（被 serve.sh 用，确保前一个服务启动后再起下一个） |
| `tool-error-degradation-detection.sh` | 工具错误降级检测 |
| `load_memory_sample.py` | 加载 memory 样本数据 |

**其他（3 个）**

| 脚本 | 干什么 |
|------|--------|
| `run-with-git-bash.cmd` | Windows 用户用 Git Bash 启动的入口 |
| `export_claude_code_oauth.py` | 导出 Claude Code 的 OAuth token |
| `wizard/` | 配置向导的子模块目录 |

### 调用链路

```
make dev
  → serve.sh
    → start-daemon.sh（起 LangGraph）
    → wait-for-port.sh（等 2024 端口就绪）
    → start-daemon.sh（起 Gateway）
    → wait-for-port.sh（等 8001 端口就绪）
    → start-daemon.sh（起 Frontend）
    → start-daemon.sh（起 nginx）

make docker-start
  → docker.sh start
    → docker compose up

make setup
  → setup_wizard.py（交互式问答）
    → configure.py（写 config.yaml）

make docker-stop
  → docker.sh stop
    → docker compose down
    → cleanup-containers.sh（清理沙箱容器）
```

设计原则：每个脚本只做一件事，Makefile 组合调用（Unix 哲学）。
