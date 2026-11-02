#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2024-2-22 14:34
# @Author  : WANGWENBIN520
# @Site    :
# @File    : run.py.py
# @Software: PyCharm

"""
启动所有的接口和调度
"""

from contextlib import asynccontextmanager
from urllib.parse import urljoin

import requests
import torch
import uvicorn
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from pytz import timezone

import util.root_config as rc
from service.api import create_api_app
from service.routers import local_model
from util.log_config import get_logger, uvicorn_log_config

logger = get_logger()

# 调度器
scheduler = AsyncIOScheduler(
    executors={"default": {"type": "threadpool", "max_workers": 4}},
    job_defaults={
        "coalesce": True,
        "max_instances": 1,  # 同一个任务在线程池中最多跑的实例数
        "misfire_grace_time": 10,  # 错过多少秒后还能继续执行
    },
    job_stroes=MemoryJobStore(),
    timezone=timezone("Asia/Shanghai"),
)


# 定期处理队列中的任务
# 定期处理队列中的任务
def split_task_trigger():
    requests.get(url=urljoin(f"http://localhost:{rc.SERVER_PORT}", "/task/auto_proc_split_task"), headers={"app-key": rc.APP_KEY["common"]})


def gen_task_trigger():
    requests.get(url=urljoin(f"http://localhost:{rc.SERVER_PORT}", "/task/auto_proc_gen_task"), headers={"app-key": rc.APP_KEY["common"]})


def vec_task_trigger():
    requests.get(url=urljoin(f"http://localhost:{rc.SERVER_PORT}", "/task/auto_proc_vec_task"), headers={"app-key": rc.APP_KEY["common"]})


# 自动清除过期session
def auto_clean_session_trigger():
    requests.get(url=urljoin(f"http://localhost:{rc.SERVER_PORT}", "/task/auto_clean_session"), headers={"app-key": rc.APP_KEY["common"]})


def run_api_server():
    # Load optional host-local monitor settings before serving requests.
    try:
        import json
        from my_workflows.zeus.rule_refine.local_settings import configure_rule_monitor
        monitor_config = configure_rule_monitor()
    except Exception as error:
        import os
        os.environ["RULE_REFINE_MONITOR_ENABLED"] = "0"
        monitor_config = {"status": "configuration_error", "enabled": False, "error_type": type(error).__name__}
    try:
        emit = logger.warning if monitor_config["status"] == "configuration_error" else logger.info
        emit("rule_refine_monitor_config %s", json.dumps(monitor_config, ensure_ascii=False))
    except Exception:
        pass
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # app启动之前
        # 1. 启动任务调度
        logger.info("API服务启动")

        # TODO 等调用方切换到mannage的doc接口后，需要注释掉，在此之前保留
        # scheduler.add_job(split_task_trigger, 'interval', seconds=60, max_instances=1)

        # scheduler.add_job(gen_task_trigger, 'interval', seconds=60, max_instances=1)

        # scheduler.add_job(vec_task_trigger, "interval", seconds=60, max_instances=1)

        # scheduler.add_job(auto_clean_session_trigger, 'interval', hours=2, max_instances=1)

        scheduler.start()
        yield

        # app关闭之前, 关闭调度，清空显存, 推送向量数据库中的缓存
        logger.info("关闭调度")
        scheduler.shutdown()

        logger.info("清空显存")
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()

        logger.info("推送向量数据库中缓存的数据")
        local_model.vector_store.col.flush()

    # 应用创建
    api_app = create_api_app(lifespan=lifespan)
    return api_app


if __name__ == "__main__":
    app = run_api_server()
    uvicorn.run(app, host="0.0.0.0", port=rc.SERVER_PORT, log_config=uvicorn_log_config)
