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
import json
import logging
import os
import shutil
import sys
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
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


class GitRepository:
    '''
    Git仓库工具类：根据给出的仓库URL、用户名和密码，下载仓库代码到临时目录，并提供获取仓库本地路径和删除临时仓库的方法
    '''

    def __init__(self, repo_url, user, password):
        '''
        初始化Git仓库工具类
        :param repo_url: 仓库URL或仓库名称（如：https://github.com/user/repo 或 repo_name）
        :param user: GitHub用户名
        :param password: GitHub密码或访问令牌
        '''
        self.repo_url = repo_url
        self.user = user
        self.password = password
        # 提取仓库名称
        self.repo_name = self._extract_repo_name(repo_url)
        # 构建带有认证的仓库URL
        self.auth_url = f"https://{user}:{password}@github.com/{user}/{self.repo_name}.git"
        # 临时目录路径
        self.temp_dir = os.path.join("tmp", self.repo_name)

    def _extract_repo_name(self, repo_url):
        '''
        从仓库URL中提取仓库名称
        :param repo_url: 仓库URL或仓库名
        :return: 仓库名称
        '''
        if repo_url.startswith("https://") or repo_url.startswith("git@"):
            # 移除 .git 后缀
            if repo_url.endswith(".git"):
                repo_url = repo_url[:-4]
            # 提取最后一部分作为仓库名
            parts = repo_url.split("/")
            return parts[-1]
        # 如果已经是仓库名，直接返回
        return repo_url

    def clone(self):
        '''
        克隆仓库代码到临时目录
        :return: 仓库本地路径，如果克隆失败返回 None
        '''
        try:
            # 如果临时目录已存在，先删除
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)

            # 创建临时目录的父目录
            os.makedirs(os.path.dirname(self.temp_dir) or ".", exist_ok=True)

            # 执行 git clone 命令
            result = subprocess.run(
                ["git", "clone", self.auth_url, self.temp_dir],
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode == 0:
                log.info(f"仓库克隆成功: {self.temp_dir}")
                return self.temp_dir
            else:
                log.error(f"仓库克隆失败: {result.stderr}")
                return None
        except Exception as e:
            log.error(f"克隆仓库时发生异常: {str(e)}")
            return None

    def get_local_path(self):
        '''
        获取仓库本地路径
        :return: 仓库本地绝对路径，如果目录不存在返回 None
        '''
        if os.path.exists(self.temp_dir):
            return os.path.abspath(self.temp_dir)
        else:
            log.warning(f"仓库本地路径不存在: {self.temp_dir}")
            return None

    def delete_temp_repo(self):
        '''
        删除临时仓库目录
        :return: 删除成功返回 True，失败返回 False
        '''
        try:
            if os.path.exists(self.temp_dir):
                shutil.rmtree(self.temp_dir)
                log.info(f"临时仓库已删除: {self.temp_dir}")
                return True
            else:
                log.warning(f"临时仓库目录不存在: {self.temp_dir}")
                return True
        except Exception as e:
            log.error(f"删除临时仓库失败: {str(e)}")
            return False


class Api:
    '''
    接口类
    '''
    @staticmethod
    @route("/agent/task/receive", "GET,POST")
    def receive_task(httpReq):
        log.info(f"【接收任务】{httpReq.post_body}")
        httpReq.Result(200, "OK")

    @staticmethod
    @route("/index", "GET")
    def index(httpReq):
        msg = f"【线程并发服务】Path: {httpReq.path}\nClient: {httpReq.client_address}"
        log.info(msg)
        httpReq.Result(200, "success")

    @staticmethod
    @route("/agent/task/query", "GET")
    def query_task(httpReq):
        log.info(f"【查询任务】{httpReq.qry}")
        httpReq.Result(200, "OK")


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

if __name__ == "__main__":

    HOST = "127.0.0.1"  # 监听所有网卡
    PORT = 8080        # 指定端口
    server = ThreadingHTTPServer((HOST, PORT), MyHandler)
    log.info(f"并发HTTP服务启动：http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
        log.info("\n服务已关闭")