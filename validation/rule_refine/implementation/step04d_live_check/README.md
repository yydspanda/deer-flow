# 第 4 步 D：人工材料的真实模型试调用

前一步内网 13 项测试通过（0.019 秒），假客户端已接上建议分析与校验。本步只验证项目现有模型代理是否能返回可解析的建议结果；人工材料不代表真实告警效果，LOCAL 任务表仍为空。

## 操作

保持现有服务运行，在另一个终端完整复制 [CHECK-STEP04D-LIVE.sh](CHECK-STEP04D-LIVE.sh)。它不安装或覆盖项目文件，不重启服务，不提交业务告警。先校验已安装模块版本，再从项目 LOCAL 配置读取代理与凭证。终端显示模型 `DeepSeek_V32` 和实际代理地址；输入 `LIVE` 才执行一次 SDK 请求。其他输入取消，无终端则停止。

真实 LLM 调用的明确确认依据 [validation/AGENTS.md](../../../AGENTS.md)：“Live LLM/provider runs require explicit confirmation”。本步骤请求由用户在内网执行，外网开发与测试只注入假客户端。

结果保存在 `~/sec-rule-refine-data/model-check-时间-随机串/`：目录 0700、JSON 文件 0600，包含人工 materials、完整 request、AnalysisDraft 输出 schema、预调用 manifest、返回正文、完整 result 和精简 summary。输入及 manifest 写入成功后才允许发起请求。正文最多保存 64KiB；更大或 Unicode 无效时记省略原因，保留原响应给适配器判断，不截取成可接受 JSON。

把终端最后的 summary JSON 贴回来即可。`format_valid=true` 且请求数为 1 表示返回通过现有解析与校验；状态可以是 proposal/no_change/insufficient_evidence/needs_existing_condition_change，不要求模型一定给出排除建议。返回无效 JSON、截断、超时等时会保存可用诊断并打印静态错误码。失败不自动再次请求，不输出密钥、完整 SDK 响应、推理字段或异常原文。

## 兼容与边界

源码依据：`util/root_config.py` 用 `env_profile` 选择环境，LOCAL 的 `LITELLM_OPENAI_SERVER` 指向本机 4001 代理；`nas/config.py` 的 `APP_KEY_DICT['litellm_key']` 与 LOCAL LiteLLM 代理配置相符；`my_workflows/zeus/flows/alert_controller.py` 默认模型为 `DeepSeek_V32`，该模型在 LOCAL 代理目录中已配置。不会重新要求用户粘贴密钥。仅允许本机代理 URL，不接受地址中嵌入凭证或查询参数；本次 HTTP 客户端使用 trust_env=false，不经过终端的 HTTP_PROXY 等代理配置，也不改变现有服务环境。

Python 与代理脚本读取环境变量名不同，所以在导入配置前同时校验 `env_profile` 和 `ENV_PROFILE`：缺失默认 LOCAL，小写 local 可接受，空字符串、空白、其他环境和冲突值均停止。不得自动修改环境后加载非预期配置；STG/PRD 配置导入存在远程取密行为，本脚本不走该路径。源码 LOCAL 导入只构造 Redis 对象，不连接数据库/Redis，不导入应用、Celery 或完整 Zeus 研判流程。

SDK 超时 240 秒、SDK 自动重试 0、一次 create；代理自身可能重试，上游尝试次数保持未知。客户端超时不证明代理或上游已取消。脚本不启动/重启代理；原 `start_proxy.sh start` 会清理已有端口进程，不应在这一步自动执行。

保留材料 `mode=demo` 与 `synthetic_fixture` 来源，模型 transport=live；模型名、SDK 版本、请求配置、提示词摘要、解析/条件校验模块摘要、返回状态、usage 和耗时均记录。现有校验仍是结构及单条件支持，engine_compatibility=not_verified。本步不调用 LocalRecommendationSink，不修改规则，不改变监测开关或在线任务。

`probe.py` 是脚本内嵌的同一 Python 程序；`test_probe.py` 仅用于本地离线测试，不随脚本写入内网项目。

## 本地验证记录

10 项离线测试通过（0.436 秒）：明确确认与取消、一次请求及参数透传、HTTP_PROXY 隔离、资源关闭、0700/0600 权限、预存失败零调用、超时留档、非法 JSON/截断、正文限额、非LOCAL环境提前停止。main 集成测试替换所有配置/SDK/HTTP客户端为假对象，未发起网络或模型调用。Ruff、Python 3.11 语法、Bash 语法及内嵌脚本一致性检查通过。

内网用户已输入 LIVE 执行 4D：SDK 请求 1 次，231.933ms 后返回 APIConnectionError；未获得模型名、结束原因或 usage，format_valid=false。模型返回兼容性与建议质量尚未验证，不能仅凭此摘要断定代理没启动或 localhost 解析异常，也不能确认上游是否收到了请求。结果目录：`/Users/zhangjianming627/sec-rule-refine-data/model-check-20261027-132721-hdb95px8`。

用户已执行 [本机连接检查](CHECK-PROXY-CONNECTION.sh)：4001 没有监听，localhost/127.0.0.1/::1 均 ConnectionRefusedError errno=61；8003 的 Python PID 55448 监听 127.0.0.1，IPv4 TCP 连接成功。现阶段可确认配置目标 4001 缺少监听服务，不需要用 IPv6 失败推断 localhost 解析问题。内网 SDK 版本为 openai 1.109.1、httpx 0.28.1、httpcore 1.0.9。

已提供 [单独补启动 LiteLLM](START-MISSING-LITELLM.sh) 命令，使用原 `start_proxy.sh` 已有的 `.venv/bin/litellm --config llm_service/litellm_proxy_config.local.yaml --host 127.0.0.1 --port 4001`。检查可执行文件及配置存在，4001 已有监听则不重复启动；前台运行，保持该终端打开。命令不会执行原整套启动脚本清理端口的逻辑，保留 8003；没有修改源码、配置、环境或业务数据，也不发送模型研判请求。

用户实际重启了三个脚本，随后确认 4001 PID 68232、8003 PID 68223 均在 127.0.0.1 监听；localhost:4001、127.0.0.1:4001、127.0.0.1:8003 的 TCP 连接均成功。仅 ::1:4001 拒绝符合IPv4绑定方式，不需要修改 localhost 或补IPv6监听。

用户重试结果已通过：SDK 请求 1 次，11,497.184ms，format_valid=true，status=insufficient_evidence，finish_reason=stop，error_code/error_type 均为空；usage 为 prompt 735、completion 540、total 1275。人工材料不足以提出建议是有效结果，不要求强行生成 proposal。结果目录：`/Users/zhangjianming627/sec-rule-refine-data/model-check-20261027-135550-_ry0skp0`。

请求模型为 DeepSeek_V32，返回字段为 default；原代理并未在 chat-completions 转发路径中改写为该值，仍不能仅凭此返回标签确认上游部署模型身份。此结果证明本次调用和现有解析通过，尚不证明真实建议效果或 ZEUS 执行兼容。下一步：[5A 条件组合试算](../step05a_filter_evaluation/README.md)。

不要用 4001 的 `/health` 作为零模型诊断：这是第三方 LiteLLM，源码包未包含该健康检查的实现。`start_proxy.sh status` 虽不启动服务，但其 lsof 未限 LISTEN，不能仅凭状态绿点判断监听正常。本检查直接限定 LISTEN。
