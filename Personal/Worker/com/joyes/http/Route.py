#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/24 15:12
# @Author  : zhou31566520
# @File    : Route.py.py
# @Software: PyCharm
# @Desc    : $END$

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

