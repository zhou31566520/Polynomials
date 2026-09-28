#!/usr/bin/env python3
"""
P2P WebRTC 信令服务器（WebSocket）
=========================
新增：注册 / 登录 / Token 校验
- SQLite 存储用户（用户名、昵称唯一）
- password = sha256(sha256(pwd) + salt)
- token = 32 位随机 hex，内存中映射到 peer_id
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
import uuid
import threading
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socketserver

# ==================== 配置 ====================
HOST = "0.0.0.0"
WS_PORT = 8889

# ★ TURN 服务器配置 (从环境变量读, 没配就不启用)
# 静态模式 (long-term credential):
#   TURN_SERVER=turn:your.ecs.ip:3478?transport=tcp
#   TURN_USERNAME=p2p
#   TURN_PASSWORD=YourStrongPassword123
#
# REST API 模式 (临时凭证, 更安全):
#   TURN_SERVER=turn:your.ecs.ip:3478?transport=tcp
#   TURN_SECRET=Coturn fingerprint 密钥
#   (Coturn 配置文件要加 fingerprint)
TURN_SERVER = os.environ.get("TURN_SERVER", "")
TURN_USERNAME = os.environ.get("TURN_USERNAME", "")
TURN_PASSWORD = os.environ.get("TURN_PASSWORD", "")
TURN_SECRET = os.environ.get("TURN_SECRET", "")
TURN_TTL = int(os.environ.get("TURN_TTL", "600"))   # 临时凭证有效期秒

def _gen_turn_credentials():
    """生成 TURN 凭证, 没配置返回 []"""
    if not TURN_SERVER:
        return []
    turn_urls = [TURN_SERVER]
    if TURN_SERVER.startswith("turn:"):
        tls_url = TURN_SERVER.replace("turn:", "turns:").replace(":3478", ":5349").replace("?transport=tcp", "?transport=tls")
        if tls_url != TURN_SERVER:
            turn_urls.append(tls_url)

    if TURN_SECRET:
        # REST API: 临时凭证
        ts = int(time.time()) + TURN_TTL
        username = f"{ts}:p2p"
        mac = hmac.new(TURN_SECRET.encode(), username.encode(), hashlib.sha1).digest()
        password = base64.b64encode(mac).decode()
        return [{"urls": turn_urls, "username": username, "credential": password}]
    elif TURN_USERNAME and TURN_PASSWORD:
        # 静态: long-term
        return [{"urls": turn_urls, "username": TURN_USERNAME, "credential": TURN_PASSWORD}]
    else:
        return []
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
os.makedirs(DATA_DIR, exist_ok=True)   # ★ 确保目录存在
DB_PATH = os.path.join(DATA_DIR, "p2p_users.db")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
log = logging.getLogger("p2p-ws-server")

# ==================== 持久化：SQLite ====================

_db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # ★ 不用 WAL（Docker 单文件挂载场景 WAL 的 -wal/-shm 会丢 → DB 损坏）
    # 默认 DELETE 模式 + synchronous NORMAL，单机足够安全
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    with _db_lock, get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                peer_id      TEXT PRIMARY KEY,
                username     TEXT UNIQUE NOT NULL,
                nickname     TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt         TEXT NOT NULL,
                created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);
            CREATE INDEX IF NOT EXISTS idx_users_nickname ON users(nickname);
        """)

def hash_password(password: str, salt: str = None) -> tuple:
    """返回 (hash, salt)，salt 64 位 hex"""
    if salt is None:
        salt = secrets.token_hex(16)   # 32 字节盐
    h = hashlib.sha256((password + salt).encode()).hexdigest()
    h = hashlib.sha256((h + salt).encode()).hexdigest()  # 二次迭代防彩虹表
    return h, salt

def verify_password(password: str, stored_hash: str, salt: str) -> bool:
    h, _ = hash_password(password, salt)
    return h == stored_hash

