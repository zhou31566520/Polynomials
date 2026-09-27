#!/usr/bin/env python3
"""
P2P WebSocket 信令服务器 —— 给浏览器 WebRTC 客户端用
功能: 只转发 SDP offer/answer 和 ICE candidates，不转发业务消息
依赖: 仅 Python 标准库（socket / threading / hashlib / base64 / json）
启动: python p2p_ws_server.py  (默认监听 0.0.0.0:8889)
"""

import socket
import threading
import hashlib
import base64
import json
import time
import uuid
import sys
import os

# ============ 配置 ============
WS_HOST = "0.0.0.0"
WS_PORT = 8889
HEARTBEAT_TIMEOUT = 30   # 秒
HEARTBEAT_INTERVAL = 10   # 客户端心跳间隔（服务器检查用）


# ============ 日志 ============
class Log:
    def __init__(self, f="log_ws.txt"):
        self.f = f
        self._l = __import__("logging").getLogger("ws-server")
        self._l.setLevel(__import__("logging").INFO)
        if not self._l.handlers:
            fmt = __import__("logging").Formatter(
                "%(asctime)s - %(levelname)s - %(message)s"
            )
            fh = __import__("logging").FileHandler(f, encoding="utf-8")
            sh = __import__("logging").StreamHandler(sys.stdout)
            fh.setFormatter(fmt); sh.setFormatter(fmt)
            self._l.addHandler(fh); self._l.addHandler(sh)
            self._l.propagate = False
    def info(self, m):   self._l.info(m)
    def warn(self, m):   self._l.warning(m)
    def error(self, m):  self._l.error(m)

log = Log()


# ============ WebSocket 协议 (RFC 6455) ============

WS_OPCODES = {
    0x1: "text",   0x2: "binary",
    0x8: "close",  0x9: "ping",  0xA: "pong",
}


def ws_handshake(conn):
    """完成 WebSocket 握手 —— 返回 True 表示成功"""
    import select

    # 设置短超时快速判断是不是 HTTP 请求（健康检查的裸 TCP 会在这里快速返回）
    try:
        conn.settimeout(3.0)
    except Exception:
        pass

    # 读 HTTP upgrade 请求
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                log.warn(f"握手: 连接关闭（未收到数据），前 {len(data)} 字节: {data[:80]}")
                return False
            data += chunk
            if len(data) > 8192:
                log.warn("握手: HTTP 头过大")
                return False
    except socket.timeout:
        log.warn(f"握手: 3秒内没收到 HTTP 请求（可能是健康检查探测），已收到 {len(data)} 字节")
        return False

    text = data.decode("utf-8", errors="replace")
    # 只打印前两行（方法 + Host），不泄露 Key
    # ★ dump 全部 HTTP 头
    log.info(f"握手: ====== 完整 HTTP 请求 =====")
    for line in text.split("\r\n")[:20]:
        if "sec-websocket-key" in line.lower():
            k = line.split(":", 1)[1].strip()
            log.info(f"  {line.split(':', 1)[0]}: {k[:8]}...")
        else:
            log.info(f"  {line}")
    log.info(f"握手: =========================")
    if "upgrade: websocket" not in text.lower():
        log.warn(f"握手: 缺少 Upgrade: websocket 头")
        return False

    # 提取 Sec-WebSocket-Key
    key = None
    for line in text.split("\r\n"):
        if line.lower().startswith("sec-websocket-key:"):
            key = line.split(":", 1)[1].strip()
            break
    if not key:
        log.warn(f"握手: 缺少 Sec-WebSocket-Key 头")
        return False

    log.info(f"握手: Key={key[:8]}...  准备 Sec-WebSocket-Accept")

    # 计算 handshake response
    magic = key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
    accept = base64.b64encode(
        hashlib.sha1(magic.encode("utf-8")).digest()
    ).decode("utf-8")

    response = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    try:
        conn.sendall(response.encode("utf-8"))
    except Exception as e:
        log.warn(f"握手: 发送响应失败: {e}")
        return False

    # 恢复原超时（handle_client 后面还要用）
    try:
        conn.settimeout(None)
    except Exception:
        pass

    log.info(f"握手: ✅ 成功")
    return True


