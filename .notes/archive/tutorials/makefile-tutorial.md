# Makefile 入门教程

> 目标：学完后能看懂 DeerFlow 项目的 Makefile。
> 实践：对照 `/home/yydspei/projects/deer-flow/Makefile` 边读边学。

---

## 1. Makefile 是什么

一个文件，里面定义了一堆**任务**（也叫 target）。你在终端敲 `make 任务名`，它就执行对应的命令。

```
终端输入          →  Makefile 里找到任务  →  执行命令
make install      →  install:            →  cd backend && uv sync ...
make dev          →  dev:                →  ./scripts/serve.sh --dev
```

本质就是**给一长串命令起个短名字**。

---

## 2. 基本语法

### 2.1 定义任务

```makefile
任务名:
 命令
```

注意：

- 任务名后面的 `:` 不能少
- 命令前面必须是 **Tab 键**，不能是空格
- 每一行命令是一个独立的 shell 语句

### 2.2 实例（DeerFlow 第 54 行）

```makefile
setup:
 @$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py
```

拆解：

- `setup:` — 任务名叫 setup
- `@` — 执行时不把命令打印到终端（静默执行）
- `$(BACKEND_UV_RUN)` — 引用一个变量（下一节讲）
- `python ../scripts/setup_wizard.py` — 要执行的命令

### 2.3 `@` 和 `-` 前缀

| 前缀 | 含义 | 例子 |
|------|------|------|
| `@` | 不打印命令本身，只显示输出 | `@echo "hello"` 只输出 hello |
| `-` | 命令失败了也继续 | `-rm -rf temp/` 删不掉也不报错 |
| 无前缀 | 打印命令 + 执行 | `echo "hello"` 会先打印 `echo "hello"` 再输出 hello |

看 DeerFlow 第 72-76 行的对比：

```makefile
install:
 @echo "Installing backend dependencies..."    # @ 静默，只显示文字
 @cd backend && uv sync                        # @ 静默
 @cd frontend && pnpm install                  # @ 静默
```

所有命令都加了 `@`，所以 `make install` 时你只会看到输出结果，不会看到命令本身。

---

## 3. 变量

### 3.1 定义变量

```makefile
BACKEND_UV_RUN = cd backend && uv run
PYTHON ?= python3
```

| 写法 | 含义 |
|------|------|
| `VAR = 值` | 普通赋值 |
| `VAR ?= 值` | 只在变量为空时才赋值（可以被环境变量覆盖） |

DeerFlow 第 5-6 行：

```makefile
BASH ?= bash                                    # 如果环境变量 BASH 没设置，就用 bash
BACKEND_UV_RUN = cd backend && uv run            # 普通变量
```

### 3.2 使用变量

用 `$(变量名)` 引用：

```makefile
setup:
 @$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py
```

展开后就是：

```bash
cd backend && uv run python ../scripts/setup_wizard.py
```

### 3.3 shell 变量 vs Makefile 变量

Makefile 里用 `$` 引用自己的变量。但 shell 命令里也用 `$`（比如 `$PATH`）。
为了区分，**shell 里的 `$` 要写两个 `$$`**。

看 DeerFlow 第 92-93 行：

```makefile
 @IMAGE=$$(grep ... | awk '{print $$2}'); \
 if [ -z "$$IMAGE" ]; then \
```

- `$$` → 传给 shell 的是 `$`，所以 shell 看到的是 `$IMAGE`
- `$$(grep ...)` → shell 执行 `$(grep ...)` 命令替换

---

## 4. 条件判断

DeerFlow 第 9-17 行：

```makefile
ifeq ($(OS),Windows_NT)
    SHELL := cmd.exe
    PYTHON ?= python
    RUN_WITH_GIT_BASH = call scripts\run-with-git-bash.cmd
else
    PYTHON ?= python3
    RUN_WITH_GIT_BASH =
endif
```

语法：

```makefile
ifeq (值1, 值2)    # 如果相等
    ...
else               # 否则
    ...
endif              # 结束
```

这段的意思是：如果是 Windows，用 `python`；如果是 Linux/Mac，用 `python3`。

---

## 5. 任务依赖

### 5.1 基本用法

```makefile
clean: stop
 @echo "Cleaning up..."
```

`clean: stop` 意思是执行 `clean` 之前，**先执行 `stop`**。

DeerFlow 第 164 行：

```makefile
clean: stop
 @echo "Cleaning up..."
 @-rm -rf backend/.deer-flow 2>/dev/null || true
 @-rm -rf backend/.langgraph_api 2>/dev/null || true
 @-rm -rf logs/*.log 2>/dev/null || true
```

所以 `make clean` 实际执行顺序：

1. 先执行 `stop` 任务（停止所有服务）
2. 再删除临时文件

### 5.2 多行命令

每个任务可以有多行命令，**按顺序执行**。任何一行失败，整个任务停止。

```makefile
install:
 @echo "Installing backend dependencies..."     # 第 1 行
 @cd backend && uv sync                         # 第 2 行
 @echo "Installing frontend dependencies..."    # 第 3 行
 @cd frontend && pnpm install                   # 第 4 行
 @echo "✓ All dependencies installed"           # 第 5 行
```

