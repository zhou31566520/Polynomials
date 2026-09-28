#!/usr/bin/env bash
# ============================================================
# 增量升级: 拉代码 + 重启服务
# ============================================================
set -euo pipefail
RED='\033[0;31m'; GRN='\033[0;32m'; NC='\033[0m'
info() { echo -e "${GRN}[INFO]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="/opt/P2P"

cd "${SCRIPT_DIR}"

echo ""
info "=== P2P Chat 增量升级 ==="
echo ""

# 1. 拉代码
if [[ -d ".git" ]]; then
    info "git pull..."
    git pull --rebase || warn "git pull 冲突, 手动解决"
fi

# 2. 同步到 /opt/P2P
if [[ "${SCRIPT_DIR}" != "${DEPLOY_DIR}" ]]; then
    info "rsync 同步到 ${DEPLOY_DIR}..."
    rsync -a --delete \
        --exclude='.git' --exclude='data' --exclude='__pycache__' \
        "${SCRIPT_DIR}/" "${DEPLOY_DIR}/"
    chown -R p2p:p2p "${DEPLOY_DIR}"
    chown -R p2p:p2p "${DEPLOY_DIR}/data"
fi

# 3. 重启服务
info "重启 p2p-ws..."
systemctl restart p2p-ws || { echo "p2p-ws 重启失败! 看日志: journalctl -u p2p-ws"; exit 1; }
sleep 2
systemctl restart p2p-web

# 4. 热重载 nginx (如果配置变了)
nginx -t 2>/dev/null && systemctl reload nginx || true

echo ""
info "✅ 升级完成!"
systemctl --no-pager --full status p2p-ws p2p-web nginx | head -15
echo ""
