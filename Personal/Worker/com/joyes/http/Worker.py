#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/22 9:11
# @Author  : zhou31566520
# @File    : Worker.py
# @Software: PyCharm
# @Desc    :
# Worker.py
# 使用 SimpleHTTPServer 启动 HTTP 服务器
# 监听端口 8080
# 访问 http://localhost:8080 即可查看服务器运行状态
# 混合多线程能力 + HTTP服务基类 = 并发服务
import json
import os
import shutil
import subprocess
import requests
from HTTPServer import *


def register_uri(ip, port):
    """
    执行器 向服务器注册自己， 发送 IP，监听端口，执行器名称
    """
    # 获取当前 IP 地址
    # 根据当前操作系统类型不同，执行不同的命令获取IP地址
    # ip = subprocess.check_output(["ifconfig", "en0"], encoding="utf-8").splitlines()[1].split(" ")[2]
    # 执行器名称
    executor_name = f"{ip}:{port}"
    # 注册到服务器
    url = "http://127.0.0.1:8080/task/mgmt/register"
    headers = {"Content-Type": "application/json"}
    data = json.dumps({"ip": ip, "port": port, "name": executor_name})
    response = requests.post(url, headers=headers, data=data)
    log.info(f"【注册】{response.text}")
    return response.json()


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


class Business:
    @staticmethod
    def register_uri():
        """
        执行器 向服务器注册自己， 发送 IP，监听端口，执行器名称
        """
        # 获取当前 IP 地址
        ip = subprocess.check_output(["ifconfig", "en0"], encoding="utf-8").splitlines()[1].split(" ")[2]
        # 监听端口
        port = 8080
        # 执行器名称
        executor_name = f"{ip}:{port}"
        # 注册到服务器
        url = ""
        headers = {"Content-Type": "application/json"}
        data = json.dumps({"ip": ip, "port": port, "name": executor_name})
        response = requests.post(url, headers=headers, data=data)
        log.info(f"【注册】{response.text}")
        return response.json()



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

    @staticmethod
    @route("/agent/register", "POST")
    def register(httpReq):
        log.info(f"【注册】{httpReq.post_body}")
        httpReq.Result(200, Business.register_uri())


if __name__ == "__main__":
    ip = "127.0.0.1"
    port = 8081
    register_uri(ip,port)
    server(ip, port)