def register_user(username: str, nickname: str, password: str) -> tuple:
    """返回 (ok, peer_id_or_msg)"""
    username = username.strip()
    nickname = nickname.strip()
    password = password.strip()

    if not username or len(username) < 3:
        return False, "用户名至少 3 位"
    if not nickname or len(nickname) < 2:
        return False, "昵称至少 2 位"
    if not password or len(password) < 4:
        return False, "密码至少 4 位"

    with _db_lock:
        conn = get_db()
        try:
            # 用户名重复？
            if conn.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                return False, "用户名已被占用"
            # 昵称重复？
            if conn.execute("SELECT 1 FROM users WHERE nickname=?", (nickname,)).fetchone():
                return False, "昵称已被占用"
            peer_id = uuid.uuid4().hex   # 32 位
            pwd_hash, salt = hash_password(password)
            conn.execute(
                "INSERT INTO users(peer_id, username, nickname, password_hash, salt) VALUES (?,?,?,?,?)",
                (peer_id, username, nickname, pwd_hash, salt),
            )
            conn.commit()
            return True, peer_id
        except Exception as e:
            return False, f"注册失败: {e}"
        finally:
            conn.close()

def authenticate_user(username: str, password: str) -> tuple:
    """返回 (ok, peer_id_or_msg, nickname)"""
    username = username.strip()
    if not username or not password:
        return False, "请输入用户名和密码", ""
    with _db_lock:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not row:
                return False, "用户名或密码错误", ""
            if not verify_password(password, row["password_hash"], row["salt"]):
                return False, "用户名或密码错误", ""
            return True, row["peer_id"], row["nickname"]
        finally:
            conn.close()

# ==================== 内存状态 ====================

clients = {}       # peer_id -> {"nickname", "ws", "address", "last_ping"}
reverse_peers = {} # ws -> peer_id
tokens = {}        # token -> {"peer_id": str, "created_at": float}
TOKEN_TTL = 24 * 3600   # 24 小时有效 (允许 Ctrl+Shift+R 刷新重连)

def _is_token_valid(token: str) -> bool:
    """检查 token 是否存在且未过期, 过期自动清理"""
    entry = tokens.get(token)
    if not entry:
        return False
    if time.time() - entry["created_at"] > TOKEN_TTL:
        tokens.pop(token, None)
        return False
    return True


HEARTBEAT_TIMEOUT = 120   # 秒

def gen_token() -> str:
    return secrets.token_hex(16)   # 32 位

def cleanup_client(peer_id: str, reason: str = ""):
    info = clients.pop(peer_id, None)
    if info and info.get("ws") in reverse_peers:
        reverse_peers.pop(info["ws"], None)
    # ★ 不再清 token！让客户端可以带着原 token 重新注册
    # token 本身已经在 register 时 get 后 pop 掉了（一次性），这里清不清都无所谓
    # 但 heartbeat_check 主动踢人的情况，token 肯定还在，留给客户端重连用
    if info:
        log.info(f"清理客户端 {peer_id[:6]} ({info.get('nickname')}) reason={reason}")
# ==================== HTTP 层：注册 + 登录 ====================

class HTTPHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        log.info("HTTP %s - %s", self.address_string(), fmt % args)

    def _send_json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self._send_json(200, {})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/health", "/ping"):
            self._send_json(200, {"status": "ok", "clients": len(clients)})
        elif path == "/api/turn":
            turn_list = _gen_turn_credentials()
            self._send_json(200, {"turn": turn_list, "stun": [
                "stun:stun.miwifi.com:3478",
                "stun:stun.chat.bilibili.com:3478",
                "stun:stun.qq.com:3478",
            ]})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode()) if raw else {}
        except Exception:
            self._send_json(400, {"error": "请求格式错误"})
            return

        path = self.path.split("?")[0]

        if path == "/api/register":
            ok, result = register_user(data.get("username",""), data.get("nickname",""), data.get("password",""))
            if ok:
                # 注册成功自动发一个登录 token（让前端无需再单独登录）
                token = gen_token()
                tokens[token] = {"peer_id": result, "created_at": time.time()}
                self._send_json(200, {"ok": True, "peer_id": result, "token": token})
            else:
                self._send_json(400, {"ok": False, "error": result})

        elif path == "/api/login":
            ok, peer_id, nickname = authenticate_user(data.get("username",""), data.get("password",""))
            if ok:
                token = gen_token()
                tokens[token] = {"peer_id": peer_id, "created_at": time.time()}
                self._send_json(200, {"ok": True, "peer_id": peer_id, "nickname": nickname, "token": token})
            else:
                self._send_json(401, {"ok": False, "error": peer_id})

        else:
            self._send_json(404, {"error": "not found"})

