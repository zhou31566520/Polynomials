#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/22 9:11
# @Author  : zhou31566520
# @File    : HTTPHandler.py
# @Software: PyCharm
# @Desc    : $END$
import enum
import urllib
# 自定义请求处理器
from http.server import BaseHTTPRequestHandler

from com.joyes.http.Log import log
from com.joyes.http.Route import get_route, list_routes


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

    def handler(self):
        try:
            self.parse_request()
            self.uri = self.path.split("?")[0]
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
