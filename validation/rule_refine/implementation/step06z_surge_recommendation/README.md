# 6Z：主要突增场景的AI过滤建议与复核

本步在内网 Mac **实际调用配置中的模型两次：生成建议、独立上下文复核**。不做程序过滤试算，不计算全量排除比例，目标是推荐能减少主要重复场景的条件。

这次先验证用户提供的 `20261102175803453.csv` 中 `RPAADM_000598` 案例。工具仅在内存中删除规则快照后来新增的排除条件，原规则文件不变；删除记录和人工预期不会送给模型。运营已经确认的“统一巡检、运维或扫描”背景会提供给模型，具体命令和过滤条件由模型从原始日志识别。

这是独立实测工具，**不安装或替换内网正在运行的后台**。Redis历史取样辅助已提供并离线测试，尚未接入在线消费者；本次脚本使用CSV取样，不能据此宣称线上自动链路已升级。

## 1. 执行

**当前先执行2.10节的只读路由检查，暂不推进正式安装或新增模型调用。** 独立分析已在内网完成，见2.9节；无需重跑同一批材料。`surge-independent-d2lva4bc`返回`proposal`，模型自主选用`event_content`中的命令内容，识别出了混合告警中的其他活动，格式及引用/字段检查通过。以下入口和2.1—2.8保留为实测过程及复现方式，不需重新依次执行。此次独立建议不等于原两阶段复核或线上效果验收完成。

保持模型代理运行。打开 [REVIEW-SURGE-WITH-AI.sh](REVIEW-SURGE-WITH-AI.sh)，**复制全文到内网Mac终端执行**。不必把这个脚本文件传到内网。

按提示填写：

1. 本次告警CSV完整路径。已按内网实际位置默认使用 `$HOME/sec_know_model/nas/original/20261102175803453.csv`，直接回车即可；文件在其他地方就填实际路径。
2. 突增前的历史告警CSV路径：需要与本次文件相同的任务导出列格式；没有就直接回车。
3. 预览模型与取样数量，输入 `LIVE` 开始。

规则配置仍读 `$HOME/sec-rule-refine-data/rules.csv`；模型仍读项目的 `config/rule_refine.analysis.local.json`，应显示你已选择的 `Pub_DeepSeek_V4_Flash`。无需重新设置环境变量或重启API/worker。

本次告警文件摘要必须与已检查的文件一致，避免对其他文件错误套用本案例已确认的运维背景。脚本记录当前项目配置和源码摘要，不再要求它们等于早期交付包的固定摘要；两次请求期间如果配置变化会停止，并保存已有结果。

## 2. 看结果

结束时显示：

```text
本次结果目录：/Users/你的账号/sec-rule-refine-data/surge-ai-review-随机后缀
建议正文：.../REPORT.md
```

查看建议正文：

```bash
bash <<'SOC_SHOW_SURGE_REPORT'
set -euo pipefail
read -r -p '本次结果目录完整路径：' soc_result_dir </dev/tty
cat "$soc_result_dir/REPORT.md"
SOC_SHOW_SURGE_REPORT
```

**贴回终端最后的JSON和 `REPORT.md` 正文。** JSON中的 `status=reviewed` 表示两次模型回答及格式检查完成，最终意见以 `analysis_status` 和正文为准，不表示建议已采纳或实际降量已验证。

`recommendation.json` 保留 `rule_code / change_type / filter`，并附AI意见及复核方式。生成和复核的原请求、原回答、实际token用量分别保存在 `generate/`、`review/`，便于离线诊断。

失败时已返回的回答仍保留；本工具不自动重试。重新执行并再次输入LIVE会新建目录、重新调用，最多两次SDK请求，代理内部尝试次数另属服务行为。先贴回失败摘要，避免盲目重复调用。

## 2.1 已定位并修复：内网路由上下文为262144 tokens

用户回传 `surge-ai-review-953j3rvi`，首轮157条告警、SDK请求一次、约18.811秒BadRequestError。随后代理日志明确：实际输入 **336423 tokens**，上下文上限 **262144 tokens**；主路由与回退路由均报同一容量。因此此前假定1M不适用于这条内部路由，不能通过回退消除超限。

新版复制脚本改为本目录的预算模块：`Pub_DeepSeek_V4_Flash` 按服务已报告的262144，沿用用户80%策略，输入估算预算 **209715**。预览应显示 `context_tokens=262144`、`input_token_budget=209715`、`context_capacity_basis=observed_internal_service_error`。日志观测绑定该内部别名和时间，不推广成官方模型规格；没有在线容量探测。

