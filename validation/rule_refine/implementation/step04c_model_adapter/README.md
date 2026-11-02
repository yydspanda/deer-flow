# 第 4 步 C：原项目模型客户端适配

第 4 步 B 已在内网通过 13 项测试（0.012 秒），人工回复形成约定格式的 proposal。本步新增 OpenAI 同步客户端适配和离线测试。LOCAL 任务表仍为空，测试使用人工材料与假客户端，尚未验证真实模型效果。

## 操作

在另一个终端完整复制 [APPLY-STEP04C.sh](APPLY-STEP04C.sh)。新增 `my_workflows/zeus/rule_refine/model_adapter.py` 与 `test/rule_refine/test_model_adapter.py`；不修改启动配置，不需要重启服务，不导入项目应用或读取凭证。相同版本可重复执行，不覆盖已有不同版本。

脚本只运行 unittest 与人工演示。最后 JSON 应显示 adapter_ready=true、status=proposal、sdk_request_calls=1、model_calls=0，数据库和 Redis 调用均为 0。这里的一次 SDK 形状调用发生在假客户端上。

## 接入边界

`analyze_with_model(materials, client, *, model, transport_mode, timeout_seconds=240)` 由调用方注入项目已有的 OpenAI 客户端，模型名和调用模式必填。使用 `with_options(max_retries=0, timeout=...)` 创建设置副本，保持调用方原设置；调用 `chat.completions.create(model=..., messages=..., stream=False, extra_body={"timeout": ..., "need_trans": True})`。不调用现有完整研判流程，不依赖 LangChain 或新模型工厂。

参考源码包：`my_workflows/zeus/utils/util_tools.py`、`my_workflows/zeus/skills/zeus_alert_main.py` 和 `llm_service/openai_completion.py`。原项目 `need_trans` 是代理正文转换参数，不是关闭模型思考。未擅自增加现有 Zeus 调用中没有使用的 response_format、temperature、max_tokens 或 thinking 控制。代理可能转换或忽略参数，真实兼容性仍需联调。

`transport_mode` 是调用方对客户端的声明，不会自动把真实客户端替换成假客户端。离线测试只能注入纯假客户端。真实测试后续单列明确确认；本步脚本不会发模型请求。

只接收单 choice、index=0、finish_reason=stop、assistant 的非空文本。拒绝截断、未知结束原因、拒绝回答、工具调用和超限文本。正文原样交给已实现的严格 JSON/条件校验；不从推理文字或 Markdown 中截取 JSON。16KiB 是接收后的正文校验上限，不是上游生成 token 预算。

复用前一步的提示词版本/摘要、回答摘要、材料来源、规则来源和条件校验记录；增加客户端请求配置/地址摘要、请求与返回模型名、结束原因、usage、耗时和静态错误码。缺失/非法 usage 记 null，实际模型名未知也记 null；不保存凭证、异常原文或推理内容。建议仅返回给调用方，完整结果和请求材料的受限目录保存由后续试调用/派发层负责。

本层每次分析至多调用一次 SDK create，没有自动输出修复或重试。原代理自身有可配置的上游重试，源码默认尝试数为 3，实际次数未知，记录 upstream_attempts=null。live 的 model_calls 按 SDK create 调用次数计，with_options 失败时为 0；mock 为 0。SDK 超时不证明上游已取消。请求超时和代理正文 timeout 使用相同值，不修改全局代理设置。

## 尚待验证

本地 13 项离线测试通过（0.034 秒），覆盖调用参数、无材料零调用、无重试、异常正文不泄漏、结束原因、拒绝/工具响应、16KiB/Unicode 边界、usage 未知值及前一步严格 JSON 校验衔接。Python 3.11 语法和 Ruff 检查通过。完整安装脚本首次安装、同版本重复执行、不同版本拒绝写入均已验证。

内网用户已确认本步 13 项测试通过（0.019 秒），adapter_ready=true、status=proposal、假客户端 sdk_request_calls=1，实际模型/数据库/Redis 调用均为 0。下一步见 [真实模型人工材料试调用](../step04d_live_check/README.md)。真实代理返回兼容性、真实材料效果、完整表达式试算、后台任务派发与建议持久化均须在各自步骤验证。当前结构/单条件校验不等于 ZEUS 引擎兼容或生产过滤效果。
