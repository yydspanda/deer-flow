#!/usr/bin/env bash
#
# start-daemon.sh — Start DeerFlow in daemon (background) mode
#
# yyds: 后台启动模式，就是 serve.sh --dev --daemon 的薄包装。
#       所有进程用 nohup 跑在后台，启动完脚本就退出。
#       保留是为了向后兼容。
#

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec "$REPO_ROOT/scripts/serve.sh" --dev --daemon "$@"
