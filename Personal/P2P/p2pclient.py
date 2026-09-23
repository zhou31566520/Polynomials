#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/9/23
# @Author  : zhou31566520
# @File    : p2pclient.py
# @Software: PyCharm
# @Desc    : P2P 客户端 - 连接信令服务器、发现节点、建立 TCP 点对点连接、文件传输
import json
import logging
import os
import socket
import struct
import sys
import threading
import time
import uuid
import hashlib

# HTTP 请求用 urllib（避免额外依赖 requests）
from urllib import request, parse

# 复用公共日志
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
class Log:
    def __init__(self, log_file="log.txt"):
        self.log_file = log_file
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)

        formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

        self.file_handler = logging.FileHandler(self.log_file, encoding="utf-8")
        self.file_handler.setFormatter(formatter)
        self.logger.addHandler(self.file_handler)
        # 添加控制台日志输出， utf-8 编码
        self.console_handler = logging.StreamHandler(sys.stdout)
        self.console_handler.setFormatter(formatter)
        self.logger.addHandler(self.console_handler)
        self.console_handler.setLevel(logging.INFO)
        self.logger.addHandler(self.console_handler)
        self.logger.propagate = False

    def info(self, msg):
        self.logger.info(msg)

    def error(self, msg):
        self.logger.error(msg)

    def warning(self, msg):
        self.logger.warning(msg)

    def debug(self, msg):
        self.logger.debug(msg)

    def critical(self, msg):
        self.logger.critical(msg)


log = Log()

# ============ 配置 ============

SIGNAL_SERVER = "http://192.168.137.198:8888"   # 信令服务器地址
P2P_PORT = 9000                            # 本机 P2P TCP 监听端口
HEARTBEAT_INTERVAL = 10                    # 心跳间隔（秒）
BUFFER_SIZE = 8192                         # 接收缓冲区
FILE_CHUNK_SIZE = 64 * 1024               # 文件分块大小 64KB
DOWNLOAD_DIR = os.path.join(os.path.dirname(__file__), "downloads")


# ============ 工具函数 ============

def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def http_post(url, data):
    """发送 HTTP POST 请求"""
    try:
        body = json.dumps(data).encode("utf-8")
        req = request.Request(
            url, data=body,
            headers={"Content-Type": "application/json"}, method="POST"
        )
        with request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error(f"HTTP POST {url} 失败: {e}")
        return None


def http_get(url):
    """发送 HTTP GET 请求"""
    try:
        with request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        log.error(f"HTTP GET {url} 失败: {e}")
        return None


def compute_md5(filepath):
    """计算文件 MD5"""
    h = hashlib.md5()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(FILE_CHUNK_SIZE)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


# ============ P2P 节点 ============

