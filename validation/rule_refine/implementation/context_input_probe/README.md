# 独立长输入实测

目标：直接向当前配置的DeepSeek上游发送一次人工长文本，核对它是否接受超过262144 tokens的输入。不依赖规则优化demo，不读取告警材料，不修改应用或模型配置。

多轮实测已回传：[记录](MULTITURN.md)。context-multiturn-qq4et5v8/probe第一轮180039完整输入tokens成功，第二轮携带原文及实际回复后为360063，被262144上下文上限拒绝，第三轮未发送。两次均显式传max_input_tokens=1048576；客户端2POST、0重试，完成1轮。当前完整历史方式仍受整包上下文限制，尚未到500K，不把跨轮用量相加当作容量。[已执行脚本](RUN-MULTITURN.sh)及以下单次实验结果保持不变。

## 内网对照结果：显式传max_input_tokens=1048576仍被拒绝

2026-11-04用户回传 `context-max-input-p1spxwrj/probe`。客户端实际HTTP JSON顶层已含整数max_input_tokens=1048576，服务仍报告输入320029 tokens超过262144上下文，HTTP400/context_length_rejected，耗时3244.991ms。主路由deepseek-v4-flash及备用路由deepseek-v4-flash-backup均报相同错误；客户端1次POST、0重试，没有去掉参数再次请求，远端实际尝试次数未知。

| 请求 | 服务报告输入tokens | 服务报告上下文tokens | 结果 |
|---|---:|---:|---|
| 未传max_input_tokens | 320029 | 262144 | HTTP400拒绝 |
| 显式传max_input_tokens=1048576 | 320029 | 262144 | HTTP400拒绝 |

两次消息相同，当前源码摘要、模型与地址相同，去参后的请求摘要与基线完全一致。新请求SHA-256为 `d60ffab39f98835d5dc182203f7775c04cc7bba4d3ac1ddcb69078cfbd8794b7`；基线为 `7d020e858b39cb04b283758e28b357c23f2af045b5a18a7a476b915d799f7185`。新请求640245字节，旧请求640218字节。

已确认这个请求参数在本次没有解除限制；仍不能确定字段被远端忽略、丢弃，还是传至后端但没有改变限额。parameter_effect_verified=false保留，不把HTTP400解释成参数已被执行或明确不支持。需平台核对远端参数转发与主备实际上下文配置，不能据此确定推理框架或永久模型能力。此次仅更新用户回传的实测记录，不再发请求，不修改应用、配置、脚本或旧报告；没有Redis/数据库调用，也不声称本地访问过内网原文件。

## 显式参数脚本说明（本次已执行）

用户明确要求设置这个请求参数后再试。[RUN-WITH-MAX-INPUT.sh](RUN-WITH-MAX-INPUT.sh)为新复制脚本：同一模型路由、同一32万单元人工输入，只在实际HTTP JSON顶层新增整数 `"max_input_tokens": 1048576`。不是仅修改预览、客户端预算或模型目录；不发送max_tokens，不改变输出额度。预览后LIVE一次POST，服务若拒绝该参数也不会删除参数重试。

去掉新增字段后的请求SHA-256仍是上一轮 `7d020e858b39cb04b283758e28b357c23f2af045b5a18a7a476b915d799f7185`；新请求640245字节。此前未传参数时服务统计输入320029 tokens，本次消息原文不变，仍记录本次服务实际计数。summary明确保存max_input_tokens_parameter_sent=true、max_input_tokens=1048576；request.json保存实际发送的请求体，结果另存 `context-max-input-*/probe/`。

这次检验携带参数后的请求结果。成功不自动证明上游采纳了该参数；仍报256K也不能区分参数被忽略或受其他限制；不支持参数则保留服务原始错误。parameter_effect_verified保持false，客户端不会自行改写服务容量证据。旧probe.py、RUN-ONCE.sh和原内网报告不变，新复制脚本自带所需源码，不用先安装任何补丁。

7项新增离线测试通过，检查实际发送字节、前后报告字段、与基准请求一致、一次发送、参数拒绝/超时不去参重试和终端取消；原20项保留。离线测试没有真实模型调用；用户另行执行的带参数实测见上方，不混计两类调用。

## 内网结果：实际320029 tokens被262144上下文限制拒绝

2026-11-04用户回传 `context-input-probe-pkl6y8v4/probe`，直连 `http://30.154.3.119/v1/chat/completions`，模型deepseek-v4-flash；输入640218字节，服务明确报告输入320029 tokens、上下文262144 tokens，HTTP400/context_length_rejected，耗时3790.325ms。这次超过256K输入已得到服务计数确认，未被接受；成功响应usage为空，数字来源是错误原文，不能写成成功推理用量。

