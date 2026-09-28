# P2P Chat 公网部署手册 (阿里云版)

## 架构总览

```
用户浏览器 (公网任意地方)
        │
        ▼
  阿里云 ECS  (只暴露 80 + 443)
        │
   ┌────┴─────────────────────────────┐
   │                                  │
 Nginx (:80/:443)                    │
   │  SSL 终止                       │
   │  /            →  p2p-web:80      │
   │  /ws/         →  p2p-ws:8889     │
   │  /api/        →  p2p-ws:8890     │
   │                                  │
   └──────────────────────────────────┘
        │
   Docker 内部网络 p2p-net
        │
   p2p-ws-server (数据持久化到 ./data)
```

**对外只开 2 个端口: 80(HTTP) + 443(HTTPS)**

---

## 第一步: 阿里云准备

### 1.1 买 ECS (推荐最低配就够用)

| 项目 | 推荐值 |
|------|--------|
| 实例规格 | 1 核 2G 即可 (t5 或 ecs.t6) |
| 系统盘 | 40GB SSD |
| 操作系统 | **Ubuntu 22.04 LTS** 或 CentOS 8 |
| 地域 | 离主要用户近的 (华东/华南) |
| 公网 IP | 必须勾选 ✅ |
| 带宽 | 按用户数选, 30 个并发 5Mbps 够了 |

### 1.2 配安全组 (关键!)

阿里云控制台 → ECS → 安全组 → 入方向规则, 添加:

| 协议 | 端口 | 来源 | 说明 |
|------|------|------|------|
| TCP  | 22   | 你的 IP | SSH 登录 (强烈建议限制来源!) |
| TCP  | 80   | 0.0.0.0/0 | HTTP (Nginx 用) |
| TCP  | 443  | 0.0.0.0/0 | HTTPS (Nginx 用) |

**❌ 不要**开放 8889/8890/8080 这些后端端口! Nginx 反代就够了。

### 1.3 配域名 (强烈推荐)

没有域名 → Chrome 下 WebRTC 会被拦 → 买一个, .com 域名 ~60 元/年

1. 阿里云买域名 (或 GoDaddy/Cloudflare)
2. 域名控制台 → DNS 解析 → 添加 A 记录:
   - 主机记录: `p2p`
   - 记录类型: `A`
   - 记录值: 你的 ECS 公网 IP
3. 等 DNS 生效 (几分钟到几小时不等)

---

## 第二步: ECS 初始化

```bash
# 1. SSH 登录
ssh root@你的ECS_IP

# 2. 安装 Docker + Docker Compose (一键脚本)
curl -fsSL https://get.docker.com | bash
systemctl enable docker
systemctl start docker

# Docker Compose v2 (新版自带, 不用单独装)
docker compose version   # 确认能输出版本
```

---

## 第三步: 上传代码

```bash
# 方式 A: 用 git (推荐, 方便 update.sh)
cd /opt
git clone 你的仓库地址 P2P
cd P2P

# 方式 B: 本地打包上传
# 在你电脑上:
#   tar czf p2p.tar.gz P2P/
#   scp p2p.tar.gz root@ECS_IP:/opt/
# 然后在 ECS 上:
#   cd /opt && tar xzf p2p.tar.gz && cd P2P

# 方式 C: 直接传文件 (用 WinSCP / FileZilla)
# 把整个 P2P 目录传到 ECS 的 /opt/P2P
```

---

## 第四步: 部署 (3 种方式)

### 方式 A: HTTPS + 域名 (最正规 ✅)

```bash
cd /opt/P2P

# 1. 一键申请 SSL 证书 (需要域名已解析到 ECS)
chmod +x setup-cert.sh deploy.sh update.sh
./setup-cert.sh p2p.yourdomain.com

# 2. 一键部署 (自动检测到证书, 用 HTTPS)
./deploy.sh
```

### 方式 B: HTTPS + 自签名证书 (临时测试)

```bash
cd /opt/P2P
mkdir -p nginx/certs

# 生成自签名证书 (会有浏览器警告, 点"继续访问"即可)
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
    -keyout nginx/certs/privkey.pem \
    -out    nginx/certs/fullchain.pem \
    -subj "/CN=你的ECS公网IP"

# 把 nginx 配置里的 SERVER_NAME 改成 IP
sed -i "s/SERVER_NAME/$(curl -s ifconfig.me)/g" nginx/sites-enabled/p2p.conf

./deploy.sh
```

### 方式 C: 纯 HTTP (临时测试, Chrome 下 WebRTC 会被拦)

```bash
cd /opt/P2P
./deploy.sh http
```

