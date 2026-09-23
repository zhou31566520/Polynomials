#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/9/23
# @Author  : zhou31566520
# @File    : p2pserver.py
# @Software: PyCharm
# @Desc    : P2P 信令服务器 - 节点注册、发现、NAT穿透辅助、信令消息转发
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler

# 将 common 目录加入路径，复用 HTTPServer
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "common"))
from http.server import HTTPServer
from socketserver import ThreadingMixIn



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
uri_map = {}


def route(uris, methods):
    """路由注册装饰器"""
    def wrapper(func):
        for uri in uris.split(","):
            for method in methods.split(","):
                uri_map[f'{uri}_{method}'] = func
                func.uri = uri
                func.method = method
                uri_map[f'{uri}_{method}'] = func
        return func
    return wrapper


class MyHandler(BaseHTTPRequestHandler):

    def Result(self, resp_code, resp_body, resp_headers=None):
        '''
        响应客户端请求
        :param resp_code: 响应状态码
        :param resp_body: 响应体
        :param resp_headers: 响应头
        '''
        if resp_headers is None:
            resp_headers = {"Content-Type": "text/plain; charset=utf-8"}

        log.info(f"接口响应 {resp_body}")
        self.send_response(resp_code)
        if resp_body == None:
            resp_body = ""
        for item, value in resp_headers.items():
            self.send_header(item, value)
        self.end_headers()
        self.wfile.write(resp_body.encode("utf-8"))

    def str2json(self,string):
        '''
        字符串转换为 JSON 格式
        :param string: 字符串
        :return: JSON 格式对象或字典对象
        '''
        if (string.startswith("{") and string.endswith("}")) or (string.startswith("[") and string.endswith("]")):
            return json.loads(string)
        elif "=" in string or "&" in string:
            return {item.split("=")[1] for item in string.split("&")}

    def get_para(self,key):
        if key in self.qry:
            return self.qry[key]
        elif key in self.post_body:
            return self.post_body[key]
        elif key in self.headers:
            return self.headers[key]
        else:
            return None

    def handler(self):
        '''
        处理 HTTP 请求
        '''
        try:
            self.uri = self.path.split("?")[0]
            self.qry = {}
            self.post_body = {}
            if "?" in self.path:
                self.uri = self.uri + "?" + self.path.split("?")[1]
                qry = self.path.split("?")[1]
                self.qry = self.str2json(qry)
                log.info(f"【查询参数】{self.qry}")
            if "Content-Length" in self.headers and int(self.headers["Content-Length"]) > 0:
                post_body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
                self.post_body = self.str2json(post_body)
                log.info(f"【请求体】{self.post_body}")
            func = uri_map.get(f'{self.uri}_{self.command}')
            if func:
                log.info(f"匹配到接口处理函数{func.__name__} {func.uri}_{func.method}")
                try:
                    func(self)
                except Exception as e:
                    log.error(f"Error in route {func.uri}_{func.method}: {e}")
                    self.Result(500, str(e))
            else:
                log.warning(f"Route not found: {self.uri}_{self.command}")
                self.Result(404, "Not Found")
        except Exception as e:
            log.error(f"Error handling request: {traceback.format_exc(100,e)}")
            self.Result(500, str(e))

    def do_GET(self):
        self.handler()

    # POST 请求处理
    def do_POST(self):
        self.handler()

    def do_DELETE(self):
        self.handler()

    def do_PUT(self):
        self.handler()

    def do_HEAD(self):
        self.handler()

    def do_OPTIONS(self):
        self.handler()

    def do_TRACE(self):
        self.handler()

    def do_CONNECT(self):
        self.handler()

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    # 设置守护线程，主程序退出自动关闭所有子线程
    daemon_threads = True