错误中主路由deepseek-v4-flash和备用路由deepseek-v4-flash-backup都返回同样的容量拒绝。客户端实际1次POST、0重试；远端尝试次数未知，不能把客户端1次等同于底层只尝试1次。请求未发送max_input_tokens或max_tokens；没有修改模型目录或部署参数，没有Redis/数据库调用。

这排除了本地规则demo输入预算和本地代理导致本次拒绝：容量限制来自当前远端服务链路。仍不能仅凭这段错误区分远端网关策略、路由版本或推理部署参数，也不能确定底层是vLLM还是SGLang，更不能推断所有DeepSeek服务都无法提供1M上下文。平台方需核对主备路由实际模型版本和上下文配置；单改目录描述是否能影响实际容量尚未测试。无需原样重复本请求。

请求SHA-256：`7d020e858b39cb04b283758e28b357c23f2af045b5a18a7a476b915d799f7185`；路由源码SHA-256：`c8e31deb92a9e7966d5e31a6bd1ab9e856d2250273e5f50880ca661f5021dd3e`。以上依据用户贴回结果，不表示本地访问过内网文件；原始响应和人工请求仍留在内网报告目录。

## 使用方式与结果解释

本次已执行，以下保留操作方式；再次输入LIVE会产生一次新的请求。

打开 [RUN-ONCE.sh](RUN-ONCE.sh)，复制全文到内网Mac终端。默认项目目录为 `$HOME/sec_know_model`。脚本只用Python标准库，以AST读取 `llm_service/openai_completion.py` 中 `PRD_MODEL_CONFIGS["Pub_DeepSeek_V4_Flash"]` 的地址、模型与鉴权信息；不执行该配置源码。预览会显示实际目标地址和模型，输入LIVE后只发送一次POST，客户端不重试。

该请求**直连配置中的上游**，绕过本地LiteLLM代理、适配器及规则优化的取样/输入预算。因此结果验证的是该上游路由本次的接收能力，不代表本地全链路或每个底层部署都支持相同容量。上游内部可能重试或fallback，其次数未知。

输入使用固定seed生成32万个空格分隔的小写字母单元，预期产生长token序列；**单元数、字符数和字节数都不冒称精确token数**。没有加载对应模型分词器，实际输入量只取服务返回的usage或明确的上下文错误数字。请求仅含model/messages/stream，不发送max_input_tokens、max_tokens或思考参数。要求回答OK，不另设输出token上限，等待上限600秒。

| 结果verdict | 含义 |
|---|---|
| accepted_over_256k | 有效模型回答且usage.prompt_tokens大于262144，本次上游接受了超256K输入 |
| context_length_rejected | 服务错误明确给出输入tokens及上下文tokens，按这些实际数字判断 |
| completed_but_over_256k_not_proven | 请求完成，但实际用量缺失或未超过262144，不能称为超256K通过 |
| request_failed_capacity_unknown / request_or_recording_failed_capacity_unknown | 鉴权、超时、其他错误或保存失败，不能归因于256K容量 |

结果另存 `$HOME/sec-rule-refine-data/context-input-probe-*/probe/`：summary.json、纯人工request.json及脱敏response.redacted.txt。凭据和请求头不保存；应用目录没有写入。只需贴回终端精简JSON。重跑并输入LIVE会再次请求，脚本不会自动放大输入或追加测试。

原RUN-ONCE.sh只构造实际长输入，没有传max_input_tokens；新增RUN-WITH-MAX-INPUT.sh显式传该请求字段。两者均未修改模型目录或部署配置。一次成功不能证明1M上限，一次失败也不代表服务永远不能扩容。

20项离线测试通过，覆盖实际token阈值、缺失用量、业务错误、上下文错误数字、超时/鉴权、一次发送、脱敏、配置只读及终端取消。Ruff、Python 3.11语法、复制脚本内外层Bash语法及内嵌源码一致性检查通过。离线测试没有调用内网服务或模型；用户另行执行的真实容量结果见上方，两类证据分开记录。

离线复验命令：

```bash
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/context_input_probe -p 'test_*.py' -v
backend/.venv/bin/python validation/rule_refine/implementation/context_input_probe/build_command.py
backend/.venv/bin/python validation/rule_refine/implementation/context_input_probe/build_max_input_command.py
bash -n validation/rule_refine/implementation/context_input_probe/RUN-ONCE.sh
bash -n validation/rule_refine/implementation/context_input_probe/RUN-WITH-MAX-INPUT.sh
```