class P2PClient:
    """
    P2P 客户端：
    1. 向信令服务器注册自己
    2. 启动 TCP 服务器等待其他节点连接
    3. 定期心跳 + 获取在线节点列表
    4. 主动连接其他节点，实现点对点文件传输
    """

    def __init__(self, name="my-node", port=0, signal_server=SIGNAL_SERVER):
        # port=0 表示由操作系统随机分配可用端口
        self.name = name
        self.port = port
        self.signal_server = signal_server
        self._bound_sock = None    # ★ 新增：保存绑定好的 server socket
        self.ip = get_local_ip()
        self.peer_id = str(uuid.uuid4())[:8]   # 短 ID
        self.running = False
        self.peers = {}                          # 已知节点 {peer_id: {ip, port, name}}
        self.connections = {}                    # 活跃连接 {peer_id: socket}
        self._sock_lock = threading.Lock()
        self._event_handlers = []  # GUI 事件回调列表

        # 确保下载目录存在
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # ---------- 线程安全的 peers 访问 ----------

    def get_peer(self, peer_id):
        """线程安全获取单个 peer 信息"""
        with self._sock_lock:
            return self.peers.get(peer_id)

    def get_peers_snapshot(self):
        """线程安全获取 peers 快照，返回 [(peer_id, info_dict), ...]"""
        with self._sock_lock:
            return list(self.peers.items())

    # ---------- 信令服务器交互 ----------

    def register(self):
        """向信令服务器注册"""
        data = {
            "peer_id": self.peer_id,
            "ip": self.ip,
            "port": self.port,
            "name": self.name,
        }
        resp = http_post(f"{self.signal_server}/p2p/register", data)
        if resp and resp.get("code") == 0:
            log.info(f"【注册成功】peer_id={self.peer_id}, ip={self.ip}:{self.port}")
            self.peers = {p["peer_id"]: p for p in resp.get("peers", [])}
            log.info(f"【发现】当前在线节点: {list(self.peers.keys())}")
            return True
        return False

    def heartbeat_loop(self):
        """心跳 + 定期拉取在线节点"""
        while self.running:
            time.sleep(HEARTBEAT_INTERVAL)
            # 心跳
            http_post(f"{self.signal_server}/p2p/heartbeat", {"peer_id": self.peer_id})
            # 拉取节点列表
            resp = http_get(f"{self.signal_server}/p2p/peers?peer_id={self.peer_id}")
            if resp and resp.get("code") == 0:
                new_peers = {p["peer_id"]: p for p in resp.get("peers", [])}
                # 发现新节点
                for pid in new_peers:
                    if pid not in self.peers:
                        log.info(f"【新节点上线】{new_peers[pid].get('name')} -> {new_peers[pid]['ip']}:{new_peers[pid]['port']}")
                self.peers = new_peers

    def unregister(self):
        """注销"""
        http_post(f"{self.signal_server}/p2p/unregister", {"peer_id": self.peer_id})
        log.info("【注销】已从信令服务器注销")

    # ---------- TCP 服务器：等待连接 ----------

    def _bind_tcp(self):
        """★ 同步绑定 TCP 端口（port=0 时 OS 随机分配），更新 self.port"""
        server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_sock.bind(("0.0.0.0", self.port))
        # 拿到实际绑定的端口（port=0 时 OS 自动分配一个可用端口）
        actual_port = server_sock.getsockname()[1]
        self.port = actual_port
        server_sock.listen(16)
        self._bound_sock = server_sock
        log.info(f"【TCP 监听】0.0.0.0:{self.port} {'(随机端口)' if actual_port != 0 else ''}")
        return server_sock

    def _accept_loop(self, server_sock):
        """TCP accept 主循环 —— 在后台线程里运行"""
        while self.running:
            try:
                conn, addr = server_sock.accept()
                log.info(f"【新连接】{addr[0]}:{addr[1]}")
                threading.Thread(target=self._handle_peer_connection, args=(conn, addr), daemon=True).start()
            except OSError:
                # socket 被 close 时触发（stop 时）
                if not self.running:
                    break
            except Exception as e:
                if self.running:
                    log.error(f"accept 异常: {e}")
        try:
            server_sock.close()
        except Exception:
            pass
        self._bound_sock = None

    def _handle_peer_connection(self, conn, addr):
        """处理来自对端的请求 —— 循环处理多条消息，直到连接关闭"""
        peer_id = None
        try:
            # 消息循环：一条连接上可持续收发多条命令
            while self.running:
                header = self._recv_line(conn)
                if not header:
                    # 对端关闭连接
                    log.info(f"【连接关闭】{addr[0]}:{addr[1]}")
                    break
                cmd = json.loads(header)
                action = cmd.get("action")

                if action == "hello":
                    # 握手：对端自我介绍
                    peer_id = cmd.get("peer_id")
                    peer_name = cmd.get("name", "")
                    with self._sock_lock:
                        self.connections[peer_id] = conn
                    log.info(f"【握手】来自 {peer_name}({peer_id})")
                    try:
                        self._send_json(conn, {"action": "hello_ack", "peer_id": self.peer_id, "name": self.name})
                    except Exception:
                        # 对端可能已关闭连接，忽略发送失败，继续读取后续数据
                        pass
                elif action == "file_request":
                    # 对方请求文件
                    filename = cmd.get("filename")
                    log.info(f"【收到请求】{peer_id or addr} 请求文件: {filename}")
                    self._handle_file_send(conn, filename)

                elif action == "file_meta":
                    # 对方发来文件元信息，准备接收
                    self._handle_file_recv(conn, cmd)
                elif action == "chat_broadcast":
                    # 公共聊天室广播消息 —— 转交给 GUI 显示
                    log.info(f"【收广播】来自 {cmd.get('name', '')}({cmd.get('peer_id')}): {cmd.get('message', '')[:30]}")
                    self._emit_event("chat_broadcast", {
                        "peer_id": cmd.get("peer_id"),
                        "name": cmd.get("name", ""),
                        "message": cmd.get("message", ""),
                        "timestamp": cmd.get("timestamp", time.time()),
                    })

                elif action == "chat_private":
                    # 私聊消息（只发给指定 peer_id，target == self.peer_id 才收到）
                    target = cmd.get("to")
                    sender = cmd.get("peer_id", addr)
                    if target == self.peer_id:
                        log.info(f"【收私聊】来自 {cmd.get('name', '')}({sender}): {cmd.get('message', '')[:30]}")
                        self._emit_event("chat_private", {
                            "peer_id": sender,
                            "name": cmd.get("name", ""),
                            "message": cmd.get("message", ""),
                            "timestamp": cmd.get("timestamp", time.time()),
                        })
                    else:
                        log.warning(f"【私聊丢弃】target={target} ≠ self={self.peer_id}  (from {sender})")

                elif action == "bye":
                    log.info(f"【对端告别】{peer_id or addr}")
                    break

                else:
                    log.warning(f"未知 action: {action}")

        except ConnectionResetError:
            log.info(f"【对端断开】{peer_id or addr}")
        except Exception as e:
            log.error(f"处理连接异常: {e}")
        finally:
            # 清理连接记录
            with self._sock_lock:
                if peer_id and peer_id in self.connections and self.connections[peer_id] == conn:
                    del self.connections[peer_id]
            try:
                conn.close()
            except Exception:
                pass

    # ---------- TCP 客户端：主动连接 ----------

    def connect_to_peer(self, ip, port, register=True):
        """
        主动连接某个节点并握手
        :param register: 是否把连接注册到 self.connections （request_file/send_file 这种短连接传 False）
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        try:
            sock.connect((ip, port))
            # 发送握手
            self._send_json(sock, {
                "action": "hello",
                "peer_id": self.peer_id,
                "name": self.name,
            })
            # 等待 ack
            header = self._recv_line(sock)
            if header:
                ack = json.loads(header)
                peer_id = ack.get("peer_id", "unknown")
                if register:
                    with self._sock_lock:
                        self.connections[peer_id] = sock
                log.info(f"【连接成功】已连接到 {ack.get('name')}({peer_id})")
                return peer_id, sock
            return None, None
        except Exception as e:
            log.error(f"连接 {ip}:{port} 失败: {e}")
            sock.close()
            return None, None

    # ---------- 文件传输 ----------

    def send_file(self, sock, filepath):
        """发送文件给已连接的对端"""
        if not os.path.isfile(filepath):
            self._send_json(sock, {"action": "file_error", "msg": f"文件不存在: {filepath}"})
            return False

        filename = os.path.basename(filepath)
        filesize = os.path.getsize(filepath)
        md5 = compute_md5(filepath)

        # 发送元信息
        self._send_json(sock, {
            "action": "file_meta",
            "filename": filename,
            "filesize": filesize,
            "md5": md5,
        })
        log.info(f"【发送】准备发送 {filename} ({filesize} bytes, md5={md5})")

        # 发送文件内容
        sent = 0
        with open(filepath, "rb") as f:
            while self.running:
                chunk = f.read(FILE_CHUNK_SIZE)
                if not chunk:
                    break
                sock.sendall(chunk)
                sent += len(chunk)
                # 进度日志
                pct = sent * 100 // filesize
                if pct % 10 == 0 and pct > 0:
                    log.info(f"【发送进度】{pct}% ({sent}/{filesize})")

        log.info(f"【发送完成】{filename}")
        return True

    def request_file(self, ip, port, filename):
        """向指定节点请求某个文件"""
        peer_id, sock = self.connect_to_peer(ip, port)
        if not sock:
            return

        try:
            # 发送文件请求
            self._send_json(sock, {"action": "file_request", "filename": filename})
            # 对方会回复 file_meta 或 file_error，由 _handle_peer_connection 的 file_meta 分支处理
            # 这里我们直接在当前 socket 上等待接收
            header = self._recv_line(sock)
            if not header:
                return
            meta = json.loads(header)
            if meta.get("action") == "file_error":
                log.warning(f"对方拒绝: {meta.get('msg')}")
                return

            self._handle_file_recv(sock, meta)
        except Exception as e:
            log.error(f"请求文件失败: {e}")
        finally:
            sock.close()

    def _handle_file_send(self, conn, filename):
        """对方请求文件时，查找本地文件并发送"""
        # 简单策略：在当前目录和 DOWNLOAD_DIR 中查找
        search_dirs = [os.path.dirname(__file__), DOWNLOAD_DIR]
        found_path = None
        for d in search_dirs:
            candidate = os.path.join(d, filename)
            if os.path.isfile(candidate):
                found_path = candidate
                break

        if found_path:
            self.send_file(conn, found_path)
        else:
            self._send_json(conn, {"action": "file_error", "msg": f"未找到文件: {filename}"})

    def _handle_file_recv(self, conn, meta):
        """接收文件"""
        filename = meta["filename"]
        filesize = int(meta["filesize"])
        expected_md5 = meta["md5"]

        save_path = os.path.join(DOWNLOAD_DIR, filename)
        log.info(f"【接收】保存到 {save_path} ({filesize} bytes)")

        received = 0
        with open(save_path, "wb") as f:
            while self.running and received < filesize:
                chunk = conn.recv(min(BUFFER_SIZE, filesize - received))
                if not chunk:
                    break
                f.write(chunk)
                received += len(chunk)
                pct = received * 100 // filesize
                if pct % 10 == 0 and pct > 0:
                    log.info(f"【接收进度】{pct}% ({received}/{filesize})")

        # 校验 MD5
        actual_md5 = compute_md5(save_path)
        if actual_md5 == expected_md5:
            log.info(f"【接收完成】{filename} MD5 校验通过")
        else:
            log.warning(f"【MD5 校验失败】期望 {expected_md5}, 实际 {actual_md5}")
    # ---------- GUI 事件回调 & 聊天 ----------

    def register_handler(self, handler):
        """注册事件回调函数 —— GUI 层用来订阅"""
        self._event_handlers.append(handler)

    def _emit_event(self, event_type, data):
        """触发事件 —— 通知所有回调"""
        for cb in self._event_handlers:
            try:
                cb(event_type, data)
            except Exception as e:
                log.error(f"事件回调异常: {e}")

    def broadcast_chat(self, message):
        """
        公共聊天室：遍历所有在线 peers 发 chat_broadcast
        每条消息只发给一个 peer_id（不包含自己）
        """
        payload = {
            "action": "chat_broadcast",
            "peer_id": self.peer_id,
            "name": self.name,
            "message": message,
            "timestamp": time.time(),
        }
        log.info(f"【发广播】: {message[:30]} → {len(self.get_peers_snapshot())-1} 个 peer")
        self._broadcast_to_peers(payload)

    def send_private_chat(self, target_peer_id, message):
        """私聊：发给指定 peer_id"""
        if target_peer_id == self.peer_id:
            return
        info = self.get_peer(target_peer_id)
        if not info:
            log.warning(f"私聊失败：未找到 {target_peer_id}")
            return
        payload = {
            "action": "chat_private",
            "peer_id": self.peer_id,
            "name": self.name,
            "to": target_peer_id,
            "message": message,
            "timestamp": time.time(),
        }
        log.info(f"【发私聊】→ {info.get('name','')}({target_peer_id}): {message[:30]}")
        # 放到 daemon 线程发送，不阻塞 GUI；用 _safe_send 防异常
        threading.Thread(
            target=self._safe_send,
            args=(info["ip"], info["port"], target_peer_id, info.get("name", target_peer_id[:6]), payload),
            daemon=True
        ).start()

    def _safe_send(self, ip, port, pid, name, payload):
        """安全发送：捕获所有异常，不让线程崩"""
        try:
            self._send_to_peer(ip, port, payload)
            log.info(f"【发送完成】→ {name}({pid}) {payload.get('action')}")
        except Exception as e:
            log.warning(f"发送给 {pid}({name}) 失败: {e}")

    def _send_to_peer(self, ip, port, payload):
        """
        主动连到对端发一条 JSON，发完就关。
        不占用 connections 长连接（每次临时 TCP）。
        """
        import socket as _sk
        s = _sk.socket(_sk.AF_INET, _sk.SOCK_STREAM)
        s.settimeout(5)
        try:
            s.connect((ip, port))
            # 先发 hello 握手
            self._send_json(s, {
                "action": "hello",
                "peer_id": self.peer_id,
                "name": self.name,
            })
            # 等 hello_ack
            try:
                ack = self._recv_line(s)
                if ack:
                    log.info(f"【握手ACK】→ {ip}:{port} 收到 hello_ack")
            except Exception:
                pass
            # 发业务消息
            self._send_json(s, payload)
            # 直接 close
        finally:
            try:
                s.close()
            except Exception:
                pass



    def _broadcast_to_peers(self, payload):
        """把 payload 并发发给所有已知 peers（不包含自己）"""
        threads = []
        for pid, info in list(self.peers.items()):
            if pid == self.peer_id:
                continue
            t = threading.Thread(
                target=self._safe_send,
                args=(info["ip"], info["port"], pid, info.get("name", pid[:6]), payload),
                daemon=True
            )
            threads.append(t)
            t.start()
        # 不 join，不等结果——发完就返回，不阻塞调用方




    # ---------- 底层收发 ----------

    def _send_json(self, sock, obj):
        """发送 JSON 行（以 \n 结尾）"""
        data = json.dumps(obj, ensure_ascii=False) + "\n"
        sock.sendall(data.encode("utf-8"))

    def _recv_line(self, sock):
        """接收一行数据（到 \n 为止）"""
        buf = b""
        while self.running:
            ch = sock.recv(1)
            if not ch:
                return None
            if ch == b"\n":
                break
            buf += ch
        return buf.decode("utf-8") if buf else None

    # ---------- 生命周期 ----------

    def _bind_tcp(self, retries=5):
        """绑定 TCP 端口（port=0 时 OS 随机分配），带重试应对 TIME_WAIT"""
        import errno
        last_err = None
        for attempt in range(retries):
            server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # Windows 上 SO_REUSEADDR + SO_DONTLINGER 才能真正绕过 TIME_WAIT
            try:
                server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            except Exception:
                pass
            try:
                # 额外设置：关闭 socket 时强制释放，不进入 TIME_WAIT
                server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack('ii', 1, 0))
            except Exception:
                pass
            try:
                server_sock.bind(("0.0.0.0", self.port))
                break  # 成功跳出重试
            except OSError as e:
                last_err = e
                try:
                    server_sock.close()
                except Exception:
                    pass
                # 如果是地址占用且 port=0，OS 会换一个随机端口，所以重试
                if attempt < retries - 1 and self.port == 0:
                    time.sleep(0.3)
                    log.warning(f"端口绑定重试 {attempt+1}/{retries}: {e}")
                else:
                    raise  # 手动指定端口的情况直接抛出
        else:
            raise last_err

        actual_port = server_sock.getsockname()[1]
        self.port = actual_port
        server_sock.listen(16)
        self._bound_sock = server_sock
        log.info(f"【TCP 监听】0.0.0.0:{self.port} {'(随机端口)' if self.port != 0 else ''}")
        return server_sock

    def start(self):
        """
        启动流程：
        1. sync bind TCP → get actual self.port
        2. register to signal server (with actual port)
        3. start heartbeat + accept threads
        Throws specific exceptions so GUI can show accurate error messages.
        """
        self.running = True

        # Step 1: bind TCP
        try:
            self._bind_tcp()
        except OSError as e:
            self.running = False
            err_code = getattr(e, 'winerror', None) or getattr(e, 'errno', None)
            if err_code in (10048, errno.EADDRINUSE):  # WSAEADDRINUSE / Linux EADDRINUSE
                raise RuntimeError(f"TCP 端口绑定失败：端口 {self.port} 被占用") from e
            raise RuntimeError(f"TCP 端口绑定失败：{e}") from e

        # Step 2: register
        if not self.register():
            self.running = False
            # Need to release bound socket before raising
            try:
                if self._bound_sock:
                    self._bound_sock.close()
                    self._bound_sock = None
            except Exception:
                pass
            raise RuntimeError("注册失败：信令服务器不可用或响应异常")

        # Step 3: start background threads
        threading.Thread(target=self.heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._accept_loop, args=(self._bound_sock,), daemon=True).start()

        log.info(f"P2P 客户端 [{self.name}] 运行中，peer_id={self.peer_id}, port={self.port}")
        return True

    def stop(self):
        self.running = False
        # ★ 关闭 server socket 让 accept_loop 退出
        try:
            if self._bound_sock:
                self._bound_sock.close()
        except Exception:
            pass
        self.unregister()
        with self._sock_lock:
            for pid, sock in self.connections.items():
                try:
                    sock.close()
                except Exception:
                    pass
        log.info("P2P 客户端已停止")


# ============ 交互式命令行 ============

def print_help():
    print("""