def server(ip, port):
    server = ThreadingHTTPServer((ip, port), MyHandler)
    log.info(f"并发HTTP服务启动：http://{ip}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        log.info("\n服务已关闭")

# ============ 全局节点注册表 ============
# peer_id -> {"ip": str, "port": int, "last_heartbeat": float, "name": str}
peers = {}
peers_lock = threading.Lock()

# 心跳超时时间（秒）
HEARTBEAT_TIMEOUT = 30


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


# ============ HTTP 接口 ============

@route("/p2p/register", "POST")
def register_peer(handler, params):
    """
    节点注册
    请求体: {"peer_id": "xxx", "ip": "x.x.x.x", "port": 9000, "name": "node1"}
    响应:   {"code": 0, "msg": "ok", "peers": [...]}
    """
    try:
        data = handler.str2json(params) if params else {}
        peer_id = data.get("peer_id")
        ip = data.get("ip")
        port = int(data.get("port", 0))
        name = data.get("name", peer_id)

        if not peer_id or not ip or not port:
            handler.Result(400, json.dumps({"code": 1, "msg": "参数不完整"}))
            return

        with peers_lock:
            peers[peer_id] = {
                "ip": ip,
                "port": port,
                "name": name,
                "last_heartbeat": time.time(),
            }

        # 构建节点列表（不包含自己）
        peer_list = [
            {"peer_id": pid, **info}
            for pid, info in peers.items()
            if pid != peer_id
        ]

        log.info(f"【注册】{name}({peer_id}) -> {ip}:{port}, 当前在线 {len(peers)} 节点")
        handler.Result(200, json.dumps({
            "code": 0,
            "msg": "ok",
            "peer_id": peer_id,
            "peers": peer_list,
        }))
    except Exception as e:
        log.error(f"注册失败: {e}")
        handler.Result(500, json.dumps({"code": 2, "msg": str(e)}))


@route("/p2p/heartbeat", "POST")
def heartbeat(handler, params):
    """
    节点心跳
    请求体: {"peer_id": "xxx"}
    响应:   {"code": 0, "msg": "ok"}
    """
    try:
        data = handler.str2json(params) if params else {}
        peer_id = data.get("peer_id")

        with peers_lock:
            if peer_id in peers:
                peers[peer_id]["last_heartbeat"] = time.time()

        handler.Result(200, json.dumps({"code": 0, "msg": "ok"}))
    except Exception as e:
        handler.Result(500, json.dumps({"code": 2, "msg": str(e)}))


@route("/p2p/peers", "GET")
def list_peers(handler, params):
    """
    获取在线节点列表
    查询参数: ?peer_id=自己的id（可选，排除自己）
    响应:   {"code": 0, "peers": [...]}
    """
    try:
        # 从 path 中提取 query string
        query = handler.path.split("?")[-1] if "?" in handler.path else ""
        my_id = ""
        if query:
            for pair in query.split("&"):
                if "=" in pair:
                    k, v = pair.split("=", 1)
                    if k == "peer_id":
                        my_id = v

        with peers_lock:
            peer_list = [
                {"peer_id": pid, "ip": info["ip"], "port": info["port"], "name": info["name"]}
                for pid, info in peers.items()
                if pid != my_id
            ]

        handler.Result(200, json.dumps({"code": 0, "peers": peer_list}))
    except Exception as e:
        handler.Result(500, json.dumps({"code": 2, "msg": str(e)}))


@route("/p2p/unregister", "POST")
def unregister_peer(handler, params):
    """
    节点注销
    请求体: {"peer_id": "xxx"}
    """
    try:
        data = handler.str2json(params) if params else {}
        peer_id = data.get("peer_id")

        with peers_lock:
            if peer_id in peers:
                removed = peers.pop(peer_id)
                log.info(f"【注销】{removed.get('name')}({peer_id}), 当前在线 {len(peers)} 节点")

        handler.Result(200, json.dumps({"code": 0, "msg": "ok"}))
    except Exception as e:
        handler.Result(500, json.dumps({"code": 2, "msg": str(e)}))


# ============ 过期节点清理 ============

def cleanup_expired_peers():
    """定期清理心跳超时的节点"""
    while True:
        time.sleep(10)
        now = time.time()
        removed = []
        with peers_lock:
            for pid in list(peers.keys()):
                if now - peers[pid]["last_heartbeat"] > HEARTBEAT_TIMEOUT:
                    removed.append(peers.pop(pid))
        for r in removed:
            log.info(f"【清理】超时节点 {r.get('name')}({pid})")


# ============ 带路由的 Handler ============

class P2PHandler(MyHandler):
    """重写 do_GET/do_POST，加入路由查找"""

    def do_GET(self):
        uri = self.path.split("?")[0]
        key = f"{uri}_GET"
        func = uri_map.get(key)
        if func:
            # 读取 query 参数体（此处简化，func 内部解析）
            try:
                func(self, "")
            except Exception as e:
                log.error(f"GET {uri} 异常: {e}")
                self.Result(500, json.dumps({"code": 2, "msg": str(e)}))
        else:
            self.Result(404, "Not Found")

    def do_POST(self):
        uri = self.path.split("?")[0]
        key = f"{uri}_POST"
        func = uri_map.get(key)
        if func:
            content_length = int(self.headers.get("Content-Length", 0))
            params = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else ""
            try:
                func(self, params)
            except Exception as e:
                log.error(f"POST {uri} 异常: {e}")
                self.Result(500, json.dumps({"code": 2, "msg": str(e)}))
        else:
            self.Result(404, "Not Found")


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """多线程 HTTP 服务器"""
    daemon_threads = True


# ============ 入口 ============

if __name__ == "__main__":
    # 支持命令行指定端口: python p2pserver.py 8888
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8888
    host = "0.0.0.0"

    # 启动清理线程
    threading.Thread(target=cleanup_expired_peers, daemon=True).start()

    server = ThreadingHTTPServer((host, port), P2PHandler)
    log.info(f"=" * 60)
    log.info(f"P2P 信令服务器启动: http://{get_local_ip()}:{port}")
    log.info(f"节点注册:  POST /p2p/register")
    log.info(f"节点列表:  GET  /p2p/peers?peer_id=xxx")
    log.info(f"心跳:      POST /p2p/heartbeat")
    log.info(f"注销:      POST /p2p/unregister")
    log.info(f"=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("服务器关闭")
        server.server_close()