def ws_recv_frame(conn):
    """接收一个 WebSocket 帧 —— 返回 (opcode, payload_bytes)，异常则返回 None"""
    # 读头
    header = b""
    while len(header) < 2:
        chunk = conn.recv(2 - len(header))
        if not chunk:
            return None
        header += chunk

    b0, b1 = header[0], header[1]
    fin = (b0 & 0x80) != 0
    opcode = b0 & 0x0F
    masked = (b1 & 0x80) != 0
    length = b1 & 0x7F

    if length == 126:
        header += conn.recv(2)
        length = int.from_bytes(header[2:4], "big")
    elif length == 127:
        header += conn.recv(8)
        length = int.from_bytes(header[2:10], "big")

    # mask key
    mask_key = b""
    if masked:
        while len(mask_key) < 4:
            chunk = conn.recv(4 - len(mask_key))
            if not chunk:
                return None
            mask_key += chunk

    # payload
    payload = b""
    remaining = length
    while remaining > 0:
        chunk = conn.recv(min(4096, remaining))
        if not chunk:
            return None
        payload += chunk
        remaining -= len(chunk)

    # 解 mask
    if masked:
        payload = bytes(
            b ^ mask_key[i % 4] for i, b in enumerate(payload)
        )

    return opcode, payload


def ws_send_frame(conn, opcode, payload_bytes):
    """发送一个 WebSocket 帧（服务器→客户端，不 mask）"""
    frame = bytearray()
    # FIN + opcode
    frame.append(0x80 | (opcode & 0x0F))
    # length
    n = len(payload_bytes)
    if n < 126:
        frame.append(n)
    elif n < 65536:
        frame.append(126)
        frame += n.to_bytes(2, "big")
    else:
        frame.append(127)
        frame += n.to_bytes(8, "big")
    frame += payload_bytes
    conn.sendall(bytes(frame))


def ws_send_text(conn, text):
    """发送文本帧"""
    ws_send_frame(conn, 0x1, text.encode("utf-8"))


def ws_send_json(conn, obj):
    """发送 JSON"""
    ws_send_text(conn, json.dumps(obj))


# ============ Peer 管理 ============

class Peer:
    """一个在线 WebSocket 客户端"""
    def __init__(self, peer_id, conn, addr):
        self.peer_id = peer_id
        self.conn = conn
        self.addr = addr
        self.name = f"peer-{peer_id[:6]}"
        self.last_activity = time.time()
        self.lock = threading.Lock()


peers = {}          # peer_id -> Peer
peers_lock = threading.Lock()
connected_clients = {}   # conn.fileno() -> peer_id （用于快速查找关闭的连接）


def broadcast_to_all(msg_json, exclude=None):
    """给所有在线 peer 发一条 JSON"""
    with peers_lock:
        targets = list(peers.values())
    for p in targets:
        if exclude and p.peer_id == exclude:
            continue
        try:
            with p.lock:
                ws_send_json(p.conn, msg_json)
        except Exception:
            pass


def send_to(peer_id, msg_json):
    """给指定 peer_id 发一条 JSON"""
    with peers_lock:
        p = peers.get(peer_id)
    if not p:
        return False
    try:
        with p.lock:
            ws_send_json(p.conn, msg_json)
        return True
    except Exception:
        return False


# ============ 客户端处理 ============

