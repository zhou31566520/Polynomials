#!/usr/bin/env bash
# ============================================================
# P2P Chat 一键部署脚本 (纯系统级, 不用 Docker)
#   - 系统 nginx 反代 (80/443)
#   - systemd 守护后端 python + 静态文件
#   - 自动 HTTPS (certbot)
#
# 用法:
#   sudo ./deploy.sh              # 首次部署 (纯 HTTP)
#   sudo ./deploy.sh p2p.xxx.com  # 部署 + 申请 HTTPS 证书
#   sudo ./deploy.sh upgrade      # 升级 (拉代码 + 重启)
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info() { echo -e "${BLU}[INFO]${NC} $*"; }
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
warn() { echo -e "${YEL}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DEPLOY_DIR="/opt/P2P"
DOMAIN="${1:-}"
ACTION="${2:-deploy}"

echo ""
echo "=============================================="
echo "  P2P Chat Deployer (System Edition)"
echo "=============================================="
echo ""

# ---------- 0. 必须 root ----------
if [[ $EUID -ne 0 ]]; then
    err "请用 sudo 或 root 运行: sudo $0 $*"
fi

# ---------- 1. 基础检查 & 安装 ----------
info "检查系统环境..."

# 1a. Python3
if ! command -v python3 &>/dev/null; then
    info "安装 python3..."
    apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv
fi
ok "Python3 $(python3 --version | awk '{print $2}')"

# 1b. Nginx
if ! command -v nginx &>/dev/null; then
    info "安装 nginx..."
    apt-get update -qq && apt-get install -y -qq nginx
    systemctl enable nginx
fi
ok "Nginx $(nginx -v 2>&1 | awk -F/ '{print $2}')"

# 1c. Certbot (可选, HTTPS 用)
if [[ -n "$DOMAIN" ]]; then
    if ! command -v certbot &>/dev/null; then
        info "安装 certbot + python3-certbot-nginx..."
        apt-get update -qq && apt-get install -y -qq certbot python3-certbot-nginx
    fi
    ok "Certbot OK"
fi

# ---------- 2. 部署代码 ----------
info "部署代码到 ${DEPLOY_DIR}..."
mkdir -p "${DEPLOY_DIR}"

if [[ -d "${SCRIPT_DIR}/.git" ]]; then
    # 有 git 仓库 → 用 rsync 同步 (排除 .git)
    rsync -a --delete \
        --exclude='.git' \
        --exclude='data' \
        --exclude='__pycache__' \
        --exclude='*.pyc' \
        --exclude='deploy' \
        "${SCRIPT_DIR}/" "${DEPLOY_DIR}/"
    ok "rsync 同步完成"
else
    # 非 git → 直接拷贝
    cp -rf "${SCRIPT_DIR}"/* "${DEPLOY_DIR}/" 2>/dev/null || true
    ok "文件拷贝完成"
fi

# 数据目录
mkdir -p "${DEPLOY_DIR}/data"

# ---------- 3. 创建 p2p 用户 ----------
if ! id -u p2p &>/dev/null; then
    info "创建系统用户 p2p..."
    useradd --system --no-create-home --shell /usr/sbin/nologin p2p
fi
chown -R p2p:p2p "${DEPLOY_DIR}"
chown -R p2p:p2p "${DEPLOY_DIR}/data"
ok "用户 p2p 就绪"

# ---------- 4. 安装 systemd services ----------
info "安装 systemd services..."

cp -f "${SCRIPT_DIR}/deploy/systemd/p2p-ws.service"  /etc/systemd/system/
cp -f "${SCRIPT_DIR}/deploy/systemd/p2p-web.service" /etc/systemd/system/

# 替换 WorkingDirectory (以防改了部署路径)
sed -i "s|/opt/P2P|${DEPLOY_DIR}|g" /etc/systemd/system/p2p-ws.service
sed -i "s|/opt/P2P|${DEPLOY_DIR}|g" /etc/systemd/system/p2p-web.service

systemctl daemon-reload

# ---------- 5. 启动后端服务 ----------
info "启动 p2p-ws (WebSocket 信令 + HTTP API)..."
systemctl enable p2p-ws
systemctl restart p2p-ws
sleep 2

if systemctl is-active --quiet p2p-ws; then
    ok "p2p-ws 运行中 ✅"
else
    err "p2p-ws 启动失败! 查看: journalctl -u p2p-ws -n 30"
fi

info "启动 p2p-web (静态文件服务器)..."
systemctl enable p2p-web
systemctl restart p2p-web
sleep 1

if systemctl is-active --quiet p2p-web; then
    ok "p2p-web 运行中 ✅"
else
    err "p2p-web 启动失败! 查看: journalctl -u p2p-web -n 30"
fi

# ---------- 6. 配置 Nginx ----------
info "配置 Nginx..."

# 备份旧配置
if [[ -f /etc/nginx/sites-enabled/default ]]; then
    rm -f /etc/nginx/sites-enabled/default
fi

# 拷贝站点配置
cp -f "${SCRIPT_DIR}/deploy/nginx/p2p.conf" /etc/nginx/sites-available/p2p
ln -sf /etc/nginx/sites-available/p2p /etc/nginx/sites-enabled/p2p

# 测试 nginx 配置
if ! nginx -t 2>&1; then
    err "Nginx 配置测试失败! 检查 /etc/nginx/sites-available/p2p"
fi

systemctl enable nginx
systemctl reload nginx
ok "Nginx 配置 OK, 已 reload"

# ---------- 7. HTTPS (可选) ----------
if [[ -n "$DOMAIN" ]]; then
    info "申请 Let's Encrypt 证书 (域名=${DOMAIN})..."

    mkdir -p /var/www/certbot
    certbot certonly --webroot \
        -w /var/www/certbot \
        -d "${DOMAIN}" \
        --email "admin@${DOMAIN}" \
        --agree-tos \
        --no-eff-email \
        --non-interactive \
        --keep-until-expiring 2>&1

    ok "证书已申请! 路径: /etc/letsencrypt/live/${DOMAIN}/"

    # 自动配置 HTTPS
    certbot --nginx -d "${DOMAIN}" --non-interactive 2>&1 || true
    systemctl reload nginx
fi

# ---------- 8. 健康检查 ----------
echo ""
info "健康检查..."
sleep 2

WS_OK=false
API_OK=false
for i in $(seq 1 10); do
    if curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8889 2>/dev/null | grep -qE "101|400|426"; then
        WS_OK=true
    fi
    if curl -s http://127.0.0.1:8890/peers 2>/dev/null | grep -q "peers"; then
        API_OK=true
    fi
    if $WS_OK && $API_OK; then break; fi
    sleep 1
done

$WS_OK   && ok "WebSocket 后端 (8889) OK"   || warn "WebSocket 后端未响应"
$API_OK  && ok "HTTP API 后端 (8890) OK"    || warn "HTTP API 后端未响应"

NGINX_OK=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1/ 2>/dev/null || echo "000")
[[ "$NGINX_OK" =~ ^(200|301|302)$ ]] && ok "Nginx 前端 (80) OK [HTTP ${NGINX_OK}]" || warn "Nginx 前端响应码: ${NGINX_OK}"

# ---------- 9. 打印结果 ----------
HOST_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[[ -z "$HOST_IP" ]] && HOST_IP=$(curl -s --connect-timeout 3 ifconfig.me 2>/dev/null || echo "YOUR_SERVER_IP")

echo ""
echo "=============================================="
echo -e "  ${GRN}✅ 部署完成!${NC}"
echo "=============================================="
echo ""

if [[ -n "$DOMAIN" ]]; then
    echo "  访问地址:  https://${DOMAIN}/"
else
    echo -e "  ${YEL}临时 HTTP 模式${NC}"
    echo "  访问地址:  http://${HOST_IP}/"
    echo ""
    echo "  Chrome 下请用 workaround:"
    echo "  chrome://flags/#unsafely-treat-insecure-origin-as-secure"
    echo "  添加: http://${HOST_IP}"
fi

echo ""
echo "  服务状态:"
systemctl is-active p2p-ws   | awk '{printf "    p2p-ws:    %s\n", $0}'
systemctl is-active p2p-web  | awk '{printf "    p2p-web:   %s\n", $0}'
systemctl is-active nginx    | awk '{printf "    nginx:     %s\n", $0}'

echo ""
echo "  常用命令:"
echo "    sudo systemctl status p2p-ws p2p-web nginx"
echo "    sudo journalctl -u p2p-ws -f"
echo "    sudo journalctl -u p2p-web -f"
echo "    sudo nginx -t && sudo systemctl reload nginx"
echo "    sudo ${SCRIPT_DIR}/deploy.sh upgrade          # 升级"
echo ""