# ==================== WebSocket 层：信令转发 ====================

async def ws_handler(reader, writer):
    address = writer.get_extra_info("peername")
    log.info(f"【新连接】来自 {address}")

    peer_id = None
    try:
        # ---- 握手 ----
        http_lines = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line: return
            http_lines.append(line)
            if line in (b"\r\n", b"\n"): break
            if len(http_lines) > 64: break

        req_text = b"".join(http_lines).decode(errors="replace")
        if not _ws_handshake(req_text, writer):
            return

        # ---- 循环处理消息 ----
        buf = b""
        while True:
            raw = await reader.read(65536)   # 不设超时，断连由 heartbeat_check 负责
            if not raw: break
            buf += raw
            frames, buf = _parse_ws_frames(buf)
            for payload in frames:
                try:
                    msg = json.loads(payload.decode(errors="replace"))
                except Exception:
                    continue

                # 首次必须带 token 注册
                # 首次必须带 token 注册 (token 24h 有效,允许刷新重连)
                if msg.get("type") == "register":
                    token = msg.get("token", "")
                    nick = msg.get("nickname", "")
                    if not token or not _is_token_valid(token):
                        _send_ws_json(writer, {"type": "error", "error": "token 无效或已过期,请重新登录"})
                        break
                    peer_id = tokens[token]["peer_id"]   # 从带时间戳 dict 取
                    # ★ 不再 pop token! 24h 内有效,允许 WS 重连
                    nickname = nick
                    # 查数据库取真实昵称（可信源）
                    with _db_lock, get_db() as conn:
                        row = conn.execute("SELECT nickname FROM users WHERE peer_id=?", (peer_id,)).fetchone()
                        if row: nickname = row["nickname"]
                    clients[peer_id] = {"nickname": nickname, "ws": writer, "address": address, "last_ping": time.time()}
                    reverse_peers[writer] = peer_id
                    log.info(f"✅ 注册 peer={peer_id[:6]} nickname={nickname}")
                    # 回给客户端
                    _send_ws_json(writer, {"type": "connected", "peer_id": peer_id, "name": nickname})
                    # 广播 peers
                    _broadcast_peers()
                    # 通知其他人
                    for pid, info in clients.items():
                        if pid != peer_id:
                            _send_ws_json(info["ws"], {"type": "peer_joined", "peer": {"peer_id": peer_id, "name": nickname}})

                elif msg.get("type") == "ping":
                    if peer_id and peer_id in clients:
                        clients[peer_id]["last_ping"] = time.time()
                    _send_ws_json(writer, {"type": "pong"})

                elif msg.get("type") == "leave":
                    break

                elif msg.get("type") == "signal":
                    to = msg.get("to", "")
                    if to in clients:
                        _send_ws_json(clients[to]["ws"], {
                            "type": "signal", "from": peer_id, "from_name": clients[peer_id]["nickname"],
                            "to": to, "to_name": clients[to]["nickname"],
                            "payload": msg.get("payload", {}),
                        })
                    else:
                        _send_ws_json(writer, {"type": "signal_error", "error": "对端不在线"})

                elif msg.get("type") == "chat":
                    """私聊中继 (P2P DataChannel 降级时走这里)"""
                    to = msg.get("to", "")
                    payload = msg.get("payload", {})
                    if to in clients:
                        _send_ws_json(clients[to]["ws"], {
                            "type": "chat",
                            "from": peer_id,
                            "from_name": clients[peer_id]["nickname"],
                            "payload": payload,
                        })

                elif msg.get("type") == "broadcast":
                    """公聊中继"""
                    payload = msg.get("payload", {})
                    for pid, info in clients.items():
                        if pid != peer_id:
                            _send_ws_json(info["ws"], {
                                "type": "chat",
                                "from": peer_id,
                                "from_name": clients[peer_id]["nickname"],
                                "payload": payload,
                                "broadcast": True,
                            })

    except asyncio.TimeoutError:
        log.warning(f"连接 {address} 超时")
    except Exception as e:
        log.error(f"连接 {address} 异常: {e}")
    finally:
        if peer_id:
            cleanup_client(peer_id, "连接断开")
            _broadcast_peers()
            for pid, info in clients.items():
                _send_ws_json(info["ws"], {"type": "peer_left", "peer_id": peer_id})
        try: writer.close(); await writer.wait_closed()
        except: pass

