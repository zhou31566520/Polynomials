# TaskMgmt

## 项目介绍
    这是一个基于Python的项目，部署在服务器上， 用于管理任务, 包括任务的创建, 分配, 执行, 结果查询等功能

## 项目功能
###    执行机管理：用户可以管理不同的执行机(记录执行器名称， 执行器类型， 执行器IP， 状态等信息）， 包括添加, 查询等操作
#### 接口设计
    接口：POST /worker/mgmt
    请求参数：worker_id, worker_name,worker_ip,worker_port, worker_status, oper_type (add, query)
    响应参数：worker_id, worker_name, worker_type (success, failed)
#### 数据库设计
    执行机表：t_worker_master(worker_id, worker_name, worker_type (cpu, gpu, memory))

###    任务创建：用户可以创建新的任务， 包括任务的描述， 执行时间， 执行人等
#### 接口设计
    接口：POST /task/create
    请求参数：task_name, task_desc, task_exec_time, task_exec_user
    响应参数：task_id, task_create_status (success, failed)
#### 数据库设计
    任务表：t_task_master(task_id, task_name, task_desc, task_exec_time, task_exec_user, task_status (pending, running, completed, failed))

###    任务分配：任务管理服务器会根据任务的创建时间， 分配任务给不同的Worker
###    任务执行：Worker会根据任务的描述， 执行任务
###    任务结果查询：用户可以查询任务的执行结果

## 项目架构
    1. 任务管理服务器：负责接收任务， 分配任务给不同的Worker， 并接收Worker的任务执行结果
    2. Worker：负责执行任务， 并向任务管理服务器报告任务执行结果
    3. 数据库：用于存储任务信息， 任务执行结果， 任务状态等

## 项目部署
    1. 任务管理服务器：部署在服务器上， 监听端口为8080
    2. Worker：部署在不同的服务器上， 监听端口为8080