Chrome 下用这个 workaround 来跑 WebRTC:
```
1. 地址栏输 chrome://flags/#unsafely-treat-insecure-origin-as-secure
2. Enable 开关打开
3. 添加: http://你的ECS_IP
4. 重启浏览器
```

---

## 第五步: 验证

部署完脚本会输出访问地址, 打开浏览器:

```
# 检查 Nginx 反代
curl http://localhost/          # 应该返回 p2p_web.html
curl http://localhost/ws/       # 应该返回 101 Switching Protocols 或错误 (正常, WS 握手需要客户端)
curl http://localhost/api/peers # 应该返回 JSON {"peers": []}

# 检查容器状态
docker compose ps               # 所有服务应该是 Up (healthy)
docker compose logs -f nginx    # 看 nginx 日志
docker compose logs -f p2p-ws-server # 看后端日志
```

---

## 日常运维

```bash
cd /opt/P2P

# 更新代码 (拉 git + 重建 + 重启, 数据不丢)
./update.sh

# 看实时日志
docker compose logs -f

# 重启全部
./deploy.sh

# 停掉
docker compose down

# 停掉并清数据 (⚠️ 慎用!)
docker compose down -v
rm -rf ./data          # 数据库也没了
```

---

## 常见问题

### Q1: Chrome 控制台报 "getUserMedia" 或 WebRTC 权限错误
→ **必须 HTTPS!** Chrome 只在 HTTPS 或 localhost 下允许 WebRTC。用方式 A 或 B。

### Q2: P2P 连接失败 (信令能连上但 DataChannel open 不了)
→ 两个公网用户之间可能是对称型 NAT。当前只有 STUN, 没有 TURN, 穿透率 ~30%。
→ 解决: 后面加 Coturn TURN 服务器 (见下方扩展)

### Q3: 登录后被踢下线
→ 服务端 token 24h 有效, 但如果 WS 被阿里云 LB/防火墙断开 (超时 < 24h), 需要重连。
→ 当前已用 2s 自动重连, 应该无感。

### Q4: 改前端后怎么生效?
```bash
./update.sh   # 自动重建 p2p-web + 热重载 nginx
```

### Q5: 数据会丢吗?
→ 用户注册数据存在 `./data/p2p_users.db` (SQLite), 已通过 volume 挂载到宿主机。
→ `docker compose down` 不删 volume, 数据不会丢。
→ 只有 `docker compose down -v` 才会删。

---

## 扩展: 加 Coturn TURN (提升 P2P 成功率)

> 加了 TURN 后, 即使 STUN 穿透失败, P2P 连接也会走 TURN 中继, 穿透率从 ~30% 提到 ~90%。
> 缺点: TURN 中继流量走你的服务器带宽, 1 对中继 = 2 倍流量。

```bash
# docker-compose.yml 加一段:
#   coturn:
#     image: instrumentisto/coturn
#     container_name: p2p-coturn
#     restart: unless-stopped
#     network_mode: host   # TURN 用 host 网络最省事
#     environment:
#       TURN_LISTEN_PORT: 3478
#       TURN_REALM: p2p.yourdomain.com
#       TURN_USER: p2p:your_turn_password_here
#       TURN_EXTERNAL_IP: 你的ECS公网IP
#       TURN_MIN_PORT: 49152
#       TURN_MAX_PORT: 65535
#     volumes:
#       - ./data/coturn:/var/lib/turn

# 阿里云安全组再开:
#   UDP 3478 (TURN)
#   UDP 49152-65535 (TURN relay ports)

# 前端 CONFIG.ICE_SERVERS 加一项:
#   { urls: "turn:p2p.yourdomain.com:3478", username: "p2p", credential: "your_turn_password_here" }
```

---

## 项目文件对照

| 文件 | 作用 | 公网部署需要改? |
|------|------|:---:|
| `docker-compose.yml` | 编排 3 个容器 | ❌ 不用改 |
| `nginx/nginx.conf` | Nginx 主配置 | ❌ 不用改 |
| `nginx/sites-enabled/p2p.conf` | HTTPS 站点配置 | ✅ 改 SERVER_NAME |
| `nginx/sites-enabled/p2p-http-only.conf` | HTTP 临时配置 | ❌ 不用改 |
| `nginx/certs/` | SSL 证书目录 | ✅ 放证书进去 |
| `p2p_web.html` | 前端 (WS/API 自动走 Nginx) | ❌ 已改好 |
| `p2p_ws_server.py` | 信令后端 (端口 8889/8890) | ❌ 不用改 |
| `deploy.sh` | 一键部署 | ❌ 不用改 |
| `setup-cert.sh` | 一键申请 SSL | ✅ 传域名参数 |
| `update.sh` | 增量更新 | ❌ 不用改 |
| `data/` | SQLite DB (volume 持久化) | ❌ 自动创建 |
