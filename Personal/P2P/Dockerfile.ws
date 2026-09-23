# ============================================================
# P2P WebSocket 信令服务器镜像 (p2p_ws_server.py)
# 单文件 + Python 标准库，零 pip 依赖
# 给浏览器 WebRTC 客户端做 SDP/ICE 信令转发
# ============================================================

# ---- 阶段 1：builder（纯净拷贝）----
FROM python:3.13-slim AS builder
WORKDIR /build
COPY p2p_ws_server.py .

# ---- 阶段 2：运行时 ----
FROM python:3.13-slim

ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata ca-certificates \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
 && echo $TZ > /etc/timezone \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /build/p2p_ws_server.py /app/p2p_ws_server.py

# WebSocket 信令端口
EXPOSE 8889

# 健康检查：检查 Python 进程是否存活 + 端口在 LISTEN
HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=3s \
    CMD python -c "import socket; s=socket.socket(); s.settimeout(1); r=s.connect_ex(('127.0.0.1',8889)); s.close(); exit(0 if r==0 else 1)"

CMD ["python", "-u", "p2p_ws_server.py"]