重新复制 [REVIEW-SURGE-WITH-AI.sh](REVIEW-SURGE-WITH-AI.sh) 全文执行，当前CSV路径及无历史对照均可回车，预览上述预算后LIVE。不需要修改内网项目配置或重启服务。重跑会新建结果目录，旧失败报告保留。

取样估算仍是UTF-8消息字节加封装余量，不是精确分词；不拿单次336423与旧估算值的比例作tokenizer校准。两阶段均使用新预算，保持完整日志和前20条历史参考策略，SDK不加输出token上限、不自动重试。本地真实CSV预检首轮40条、估算209237 tokens；这些是取样结果，不是新的模型效果。

[CHECK-PROXY-ERROR.sh](CHECK-PROXY-ERROR.sh) 保留作只读诊断工具，当前不用重复查同一错误。新版HTTP错误正文会以0600权限保存在阶段目录 `provider-error.json`，公开摘要只显示状态和文件名。旧失败报告的正文不会被追补。

## 2.2 当前步骤：复用已返回的回答，离线检查代码块格式

内网 `surge-ai-review-dvy79hvr` 已正常返回：当前40条、实际输入86919 tokens、输出401 tokens，`finish_reason=stop`。容量问题在这次请求已解除；失败代码是本地 `invalid_json_fence`，不是服务再次拒绝请求。原回答已保存，第二步AI复核尚未开始。

旧解析器要求开闭围栏各自独占一行且严格三个反引号。先增加完整单个代码块的排版兼容后，内网仍为blocked；随后用户贴出完整回答，已确认实际是 **完整JSON后漏写结束围栏**。新版对此保留原JSON并记录 `missing_closing_json_fence`，不补JSON内容；额外解释、多份答案、残缺JSON或错误结束标记仍明确报告。也修复了 `splitlines()` 改写JSON字符串内Unicode分隔符的问题。

打开 [RECHECK-SAVED-GENERATION.sh](RECHECK-SAVED-GENERATION.sh)，复制全文到内网终端执行。原报告目录默认 `surge-ai-review-dvy79hvr`，回车即可。没有LIVE提示，没有模型/Redis/数据库调用，不加载应用，不要求CSV或当前模型配置。

脚本校验原回答摘要、原请求摘要及请求内的上下文一致性，只以原请求中当时展示的规则和样本做格式、引用、字段检查，不把全量2205条扩大为原回答证据。结果另存为 `surge-generation-replay-*`，保留原始请求、回答、工具摘要和新解析器摘要；原报告和内网应用不修改。

执行后会自动显示JSON及草案正文。`status=passed` 只表示原回答通过当前校验，**不是第二步AI复核完成**。这是保留的纯离线工具，当前无需再单独执行它，下面的接续脚本已包含该校验。

## 2.3 当前执行：只继续第二步AI复核

原模型已经提出 `detail_process_name equal net1.exe && detail_process_tree contain net1 accounts`，并要求按 `event_content / net1 accounts` 补样。该建议来自已保存模型回答，没有把人工预期条件写入生成请求。

已在外网用用户贴出的回答和同一CSV离线复现：回答SHA256为 `8a8041967cb0eae77063b37a0508e0fb6dd27e60d6ab87c614791d21f802569c`，生成请求SHA256为 `591944c7b1c4a8ea180fdc9bc25f6bc6253475648eccbf721def5007403bcc48`，均与内网记录一致。新解析器通过完整格式和引用检查；这次校验没有调用模型，也没有执行过滤条件。

打开 [CONTINUE-AI-REVIEW.sh](CONTINUE-AI-REVIEW.sh)，复制全文到内网Mac终端执行：

1. 原报告目录直接回车，默认 `surge-ai-review-dvy79hvr`。
2. 工具自动离线校验原回答、读取原来源CSV并准备复核。当前应预览38条材料：10条原引用、28条新增；历史仍为0。无需重填CSV位置。
3. 看到 `generation_reused=true`、`max_sdk_requests=1` 后，输入 `LIVE` 只调用第二步AI复核。
4. 贴回自动显示的JSON及报告正文。结果另存 `surge-review-only-*`，原报告及内网应用不修改。

修复了补样时的混合告警遗漏：按AI指定的字段和文本，分别找含该文本和不含该文本的日志所在告警，整条原文带入；同一告警不重复，最多两条指定例子，其余按时间补样。不引入跨topic字段聚类，不执行建议过滤条件。此次本地预览已包含已知混合行为告警，但不据此声称复核覆盖全部少数场景。

复核预算仍为262144×80%，本地预览估算207001 tokens、224287输入字节；SDK不设输出token上限，超时600秒、最多一次请求，代理内部尝试次数未知。复核使用原规则及原背景，允许维持、修改或撤回草案；历史缺失仍不能证明前后变化。

