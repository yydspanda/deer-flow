#!/usr/bin/env bash
#
# cleanup-containers.sh - Clean up DeerFlow sandbox containers
#
# yyds: 清理沙箱容器。Agent 执行代码时会在隔离容器里跑（安全隔离），
#       这个脚本把遗留的沙箱容器停掉。
#       支持两种容器运行时：Docker（Linux/WSL）和 Apple Container（macOS）。
#       容器名都以 deer-flow-sandbox 为前缀，按前缀过滤批量清理。
#       用法：./cleanup-containers.sh [前缀]  默认前缀是 deer-flow-sandbox
#

set -e

PREFIX="${1:-deer-flow-sandbox}"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo "Cleaning up sandbox containers with prefix: ${PREFIX}"

# Function to clean up Docker containers
# yyds: Docker 清理——查找名字匹配前缀的容器，批量 stop
cleanup_docker() {
    if command -v docker &> /dev/null; then
        echo -n "Checking Docker containers... "
        DOCKER_CONTAINERS=$(docker ps -q --filter "name=${PREFIX}" 2>/dev/null || echo "")

        if [ -n "$DOCKER_CONTAINERS" ]; then
            echo ""
            echo "Found Docker containers to clean up:"
            docker ps --filter "name=${PREFIX}" --format "table {{.ID}}\t{{.Names}}\t{{.Status}}"
            echo "Stopping Docker containers..."
            echo "$DOCKER_CONTAINERS" | xargs docker stop 2>/dev/null || true
            echo -e "${GREEN}✓ Docker containers stopped${NC}"
        else
            echo -e "${GREEN}none found${NC}"
        fi
    else
        echo "Docker not found, skipping..."
    fi
}

# Function to clean up Apple Container containers
# yyds: Apple Container 清理——macOS 上的轻量级容器运行时（不是 Docker）
cleanup_apple_container() {
    if command -v container &> /dev/null; then
        echo -n "Checking Apple Container containers... "

        # List all containers and filter by name
        CONTAINER_LIST=$(container list --format json 2>/dev/null || echo "[]")

        if [ "$CONTAINER_LIST" != "[]" ] && [ -n "$CONTAINER_LIST" ]; then
            # Extract container IDs that match our prefix
            CONTAINER_IDS=$(echo "$CONTAINER_LIST" | python3 -c "
import json
import sys
try:
    containers = json.load(sys.stdin)
    if isinstance(containers, list):
        for c in containers:
            if isinstance(c, dict):
                # Apple Container uses 'id' field which contains the container name
                cid = c.get('configuration').get('id', '')
                if '${PREFIX}' in cid:
                    print(cid)
except:
    pass
" 2>/dev/null || echo "")

            if [ -n "$CONTAINER_IDS" ]; then
                echo ""
                echo "Found Apple Container containers to clean up:"
                echo "$CONTAINER_IDS" | while read -r cid; do
                    echo "  - $cid"
                done

                echo "Stopping Apple Container containers..."
                echo "$CONTAINER_IDS" | while read -r cid; do
                    container stop "$cid" 2>/dev/null || true
                done
                echo -e "${GREEN}✓ Apple Container containers stopped${NC}"
            else
                echo -e "${GREEN}none found${NC}"
            fi
        else
            echo -e "${GREEN}none found${NC}"
        fi
    else
        echo "Apple Container not found, skipping..."
    fi
}

# Clean up both runtimes
cleanup_docker
cleanup_apple_container

echo -e "${GREEN}✓ Container cleanup complete${NC}"
