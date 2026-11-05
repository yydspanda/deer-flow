#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2024-8-29 18:17
# @Author  : WANGWENBIN520
# @Site    :
# @File    : celery_config.py
# @Software: PyCharm


import socket
from datetime import timedelta

from kombu import Exchange, Queue

import util.root_config as rc

broker_url = rc.REDIS_URL
result_backend = rc.REDIS_URL

# 时区
timezone = "Asia/Shanghai"
enable_utc = False

# ============================================================
# 序列化
# ============================================================

# Celery 新式配置名必须使用小写。
# 如果任务参数暂时无法 JSON 化，可以继续使用 pickle。
# 使用 pickle 时必须确保只有可信生产者能够访问 Redis Broker。
task_serializer = "pickle"
result_serializer = "pickle"
accept_content = ["json", "pickle"]

# Redis result backend 中的任务结果保留 3 天。
result_expires = 60 * 60 * 24 * 3
result_persistent = False

# ============================================================
# 任务确认与重投策略
# ============================================================

# 任务执行完成后才 ACK。
# Worker/Broker 异常时允许未完成任务重新投递。
task_acks_late = True

# 普通业务异常或超时后 ACK，避免失败任务无限重投。
# 如果确实需要重试，应由任务代码显式 self.retry()。
task_acks_on_failure_or_timeout = True

# 执行任务的 prefork 子进程异常退出时重新投递。
task_reject_on_worker_lost = True

task_track_started = True

# Broker 连接丢失时终止正在执行的 late-ack 任务。
#
# 必须保持 True：
# 如果设为 False，原任务会继续运行，同时 Broker 可能重投消息。
# 此时 on_success 尚未执行、成功 key 尚不存在，两个任务可能并行执行。
worker_cancel_long_running_tasks_on_connection_loss = True

# 对相同 task_id、已经在 Redis result backend 中记录为 SUCCESS
# 的 redelivered 消息进行去重，避免再次进入 before_start/run。
worker_deduplicate_successful_tasks = True


# ============================================================
# Worker
# ============================================================

# 每个并发执行单元最多预留一个消息。
worker_prefetch_multiplier = 1

# Broker 重连期间，根据当前正在执行的任务减少预取量。
worker_enable_prefetch_count_reduction = True

# 每执行 50 个任务后回收子进程，控制 LlamaIndex/模型客户端长期内存增长。
worker_max_tasks_per_child = 50

# 不配置 worker_shutdown_timeout：
# Celery 5.4 没有这个配置项。


# ============================================================
# Broker 连接与重连
# ============================================================

broker_connection_retry = True
broker_connection_retry_on_startup = True

# Worker 运行期间无限尝试重新连接 Broker。
broker_connection_max_retries = None


# ============================================================
# TCP keepalive
# ============================================================

_keepalive_options = {}

if hasattr(socket, "TCP_KEEPIDLE"):
    # TCP 空闲 60 秒后开始探测。
    _keepalive_options[socket.TCP_KEEPIDLE] = 60

if hasattr(socket, "TCP_KEEPINTVL"):
    # 每隔 20 秒再次探测。
    _keepalive_options[socket.TCP_KEEPINTVL] = 20

if hasattr(socket, "TCP_KEEPCNT"):
    # 连续 3 次失败后判定连接失效。
    _keepalive_options[socket.TCP_KEEPCNT] = 3


# ============================================================
# Redis Broker transport
# ============================================================

# 当前任务通常执行 100～500 秒，1 小时已有充足余量。
#
# visibility_timeout 只控制：
# 未 ACK 消息多久后允许重新投递。
# 它不控制 Redis TCP 空闲连接。
visibility_timeout = 3600

broker_transport_options = {
    "visibility_timeout": visibility_timeout,
    # 检查 Redis 连接健康状况。
    "health_check_interval": 30,
    "socket_keepalive": True,
    "socket_keepalive_options": _keepalive_options,
    # 建立连接最多等待 10 秒。
    "socket_connect_timeout": 10,
    # Redis 操作发生 timeout 时允许重试。
    "retry_on_timeout": True,
    # 不要设置 socket_timeout。
    #
    # 原配置的 socket_timeout=60 是 60 秒而不是 10 分钟，
    # 它会主动制造 Timeout reading from socket。
    # 半开连接交给 health check、TCP keepalive 和重连机制发现。
}


# ============================================================
# Redis result backend
# ============================================================

redis_backend_health_check_interval = 30

redis_socket_keepalive = True
redis_socket_connect_timeout = 10

# 结果后端的单次 Redis 读写超时。
# 这不是 Celery 任务运行时间。
redis_socket_timeout = 120

redis_retry_on_timeout = True

result_backend_transport_options = {
    # Celery 5.4 的 Redis 文档建议三处 visibility_timeout 保持一致。
    "visibility_timeout": visibility_timeout,
    "retry_policy": {
        "timeout": 5.0,
    },
}


# ============================================================
# 任务模块
# ============================================================

# 必须是元组或列表，不能直接写 imports = "tasks.task"。
# 请根据实际模块路径调整。
imports = ("tasks.task",)


