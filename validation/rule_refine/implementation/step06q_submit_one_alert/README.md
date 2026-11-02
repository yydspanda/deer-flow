# 6Q：一条真实历史告警的入口联调

6P内网已准备成功：`ingress-one-ready-367kxcn7/request.json`，135592字节，SHA256为`283adc1f05877538b0f33658c25e92d90bfb462f4710fe424506cd229fd50572`。来自原样本第14行，ptp-nids/RPAADM_002627；原ID、内容、请求包装未改，接口格式校验通过，归属预览只有1个规则。此处没有保存原始告警正文或ID。

## 操作

打开`SUBMIT-ONE-ALERT.sh`，全部复制到内网另一个终端执行，不需要安装文件或更新部署包。

1. 脚本自动读取上述准备目录、检查原始内容摘要、回调/接口源码、本机8090进程归属与LOCAL环境、LOCAL任务表是否已有该告警。
2. 打印具体影响后，由操作者输入`LIVE`才发送。其他输入取消，取消不会产生提交标记。
3. 最多向`127.0.0.1:8090/workflow/task` POST一次，直接发送已保存的请求字节；凭证只在本机读取，不导入应用配置、不打印或保存令牌、不使用环境代理、不跟随重定向。
4. 收到任务ID后，在固定LOCAL SQLite只读查询最多约30秒，同时检查API日志中这条告警的监测登记。终端和结果文件只保存摘要。
5. 如果仍是created/running，稍后再次执行同一份脚本即可。**再次执行只查询，不提交。** 超时、响应丢失或进程中断也不会自动重发；没有明确任务ID时标为未确认，不猜测完成。

结果目录固定为`~/sec-rule-refine-data/ingress-one-submit-283adc1f05877538`。提交标记先于HTTP写入并fsync，文件锁覆盖提交和收集；不要为了重跑而删除标记。HTTP401/403/422或其他故障也先回传摘要，再决定是否需要显式恢复，不自行重灌。

## 这次操作的真实影响

这是原业务入口，会写任务表、进入原Celery队列、调用原研判模型，并由原成功/失败钩子回调Zeus。一次HTTP请求不等于一次SDK请求，原流程的模型调用和重试仍沿用其原有行为。

源码LOCAL和STG分支的Redis主机、端口、数据库编号均不同；Celery的broker_url和result_backend直接使用当前root_config.REDIS_URL。按各自配置启动的进程使用不同broker，同名队列不会因此跨环境混用。LOCAL的Zeus回调URL配置与STG相同，这是独立的HTTP回调设置，不表示两者共用Redis。此前将非回环Redis描述成“共享Redis”缺乏证据，已更正工具提示；不为该推测增加诊断步骤。本脚本仍不把静态配置和API进程检查当作worker实际执行或回调成功的证据。

本步骤只验证一条告警的HTTP入口、计数登记和原任务落库/处理衔接。不会降低1000条监测阈值，也不会人为构造其他999条；单条本身不足以达到阈值，后台仍idle并非失败。如果其他真实流量已积累到阈值，原后台仍按当前开关自然处理。

本脚本对模型调用次数和Zeus回调结果标为未测量/未验证。任务表successed不证明回调成功；日志缺失或轮转不证明监测失败。监测日志按告警身份及本次提交后区间关联，如果其他请求并发推送相同ID，不能独占归因。准确任务ID及所存告警内容相等是任务关联的主要依据。

## 验证

```bash
backend/.venv/bin/python -B validation/rule_refine/implementation/step06q_submit_one_alert/test_submit.py
backend/.venv/bin/python -B validation/rule_refine/implementation/step06q_submit_one_alert/build_command.py
bash -n validation/rule_refine/implementation/step06q_submit_one_alert/SUBMIT-ONE-ALERT.sh
```

13项本地测试通过：原请求保留、AST凭证提取不执行应用、API进程检查不冒充worker确认、单次POST及不跟随重定向、超时不重发、准确任务ID/原正文比对、日志只取新匹配摘要、取消不提交、持久标记先于请求、再次执行仅收集、中断后不重发、已有任务停止、SQL材料长度限制及异常值不输出、完整内嵌命令非LOCAL提前停止。测试使用人工数据、临时SQLite及HTTP/进程测试实现，无真实业务POST或模型调用。

内网首次提交已回传：HTTP200/accepted，任务a0249fb8-3239-4522-a77e-cdf6bdd14d7a；LOCAL SQLite准确任务存在且正文与原请求相等，目前created、无回答、失败次数0。新监测日志ready，1个规则计数、1个新增、0重复、0信号。已确认入口接收、监测登记和任务落库；worker研判开始/完成及回调仍待观察。稍后重复原脚本仅查询，无需再提交或修改Redis。

内网随后只读查询已回传：同一准确任务为successed，原材料相等、回答存在、失败次数0、completion_verified=true；本次提交次数0，累计提交标记1。真实入口、监测登记、原研判任务完成及结果落库已验证。仍未测量原流程模型调用次数和Zeus回调结果；new_signals=0，未据此声称突增阈值触发或自动建议已验证。下一步为[6R历史样本建议验证](../step06r_historical_review/README.md)。
