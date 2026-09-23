#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
P2P Worker 节点 —— 脱离中央服务器的分布式执行器

功能：
  1. UDP 广播自动发现同局域网其他 Worker
  2. TCP 长连接发送任务 / 接收结果
  3. 心跳检测 + 动态节点列表
  4. 主从选举：最先启动的 Worker 自动成为 Coordinator

使用：
  启动多个实例（不同端口）即可组网：
    python P2PWorker.py --port 9001
    python P2PWorker.py --port 9002
    python P2PWorker.py --port 9003
"""

import socket
import threading
import time
import json
import uuid
import argparse
import struct
import logging
from typing import Dict, Optional, List

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("P2P")

# ============ 配置常量 ============
BROADCAST_PORT = 9527        # UDP 广播端口（发现用）
BROADCAST_INTERVAL = 3       # 广播间隔（秒）
HEARTBEAT_INTERVAL = 5       # TCP 心跳间隔（秒）
PEER_TIMEOUT = 15            # 超过此时间没收到心跳认为节点离线
TCP_RECV_BUF = 4096


def get_lan_ip() -> str:
    """获取本机局域网 IP（不调用服务器，纯本地）"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # 仅用于触发路由选择，UDP 不发包
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ============ 消息协议 ============
class MsgType:
    HELLO = "HELLO"               # 广播发现
    HELLO_ACK = "HELLO_ACK"       # 发现响应
    HEARTBEAT = "HB"              # 心跳
    TASK_SUBMIT = "TASK"          # 提交任务
    TASK_RESULT = "RESULT"        # 返回结果
    PEER_LIST = "PEERS"           # 节点列表同步


