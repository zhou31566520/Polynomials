#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/22 21:19
# @Author  : zhou31566520
# @File    : Api.py
# @Software: PyCharm
# @Desc    : $END$
from com.joyes.http.Route import route
from com.joyes.http.Log import log


@route("/agent/task/receive", "GET,POST")
def receive_task(httpReq):
    httpReq.parse_request()
    post_body = httpReq.rfile.read(int(httpReq.headers.get("Content-Length", 0))).decode("utf-8")
    log.info(f"【接收任务】{post_body}")
    httpReq.Result(200, "success")


@route("/index", "GET")
def index(httpReq):
    httpReq.parse_request()
    msg = f"【线程并发服务】Path: {httpReq.path}\nClient: {httpReq.client_address}"
    log.info(msg)
    httpReq.Result(200, "success")