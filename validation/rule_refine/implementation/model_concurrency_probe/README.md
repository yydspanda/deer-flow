# Pub 模型接口 27 并发测试

独立的 Python 3.10+ 标准库脚本，不需要安装依赖。将 [probe.py](probe.py) 复制到内网
`sec_know_model` 根目录，命名为 `test_pub_concurrency.py`。

不下载 Python 文件时，也可完整复制执行 [RUN-27.sh](RUN-27.sh)。该命令内嵌相同脚本并校验摘要，
直接执行一次 27 并发批次；不会向项目中安装文件或修改启动配置。

先预览（不调用模型）：

```bash
python3 -B test_pub_concurrency.py
```

执行一次明确授权的批次（`--live` 会直接发出请求，不再询问）：

```bash
python3 -B test_pub_concurrency.py --live
```

默认同时发出 27 次请求，每条输入为 5000 个独立随机字母单元及少量指令。按之前同一路由
的实测，这约为 5000 tokens，准确数量以每条返回的 `usage.prompt_tokens` 为准。
27 条内容不同，请求编号也不同，避免完全相同文本造成整段缓存复用；服务实际缓存量若有
返回则记录在 `cached_tokens`。只要求回复 `OK`，不额外设置 `max_tokens`、thinking 参数。

脚本通过 AST 读取本地 `llm_service/openai_completion.py` 的
`PRD_MODEL_CONFIGS["Pub_DeepSeek_V4_Flash"]` 常量配置，然后直连该上游的
`/chat/completions`。不导入应用、不通过 Celery、本地代理、容量信号量或告警处理流程；
不访问 Redis/数据库，不提交告警、修改规则、启动或重启服务。凭据仅用于内存中的请求头，
不会写入结果。脚本不内置任何内部地址或凭据。

不预热、不自动重试、不跟随重定向、不使用环境代理，最多 27 次客户端 POST。
远端代理可能自行排队、重试或切换后端，远端尝试次数未知。某次失败不会取消其他已授权请求。
socket 超时默认 600 秒，不是整个请求的强制总时限；不增加自动重跑。

结果保存在新的 `~/sec-rule-refine-data/model-concurrency-*/probe/`：

- `requests.json`：仅人工请求正文，不包含认证头。
- `preview.json`：模型/路由来源摘要及本次参数。
- `request-NN.json`：每条的 HTTP 状态、开始/结束偏移、耗时、usage 和错误类型，完成即保存。
- `summary.json`：成功率、成功请求延迟 p50/p95/max、批次耗时、客户端请求重叠峰值等。

原始回答、思考内容和服务端错误正文不落盘，避免错误响应回显凭据。`finish_reason=length`
记为未完整返回；HTTP 200 内的业务错误也记为失败。正文是否符合 `OK` 单独记录。
收到响应头的时间不是模型首 token 时间；非流式测试不报告 TTFT。

`peak_in_flight=27` 仅表示客户端同时在等待 27 次 HTTP 调用，不能证明服务端有 27 个推理
槽位或 Celery 配置正确。短输出单批次也不能证明长时间吞吐和完整告警研判不会积压；
此时 `server_concurrency_verified`、`celery_capacity_verified` 和
`sustained_throughput_verified` 均保持 `false`。不预设“快”的阈值，按实际耗时评估。

用户已回传内网批次 `pub-27-concurrency-qrlaigl6`：27次请求，26成功、1次HTTP502；成功请求
均5045输入tokens，26条回复符合OK。客户端峰值27、启动跨度0.000727秒，批次4.453秒；
成功请求平均3.109秒、p50为3.146秒、p95为4.411秒。客户端重试0，远端尝试次数未知。
这是短输出单批次的用户回传证据，未运行Celery任务，也不代表真实告警研判耗时或长期吞吐。

离线测试使用假传输，不调用模型：

```bash
python3 -B -m unittest discover -s validation/rule_refine/implementation/model_concurrency_probe -p 'test_*.py' -v
```
