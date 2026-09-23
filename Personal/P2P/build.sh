# ===== 构建 =====
cd F:\华为家庭存储\workspace\mygit\Personal

# 信令服务器镜像
docker build -f P2P\Dockerfile.server -t p2p-server:latest .

# 客户端镜像
docker build -f P2P\Dockerfile.client -t p2p-client:latest .

# ===== 查看镜像体积 =====
docker images p2p-server p2p-client
# 预期：两个镜像都约 120MB（python:3.13-slim 基础 ~100MB + 代码几乎忽略）

# ===== 运行 =====

# 1) 起信令服务器
docker run -d --name p2p-server \
    -p 8888:8888 \
    -e PORT=8888 \
    p2p-server:latest

# 2) 起客户端 A（后台守护模式，TTY 自动检测）
docker run -d --name p2p-client-a \
    -p 9000:9000 \
    -e NODE_NAME=node-A \
    -e P2P_PORT=9000 \
    -e SIGNAL_SERVER=http://host.docker.internal:8888 \
    -v D:\p2p-downloads-a:/app/downloads \
    p2p-client:latest

# 3) 起客户端 B
docker run -d --name p2p-client-b \
    -p 9001:9000 \
    -e NODE_NAME=node-B \
    -e P2P_PORT=9000 \
    -e SIGNAL_SERVER=http://host.docker.internal:8888 \
    -v D:\p2p-downloads-b:/app/downloads \
    p2p-client:latest

# ===== 观察日志 =====
docker logs -f p2p-server
docker logs -f p2p-client-a
docker logs -f p2p-client-b

# ===== 清理 =====
docker rm -f p2p-server p2p-client-a p2p-client-b
docker rmi p2p-server:latest p2p-client:latest