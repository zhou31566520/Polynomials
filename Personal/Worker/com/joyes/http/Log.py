#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/22 10:59
# @Author  : zhou31566520
# @File    : Log.py
# @Software: PyCharm
# @Desc    : $END$

# 日志模块
import logging
import sys


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