def pack_msg(data: dict) -> bytes:
    """消息封包：4字节长度头 + JSON body"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return struct.pack(">I", len(body)) + body


def recv_exact(sock: socket.socket, n: int) -> bytes:
    """精确接收 n 字节"""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("对端断开")
        buf += chunk
    return buf


def recv_msg(sock: socket.socket) -> dict:
    """完整接收一条消息"""
    header = recv_exact(sock, 4)
    length = struct.unpack(">I", header)[0]
    body = recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


# ============ Peer 节点 ============
class Peer:
    __slots__ = ("node_id", "ip", "port", "last_seen", "sock", "role")

    def __init__(self, node_id: str, ip: str, port: int, role: str = "worker"):
        self.node_id = node_id
        self.ip = ip
        self.port = port
        self.last_seen = time.time()
        self.sock: Optional[socket.socket] = None
        self.role = role

    def address(self) -> str:
        return f"{self.ip}:{self.port}"


# ============ P2P Worker 核心 ============
class P2PWorker:
    def __init__(self, port: int = 9001, name: str = None):
        self.node_id = str(uuid.uuid4())[:8]
        self.name = name or f"Worker-{self.node_id}"
        self.ip = get_lan_ip()
        self.port = port
        self.role = "worker"   # worker / coordinator
        self.start_time = time.time()

        self.peers: Dict[str, Peer] = {}       # node_id -> Peer
        self.task_queue: Dict[str, dict] = {}  # task_id -> task
        self.running = False

        # 锁
        self._lock = threading.Lock()

        log.info(f"🌱 节点启动 [{self.name}] id={self.node_id} 监听={self.ip}:{self.port}")

    # ---------- 1. UDP 广播发现 ----------
    def _broadcast_loop(self):
        """定期广播 HELLO，让局域网其他节点发现自己"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = json.dumps({
            "type": MsgType.HELLO,
            "node_id": self.node_id,
            "name": self.name,
            "ip": self.ip,
            "port": self.port,
        }).encode()
        while self.running:
            try:
                sock.sendto(msg, ("255.255.255.255", BROADCAST_PORT))
            except Exception as e:
                log.warning(f"广播发送失败: {e}")
            time.sleep(BROADCAST_INTERVAL)

    def _listen_broadcast(self):
        """监听其他节点的 HELLO 广播"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", BROADCAST_PORT))
        sock.settimeout(1.0)

        while self.running:
            try:
                data, addr = sock.recvfrom(2048)
                msg = json.loads(data.decode())
                if msg["node_id"] == self.node_id:
                    continue  # 忽略自己

                if msg["type"] == MsgType.HELLO:
                    self._handle_hello(msg, addr)
                elif msg["type"] == MsgType.HELLO_ACK:
                    self._handle_hello_ack(msg)
            except socket.timeout:
                continue
            except Exception as e:
                log.debug(f"广播监听异常: {e}")

    def _handle_hello(self, msg: dict, addr: tuple):
        """收到新节点广播：记录 + 回 ACK + 建 TCP 连接"""
        nid = msg["node_id"]
        with self._lock:
            if nid not in self.peers:
                log.info(f"🔍 发现新节点 [{msg['name']}] {msg['ip']}:{msg['port']}")
                peer = Peer(nid, msg["ip"], msg["port"], msg.get("role", "worker"))
                self.peers[nid] = peer
                # 建 TCP 长连接
                threading.Thread(target=self._connect_peer, args=(peer,), daemon=True).start()
            else:
                self.peers[nid].last_seen = time.time()

        # 回复 ACK
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.sendto(json.dumps({
                "type": MsgType.HELLO_ACK,
                "node_id": self.node_id,
                "name": self.name,
                "ip": self.ip,
                "port": self.port,
            }).encode(), addr)
            s.close()
        except Exception:
            pass

    def _handle_hello_ack(self, msg: dict):
        """收到 ACK 也记录一下（防止只有一方收到广播的情况）"""
        nid = msg["node_id"]
        with self._lock:
            if nid not in self.peers:
                log.info(f"🔍 ACK 发现节点 [{msg['name']}] {msg['ip']}:{msg['port']}")
                peer = Peer(nid, msg["ip"], msg["port"], msg.get("role", "worker"))
                self.peers[nid] = peer
                threading.Thread(target=self._connect_peer, args=(peer,), daemon=True).start()
            else:
                self.peers[nid].last_seen = time.time()

    # ---------- 2. TCP 连接管理 ----------
    def _connect_peer(self, peer: Peer):
        """主动发起到 peer 的 TCP 长连接"""
        time.sleep(0.3)  # 错开双方同时连的瞬间
        try:
            s = socket.create_connection((peer.ip, peer.port), timeout=3)
            peer.sock = s
            log.info(f"🔗 连接到 {peer.address()}")
            self._tcp_dispatch(peer)
        except Exception as e:
            log.debug(f"连接 {peer.address()} 失败(对端可能已主动连入): {e}")

    def _listen_tcp(self):
        """监听端口接受其他节点的 TCP 连接"""
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("0.0.0.0", self.port))
        server.listen(32)
        log.info(f"📡 TCP 监听 0.0.0.0:{self.port}")

        while self.running:
            try:
                client, addr = server.accept()
                peer = Peer(f"remote-{addr}", addr[0], addr[1])
                peer.sock = client
                threading.Thread(target=self._tcp_dispatch, args=(peer,), daemon=True).start()
            except Exception as e:
                if self.running:
                    log.error(f"接受连接失败: {e}")

    def _tcp_dispatch(self, peer: Peer):
        """TCP 消息分发循环"""
        assert peer.sock is not None
        peer.sock.settimeout(HEARTBEAT_INTERVAL + 2)
        with self._lock:
            self.peers[peer.node_id] = peer

        try:
            while self.running:
                msg = recv_msg(peer.sock)
                peer.last_seen = time.time()

                t = msg.get("type", "")
                if t == MsgType.HEARTBEAT:
                    peer.sock.sendall(pack_msg({"type": MsgType.HEARTBEAT, "node_id": self.node_id}))
                elif t == MsgType.TASK_SUBMIT:
                    self._handle_task(peer, msg)
                elif t == MsgType.TASK_RESULT:
                    self._handle_result(msg)
                elif t == MsgType.PEER_LIST:
                    self._handle_peer_list(msg)
                else:
                    log.debug(f"未知消息: {msg}")
        except (ConnectionError, socket.timeout, OSError) as e:
            log.info(f"⚠️  {peer.address()} 断开: {e}")
        finally:
            with self._lock:
                self.peers.pop(peer.node_id, None)
            try:
                peer.sock.close()
            except Exception:
                pass

    # ---------- 3. 心跳 + 离线清理 ----------
    def _heartbeat_loop(self):
        while self.running:
            time.sleep(HEARTBEAT_INTERVAL)
            now = time.time()
            stale: List[str] = []
            with self._lock:
                for nid, peer in self.peers.items():
                    if now - peer.last_seen > PEER_TIMEOUT:
                        stale.append(nid)
                    elif peer.sock:
                        try:
                            peer.sock.sendall(pack_msg({"type": MsgType.HEARTBEAT, "node_id": self.node_id}))
                        except Exception:
                            stale.append(nid)
                for nid in stale:
                    p = self.peers.pop(nid, None)
                    if p and p.sock:
                        try:
                            p.sock.close()
                        except Exception:
                            pass
                    log.info(f"💔 节点离线: {nid}")

    # ---------- 4. 任务处理 ----------
    def _handle_task(self, peer: Peer, msg: dict):
        task_id = msg["task_id"]
        payload = msg["payload"]
        log.info(f"📨 收到任务 {task_id} 来自 {peer.address()}: {payload}")
        # 实际执行（模拟）
        result = self._execute(payload)
        peer.sock.sendall(pack_msg({
            "type": MsgType.TASK_RESULT,
            "task_id": task_id,
            "node_id": self.node_id,
            "result": result,
            "ts": time.time(),
        }))

    def _execute(self, payload) -> dict:
        """任务执行函数 —— 子类或替换这里实现真正业务"""
        import subprocess
        try:
            if isinstance(payload, dict) and "cmd" in payload:
                r = subprocess.run(payload["cmd"], shell=True, capture_output=True, text=True, timeout=30)
                return {"ok": True, "stdout": r.stdout[:500], "stderr": r.stderr[:500], "rc": r.returncode}
            return {"ok": True, "echo": str(payload)}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def _handle_result(self, msg: dict):
        log.info(f"✅ 任务 {msg['task_id']} 完成 (来自 {msg['node_id']}): {msg.get('result')}")

    # ---------- 5. 对外 API ----------
    def submit_to_all(self, payload):
        """广播任务给所有已知节点"""
        task_id = str(uuid.uuid4())[:8]
        sent = 0
        with self._lock:
            for nid, peer in list(self.peers.items()):
                if peer.sock:
                    try:
                        peer.sock.sendall(pack_msg({
                            "type": MsgType.TASK_SUBMIT,
                            "task_id": task_id,
                            "node_id": self.node_id,
                            "payload": payload,
                        }))
                        sent += 1
                    except Exception as e:
                        log.warning(f"发送到 {nid} 失败: {e}")
        log.info(f"📤 任务 {task_id} 已发送给 {sent} 个节点")
        return task_id, sent

    def submit_to(self, node_id: str, payload):
        """定向发送给某个节点"""
        with self._lock:
            peer = self.peers.get(node_id)
        if not peer or not peer.sock:
            log.error(f"节点 {node_id} 不可达")
            return False
        task_id = str(uuid.uuid4())[:8]
        peer.sock.sendall(pack_msg({
            "type": MsgType.TASK_SUBMIT,
            "task_id": task_id,
            "node_id": self.node_id,
            "payload": payload,
        }))
        return True

    def status(self) -> dict:
        """查看网络状态"""
        with self._lock:
            peers_info = [
                {"id": p.node_id, "name": p.name if hasattr(p, 'name') else p.node_id[:6],
                 "addr": p.address(), "alive": time.time() - p.last_seen < PEER_TIMEOUT}
                for p in self.peers.values()
            ]
        return {
            "self": {"id": self.node_id, "name": self.name, "addr": f"{self.ip}:{self.port}", "role": self.role},
            "peers": peers_info,
            "peer_count": len(peers_info),
            "uptime": int(time.time() - self.start_time),
        }

    # ---------- 6. 启动 ----------
    def start(self):
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        threading.Thread(target=self._listen_broadcast, daemon=True).start()
        threading.Thread(target=self._listen_tcp, daemon=True).start()
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()

    def stop(self):
        self.running = False
        with self._lock:
            for p in self.peers.values():
                try:
                    p.sock.close()
                except Exception:
                    pass

    # ---------- 7. 交互式 CLI ----------
    def run_cli(self):
        self.start()
        log.info("🎮 交互模式 —— 命令: help / status / send <cmd> / dir <peer_id> <cmd> / quit")
        try:
            while True:
                line = input(f"[{self.name}]> ").strip()
                if not line:
                    continue
                if line in ("quit", "exit", "q"):
                    break
                elif line == "help":
                    print("""
  help              显示帮助
  status            网络状态
  peers             列出所有节点
  send <json任务>   广播任务给所有节点  例: send {"cmd":"echo hello"}
  dir <id> <json>   定向发任务给某个节点
  quit              退出
""")
                elif line == "status":
                    print(json.dumps(self.status(), indent=2, ensure_ascii=False))
                elif line == "peers":
                    s = self.status()
                    for p in s["peers"]:
                        mark = "🟢" if p["alive"] else "🔴"
                        print(f"  {mark} {p['id']}  {p['addr']}")
                elif line.startswith("send "):
                    payload = line[5:]
                    try:
                        self.submit_to_all(json.loads(payload))
                    except json.JSONDecodeError:
                        self.submit_to_all({"cmd": payload})
                elif line.startswith("dir "):
                    parts = line[4:].split(" ", 1)
                    if len(parts) == 2:
                        nid, payload = parts
                        self.submit_to(nid, json.loads(payload))
                else:
                    print("未知命令，输入 help 查看")
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()
            log.info("👋 已退出")


# ============ 入口 ============
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="P2P Worker 节点")
    parser.add_argument("--port", type=int, default=9001, help="TCP 监听端口 (默认 9001)")
    parser.add_argument("--name", default=None, help="节点名称")
    args = parser.parse_args()

    node = P2PWorker(port=args.port, name=args.name)
    node.run_cli()
