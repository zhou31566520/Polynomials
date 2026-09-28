#!/usr/bin/env bash
# ============================================================
# 一键申请 SSL 证书 (Let's Encrypt via certbot)
# 用法: ./setup-cert.sh p2p.yourdomain.com
# 需要: 域名已解析到本服务器 IP
# ============================================================
set -e
DOMAIN="${1:?用法: $0 your.domain.com}"

RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; BLU='\033[0;34m'; NC='\033[0m'
info() { echo -e "${BLU}[INFO]${NC} $*"; }
ok()   { echo -e "${GRN}[OK]${NC} $*"; }

cd "$(dirname "$0")"

echo ""
info "为域名 ${DOMAIN} 申请 Let's Encrypt 证书"
echo ""

# 检查 certbot
if ! command -v certbot &>/dev/null; then
    info "安装 certbot..."
    apt-get update -qq && apt-get install -y -qq certbot 2>/dev/null || \
        yum install -y certbot 2>/dev/null || \
        pip install certbot
fi

# 创建 certbot webroot 目录
mkdir -p /var/www/certbot

# 停 nginx (如果在跑)
docker compose stop nginx 2>/dev/null || true

# 申请证书
info "正在申请证书 (http-01 验证)..."
certbot certonly --standalone \
    -d "$DOMAIN" \
    --email "admin@${DOMAIN}" \
    --agree-tos \
    --no-eff-email \
    --non-interactive

# 拷贝到项目目录
CERT_SRC="/etc/letsencrypt/live/${DOMAIN}"
CERT_DST="./nginx/certs"
mkdir -p "$CERT_DST"
cp -f "${CERT_SRC}/fullchain.pem" "${CERT_DST}/"
cp -f "${CERT_SRC}/privkey.pem"   "${CERT_DST}/"

# 改 nginx 配置里的 SERVER_NAME
sed -i "s/SERVER_NAME/${DOMAIN}/g" ./nginx/sites-enabled/p2p.conf

ok "证书已部署到 ${CERT_DST}/"
ok "nginx 配置已更新, 服务器域名=${DOMAIN}"
ok "现在运行 ./deploy.sh https 即可!"
echo ""
echo "⚠️  证书自动续期 (90 天有效期):"
echo "    echo '0 0 * * * certbot renew --quiet' | crontab -"
echo ""