新摘要区分原生成调用和本次调用：生成阶段 `transport_origin=saved_request`、`sdk_request_calls_this_command=0`，旧usage保留；顶层SDK次数只计这次复核。重复执行并再次输入LIVE会重新复核，不会重新生成。出现失败时先检查保存结果，不自动重试。

## 2.4 已执行：离线恢复已返回的AI复核结果

内网 `surge-review-only-8s9i3edf` 已完成第二步请求：实际输入85572、输出420 tokens，约30秒，`response_received=true`、`finish_reason=stop`。最初报 `unexpected_reply_field`；用户随后回传原文，字段实际是 `sample_alert_idsした`。回答虽然维持原条件，却同时声称存在其他活动及全部属于同类，不能仅凭格式恢复认定复核可靠。

已调整格式处理：额外顶层字段原样放入 `model_extra_fields`，记录 `extra_reply_fields_preserved`，报告完整展示补充意见；不因多一个说明字段否定整份回答，也不把它合并到自动执行开关或校验状态。核心status、建议、条件、引用和字段名仍校验。若生成阶段有补充字段，后续请求另以模型数据保留，不静默丢失其语义。

打开 [RECHECK-SAVED-REVIEW.sh](RECHECK-SAVED-REVIEW.sh)，复制全文到内网终端执行。目录提示直接回车，默认读取 `surge-review-only-8s9i3edf`。**没有LIVE提示，不需要代理运行，不读取当前CSV或服务配置，不调用模型/Redis/数据库。**

脚本逐阶段核对原请求与回答摘要，并确认复核收到的是原草案、共用样本未被替换。只使用各自请求里当时可见的40条/38条材料，结果另存 `surge-final-replay-*`。通过时保存 `recommendation.json`，自动打印完整报告及额外字段；`status=passed` 表示保存回答已通过校验，实际建议看 `analysis_status` 与原文，不能等同实际降量已验证。若另有核心字段问题，报告直接展示错误位置及已保存的复核原文，便于继续定位。

贴回自动显示的JSON和正文即可。生成、复核的原usage保留在 `original_model_transport`，本次SDK计数均为0；原失败报告不修改，内网应用代码也不安装。

## 2.5 已执行：v2提示词复核同一批材料

打开 [REVIEW-WITH-NEW-PROMPT.sh](REVIEW-WITH-NEW-PROMPT.sh)，复制全文到内网Mac终端。模型代理保持运行，无需安装应用或重启服务。

1. 原报告目录直接回车，默认 `surge-review-only-8s9i3edf`。
2. 预览应显示 `generation_reused=true`、`review_samples_reused=true`、`review_prompt_version=rule-surge-generate-and-review-v2`，当前38条、历史0条。
3. 输入 `LIVE`，最多发起1次SDK请求，只重新进行第二步复核。代理内部可能重试。
4. 贴回最后JSON和自动打印的报告。结果另存 `surge-review-v2-*`。

新提示词分开生成与复核目标，提供完整8字段JSON模板。复核须说明支持证据、不同活动、具体条件边界，以及保留/收窄/撤回的理由；同一告警中的不同日志分别判断，不能因其他行为数量少就忽略。要求说明文字相互一致，不编造精确样本数或降量率；没有写入本案例的预期命令或条件答案。

新脚本保持原生成请求与回答、原复核样本内容及顺序、运营背景、模拟规则不变，只更新复核system提示词。旧复核回答仅留作追溯，不作为模型输入；原报告不改。原38条材料按提示词增量计算，预计输入估算207577（旧207001），低于209715预算；实际以运行预览为准。SDK仍不设输出token上限，超时600秒。

本次已观测的 `sample_alert_idsした` 仅在无歧义时映射到原键名，仍核对引用ID，并保存原字段、转换记录和警告；不对未知键名做模糊猜测。此兼容不修改模型原文，也不能证明其中业务判断正确。新版提示词是否改善真实复核内容，需要这次内网回答验证。

## 2.6 已完成：完整保留建议，显示待调整字段

复制 [RECHECK-SAVED-REVIEW.sh](RECHECK-SAVED-REVIEW.sh) 全文到内网终端，目录直接回车，默认读取`surge-review-v2-ly8xsiwm`。无需代理运行、没有LIVE提示、不调用模型，不改内网应用和原报告。

这次应显示`status=needs_revision`、`analysis_status=proposal`、`recommendation_ready=false`，一次列出`filter.A.name`和`filter.B.name`两个问题，报告展示全部建议、条件、复核说明及可用字段目录。保存为`draft-recommendation.json`，不会输出可直接使用的`recommendation.json`。复核阶段不再补样，模型多给出的`sample_request`会原样留在`ignored_sample_request`，警告`review_sample_request_ignored`，不阻断主要建议。

