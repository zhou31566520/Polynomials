#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/22 9:11
# @Author  : zhou31566520
# @File    : HTTPServer.py
# @Software: PyCharm
# @Desc    :
# HTTPServer.py
# 使用 SimpleHTTPServer 启动 HTTP 服务器
# 监听端口 8080
# 访问 http://localhost:8080 即可查看服务器运行状态
# 混合多线程能力 + HTTP服务基类 = 并发服务
import enum
import json
import logging
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

from Worker.com.joyes.http.HTTPHandler import HTTPHandler
from com.joyes.http import ALL
from com.joyes.http.Log import log

# 启动前加载当前模块下的所有类

# 创建本地日志类Log， 同事输出日志到文件和控制台
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
# 初始化加载所有路由， 读取当前模块下的所有使用 @route 装饰器注册的路由函数，
# 并将它们添加到 uri_map 中
def load_routes():
    for func in globals().values():
        if hasattr(func, 'uri') and hasattr(func, 'method'):
            uri_map[f'{func.uri}_{func.method}'] = func
    """加载所有路由"""

load_routes()

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

def get_route(uri, method):
    """获取路由处理函数"""
    return uri_map.get(f'{uri}_{method}')

def list_routes():
    """列出所有已注册的路由"""
    return list(uri_map.keys())

class Api:
    @staticmethod
    @route("/agent/task/receive", "GET,POST")
    def receive_task(httpReq):
        httpReq.parse_request()
        post_body = httpReq.rfile.read(int(httpReq.headers.get("Content-Length", 0))).decode("utf-8")
        log.info(f"【接收任务】{post_body}")
        httpReq.Result(200, "success")

    @staticmethod
    @route("/index", "GET")
    def index(httpReq):
        httpReq.parse_request()
        msg = f"【线程并发服务】Path: {httpReq.path}\nClient: {httpReq.client_address}"
        log.info(msg)
        httpReq.Result(200, "success")




class Command(enum.Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    OPTIONS = "OPTIONS"
    TRACE = "TRACE"
    CONNECT = "CONNECT"
    HEAD = "HEAD"
    PATCH = "PATCH"

class HTTPHandler(BaseHTTPRequestHandler):

    def Result(self, resp_code, resp_body, resp_headers=None):
        if resp_headers is None:
            resp_headers = {"Content-Type": "text/plain; charset=utf-8"}

        log.info(f"接口响应 {resp_body}")
        if resp_body == None:
            resp_body = ""
        self.send_response(resp_code,resp_body)
        for item, value in resp_headers.items():
            self.send_header(item, value)
        self.end_headers()

    def str2json(self,str):
        if (str.startswith("{") and str.endswith("}")) or (str.startswith("[") and str.endswith("]")):
            return json.loads(str)
        elif "=" in str or "&" in str:
            return {item.split("=")[1] for item in str.split("&")}

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
        try:
            self.uri = self.path.split("?")[0]
            self.qry = {}
            self.post_body = {}
            if "?" in self.path:
                self.uri = self.uri + "?" + self.path.split("?")[1]
                qry = self.path.split("?")[1]
                self.qry = self.str2json(qry)
                log.info(f"【查询参数】{self.qry}")
            if int(self.headers["Content-Length"]) > 0:
                post_body = self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8")
                self.post_body = self.str2json(post_body)
                log.info(f"【请求体】{self.post_body}")
            func = get_route(self.uri,self.command)
            if func:
                log.info(f"匹配到接口处理函数{func.__name__} {func.uri}_{func.method}")
                try:
                    func(self)
                except Exception as e:
                    log.error(f"Error in route {func.uri}_{func.method}: {e}")
                    self.Result(500, str(e))
            else:
                log.warning(f"route map {list_routes()}")
                log.warning(f"Route not found: {self.uri}_{self.command}")
                self.Result(404, "Not Found")
        except Exception as e:
            log.error(f"Error handling request: {e}")
            self.Result(500, str(e))

    def do_GET(self):
        self.command = Command.GET.value
        log.info(f"【线程并发服务】Path:GET {self.path}\nClient: {self.client_address}")
        self.handler()

    # POST 请求处理
    def do_POST(self):
        log.info(f"【线程并发服务】Path:POST {self.path}\nClient: {self.client_address}")
        self.command = Command.POST.value
        self.handler()

    def do_DELETE(self):
        log.info(f"【线程并发服务】Path:DELETE {self.path}\nClient: {self.client_address}")
        self.command = Command.DELETE.value
        self.handler()

    def do_PUT(self):
        log.info(f"【线程并发服务】Path:PUT {self.path}\nClient: {self.client_address}")
        self.command = Command.PUT.value
        self.handler()

    def do_HEAD(self):
        log.info(f"【线程并发服务】Path:HEAD {self.path}\nClient: {self.client_address}")
        self.command = Command.HEAD.value
        self.handler()

    def do_OPTIONS(self):
        log.info(f"【线程并发服务】Path:OPTIONS {self.path}\nClient: {self.client_address}")
        self.command = Command.OPTIONS.value
        self.handler()

    def do_TRACE(self):
        log.info(f"【线程并发服务】Path:TRACE {self.path}\nClient: {self.client_address}")
        self.command = Command.TRACE.value
        self.handler()

    def do_CONNECT(self):
        log.info(f"【线程并发服务】Path:CONNECT {self.path}\nClient: {self.client_address}")
        self.command = Command.CONNECT.value
        self.handler()

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    # 设置守护线程，主程序退出自动关闭所有子线程
    daemon_threads = True

if __name__ == "__main__":

    HOST = "127.0.0.1"  # 监听所有网卡
    PORT = 8080        # 指定端口
    server = ThreadingHTTPServer((HOST, PORT), HTTPHandler)
    log.info(f"并发HTTP服务启动：http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        log.info("\n服务已关闭")