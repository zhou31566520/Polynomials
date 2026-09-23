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
import sqlite3
import subprocess

import requests
from HTTPServer import *
class DB:
    """
    数据数据库操作类
    db = DB()
    # 创建任务表
    db.execute("create table if not exists task (id integer primary key autoincrement, name text, status text, create_time text, update_time text)")
    # 查看表数据
    db.execute("select * from task ")
    # 查看库中 有那些表
    db.execute("select name from sqlite_master where type='table'")
    """

    def __init__(self):
        # 链接本地数据库 task_mgmt.sqlite3,若不存在则创建task_mgmt.sqlite3文件
        self.conn = sqlite3.connect("task_mgmt.sqlite3")
        self.cursor = self.conn.cursor()

    def close(self):
        self.cursor.close()
        self.conn.close()

    def commit(self):
        self.conn.commit()

    def execute(self, sql, params=()):
        log.info(f"【执行】{sql} {params}")
        self.cursor.execute(sql, params)
        resp = self.cursor.fetchall()
        log.info(f"【查询结果】{resp}")
        return resp

    def insert(self, sql, params=()):
        self.cursor.execute(sql, params)
        self.commit()
        return self.cursor.lastrowid

    def update(self, sql, params=()):
        self.cursor.execute(sql, params)
        self.commit()
        return self.cursor.rowcount

    def delete(self, sql, params=()):
        self.cursor.execute(sql, params)
        self.commit()
        return self.cursor.rowcount

    def query(self, sql, params=()):
        return self.execute(sql, params)

    def query_one(self, sql, params=()):
        return self.execute(sql, params)[0]

    def query_count(self, sql, params=()):
        return self.execute(sql, params)[0][0]

    def query_all(self, sql, params=()):
        return self.execute(sql, params)


class Init:
    """
    环境初始化类
    """

    def __init__(self):
        self.db_init()
        self.db = DB()

    def close(self):
        self.db.close()

    def db_init(self):
        """
        初始化数据库
        """
        sqls = []
        sqls.append("create table if not exists t_task_main ("
                    "task_id integer primary key autoincrement, "
                    "task_name varchar(255) not null, "
                    "task_properties text not null default '{}', "
                    "status integer default 0 comment '0: 待执行, 1: 执行中, 2: 已完成', "
                    "task_result text not null default '{}', "
                    "task_type integer default 0 comment '0: 普通任务, 1: 依赖任务', "
                    "task_plateform varchar(32) default 'java' comment 'java, python, c, c++, c#, linux, windows, mac', "
                    "executor varchar(32), "
                    "create_time datetime, "
                    "creator varchar(32), "
                    "update_time datetime)")
        for sql in sqls:
            self.db.execute(sql)


class Business:
    @staticmethod
    def register(req):
        """
        执行器 向服务器注册自己， 发送 IP，监听端口，执行器名称
        """
        ip = req.get("ip")
        port = req.get("port")
        name = req.get("name")
        log.info(f"注册worker: {ip}, {port}, {name}")
        return {"returnCode":0,"returnMsg":"OK"}


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
    @route("/task/mgmt/task/register", "GET,POST")
    def receive_task(httpReq):
        log.info(f"【执行器注册任务】{httpReq.post_body}")
        resp = Business.register(httpReq.post_body)
        httpReq.Result(200, resp)

    @staticmethod
    @route("/task/mgmt/task/receive", "GET,POST")
    def receive_task(httpReq):
        log.info(f"【接收任务】{httpReq.post_body}")
        httpReq.Result(200, "OK")

    @staticmethod
    @route("/task/mgmt/index", "GET")
    def index(httpReq):
        msg = f"【线程并发服务】Path: {httpReq.path}\nClient: {httpReq.client_address}"
        log.info(msg)
        httpReq.Result(200, "success")

    @staticmethod
    @route("/task/mgmt/task/query", "GET")
    def query_task(httpReq):
        log.info(f"【查询任务】{httpReq.qry}")
        httpReq.Result(200, "OK")


if __name__ == "__main__":
    server("127.0.0.1",8080)
