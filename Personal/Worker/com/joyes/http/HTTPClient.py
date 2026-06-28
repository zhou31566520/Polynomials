#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# @Time    : 2026/6/22 9:11
# @Author  : zhou31566520
# @File    : HTTPClient.py
# @Software: PyCharm
# @Desc    : $END$
import requests

response = requests.get("http://127.0.0.1:8080/agent/task/receive?taskid=12235423512341&taskname=joyes")
print(response.status_code)
print(response.text)