代码现在区分三件事：请求/JSON核心结构失败仍为失败；已返回但条件字段待调整保留为`needs_revision`；格式及字段检查通过才按原状态交付。可见字段名不等于字段值承载目标含义，结构通过仍不是语义正确、引擎验证或降量证明。

第一步若有字段问题，原定第二步AI复核会收到全部问题及实际字段目录进行修正，不增加调用次数。最终仍有问题则呈现待调整全文，不猜测改名、不重复调用。真正的格式失败也自动展示保存原文和错误位置，减少手动读取日志。

提示词v3已加入从本次原日志字典键生成的`visible_field_names`，要求逐字选择，不把平铺下划线字段改为点分路径，不使用字符串内JSON中的虚拟字段。还要求解决“字段实际不包含目标内容”的自我矛盾。补充字段目录计入输入预算；旧保存请求继续保持原样，重新复核入口仅添加可追溯的派生目录，原38条材料不变。没有硬编码本案例的预期过滤答案。

同样本v3预览输入估算208913，仍低于209715。离线恢复已在内网完成，输出surge-final-replay-ci_wegug；两个错误字段均保留，ignored_sample_request正确记录。后续v3已实测，见2.8节。所有改动仍属于实测工具，内网自动分析后台尚未安装。

## 2.7 已执行：v3实际字段目录复核

复制最新版 [REVIEW-WITH-NEW-PROMPT.sh](REVIEW-WITH-NEW-PROMPT.sh) 全文到内网终端。模型代理保持运行；无需安装应用或重启服务。

1. 原报告目录直接回车，默认`surge-review-v2-ly8xsiwm`。读取原始请求目录，不填离线恢复目录`surge-final-replay-ci_wegug`。
2. 预览应显示`review_prompt_version=rule-surge-generate-and-review-v3`、`generation_reused=true`、`review_samples_reused=true`、当前38条/历史0条、`visible_field_count=62`。`estimated_input_tokens`是估算输入，实际用量在请求后记录。
3. 输入`LIVE`，只进行一次新的AI复核SDK请求，代理内部可能重试。结果另存`surge-review-v3-*`，贴回最后JSON和自动显示的报告。

预览中的`generation_filter`是第一步原草案，不是这次模型的新结论。脚本保留原生成回答及同一批38条原文，只增加实际字段目录与v3提示词；不重做生成，不输入手工预期答案，也不把旧复核回答作为新输入。检查字段真实内容由AI完成，不执行程序过滤试算；最终若仍有问题会保留为待调整建议。

本次验收看两点：条件采用实际字段名称；条件与所选字段的原值含义一致、能区分样本中的其他活动。仅`reviewed/recommendation_ready=true`并不足以证明这两点或线上降量，仍需阅读AI给出的条件和解释。

## 2.8 已执行：移除旧草案，独立分析同一批材料

v3已收到模型正文：实际输入86049、输出744 tokens，约22.063秒，`finish_reason=stop`。回答在`review_notes`后添加无键名字符串，构成无效JSON；同时仍选择进程树匹配命令，并一面称没有不同活动，一面提到混合告警中的其他进程。这些是实质问题，不能通过离线补标点视为通过。38条告警可能有39条日志，二者数量不能直接比较。

源码核对表明，每次复核都携带最初的完整错误草案。草案影响后续判断是待验证的解释，尚不能认定唯一原因。现在保留同样本、同规则、同运营背景，实际去掉旧草案及旧复核文字，要求模型先引用逐日志原文证据、说明不同活动，再给自己的条件。没有把人工预期字段或条件写入提示词。

打开 [INDEPENDENT-AI-ASSESSMENT.sh](INDEPENDENT-AI-ASSESSMENT.sh)，复制全文到内网Mac终端；代理保持运行，无需重启API或worker。

1. 原报告目录回车使用`surge-review-v3-0uw8bmkc`。保存的请求和回答摘要会先核对，复用材料仍与原CSV逐条核对。
2. 预览`ready_for_independent_assessment`、`current_count=38`、`history_count=0`、`new_current_count=0`、`original_proposal_not_provided=true`、`original_review_not_provided=true`。
3. 输入`LIVE`，最多一次新的SDK请求，超时600秒；SDK不重试，代理内部可能重试。不设置输出token上限，输入沿用当前262144×80%估算预算，不再次缩小材料范围。
4. 贴回最后JSON与报告。结果另存`surge-independent-*`，原报告保留；新请求、原始回答和transport在`assessment/`。

