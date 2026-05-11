# yyds: DeerFlow Makefile — 项目所有命令的入口
#
# Makefile 是什么？
#   一个用 make 命令执行的"任务清单"。你敲 make xxx，它就执行对应的命令。
#   好处：不用记各种脚本的路径和参数，统一用 make xxx 就行。
#   这比每个项目自己搞一套 scripts/xxx.sh 的方式更规范、更通用。
#
# 为什么惊艳？
#   - 一条命令 `make dev` 启动整个开发环境（3 个进程）
#   - 一条命令 `make setup` 交互式配置（选模型、填 API key）
#   - 一条命令 `make doctor` 健康检查（依赖 + 配置 + API key 全查）
#   - 每个命令背后可能调多个脚本，但用户只需要记 make xxx
#   - 这是企业级项目的标准做法，以后你自己的项目也可以这样搞
#
# 所有命令一览：
#   ┌─ 首次使用 ─────────────────────────────────────┐
#   │  make check       检查依赖是否安装               │
#   │  make install     安装所有依赖                   │
#   │  make setup       交互式配置向导（推荐首次用）    │
#   │  make doctor      深度健康检查                   │
#   └──────────────────────────────────────────────────┘
#   ┌─ 本地开发 ─────────────────────────────────────┐
#   │  make dev         启动开发环境（热重载）          │
#   │  make stop        停止所有服务                   │
#   │  make dev-daemon  后台启动（不占终端）            │
#   └──────────────────────────────────────────────────┘
#   ┌─ 配置管理 ─────────────────────────────────────┐
#   │  make config      生成配置文件（首次）            │
#   │  make config-upgrade  升级配置（upstream 更新后） │
#   └──────────────────────────────────────────────────┘
#   ┌─ Docker 开发 ──────────────────────────────────┐
#   │  make docker-init   拉沙箱镜像（首次）           │
#   │  make docker-start  Docker 开发模式              │
#   │  make docker-stop   停止 Docker                  │
#   │  make docker-logs   查看 Docker 日志             │
#   └──────────────────────────────────────────────────┘
#   ┌─ Docker 生产 ──────────────────────────────────┐
#   │  make up          构建并启动生产容器              │
#   │  make down        停止生产容器                   │
#   └──────────────────────────────────────────────────┘
#
# DeerFlow - Unified Development Environment

# yyds: .PHONY 声明这些不是文件名，而是"伪目标"（phony targets）
#       如果不声明，make 发现当前目录有个叫 "dev" 的文件时，会认为目标已存在而不执行
#       声明后 make 就知道这些是命令名，不是文件名
.PHONY: help config config-upgrade check install setup doctor detect-thread-boundaries dev dev-daemon start start-daemon stop up down clean docker-init docker-start docker-stop docker-logs docker-logs-frontend docker-logs-gateway

# yyds: 变量定义（Makefile 里的变量用 $() 或 ${} 引用）
#       ?= 表示"如果没设置才用这个默认值"（可以被环境变量覆盖）
#       := 表示"立即赋值"
BASH ?= bash
# yyds: BACKEND_UV_RUN = 在 backend/ 目录下用 uv 运行 Python 脚本
#       很多 Python 脚本需要 uv 管理的虚拟环境里的依赖，所以用 uv run 跑
BACKEND_UV_RUN = cd backend && uv run

# Detect OS for Windows compatibility
# yyds: Windows 兼容处理。Windows 原生没有 bash，
#       所以 .sh 脚本需要通过 Git Bash 运行（run-with-git-bash.cmd）
#       WSL/Linux/macOS 用户不受影响，RUN_WITH_GIT_BASH 为空
ifeq ($(OS),Windows_NT)
    SHELL := cmd.exe
    PYTHON ?= python
    # Run repo shell scripts through Git Bash when Make is launched from cmd.exe / PowerShell.
    RUN_WITH_GIT_BASH = call scripts\run-with-git-bash.cmd
else
    PYTHON ?= python3
    RUN_WITH_GIT_BASH =
endif