def _broadcast_peers():
    peers_list = [{"peer_id": pid, "name": info["nickname"]} for pid, info in clients.items()]
    for pid, info in clients.items():
        _send_ws_json(info["ws"], {"type": "peers", "peers": peers_list})

def _send_ws_json(writer, obj: dict):
    try:
        payload = json.dumps(obj, ensure_ascii=False).encode()
        frame = _make_ws_frame(payload)
        writer.write(frame)
    except: pass
    try: asyncio.get_event_loop().run_in_executor(None, writer.drain)
    except: pass

def _make_ws_frame(payload: bytes) -> bytes:
    # 简单的 unmasked server frame（浏览器客户端屏蔽掩码位不处理）
    length = len(payload)
    if length < 126:
        header = bytes([0x81, length])
    elif length < 65536:
        header = bytes([0x81, 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([0x81, 127]) + length.to_bytes(8, "big")
    return header + payload

def _parse_ws_frames(buf: bytes) -> tuple:
    frames = []
    while len(buf) >= 2:
        b0, b1 = buf[0], buf[1]
        mask = b1 & 0x80
        plen = b1 & 0x7F
        offset = 2
        if plen == 126:
            if len(buf) < 4: break
            plen = int.from_bytes(buf[2:4], "big"); offset = 4
        elif plen == 127:
            if len(buf) < 10: break
            plen = int.from_bytes(buf[2:10], "big"); offset = 10
        mask_key = buf[offset:offset+4] if mask else b""
        payload_start = offset + (4 if mask else 0)
        if len(buf) < payload_start + plen: break
        payload = buf[payload_start:payload_start+plen]
        if mask: payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        # 跳过控制帧
        op = b0 & 0x0F
        if op == 0x08: return frames, b""   # close
        if op == 0x09:   # ping → 回 pong
            pass
        elif op in (0x01, 0x02):
            frames.append(payload)
        buf = buf[payload_start + plen:]
    return frames, buf

def _ws_handshake(text: str, writer) -> bool:
    if "\r\nUpgrade: websocket\r\n" not in text and "upgrade: websocket" not in text.lower():
        log.warning("握手: 缺少 Upgrade: websocket 头"); return False
    if "sec-websocket-key" not in text.lower():
        log.warning("握手: 缺少 Sec-WebSocket-Key"); return False
    import base64, hashlib as _h
    for line in text.split("\r\n"):
        if "sec-websocket-key" in line.lower():
            key = line.split(":", 1)[1].strip(); break
    else: return False
    accept = base64.b64encode(_h.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    writer.write(resp.encode())
    log.info(f"握手: ✅ 成功")
    return True

# ==================== 心跳清理定时器 ====================
async def heartbeat_check():
    while True:
        await asyncio.sleep(10)
        now = time.time()
        for pid, info in list(clients.items()):
            if now - info.get("last_ping", 0) > HEARTBEAT_TIMEOUT:
                log.warning(f"超时清理 {pid[:6]} ({info.get('nickname')})")
                try: info["ws"].close()
                except: pass
                cleanup_client(pid, "心跳超时")
                for other_pid, other_info in clients.items():
                    _send_ws_json(other_info["ws"], {"type": "peer_left", "peer_id": pid})
                _broadcast_peers()

# ==================== 启动 ====================

def start_http_server():
    server = ThreadingHTTPServer((HOST, WS_PORT + 1), HTTPHandler)
    log.info(f"HTTP API 监听 {HOST}:{WS_PORT + 1}  (注册/登录)")
    server.serve_forever()

async def start_websocket_server():
    server = await asyncio.start_server(ws_handler, HOST, WS_PORT)
    log.info(f"WebSocket 信令 监听 {HOST}:{WS_PORT}")
    asyncio.create_task(heartbeat_check())
    async with server:
        await server.serve_forever()

def main():
    init_db()
    log.info(f"SQLite 用户库: {DB_PATH}")
    threading.Thread(target=start_http_server, daemon=True).start()
    asyncio.run(start_websocket_server())

if __name__ == "__main__":
    main()