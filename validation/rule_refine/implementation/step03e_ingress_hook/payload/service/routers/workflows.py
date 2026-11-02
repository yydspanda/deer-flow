#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time    : 2024-12-24 14:31
# @Author  : WANGWENBIN520
# @Site    : 
# @File    : workflows.py
# @Software: PyCharm


"""
工作流相关的接口

自定义的一些复杂工作流和场景，通过统一的接口对外，便于isec进行接口的管理

"""
import uuid
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from model.db import schemas, crud
from service.dependencies import api_key_scheme
from service.io_models import WorkFlowMsgInput, IntentInput, WorkFlowTaskInput
from lib.my_llama_intent_slot import MultiIntentManagerAgent
from util.log_config import get_logger
from service.service_util import to_task_out
from my_workflows.workflow_router import flow_factory

logger = get_logger()


def mount_workflow_routes():
    router = APIRouter(prefix="/workflow",
                       tags=["workflow"],
                       dependencies=[Depends(api_key_scheme)],
                       responses={404: {"description": "Not found"}})

    # ===================统一定义路由========================
    router.post("/apply", summary="通用的同步工作流或Agent接口",
                description="执行指定子系统下的工作流场景")(flow_router)

    router.post("/apply_task", summary="通用的异步工作流或Agent任务执行",
                description="通用的异步工作流任务执行")(comm_flow_task_router)

    router.post("/task", summary="由系统触发的工作流或者agent任务",
                description="由系统触发的工作流任务,专门用于预警研判")(flow_task_router)

    router.post("/intent/intent_agent", summary="使用agent来意图识别和槽位填充",
                description="使用agent来实现多轮对话下的意图识别与槽位填充")(multi_intent_agent)
    return router


def _get_flow_instance(flow_msg: WorkFlowMsgInput):
    if flow_msg.session_id is None or flow_msg.session_id.strip() == "":
        flow_msg.session_id = uuid.uuid4().hex

    app_code = flow_msg.app_code
    flow_id = flow_msg.flow_id

    # 获取执行动作
    flow_cls = flow_factory.get(app_code, {}).get(flow_id, {}).get("flow_cls")
    if flow_cls is None:
        raise HTTPException(401, detail="指定的场景名不存在")
    else:
        # 数据校验
        flow_input_params = flow_factory.get(app_code, {}).get(flow_id, {}).get("input_data_validation")
        if flow_input_params is not None:
            try:
                flow_param_data = flow_input_params(**flow_msg.params).dict()
            except ValidationError as e:
                raise HTTPException(422, detail=e.json())
        else:
            flow_param_data = flow_msg.params

        # 实例化执行
        try:
            logger.info(f"当前执行的查询路径{flow_msg.flow_id}, 查询session_id:{flow_msg.session_id}, 查询请求：{flow_msg.query}")
            flow = flow_cls(session_id=flow_msg.session_id, **flow_param_data)
        except Exception as e:
            logger.error(e)
            raise HTTPException(500, detail=f"实例化错误:{e}")
        return flow


def flow_router(flow_msg: WorkFlowMsgInput):
    """
    总的工作流路由，对外只保留这一个接口，便于其他系统的对接
    :param flow_msg:
    :return:生成的内容为流式输出，包含多种不同的类型，gen_answer是必须有的，其他的为可选
    {"gen_answer":"xxx"}
    {"xxx":"xxx}
    """
    # 工作流校验，具体的实现还需要在celery的task中，否则会出现序列化错误
    flow = _get_flow_instance(flow_msg)
    # 区分是同步来返回还是调用异步任务
    if flow_msg.is_async:
        task_result = crud.workflow_task.create_comm_work_flow_task(flow_msg)
        return to_task_out(task_result)
    else:
        _do_gen_answer = flow.run_streaming(flow_msg.query)
        return StreamingResponse(_do_gen_answer, media_type="application/x-ndjson")


def comm_flow_task_router(flow_msg: WorkFlowMsgInput):
    """
    通用的任务工作流
    :param flow_msg:
    :return:生成的内容为流式输出，包含多种不同的类型，gen_answer是必须有的，其他的为可选
    {"gen_answer":"xxx"}
    {"xxx":"xxx}
    """
    # 实例化执行
    _get_flow_instance(flow_msg)
    task_result = crud.workflow_task.create_comm_work_flow_task(flow_msg)
    return to_task_out(task_result)


def multi_intent_agent(query_msg: IntentInput):
    """
    使用Agent来实现多轮对话下的意图识别和词槽填充
    生成的内容为流式输出，包含2种不同的类型，分别是：
    {"gen_answer":"xxx"}
    {"trace_msg":"['role':'user','content':'xxxx', ’role':'assistant','content':'xxxx']"}
    其中gen_answer是最终的结果，history是agent的研判思路过程
    """
    intent_agent = MultiIntentManagerAgent(session_id=query_msg.session_id, intent_introduce=query_msg.intent_introduce,
                                           intent_list=query_msg.intent_list,
                                           intent_map_slot=query_msg.intent_map_slot)

    _do_gen_answer = intent_agent.run_streaming(query=query_msg.query)

    return StreamingResponse(_do_gen_answer, media_type="application/x-ndjson")


def flow_task_router(flow_task_msg: WorkFlowTaskInput):
    """
    总的任务形式的工作流，接受预警id和预警数据，推送到任务队列并执行
    :param flow_task_msg:
    :return:
    """
    # 创建任务，顺序执行, 优先级最高
    task_info = schemas.WorkflowTaskCreate(session_id=flow_task_msg.session_id,
                                           app_code=flow_task_msg.app_code,
                                           alert_id=flow_task_msg.alert_id,
                                           flow_id=flow_task_msg.flow_id,
                                           alert_data=flow_task_msg.alert_data,
                                           status="created")
    # Optional rule-volume observation; it does not depend on model/task success.
    try:
        from my_workflows.zeus.rule_refine.runtime_monitor import observe_workflow_request
        observe_workflow_request(flow_task_msg)
    except Exception as monitor_error:
        try:
            logger.warning("rule_refine_ingress_unavailable error_type=%s", type(monitor_error).__name__)
        except Exception:
            pass
    try:
        task_result = crud.workflow_task.create_work_flow_task(task_info)

        logger.info(f"收到Agent任务，预警ID【{flow_task_msg.alert_id}】，任务id：{task_result['id'] if isinstance(task_result,dict) else task_result.id}")
    except Exception as e:
        logger.error("创建任务错误", e)
        raise HTTPException(500, detail=f"任务创建错误：{e}")

    return to_task_out(task_result)