# yyds: help 目标——敲 make 或 make help 时显示这个帮助信息
#       @ 符号表示"执行命令但不打印命令本身"（只打印命令的输出）
help:
	@echo "DeerFlow Development Commands:"
	@echo "  make setup           - Interactive setup wizard (recommended for new users)"
	@echo "  make doctor          - Check configuration and system requirements"
	@echo "  make config          - Generate local config files (aborts if config already exists)"
	@echo "  make config-upgrade  - Merge new fields from config.example.yaml into config.yaml"
	@echo "  make check           - Check if all required tools are installed"
	@echo "  make detect-thread-boundaries - Inventory async/thread boundary points"
	@echo "  make detect-blocking-io        - Inventory blocking IO that may block the backend event loop"
	@echo "  make install         - Install all dependencies (frontend + backend + pre-commit hooks)"
	@echo "  make setup-sandbox   - Pre-pull sandbox container image (recommended)"
	@echo "  make dev             - Start all services in development mode (with hot-reloading)"
	@echo "  make dev-daemon      - Start dev services in background (daemon mode)"
	@echo "  make start           - Start all services in production mode (optimized, no hot-reloading)"
	@echo "  make start-daemon    - Start prod services in background (daemon mode)"
	@echo "  make stop            - Stop all running services"
	@echo "  make clean           - Clean up processes and temporary files"
	@echo ""
	@echo "Docker Production Commands:"
	@echo "  make up              - Build and start production Docker services (localhost:2026)"
	@echo "  make down            - Stop and remove production Docker containers"
	@echo ""
	@echo "Docker Development Commands:"
	@echo "  make docker-init     - Pull the sandbox image"
	@echo "  make docker-start    - Start Docker services (mode-aware from config.yaml, localhost:2026)"
	@echo "  make docker-stop     - Stop Docker development services"
	@echo "  make docker-logs     - View Docker development logs"
	@echo "  make docker-logs-frontend - View Docker frontend logs"
	@echo "  make docker-logs-gateway - View Docker gateway logs"

## Setup & Diagnosis
# yyds: ─── 配置和诊断 ─────────────────────────────────────

# yyds: make setup → 运行交互式配置向导（选模型、填 API key、选沙箱模式）
#       首次使用的推荐入口
setup:
	@$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py

# yyds: make doctor → 深度健康检查（依赖 + 配置 + API key + 模型包 + 沙箱）
doctor:
	@$(BACKEND_UV_RUN) python ../scripts/doctor.py

detect-thread-boundaries:
	@$(PYTHON) ./scripts/detect_thread_boundaries.py

detect-blocking-io:
	@$(MAKE) -C backend detect-blocking-io

# yyds: make config → 从示例文件复制配置（如果 config.yaml 已存在会拒绝）
#       首次用 make setup 更好，这个适合想手动编辑配置的人
config:
	@$(PYTHON) ./scripts/configure.py

# yyds: make config-upgrade → 升级配置文件（upstream 新增了配置项时用）
#       只加新字段，不覆盖已有配置
config-upgrade:
	@$(RUN_WITH_GIT_BASH) ./scripts/config-upgrade.sh

# Check required tools
# yyds: make check → 检查 4 个依赖是否安装（Node.js/pnpm/uv/nginx）
check:
	@$(PYTHON) ./scripts/check.py

# Install all dependencies
# yyds: make install → 安装所有依赖：
#       1. uv sync（Python 后端依赖）
#       2. pnpm install（前端依赖）
#       3. pre-commit install（Git pre-commit 钩子）
install:
	@echo "Installing backend dependencies..."
	@cd backend && uv sync
	@echo "Installing frontend dependencies..."
	@cd frontend && pnpm install
	@echo "Installing pre-commit hooks..."
	@$(BACKEND_UV_RUN) --with pre-commit pre-commit install
	@echo "✓ All dependencies installed"
	@echo ""
	@echo "=========================================="
	@echo "  Optional: Pre-pull Sandbox Image"
	@echo "=========================================="
	@echo ""
	@echo "If you plan to use Docker/Container-based sandbox, you can pre-pull the image:"
	@echo "  make setup-sandbox"
	@echo ""