def handle_client(conn, addr):
    """处理一个 WebSocket 客户端连接的主循环"""
    peer_id = None
    try:
        # WebSocket 握手
        if not ws_handshake(conn):
            log.warn(f"握手失败: {addr}")
            return
        conn.settimeout(HEARTBEAT_TIMEOUT + 5)

        # 接收帧
        while True:
            try:
                result = ws_recv_frame(conn)
            except socket.timeout:
                log.warn(f"超时断开 {peer_id or addr}")
                break
            except OSError:
                break

            if result is None:
                break

            opcode, payload = result

            if opcode == 0x8:   # close
                break
            elif opcode == 0x9:  # ping
                ws_send_frame(conn, 0xA, payload)
                continue
            elif opcode in (0x1, 0x2):
                pass  # 正常业务帧
            else:
                continue

            # 解析 JSON
            try:
                msg = json.loads(payload.decode("utf-8"))
            except Exception:
                continue

            # 心跳
            if msg.get("type") == "ping":
                if peer_id:
                    with peers_lock:
                        p = peers.get(peer_id)
                    if p:
                        p.last_activity = time.time()
                send_to(peer_id, {"type": "pong"})
                continue

            # 注册 (第一条消息必须是 register)
            if msg.get("type") == "register":
                if peer_id:
                    continue  # 已注册就忽略
                name = msg.get("name") or f"peer-{uuid.uuid4().hex[:6]}"
                peer_id = uuid.uuid4().hex[:8]
                p = Peer(peer_id, conn, addr)
                p.name = name
                with peers_lock:
                    peers[peer_id] = p
                    connected_clients[conn.fileno()] = peer_id
                log.info(f"【新客户端】{name}({peer_id}) 来自 {addr}")
                # 确认注册成功
                ws_send_json(conn, {
                    "type": "connected",
                    "peer_id": peer_id,
                    "name": name,
                })
                # 通知其他人：有新 peer 上线
                broadcast_to_all({
                    "type": "peer_joined",
                    "peer": {"peer_id": peer_id, "name": name},
                }, exclude=peer_id)
                # 告诉新 peer 当前有哪些人
                with peers_lock:
                    peer_list = [
                        {"peer_id": pid, "name": p.name}
                        for pid, p in peers.items()
                    ]
                ws_send_json(conn, {"type": "peers", "peers": peer_list})
                continue

            # 下面的都必须已注册
            if not peer_id:
                continue

            # 请求 peer 列表
            if msg.get("type") == "list":
                with peers_lock:
                    peer_list = [
                        {"peer_id": pid, "name": p.name}
                        for pid, p in peers.items()
                    ]
                send_to(peer_id, {"type": "peers", "peers": peer_list})
                continue

            # 转发 WebRTC 信令（offer / answer / ice_candidate）
            if msg.get("type") == "signal":
                target = msg.get("to")
                payload = msg.get("payload")
                if not target or not payload:
                    continue
                forwarded = {
                    "type": "signal",
                    "from": peer_id,
                    "from_name": peers.get(peer_id, Peer("", conn, addr)).name,
                    "to": target,
                    "payload": payload,
                }
                if not send_to(target, forwarded):
                    # 目标不在线，告诉发送方
                    send_to(peer_id, {
                        "type": "signal_error",
                        "to": target,
                        "msg": "target offline",
                    })
                continue

            # leave
            if msg.get("type") == "leave":
                break

    except Exception as e:
        log.error(f"handle_client 异常 {addr}: {e}")
    finally:
        # 清理
        if peer_id:
            with peers_lock:
                peers.pop(peer_id, None)
                connected_clients.pop(conn.fileno(), None)
            # 通知其他人
            broadcast_to_all({
                "type": "peer_left",
                "peer_id": peer_id,
            })
            log.info(f"【客户端下线】{peer_id}")
        try:
            conn.close()
        except Exception:
            pass


# ============ 超时清理线程 ============

def cleanup_loop():
    while True:
        time.sleep(5)
        now = time.time()
        stale = []
        with peers_lock:
            for pid, p in list(peers.items()):
                if now - p.last_activity > HEARTBEAT_TIMEOUT:
                    stale.append(pid)
        for pid in stale:
            log.warn(f"【超时清理】{pid}")
            with peers_lock:
                p = peers.pop(pid, None)
            if p:
                try:
                    with p.lock:
                        p.conn.close()
                except Exception:
                    pass
            broadcast_to_all({"type": "peer_left", "peer_id": pid})


# ============ 入口 ============

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def main():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((WS_HOST, WS_PORT))
    server_sock.listen(64)
    server_sock.settimeout(1.0)

    threading.Thread(target=cleanup_loop, daemon=True).start()

    log.info("=" * 60)
    log.info(f"WebSocket 信令服务器启动")
    log.info(f"监听: ws://{get_local_ip()}:{WS_PORT}")
    log.info(f"超时: {HEARTBEAT_TIMEOUT}s | 清理: 每 5s")
    log.info("=" * 60)

    try:
        while True:
            try:
                conn, addr = server_sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                threading.Thread(
                    target=handle_client, args=(conn, addr), daemon=True
                ).start()
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                break
    finally:
        server_sock.close()
        log.info("服务器关闭")


if __name__ == "__main__":
    main()