# 配置不同模型的队列并发
"""
TODO DeepSeek_V32 DeepSeek_V32_queue 实际会映射到 DeepSeek_V4_Flash_0731 了，待@wangwenbin520 修改模型队列配置后，@zhangjianming627 改zeus任务队列选择配置，其他用到的地方同样如此
"""
MODEL_QUEUE_CONFIG = {
    "Pingangpt_pro": {"queue": "Pingangpt_pro_queue", "concurrency": 1},
    "PingAnGPT_R": {"queue": "PingAnGPT_R_queue", "concurrency": 6},
    "DeepSeek_V32": {"queue": "DeepSeek_V32_queue", "concurrency": 6},
    "DeepSeek_V4_Flash_My": {"queue": "DeepSeek_V4_Flash_My_queue", "concurrency": 6},
    "Pub_DeepSeek_V4_Flash": {"queue": "Pub_DeepSeek_V4_Flash_queue", "concurrency": 15},
}

# 仅合并生产和本地的告警队列，STG保持原配置
if rc.ENV in {"PRD", "LOCAL"}:
    MODEL_QUEUE_CONFIG.pop("DeepSeek_V32", None)
    MODEL_QUEUE_CONFIG.pop("DeepSeek_V4_Flash_My", None)
    MODEL_QUEUE_CONFIG["Pub_DeepSeek_V4_Flash"]["concurrency"] = 27

# 任务队列的配置
doc_task_routing = {"queue": "doc_task", "routing_key": "doc_task_key"}
gen_content_routing = {"queue": "gen_content", "routing_key": "gen_content_key"}
default_routing = {"queue": "default", "routing_key": "default"}
# save_vector_routing = {"queue": "save_vector", "routing_key": 'save_vector_key'}
periodic_queue_routing = {"queue": "periodic_queue", "routing_key": "periodic_queue_key"}

# 任务队列路由,可以为每个任务指定队列
task_routes = {
    "tasks.task.doc_task": doc_task_routing,
    "tasks.task.gen_task": gen_content_routing,
    "tasks.task.scenario_task": gen_content_routing,
    "tasks.task.agent_task": default_routing,
    "tasks.task.doc_external_task": gen_content_routing,
    # "tasks.task.multiply": save_vector_routing
    "tasks.task.comm_agent_task": default_routing,
}

default_exchange = Exchange("default", type="direct")
task_queues = (
    Queue("default", default_exchange, routing_key="default", queue_arguments={"x-max-priority": 10}),
    Queue("doc_task", default_exchange, routing_key="doc_task_key", queue_arguments={"x-max-priority": 10}),
    Queue("gen_content", default_exchange, routing_key="gen_content_key", queue_arguments={"x-max-priority": 10}),
    Queue("Pingangpt_pro_queue", queue_arguments={"x-max-priority": 10}),
    Queue("PingAnGPT_R_queue", queue_arguments={"x-max-priority": 10}),
    Queue("DeepSeek_V32_queue", queue_arguments={"x-max-priority": 10}),
    Queue("DeepSeek_V4_Flash_My_queue", queue_arguments={"x-max-priority": 10}),
    Queue("Pub_DeepSeek_V4_Flash_queue", queue_arguments={"x-max-priority": 10}),
    # Queue('save_vector', default_exchange, routing_key='save_vector_key'),
    Queue("periodic_queue", routing_key="periodic_queue_key", queue_arguments={"x-max-priority": 10}),
)

if rc.ENV in {"PRD", "LOCAL"}:
    task_queues = tuple(
        queue
        for queue in task_queues
        if queue.name
        not in {
            "DeepSeek_V32_queue",
            "DeepSeek_V4_Flash_My_queue",
        }
    )

task_default_queue = "default"
task_default_exchange = "default"
task_default_routing_key = "default"

# 定时任务的配置，需要启动beat来产生定时任务
beat_schedule = {
    "cache_all_agent_files": {
        "task": "tasks.task.cache_all_agent_files_task",
        "schedule": timedelta(minutes=30),  # 每10分钟一次
        "options": {"queue": "periodic_queue"},
    },
}


# Rule refinement: reuse the existing LOCAL periodic worker and beat.
import os as _rule_refine_os

if all(_rule_refine_os.environ.get(key, "LOCAL").upper() == "LOCAL" for key in ("env_profile", "ENV_PROFILE")):
    beat_schedule["rule_refine_pending"] = {
        "task": "tasks.task.rule_refine_tick",
        "schedule": 30.0,
        "options": {"queue": "periodic_queue", "expires": 25},
    }

# Topic filtering uses the same periodic task; STG routing stays unchanged.
elif rc.ENV == "PRD":
    from pathlib import Path as _RuleRefinePath
    from my_workflows.zeus.rule_refine.topic_settings import topic_enabled as _topic_enabled

    if _topic_enabled(_RuleRefinePath(__file__).resolve().parents[1], _rule_refine_os.environ):
        beat_schedule["rule_refine_pending"] = {
            "task": "tasks.task.rule_refine_tick",
            "schedule": 30.0,
            "options": {"queue": "periodic_queue", "expires": 25},
        }