# Pre-pull sandbox Docker image (optional but recommended)
# yyds: make setup-sandbox → 预拉沙箱镜像（Agent 执行代码时用的隔离容器）
#       用容器沙箱时需要先拉镜像，local 沙箱不需要
setup-sandbox:
	@echo "=========================================="
	@echo "  Pre-pulling Sandbox Container Image"
	@echo "=========================================="
	@echo ""
	@IMAGE=$$(grep -A 20 "# sandbox:" config.yaml 2>/dev/null | grep "image:" | awk '{print $$2}' | head -1); \
	if [ -z "$$IMAGE" ]; then \
		IMAGE="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"; \
		echo "Using default image: $$IMAGE"; \
	else \
		echo "Using configured image: $$IMAGE"; \
	fi; \
	echo ""; \
	if command -v container >/dev/null 2>&1 && [ "$$(uname)" = "Darwin" ]; then \
		echo "Detected Apple Container on macOS, pulling image..."; \
		container image pull "$$IMAGE" || echo "⚠ Apple Container pull failed, will try Docker"; \
	fi; \
	if command -v docker >/dev/null/ 2>&1; then \
		echo "Pulling image using Docker..."; \
		if docker pull "$$IMAGE"; then \
			echo ""; \
			echo "✓ Sandbox image pulled successfully"; \
		else \
			echo ""; \
			echo "⚠ Failed to pull sandbox image (this is OK for local sandbox mode)"; \
		fi; \
	else \
		echo "✗ Neither Docker nor Apple Container is available"; \
		echo "  Please install Docker: https://docs.docker.com/get-docker/"; \
		exit 1; \
	fi

# ─── 本地开发 ─────────────────────────────────────────────

# Start all services in development mode (with hot-reloading)
# yyds: make dev → 启动开发环境！最常用的命令
#       1. 先跑 check.py 检查依赖
#       2. 调 serve.sh --dev 启动 3 个进程：
#          Gateway(:8001) + Frontend(:3000) + Nginx(:2026)
#       --dev 模式下 Gateway 开了热重载，改 .yaml 自动重启
dev:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev

# Start all services in production mode (with optimizations)
# yyds: make start → 生产模式启动（前端用 preview 而非 dev，没有热重载）
start:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --prod

# Start all services in daemon mode (background)
# yyds: make dev-daemon → 后台启动，不占终端（用 nohup 跑）
dev-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev --daemon

# Start prod services in daemon mode (background)
start-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --prod --daemon

# Stop all services
# yyds: make stop → 杀掉所有 DeerFlow 进程（uvicorn + next + nginx）
stop:
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --stop

# Clean up
# yyds: make clean → 先 stop，再删临时文件（.deer-flow 缓存、日志等）
#       - 前缀表示"忽略错误继续"（文件可能不存在）
clean: stop
	@echo "Cleaning up..."
	@-rm -rf backend/.deer-flow 2>/dev/null || true
	@-rm -rf backend/.langgraph_api 2>/dev/null || true
	@-rm -rf logs/*.log 2>/dev/null || true
	@echo "✓ Cleanup complete"

# ==========================================
# Docker Development Commands
# yyds: ─── Docker 开发模式 ────────────────────────────────
# 和 make dev 的区别：所有服务跑在 Docker 容器里，不依赖本地安装
# ==========================================

# Initialize Docker containers and install dependencies
# yyds: make docker-init → 预拉沙箱镜像（首次用 Docker 模式前执行一次）
docker-init:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh init

# Start Docker development environment
# yyds: make docker-start → 启动 Docker 开发环境（frontend + gateway + nginx 容器）
docker-start:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh start

# Stop Docker development environment
docker-stop:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh stop

# View Docker development logs
# yyds: make docker-logs → 实时查看所有容器日志（Ctrl+C 退出）
docker-logs:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs

# View Docker development logs
# yyds: 按服务过滤日志
docker-logs-frontend:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs --frontend
docker-logs-gateway:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs --gateway

# ==========================================
# Production Docker Commands
# yyds: ─── Docker 生产部署 ────────────────────────────────
# 用 docker-compose.yaml（不是 dev 版本），适合部署到服务器
# ==========================================

# Build and start production services
# yyds: make up → 构建镜像 + 启动生产容器（localhost:2026）
up:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh

# Stop and remove production containers
# yyds: make down → 停止并删除生产容器
down:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh down
