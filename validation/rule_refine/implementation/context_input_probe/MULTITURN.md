# 三轮完整对话输入测试

## 内网结果：第一轮成功，第二轮完整历史超限

2026-11-04用户回传 `context-multiturn-qq4et5v8/probe`。直连30.154.3.119/v1/chat/completions、deepseek-v4-flash；两次实际请求均显式携带max_input_tokens=1048576，没有设置max_tokens。

| 轮次 | 实际携带消息 | 服务报告完整输入tokens | 结果 |
|---|---|---:|---|
| 1 | system＋user1 | 180039 | HTTP200，输出2 tokens，13.900222秒 |
| 2 | system＋user1＋实际assistant1＋user2 | 360063 | HTTP400，上下文上限262144，3.086087秒 |
| 3 | 未发送 | 未测量 | 第二轮失败后停止 |

第一轮输入量来自成功响应usage，第二轮来自服务超长错误，不混计成功用量。第二轮主路由deepseek-v4-flash和备用路由deepseek-v4-flash-backup均返回同样的容量错误。客户端实际2次POST、0重试，完成1轮；远端尝试次数未知。两轮新增量均为180000字母单元，未独立精确分词第二轮新增消息；完整消息计数分别为2和4，历史没有裁剪、摘要或伪造。

已确认当前接口把携带的历史计入整次请求输入，按完整上下文262144拒绝第二轮。这个结果不是单条user消息的独立上限，也不是会话整个生命周期累计只能处理256K的证明。本次最大观察输入360063，尚未到500K（512000）；full_request_over_500k_observed和input_over_500k_accepted_verified均false，不能称为“已经发送500K并失败”。显式请求参数仍没有解除本次限制，其远端执行情况及具体部署配置仍未知。

第一轮request SHA-256为 `de150a71ad01470671392cb726527844ed1fd6fe804a0dec673c58abfb09dff6`；第二轮为 `27667b8a47011b3740390c4c9c706b212f2ba5871279e93fc7a8e8146b202b8d`。成功输入累计仅180039，不能与被拒绝的360063相加声称上下文容量。应用和配置未改，无Redis/数据库调用，不评价语义记忆质量；按用户回传记账，不声称本地访问内网原文件。

## 已执行脚本与计划

以下保留原测试方式。本次结果已取得，无需原样再跑或补发第三轮。

打开 [RUN-MULTITURN.sh](RUN-MULTITURN.sh)，复制全文到内网Mac终端，预览后输入LIVE。脚本自带Python源码，不需要安装补丁；默认项目目录 `$HOME/sec_know_model`，从现有PRD_MODEL_CONFIGS读取Pub_DeepSeek_V4_Flash上游地址及凭据，直接调用deepseek-v4-flash。

每轮新增18万个空格分隔字母单元，每轮使用不同固定seed的纯人工内容。沿用此前320000单元被服务计为320029输入tokens的观察，预计每轮新增约18万tokens，低于256K；这仍是估算，没有对应模型分词器逐条精确计数。

| 轮次 | 新增输入估算 | 完整messages预计输入 |
|---|---:|---:|
| 1 | 约18万tokens | 约18万tokens |
| 2 | 约18万tokens | 约36万tokens＋第一轮回复 |
| 3 | 约18万tokens | 约54万tokens＋前两轮回复 |

**每轮新增消息小于256K，不等于每次HTTP完整输入小于256K。** 这个脚本测试同一段完整对话：第二轮实际发送system＋user1＋模型真实assistant1＋user2；第三轮再追加真实assistant2＋user3。不会把三条独立请求、只发送新消息、累计计费用量或摘要后的窗口当作完整长对话。

继续在每次HTTP JSON顶层显式传max_input_tokens=1048576，与上一轮实验保持一致；请求没有max_tokens等输出额度，不修改服务端配置。最多3次POST，每轮超时600秒，客户端不重试。若第2轮已经因历史累计超限被拒绝，就保存该错误并停止，第3轮不会发出；这表示本次未达到500K目标，不能伪造assistant回复后继续。

500K按500×1024=512000 tokens计算。只有某轮成功完成、且该次服务报告usage.prompt_tokens大于512000，input_over_500k_accepted_verified才为true。返回未知用量时不能用字节或单元估算替代。各轮成功prompt_tokens之和重复计算了历史，successful_prompt_tokens_sum不是单次上下文容量。即使接收成功，也没有证明模型完整利用所有历史，history_semantic_retention_verified保持false。

输出在 `$HOME/sec-rule-refine-data/context-multiturn-*/probe/`，每轮保存实际request.json、脱敏response.redacted.txt和summary.json，根summary汇总实际轮数、完整输入量、停止原因及500K是否被接受。原应用、配置和已有结果不变；无业务数据、Redis或数据库调用。远端网关可能内部重试/fallback，实际尝试次数未知。

输入LIVE授权本次最多3轮；再次运行会新建目录并从第一轮重新请求，没有自动恢复或重试。终端会打印每轮进度及最终JSON。用户已完成上述内网测试，真实结果与离线测试分开记录。

13项新增离线测试通过，连同既有两种单次探测共40项通过；覆盖完整历史/真实回复保留、首错停止、严格512000边界、用量求和不伪证、缺失用量、保存失败和脱敏。Ruff、Python 3.11及复制脚本内外层Bash语法、内嵌源码一致性检查通过。离线没有网络或模型调用。

离线复验：

```bash
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/context_input_probe -p 'test_*.py' -v
backend/.venv/bin/python validation/rule_refine/implementation/context_input_probe/build_multiturn_command.py
bash -n validation/rule_refine/implementation/context_input_probe/RUN-MULTITURN.sh
```