新请求显式携带`response_format={"type":"json_object"}`，记录`requested_response_format`及新的请求配置摘要。源码包里的8003适配器转发此字段，但4001 LiteLLM配置包含`drop_params:true`，实际运行实例是否透传及上游是否强制均未验证，因此记录`response_format_enforcement_verified=false`。JSON模式不能证明字段选择或业务判断正确；若参数被拒绝也不会静默移除并重试。

这次是**独立建议**，不是旧草案完成两阶段AI复核；摘要明确`review_method=independent_reassessment`、`ai_review_completed=false`、`semantic_quality_verified=false`。它保留已抽取的38条样本，不新增抽样；此前这些样本的选取仍受第一轮补样请求影响，不能称为完全独立的随机验证集。建议可读但字段待调整时保存为草案；JSON损坏时原文保留为失败，不自动修补。验收仍需核对条件字段原值与目标含义是否一致、不同活动是否被正确区分。

本工具和可选JSON模式已进入本地源码及测试，内网仍只运行内存中的复制脚本，未改应用代码。没有程序过滤试算、线上覆盖率计算或规则提交。

## 2.9 内网实测：已返回目标场景的建议

用户回传`surge-independent-d2lva4bc`：`status=proposal`、`recommendation_ready=true`、无输出警告或校验问题。38条当前告警、0条历史；原始材料和顺序复用，未提供旧草案、旧复核正文或人工预期条件。一次SDK请求，返回模型`deepseek-v4-flash`，实际输入85667、输出791、合计86458 tokens，约22.096秒，`finish_reason=stop`。回答SHA为`a61c9ab85e5c9875f3752464a5264b3026e2dd80c5558cf81458c2fd9ec46fea`，请求配置SHA为`7736803b50a61a184e399304b5119cd4acc319b7575b685e679be3a02ddff875`。

模型返回条件为`detail_process_name contain net1.exe && event_content contain net1 accounts`，topic为`security_qthids`。与早期错误草案相比，B已选到实际承载命令的字段；模型也明确识别出混合告警中的`mshta`活动。因此本案例已经取得与用户目标相符的AI推荐，而非助手手工替换字段得到的条件。

仍需准确解释原回答：正文写了进程名“等于”，JSON实际为`contain`；其“38条里34条”等数量未经程序统计核对，不作为覆盖率或源数据结论。模型提出“历史上无利用该命令的真实攻击”的前提也未验证，且不应成为要求用户重复证明已确认运维背景的新统一门槛。原文、条件均保留；若后续选择严格等值，应作为明确的新修订，不能默改原模型输出。模型自行提及程序试算不改变用户不执行试算的约定。

这是一个案例的一次独立分析成功，不证明方法已稳定，也不能把改善唯一归因于删除旧草案或JSON模式；本次两者同时变化，服务是否强制JSON仍为未验证。`ai_review_completed=false`表示没有为这条新建议再做第二次AI复核；`semantic_quality_verified=false`及`filter_coverage_measured=false`继续保留。`recommendation_ready`仅表示结构、引用、表达式和字段检查通过，不是自动上线授权。条件保留其他活动的实际效果及ZEUS执行语义未验证。

无需重复这次模型调用。下一步收敛正式生成/复核流程：让模型先根据原文独立识别场景，再核对建议，避免旧草案决定后续字段选择；沿用可选JSON模式和原文保存。正式接线与内网安装仍待实施，本次未改应用、未提交告警、未回调或修改规则。

## 2.10 当前排查：256K上下文限制来自哪一层

用户提供2026-10-19更新的EAGW OpenAI兼容接口文档，并要求在正式落地前解释内网256K与官方1M的差异。仅核对源码、公共官方文档及既有日志；不使用文档示例凭据，不尝试用大请求探测容量。

已有证据：