可用命令:
  list                列出当前在线节点
  connect <peer_id>   连接到指定节点
  send <peer_id> <path>   发送文件给某节点
  request <peer_id> <filename>  向某节点请求文件
  info                显示本机信息
  quit                退出
  help                显示帮助
""")


def interactive_loop(client):
    print_help()
    while client.running:
        try:
            cmd = input(f"[{client.name}@{client.peer_id}]> ").strip()
            if not cmd:
                continue

            parts = cmd.split(maxsplit=2)
            action = parts[0].lower()

            if action == "quit" or action == "exit":
                break
            elif action == "help":
                print_help()

            elif action == "info":
                print(f"  peer_id : {client.peer_id}")
                print(f"  name    : {client.name}")
                print(f"  ip:port : {client.ip}:{client.port}")
                print(f"  server  : {client.signal_server}")
                print(f"  下载目录: {DOWNLOAD_DIR}")

            elif action == "list":
                if not client.peers:
                    print("  暂无在线节点")
                else:
                    print(f"  {'peer_id':<10}{'name':<15}{'ip:port'}")
                    print("  " + "-" * 50)
                    for pid, info in client.peers.items():
                        print(f"  {pid:<10}{info.get('name', ''):<15}{info['ip']}:{info['port']}")

            elif action == "connect":
                if len(parts) < 2:
                    print("用法: connect <peer_id>")
                    continue
                pid = parts[1]
                info = client.peers.get(pid)
                if not info:
                    print(f"未找到节点 {pid}")
                    continue
                client.connect_to_peer(info["ip"], info["port"])

            elif action == "send":
                if len(parts) < 3:
                    print("用法: send <peer_id> <filepath>")
                    continue
                pid, filepath = parts[1], parts[2]
                info = client.peers.get(pid)
                if not info:
                    print(f"未找到节点 {pid}")
                    continue
                # 连接 + 发送（后台线程）
                def do_send():
                    peer_id, sock = client.connect_to_peer(info["ip"], info["port"])
                    if sock:
                        try:
                            client.send_file(sock, filepath)
                        finally:
                            sock.close()
                threading.Thread(target=do_send, daemon=True).start()

            elif action == "request":
                if len(parts) < 3:
                    print("用法: request <peer_id> <filename>")
                    continue
                pid, filename = parts[1], parts[2]
                info = client.peers.get(pid)
                if not info:
                    print(f"未找到节点 {pid}")
                    continue
                client.request_file(info["ip"], info["port"], filename)

            else:
                print(f"未知命令: {cmd}，输入 help 查看帮助")

        except KeyboardInterrupt:
            break
        except EOFError:
            break
        except Exception as e:
            log.error(f"命令异常: {e}")


# ============ 入口 ============

if __name__ == "__main__":
    # 支持命令行: python p2pclient.py [name] [port]
    # 同时支持环境变量（Docker 友好）：NODE_NAME, P2P_PORT, SIGNAL_SERVER
    name = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NODE_NAME", f"node-{get_local_ip().split('.')[-1]}")
    port = int(sys.argv[2]) if len(sys.argv) > 2 else int(os.environ.get("P2P_PORT", P2P_PORT))
    signal_server = os.environ.get("SIGNAL_SERVER", SIGNAL_SERVER)

    client = P2PClient(name=name, port=port, signal_server=signal_server)
    client.start()

    # 检测是否有 TTY：有则进入交互模式，无则后台运行（Docker 场景）
    if sys.stdin.isatty():
        try:
            interactive_loop(client)
        finally:
            client.stop()
    else:
        log.info("无 TTY，进入后台守护模式（Ctrl+C 退出）")
        try:
            while client.running:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            client.stop()