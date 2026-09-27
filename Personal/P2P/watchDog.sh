#!/bin/bash
# ============================================================
# P2P Watchdog（智能版）
# 逻辑：git fetch → 对比 HEAD → 有变更才 rebuild
#       → rebuild 也只 build 有变化的服务，不碰无关容器
# 用法：crontab 里写  */2 * * * * /root/Polynomials/Personal/P2P/watchDog.sh
# ============================================================

# ---------- 基础配置 ----------
REPO_DIR="/root/Polynomials/Personal/P2P"
BRANCH="master"
LOG_FILE="${REPO_DIR}/watchdog.log"
LOCK_FILE="/tmp/p2p_watchdog.lock"

# 这些文件变更才触发 rebuild（和 p2p_watchdog.py 的 WATCH_FILES 保持一致）
WATCH_PATHS=(
    "P2P/p2p_ws_server.py"
    "P2P/p2p_web.html"
    "P2P/Dockerfile.ws"
    "P2P/Dockerfile.web"
    "P2P/docker-compose.yml"
    "P2P/nginx.conf"
    "P2P/Dockerfile.server"
)

# ---------- 日志 ----------
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# ---------- 防并发 ----------
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log "⚠️ 上一次 watchdog 还在跑，跳过本次"
    exit 0
fi

cd "$REPO_DIR" || { log "❌ 目录不存在: $REPO_DIR"; exit 1; }

log "========================================="
log "🐕 Watchdog 启动"

# ---------- Step 1: fetch 远端 ----------
git fetch origin "$BRANCH" 2>>"$LOG_FILE" || {
    log "❌ git fetch 失败"
    exit 1
}

LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_HEAD=$(git rev-parse "origin/${BRANCH}")

log "  本地 HEAD : ${LOCAL_HEAD:0:8}"
log "  远端 HEAD : ${REMOTE_HEAD:0:8}"

# ---------- Step 2: 没变更 → 直接退出 ----------
if [ "$LOCAL_HEAD" = "$REMOTE_HEAD" ]; then
    log "✅ 代码无变更，跳过 rebuild"

    # 顺便做个健康检查：确保所有容器在运行
    DOWN=$(docker compose ps --status exited --format json 2>/dev/null)
    if [ -n "$DOWN" ]; then
        log "⚠️ 发现已停止的容器，启动它..."
        docker compose up -d 2>>"$LOG_FILE"
    fi

    log "👋 Watchdog 退出"
    exit 0
fi

# ---------- Step 3: 有变更 → 看变更是否命中我们关心的文件 ----------
CHANGED_FILES=$(git diff --name-only "$LOCAL_HEAD" "$REMOTE_HEAD")
log "📋 变更文件列表:"
echo "$CHANGED_FILES" | while read -r f; do log "    $f"; done

REBUILD_NEEDED=false
AFFECTED_SERVICES=()

for f in $CHANGED_FILES; do
    for watch in "${WATCH_PATHS[@]}"; do
        if [[ "$f" == *"$watch"* ]] || [[ "$f" == "$watch" ]]; then
            REBUILD_NEEDED=true
            # 推断影响哪个服务
            case "$f" in
                *p2p_ws_server.py*|*Dockerfile.ws*)
                    AFFECTED_SERVICES+=("p2p-ws-server") ;;
                *p2p_web.html*|*Dockerfile.web*|*nginx.conf*)
                    AFFECTED_SERVICES+=("p2p-web") ;;
                *p2pserver.py*|*Dockerfile.server*)
                    AFFECTED_SERVICES+=("p2p-server") ;;
                *docker-compose.yml*)
                    # compose 改了保险起见 rebuild 全部
                    AFFECTED_SERVICES=("p2p-ws-server" "p2p-web" "p2p-server") ;;
            esac
            break
        fi
    done
done

# 去重
AFFECTED_SERVICES=($(echo "${AFFECTED_SERVICES[@]}" | tr ' ' '\n' | sort -u | tr '\n' ' '))

if [ "$REBUILD_NEEDED" = false ]; then
    log "✅ 变更不涉及 P2P 相关文件，跳过 rebuild"
    git pull --ff-only 2>>"$LOG_FILE"
    log "👋 Watchdog 退出"
    exit 0
fi

# ---------- Step 4: pull + 增量 rebuild 受影响的服务 ----------
log "🔧 涉及服务: ${AFFECTED_SERVICES[*]}"
log "⬇️  git pull --ff-only"

git pull --ff-only 2>>"$LOG_FILE" || {
    log "❌ git pull 失败"
    exit 1
}

log "🚀 开始 rebuild（仅受影响的服务）..."

for svc in "${AFFECTED_SERVICES[@]}"; do
    log "  ⏳ docker compose build $svc"
    docker compose build "$svc" 2>>"$LOG_FILE" || {
        log "❌ build $svc 失败，但不中断其他服务"
    }
done

log "  ⏳ docker compose up -d ${AFFECTED_SERVICES[*]}"
docker compose up -d ${AFFECTED_SERVICES[@]} 2>>"$LOG_FILE" || {
    log "❌ up 失败"
    exit 1
}

log "✅ 部署完成！"
log "👋 Watchdog 退出"