- 用户日志记录服务拒绝336423输入tokens，并明确声明上下文262144。6Z的262144常量是在该错误之后依据观测设置的本地取样预算，不是通过SDK请求改变服务上下文。当前SDK不传`max_tokens`或`max_completion_tokens`。
- 用户贴的EAGW文档将`max_tokens`定义为输出上限，缺省按剩余上下文计算；[DeepSeek官方API](https://api-docs.deepseek.com/api/create-chat-completion/)也区分输出上限与输入/输出共用的上下文。改变该参数不能让超过256K的输入进入一个256K服务。
- [DeepSeek官方价格页](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)当前确实列1M；官方旧`deepseek-v4-flash`别名现映射V4.1-Flash-1017。这个官方别名更新不能推断内网同名路由或部署已经升级。
- 上传源码包中`litellm_proxy_config.local.yaml:46–50`将Pub别名送往本机8003；`openai_completion.py:179–183`的Pub配置是OPENAI类型、上游模型`deepseek-v4-flash`，指向另一内网兼容端点，而非本文EAGW URL。`DeepSeek_V4_Flash_My`才是EAGW分支。`_openai_payload:390–397`仅复制请求体并替换模型名，没有注入256K上下文或默认输出token值。磁盘源码不能替代运行中路由证明。
- 本地YAML另有`max_chat_completion_tokens:8192`。用户回传实际安装LiteLLM为1.80.0；核对该版本官方wheel的1194个Python文件，没有此配置名的读取。配置加载器对未知项直接`setattr(litellm, key, value)`，因此原版中只是无人使用的模块属性，不能据此声称设置了8K输出上限。内网包有无私有修改未验证。日志中主备服务均返回256K，上传源码包未包含该远端fallback映射，不能直接认定为本地LiteLLM设置。
- 找到与错误文本完全对应的[SGLang v0.5.4官方校验代码](https://github.com/sgl-project/sglang/blob/v0.5.4/python/sglang/srt/managers/tokenizer_manager.py#L573)：先判断输入是否超过`self.context_len`，随后才另行检查输入加指定输出量。该值来自服务模型配置。错误高度吻合此实现，但代理可能转述或复用文本，不能仅凭文本确认实际框架及版本。

补充框架线索：`chat_template_kwargs`是聊天模板的附加变量，[vLLM官方文档](https://docs.vllm.ai/en/v0.33.0/features/reasoning_outputs/)与[SGLang官方协议代码](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/entrypoints/openai/protocol.py)均支持，不能单靠参数名区别二者。结合完全对应的输入限长报错，更倾向SGLang或使用其校验实现的上游；`enable_thinking`及`reasoning_effort`是否生效还依赖实际模板，均不是上下文扩容开关。

仍待查明：当前路由对应的部署/模型版本，以及容量是否受服务启动配置、模型配置或网关限制。[SGLang的`--context-length`](https://docs.sglang.io/docs/advanced_features/server_arguments)与[vLLM的`max_model_len`](https://docs.vllm.ai/en/stable/api/vllm/config/model/)都是部署侧上下文配置例子，不是已核实的内网启动参数。用户文档同时覆盖多种推理框架，本身不能识别当前Pub上游采用哪一种。

用户已执行 [CHECK-CONTEXT-ROUTE.sh](CHECK-CONTEXT-ROUTE.sh)：运行中8003报告所选Pub路由为OPENAI，与磁盘PRD配置的同一内网`/v1`地址一致；磁盘上游模型为`deepseek-v4-flash`。本次只读取本机health及源码，未发送上游请求。运行中适配器去向已确认；远端模型revision、框架、上下文配置仍未知。无需重复执行此脚本。

地址归属澄清（再次只读核对上传ZIP）：`PRD_MODEL_CONFIGS`是本项目调用模型的配置分组，不是本项目生产服务的监听地址。467个UTF-8文本文件中，`30.154.3.119`只出现于`llm_service/openai_completion.py:181`的Pub上游`base_url`。`util/root_config.py:339–341`的“自己服务的测试和生产地址”注释另记生产`30.160.87.182:80`；注释不代替当前部署证明，但两类地址在源码中分开。LOCAL在`openai_completion.py:205–211`合并STG和PRD模型配置，因此本地运行也能选到该PRD模型上游。

ZIP的PRD LiteLLM配置仍先将Pub转交本机8003，再由适配器请求该上游。8003的`/v1/models`（`openai_completion.py:2983–2993`）仅返回别名及基础字段；整个ZIP未包含`/model/info`实现或本次远端8条后端标签，也未找到262144数值/常见表达式、`max_model_len`、`max_input_tokens`或SGLang/vLLM部署配置。因此这份源码里找不到该256K设置符合目前证据。另有`util/root_config.py:337`的`MAX_CONTEXT_LENGTH=int(8092*0.7)`，用于知识检索文本字符切片，与本次上游256K token校验不是同一限制。若上游也由用户团队维护，应查看该地址实际模型网关及推理服务的部署配置，不能由PRD变量名推断它与此ZIP是同一服务。

LiteLLM1.80.0核对来源：[PyPI版本元数据](https://pypi.org/pypi/litellm/1.80.0/json)，wheel SHA-256为`fd0009758f4772257048d74bf79bb64318859adb4ea49a8b66fdbc718cd80b6e`。关键源码与commit `06eeb28c8fe30ee6e90671408dcb23bbdf691e7a`逐字节一致：[未知配置项处理](https://github.com/BerriAI/litellm/blob/06eeb28c8fe30ee6e90671408dcb23bbdf691e7a/litellm/proxy/proxy_server.py#L2301-L2305)、[OpenAI参数列表](https://github.com/BerriAI/litellm/blob/06eeb28c8fe30ee6e90671408dcb23bbdf691e7a/litellm/llms/openai/chat/gpt_transformation.py#L134-L200)、[额外参数进入extra_body](https://github.com/BerriAI/litellm/blob/06eeb28c8fe30ee6e90671408dcb23bbdf691e7a/litellm/utils.py#L4131-L4175)。`drop_params`负责删除不支持的已知OpenAI参数；`chat_template_kwargs`通过额外请求体传递，不依赖`drop_params`开启，也不会改变服务上下文。

[CHECK-UPSTREAM-CONTEXT.sh](CHECK-UPSTREAM-CONTEXT.sh)已由用户在内网执行，不需重跑。脚本重新核对所选模型、AST字面量配置及回环health与已确认路由一致后，使用该路由已有凭据，只GET同一远端origin的四个固定元数据接口。没有模型、Redis、数据库调用或应用修改；结果为`metadata_observed`。

本次回传新增证据：

| 来源 | 已观察结果 | 能证明的范围 |
|---|---|---|
| `/v1/models`所选模型 | `max_input_tokens=262144`、`max_output_tokens=8192` | 远端目录声明输入256K、输出8K；不保证二者可同时用满 |
| `/model/info?model=deepseek-v4-flash` | 同一模型名匹配8条配置；前5条声明输入262144，后3条未返回输入额度 | 多个后端映射，不能声称8条全支持256K、全启用或属于8个独立模型 |
| 后端标签 | 包含`deepseek/deepseek-v4-flash`、`deepseek-v4-flash-0805`、`deepseek_v4`、`deepseek-v4-flash-810e--0806`、`deepseek/dsv4-0731`等 | 标签不证明具体权重revision、发布日期、官方直连或实际命中后端 |
| `/get_model_info`、`/get_server_info` | 均404 | 当前网关没有开放这些路径，不能据此否定SGLang或认定vLLM |

结合先前输入336423被服务以262144上下文拒绝的实测错误，256K已定位到远端服务链路，不是客户端漏传`max_tokens`导致的默认值。究竟由哪个后端、服务启动参数或网关策略决定，仍未核实。`context_capacity_status=unknown`是本工具未取得明确部署上下文元数据的状态，不抹掉已取得的输入额度和拒绝记录。

远端`max_output_tokens=8192`与本地无消费者的`max_chat_completion_tokens:8192`是不同层的证据：前者是上游目录登记值，是否作为请求默认值或硬上限仍未知；不能因数值相同而认定因果，也不据此给客户端补8K输出限制。[LiteLLM路由文档](https://docs.litellm.ai/docs/routing)说明相同模型名可以关联多个部署；本次实际选择和主备策略需平台日志确认。

这些接口依据[LiteLLM模型管理文档](https://docs.litellm.ai/docs/proxy/model_management)及[SGLang元数据接口源码](https://github.com/sgl-project/sglang/blob/v0.5.4/python/sglang/srt/entrypoints/http_server.py#L438-L468)。只提取所选模型和允许的数值、版本字段，保留来源；代理目录可能只是登记能力，不等于部署生效值。`max_tokens`单独出现不作为上下文容量依据。未取得明确关联的部署元数据时，仍需平台提供该路由的模型revision、主备部署配置、最终生效context length，以及是否存在独立1M路由；不得猜测“改大max_tokens即可扩容”。

当前下一步：向该内网服务维护方核对2026-11-03 16:43:21失败请求实际命中的后端及备用、权重revision、推理框架和生效上下文参数；说明256K是部署设置还是网关策略，8192是目录值、默认输出还是硬上限。如有1M服务，需给明确的可调用模型名/路由并确认所有可能命中的主备容量一致。不再继续猜元数据路径或重复提交大请求；现有规则建议结果保留，用户已于2026-11-04恢复实施；新后台安装与DEV历史对照见[第7步](../step07_surge_runtime/README.md)。容量问题仍由平台并行核对。

### 2.11 `max_input_tokens`可以调高，但尚未做修改实验

用户质疑“登记值能否调大”后，已核对LiteLLM 1.80.0：部署 `model_info` 可覆盖内置目录的 `max_input_tokens`；`/model/info` 也会从目录补齐缺失字段，不能据此断言维护者显式设置了256K。启用 `enable_pre_call_checks` 后，该值才参与这一层的输入预检查。调高目录值可以放宽该层检查，不能替下游修改实际生效的上下文。

此前原错误为 `DeepseekException` 包装 `decode_bad_request`，与原版LiteLLM预检查的 `Max Input Tokens=..., Got=...` 格式不同，更像下游拒绝，但没有远端部署配置和路由固定实验，不能声称8条映射都已证实只有256K。**没有修改远端配置后复测。** 本机1.80.0也不等于已确认远端版本。

来源：[官方自定义输入上限说明](https://docs.litellm.ai/docs/proxy/reliability#custom-max_input_tokens-per-deployment)、[目录覆盖源码](https://github.com/BerriAI/litellm/blob/06eeb28c8fe30ee6e90671408dcb23bbdf691e7a/litellm/router.py#L5959-L5969)、[预检查](https://github.com/BerriAI/litellm/blob/06eeb28c8fe30ee6e90671408dcb23bbdf691e7a/litellm/router.py#L6966-L7047)、[model/info补齐目录值](https://github.com/BerriAI/litellm/blob/06eeb28c8fe30ee6e90671408dcb23bbdf691e7a/litellm/proxy/proxy_server.py#L7473-L7507)。平台应固定同一后端，对比元数据预检查和实际推理部署上下文，再验证调大是否有效。

## 3. 取样方式

- 当前批次默认从本文件目标告警最早时间开始；可复用的材料函数也支持显式传入突增起点。
- 历史对照只取该起点之前、同企业/topic/规则最近20条不同告警，不要求落在前1小时。历史不足就用现有数量，实际起止时间保留在coverage中。
- 当前CSV不含前序材料时，历史参考为0；不把本次最早20条冒充历史，也不把缺失当作历史零告警。可另提供一份相同格式的历史导出CSV，工具自动筛选最近20条。
- 初始当前样本按时间分散选取，预留另一组供AI复核。模型可以提出一个实际字段和文本，程序再找包含/不包含该文本的例子供复核，不执行模型过滤条件、不汇总条件覆盖率。
- 复核使用新上下文、草案引用的原文、新选当前样本及共用历史参考。只有一条等场景无法补新样本时会明确记录；不把共用历史称为独立验证集。
- 不写死某种topic的进程、命令或子规则字段，不做固定字段聚类。保留完整告警关联和原始日志，不提供历史模型回答作为正常业务标签。
- 按这条内网路由报告的262144上下文、80%即209715输入token估算预算；整条告警选择，放不下时记录取样范围，不截断日志。必要时减少未被引用的历史材料以保留当前样本，报告区分可用数量和实际展示数量。SDK不传输出token上限，超时600秒。

## 4. 本地验证与在线接入边界

```bash
backend/.venv/bin/python -m unittest discover -s validation/rule_refine/implementation/step06z_surge_recommendation -p 'test_*.py' -q
backend/.venv/bin/python validation/rule_refine/implementation/step06z_surge_recommendation/build_command.py
bash -n validation/rule_refine/implementation/step06z_surge_recommendation/REVIEW-SURGE-WITH-AI.sh
```

**212项流程离线测试通过**，此前6项适配器测试通过；Ruff格式/检查、9份脚本的Python3.11语法和Bash语法检查通过。测试覆盖历史严格早于起点、去重/冲突、时间排序、跨topic字段、混合告警补样、引用原文保留、两次SDK请求、不设置输出token上限、失败不误报成功、保存原始回答及复制脚本一致性，以及围栏变体/遗漏、Unicode值保留、离线复核证据与原报告不变。额外字段保留且不覆盖控制元数据；精确键名兼容仍校验引用、拒绝冲突。新版复核入口验证同一材料完整保留、旧复核答案不入模型、旧请求补充派生字段目录、原草案不重生成及最多一次SDK调用。字段问题全量保留为待调整，生成阶段可由复核修正；最终不可用条件只保存草稿，忽略无关补样不阻断建议。独立入口另验证原38条材料复用、旧草案和旧回答均不输入、一次JSON模式请求且不设输出token参数、损坏JSON原文保留、可选说明缺失仍生成完整报告、来源变化不发布建议，以及拒绝LIVE时零调用。新增元数据工具验证路由变化时不发远端凭据、固定GET、无代理/重定向、认证拒绝停止、凭据及无关模型信息不输出、输出token限制不冒充上下文证明；实际源码ZIP的AST提取兼容检查通过。使用人工响应/替身SDK，没有真实模型调用或上游请求。

实际CSV已离线读取：2205条目标告警、2217条完整日志、默认历史参考0。按新209715估算输入预算，首轮可放入40条完整告警（209237估算tokens）；复核条数取决于AI草案引用和补样请求。这里只证明材料读取和取样，不是模型效果。

`history.py` 的 `load_recent_history` 为后续在线接入准备：复用同一计数器的Redis索引与现有任务读取器，严格取突增起点前的最近引用，不设前1小时下界。现有Redis保留期仍影响可查询范围，超出保留期的历史为未知；结果还需材料层核对规则范围和原文冲突。它不写Redis或数据库，不直接进入本次CSV脚本。在线消费者接线、内网真实模型建议质量及ZEUS联调尚待各自验证。