---

## 6. 多行 shell 命令（反斜杠续行）

Makefile 的每一行命令会在**独立的 shell** 里执行。如果你想在同一个 shell 里跑多行命令，用 `\` 连接：

DeerFlow 第 92-117 行的 `setup-sandbox` 任务：

```makefile
setup-sandbox:
 @IMAGE=$$(grep ... ); \
 if [ -z "$$IMAGE" ]; then \
  IMAGE="default-image"; \
  echo "Using default image: $$IMAGE"; \
 else \
  echo "Using configured image: $$IMAGE"; \
 fi; \
 echo ""; \
 if command -v docker >/dev/null 2>&1; then \
  docker pull "$$IMAGE"; \
 fi
```

注意每一行末尾的 `\`。没有 `\` 的话，每行是独立的 shell，上一行的变量在下一行就丢了。

---

## 7. .PHONY

```makefile
.PHONY: help config check install setup doctor dev stop clean
```

告诉 make：这些名字是任务名，不是文件名。

**为什么需要**：make 默认认为 `make xxx` 是要生成一个叫 `xxx` 的文件。如果当前目录碰巧有个叫 `clean` 的文件，`make clean` 就会跳过不执行。加了 `.PHONY` 就无条件执行。

**规则**：所有不生成文件的任务，都应该加到 `.PHONY` 里。

---

## 8. 注释

```makefile
# 这是注释
```

`#` 开头的行是注释，make 会忽略。

---

## 9. 默认任务

如果你只敲 `make` 不加任务名，它执行**第一个任务**。

DeerFlow 的第一个任务是 `help`（第 19 行），所以单独敲 `make` 会显示帮助信息。

---

## 10. 完整对照表：读懂 DeerFlow 的 Makefile

逐段对应：

### 第 1-3 行：声明和 .PHONY

```makefile
# DeerFlow - Unified Development Environment          ← 注释
.PHONY: help config check install setup ...            ← 声明所有任务名
```

### 第 5-17 行：变量和条件判断

```makefile
BASH ?= bash                                          ← 变量（可被环境变量覆盖）
BACKEND_UV_RUN = cd backend && uv run                  ← 变量（拼接常用前缀）
ifeq ($(OS),Windows_NT)                                ← Windows 判断
    PYTHON ?= python
else
    PYTHON ?= python3                                  ← Linux/Mac 用 python3
endif
```

### 第 19-51 行：help 任务

```makefile
help:                                                  ← 默认任务（第一个）
 @echo "DeerFlow Development Commands:"             ← @ 静默输出
 @echo "  make setup           - ..."
 @echo "  make dev             - ..."
 ...
```

`make` 或 `make help` 会显示所有可用命令的说明。

### 第 54-84 行：配置和安装

```makefile
setup:                                                 ← 启动配置向导
 @$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py

check:                                                 ← 检查依赖工具
 @$(PYTHON) ./scripts/check.py

install:                                               ← 安装前后端依赖
 @cd backend && uv sync
 @cd frontend && pnpm install
```

### 第 87-117 行：复杂任务（多行 shell）

```makefile
setup-sandbox:                                         ← 拉取 sandbox 镜像
 @IMAGE=$$(grep ... ); \                            ← shell 变量赋值
 if [ -z "$$IMAGE" ]; then \                        ← if-else 判断
  ...
 fi; \
 if command -v docker ...; then \                   ← 检查 docker 是否安装
  docker pull "$$IMAGE"; \                       ← 拉镜像
 fi
```

### 第 120-161 行：启动和停止

```makefile
dev:                                                   ← 启动开发服务
 @$(PYTHON) ./scripts/check.py                      ← 先检查
 @$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev     ← 再启动

stop:                                                  ← 停止服务
 @$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --stop
```

### 第 164-168 行：任务依赖 + 容错

```makefile
clean: stop                                            ← 先执行 stop，再清理
 @echo "Cleaning up..."
 @-rm -rf backend/.deer-flow 2>/dev/null || true    ← - 容错，删不掉也继续
 @-rm -rf logs/*.log 2>/dev/null || true
```

---

## 速查表

| 语法 | 含义 | 例子 |
|------|------|------|
| `任务名:` | 定义任务 | `setup:` |
| `任务名: 其他任务` | 先执行依赖任务 | `clean: stop` |
| `@命令` | 静默执行 | `@echo "hello"` |
| `-命令` | 失败了也继续 | `-rm -rf temp/` |
| `$(VAR)` | 引用 Makefile 变量 | `$(PYTHON)` |
| `$$VAR` | 引用 shell 变量 | `$$IMAGE` |
| `\` | 续行（同一个 shell） | `命令1; \` |
| `VAR = 值` | 定义变量 | `BACKEND_UV_RUN = cd backend && uv run` |
| `VAR ?= 值` | 有值就不覆盖 | `PYTHON ?= python3` |
| `ifeq (a,b)` | 条件判断 | `ifeq ($(OS),Windows_NT)` |
| `#` | 注释 | `# 这是注释` |
| `.PHONY:` | 声明非文件任务 | `.PHONY: clean` |
